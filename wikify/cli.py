"""wikify CLI (implementation.md §4).

Two halves around agent synthesis, handed off via files:

    wikify prepare <slug>   → Stages 0-4: index, build graph, emit packets + plan
       ↓  [agent writes one page per packet, driven by SKILL.md]
    wikify finalize <slug>  → Stage 6: lint, assemble index, update state

``plan`` is a dry-run delta; ``lint`` re-runs the citation gate alone. The
deterministic half never calls a model; the agent half never parses protobuf.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import replace
from pathlib import Path

import typer

from . import (
    acquire,
    assemble,
    bazel_cc,
    connect as connect_mod,
    coverage as coverage_mod,
    diff,
    discover,
    docs as docs_mod,
    fix as fix_mod,
    languages as lang_mod,
    lint,
    packet,
    scip_index,
    state as state_mod,
    subsystems as subsystems_mod,
    verify as verify_mod,
)
from .config import Concept, RepoConfig, load_config

app = typer.Typer(add_completion=False, help="Ingest a repo into a grounded markdown wiki.")


# --------------------------------------------------------------------------- #
# Layout helpers
# --------------------------------------------------------------------------- #
class Paths:
    def __init__(self, root: Path, slug: str) -> None:
        self.root = root
        self.slug = slug
        self.cache = root / ".cache"
        self.raw = root / "raw"
        self.config = root / "config" / f"{slug}.md"
        self.scip = self.cache / "scip" / f"{slug}.scip"
        self.scip_cpp = self.cache / "scip" / f"{slug}.cpp.scip"  # C++ index (scip-clang)
        self.state = state_mod.state_path(self.cache, slug)
        self.wiki = root / "wiki"
        self.set_wiki_subdir("code")  # default; _load() overrides from config

    def set_wiki_subdir(self, subdir: str | None) -> None:
        """Place this repo's wiki at ``wiki/<subdir>/<slug>`` (subdir="" → ``wiki/<slug>``)."""
        self.wiki_subdir = subdir or ""
        self.wiki_base = self.wiki / self.wiki_subdir if self.wiki_subdir else self.wiki
        self.wiki_slug = self.wiki_base / self.slug


def _today() -> str:
    return datetime.date.today().isoformat()


def _scip_clang_bin() -> str:
    """The vendored scip-clang if present (glibc-compatible build), else PATH."""
    vbin = Path(__file__).parents[1] / "vendor" / "bin"
    for cand in sorted(vbin.glob("scip-clang*"), reverse=True):
        if cand.is_file():
            return str(cand)
    return "scip-clang"


def _load(root: Path, slug: str) -> tuple[Paths, RepoConfig]:
    p = Paths(root, slug)
    if not p.config.exists():
        typer.echo(f"error: no config at {p.config}", err=True)
        raise typer.Exit(2)
    cfg = load_config(p.config)
    p.set_wiki_subdir(cfg.wiki_subdir)
    return p, cfg


def _source(cfg: RepoConfig, repo: str | None) -> str:
    src = repo or cfg.repo
    if not src:
        typer.echo("error: no repo source (pass --repo or set 'repo:' in config)", err=True)
        raise typer.Exit(2)
    return src


def _find_docs(repo_dir: Path, patterns: list[str]) -> list[str]:
    """Repo-relative project-doc paths matched by ``cfg.docs`` globs (sorted, deduped).

    Skips vendored/build/checkpoint noise so the doc-ingest worklist is the real
    authored docs (README / docs/), not generated or third-party markdown."""
    skip = ("bazel-", ".git/", "third_party/", "vendor/", "node_modules/",
            ".ipynb_checkpoints/", "build/")
    out: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in sorted(repo_dir.glob(pat)):
            rel = m.relative_to(repo_dir).as_posix()
            if rel in seen or any(s in rel for s in skip) or not m.is_file():
                continue
            seen.add(rel)
            out.append(rel)
    return out


def _expand_shards(repo_dir: Path, patterns: list[str]) -> list[str]:
    """Expand ``index_shards`` globs to sorted, de-duped repo-relative paths."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in sorted(repo_dir.glob(pat)):
            rel = m.relative_to(repo_dir).as_posix()
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def _scip_indexes(p: Paths) -> list[Path]:
    """Every ``.scip`` for this slug — ``<slug>.scip`` (python) + ``<slug>.<lang>.scip`` (cpp,
    ts, go, rust). One graph is built from all of them, so languages merge automatically."""
    d = p.cache / "scip"
    return sorted(set(d.glob(f"{p.slug}.scip")) | set(d.glob(f"{p.slug}.*.scip")))


def _graph(p: Paths):
    """Build the graph, merging every language's SCIP index present in the cache."""
    indexes = [scip_index.parse_index(f) for f in _scip_indexes(p)]
    return scip_index.build_graph(*indexes)


class Agenda:
    """The DERIVED agenda for one run: concepts + how each is seeded and scoped."""

    def __init__(self, cfg: RepoConfig, seedmap: dict, scopes: dict, n_discovered: int,
                 mode: str, subsystems: list, defaulted: bool,
                 scope_sets: dict | None = None) -> None:
        self.cfg = cfg                  # RepoConfig with ``concepts`` = the full agenda
        self.seedmap = seedmap          # concept slug → seed monikers (discovered/subsystem)
        self.scopes = scopes            # concept slug → rendered ``## Scope`` block
        self.scope_sets = scope_sets or {}  # concept slug → unit member monikers (budget scope)
        self.n_discovered = n_discovered
        self.mode = mode                # "subsystems" | "modules"
        self.subsystems = subsystems    # planned Subsystem objects (subsystems mode)
        self.defaulted = defaulted      # mode came from the fresh/existing rule, not config

    @property
    def concepts(self):
        return self.cfg.concepts

    def summary(self) -> str:
        return (f"agenda: {self.n_discovered} discovered + "
                f"{len(self.cfg.concepts) - self.n_discovered} config = "
                f"{len(self.cfg.concepts)} concepts ({self.mode})")


def _agenda_mode(cfg: RepoConfig, state: dict | None, override: str | None = None) -> tuple[str, bool]:
    """Resolve the planner mode: explicit (CLI/config) wins; else a fresh silo plans by
    subsystems and an existing silo keeps module discovery (no surprise rebuilds)."""
    if override:
        return override, False
    if cfg.agenda:
        return cfg.agenda, False
    fresh = not state or not state.get("pages")
    return ("subsystems" if fresh else "modules"), True


def _derive_agenda(graph, cfg: RepoConfig, state: dict | None = None,
                   agenda_override: str | None = None) -> Agenda:
    """The DERIVED agenda (decision 8): a planner proposes units and auto-seeds concepts;
    config concepts override/extend on slug collision.

    ``subsystems`` (§10.11): directory-shaped units ranked by external fan-in, seeded from
    entry points + hubs, each packet carrying a ``## Scope`` block. ``modules`` (legacy):
    single modules ranked by centrality (``discover.discover_concepts``). Config entries
    with ``seeds: (subsystem: <prefix>)`` are seeded from that directory in either mode.

    Shared by ``prepare`` and ``plan`` so the dry-run models the real run."""
    mode, defaulted = _agenda_mode(cfg, state, agenda_override)
    seedmap: dict[str, list[str]] = {}
    scopes: dict[str, str] = {}
    scope_sets: dict[str, set[str]] = {}
    subs: list = []
    cfg_slugs = {c.slug for c in cfg.concepts}
    if mode == "subsystems":
        subs = subsystems_mod.discover_subsystems(
            graph,
            max_subsystems=cfg.agenda_max or subsystems_mod.DEFAULT_MAX_SUBSYSTEMS,
            exclude_globs=cfg.agenda_exclude,
        )
        # A config concept seeded from a directory REPLACES the planned unit(s) at or
        # under that prefix (renaming a unit must never build it twice).
        pinned = [c.subsystem for c in cfg.concepts if c.subsystem is not None]
        subs = [u for u in subs if not any(_covers(pfx, u.prefix) for pfx in pinned)]
        discovered_slugs = []
        for sub in subs:
            seedmap[sub.slug] = sub.seeds
            scopes[sub.slug] = subsystems_mod.render_scope(sub, graph)
            scope_sets[sub.slug] = set(sub.symbols)
            discovered_slugs.append(sub.slug)
    else:
        discovered = discover.discover_concepts(
            graph, max_deep=cfg.agenda_max or 24)
        discovered_slugs = [d.slug for d in discovered]
        seedmap.update({d.slug: d.seeds for d in discovered})
    # Config concepts seeded from a whole directory: re-derived every run.
    for c in cfg.concepts:
        if c.subsystem is not None:
            sub = subsystems_mod.subsystem_for_prefix(graph, c.subsystem, slug=c.slug)
            if sub is None:
                typer.echo(f"warning: concept {c.slug}: no library module under "
                           f"'{c.subsystem}' (seeds: (subsystem: ...))", err=True)
                continue
            seedmap[c.slug] = sub.seeds
            scopes[c.slug] = subsystems_mod.render_scope(sub, graph)
            scope_sets[c.slug] = set(sub.symbols)
    agenda = [Concept(slug=s) for s in discovered_slugs if s not in cfg_slugs] + cfg.concepts
    return Agenda(replace(cfg, concepts=agenda), seedmap, scopes, len(discovered_slugs),
                  mode, subs, defaulted, scope_sets)


def _covers(config_prefix: str, unit_prefix: str) -> bool:
    """Does a config ``(subsystem: <config_prefix>)`` entry cover a planned unit? Exact
    match, a descendant directory, or a community unit (``dir::stem``) whose dir matches."""
    cp = config_prefix.strip().strip("/")
    if cp == ".":
        cp = ""
    if unit_prefix == cp:
        return True
    dir_part = unit_prefix.split("::", 1)[0]
    if cp == "":
        return True                       # the repo root covers everything
    return dir_part == cp or dir_part.startswith(cp + "/")


_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _overview_link_warnings(silo: Path) -> list[str]:
    """Relative links in overview.md that point at nothing. The overview is not under the
    citation gate (it is synthesis over concept pages), so its task/question routing can
    only be checked for existence — as a warning, never a failure."""
    ov = silo / "overview.md"
    if not ov.exists():
        return []
    text = re.sub(r"```.*?```", "", ov.read_text(encoding="utf-8", errors="replace"), flags=re.S)
    missing: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        rel = target.split("#", 1)[0]
        if rel and not (ov.parent / rel).exists() and target not in missing:
            missing.append(target)
    return missing


def _write_agenda_file(p: Paths, agenda: Agenda, graph) -> Path | None:
    """Persist the planner's proposal (subsystems mode) for the skill to show the user."""
    if agenda.mode != "subsystems":
        return None
    out = p.cache / "plan" / f"{p.slug}.agenda.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(subsystems_mod.render_agenda(agenda.subsystems, graph, p.slug), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# Docs mode (source_type: docs) — the prose track (docs.py, design.md "Docs mode")
# --------------------------------------------------------------------------- #
def _prepare_docs(p: Paths, cfg: RepoConfig, acq) -> None:
    """Docs-mode prepare: no SCIP — enumerate docs, build the anchor map, emit one packet
    per doc. The agent then synthesizes topics/ + sources/ pages citing `src:` sections."""
    docs = docs_mod.enumerate_docs(acq.repo_dir, cfg.doc_globs)
    if not docs:
        typer.echo("no docs matched (check `doc_globs`); nothing to ingest.")
        return
    doc_map = docs_mod.build_doc_map(acq.repo_dir, docs)
    n_anchor = sum(len(i.anchors) for i in doc_map.values())
    typer.echo(f"docs: {len(doc_map)} document(s), {n_anchor} resolvable section anchor(s)")
    pkts = docs_mod.write_doc_packets(p.cache, p.slug, acq.repo_dir, doc_map, acq.commit, _today())
    # Persist the doc map for finalize (lint + coverage resolve against it).
    import json
    dm_path = p.cache / "docs" / f"{p.slug}.docmap.json"
    dm_path.parent.mkdir(parents=True, exist_ok=True)
    dm_path.write_text(json.dumps(
        {rel: sorted(info.anchors) for rel, info in doc_map.items()}), encoding="utf-8")
    typer.echo(f"wrote {len(pkts)} doc packet(s) → {p.cache/'packets'/p.slug}/")
    typer.echo(f"\nNow run agent synthesis (prompts/synthesis-docs.md), then "
               f"`wikify finalize {p.slug}`.")


def _finalize_docs(p: Paths, cfg: RepoConfig, repo: str | None) -> None:
    """Docs-mode finalize: gate `src:` citations against the doc map + coverage floor +
    assemble the docs index."""
    acq = acquire.acquire(_source(cfg, repo), p.slug, p.raw, ref=cfg.ref, mode=cfg.acquire)
    import json
    dm_path = p.cache / "docs" / f"{p.slug}.docmap.json"
    if dm_path.exists():
        raw = json.loads(dm_path.read_text(encoding="utf-8"))
        doc_map = {rel: docs_mod.DocInfo(relpath=rel, anchors=set(a)) for rel, a in raw.items()}
    else:  # fall back to re-deriving from the repo
        docs = docs_mod.enumerate_docs(acq.repo_dir, cfg.doc_globs)
        doc_map = docs_mod.build_doc_map(acq.repo_dir, docs)

    report = docs_mod.lint_docs(p.wiki_slug, doc_map)
    if not report.ok:
        typer.echo(f"\nLINT FAILED ({len(report.errors)} error(s)):", err=True)
        for e in report.errors:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)
    typer.echo("lint: OK — every source citation resolves.")

    cov = docs_mod.docs_coverage(doc_map, p.wiki_slug)
    typer.echo(cov.render())

    state = state_mod.load_state(p.state)
    state_mod.set_ref(state, acq.commit)
    state_mod.save_state(p.state, state)
    docs_mod.assemble_docs_index(p.wiki_slug, p.slug, acq.commit, _today(), cov)
    assemble.write_top_index(
        p.wiki_base, [d.name for d in p.wiki_base.iterdir() if d.is_dir()], _today())
    rel = f"{p.wiki_subdir}/{p.slug}" if p.wiki_subdir else p.slug
    typer.echo(f"assembled wiki/{rel}/index.md  (docs mode, commit {acq.commit[:10]})")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def prepare(
    slug: str,
    ref: str = typer.Option(None, help="Pinned commit/tag to ingest."),
    repo: str = typer.Option(None, help="Source path or git URL (overrides config)."),
    root: Path = typer.Option(Path("."), help="Project root."),
    reindex: bool = typer.Option(True, help="(Re)run scip-python."),
    install_indexers: bool = typer.Option(
        True, help="Auto-install missing on-demand indexers (TS/Go/Rust), announced; "
                   "--no-install-indexers prints guidance and skips the language instead."),
    agenda_mode: str = typer.Option(
        None, "--agenda", help="Planner: 'subsystems' (directory-shaped units, default for a "
                               "fresh silo) or 'modules' (legacy centrality). Overrides config."),
) -> None:
    """Stages 0-4: acquire, index, build graph, emit packets, print the plan."""
    p, cfg = _load(root, slug)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=ref or cfg.ref, mode=cfg.acquire)
    typer.echo(f"acquired {slug} @ {acq.commit[:10]}  ({acq.repo_dir})")
    if cfg.synthesis_focus.strip():
        typer.echo(f"synthesis focus (lens): {cfg.synthesis_focus.strip()}")

    if cfg.source_type == "docs":
        _prepare_docs(p, cfg, acq)
        return

    # Languages: explicit config wins; else detect from the repo (markers + extensions),
    # defaulting to python so existing python repos are unaffected.
    langs = cfg.languages or (lang_mod.detect_languages(acq.repo_dir) or ["python"])
    if langs != (cfg.languages or ["python"]):
        typer.echo(f"detected languages: {', '.join(langs)}")
    if "python" in langs and (reindex or not p.scip.exists()):
        if cfg.index_shards:
            targets = _expand_shards(acq.repo_dir, cfg.index_shards)
            typer.echo(f"indexing with scip-python ({len(targets)} shards, "
                       f"--target-only) ...")
            scip_index.run_indexer_sharded(acq.repo_dir, p.scip, targets,
                                           project_name=slug)
        else:
            typer.echo("indexing with scip-python ...")
            scip_index.run_indexer(acq.repo_dir, p.scip, project_name=slug)
    # C++ path (Stage 1, mixed-language): run scip-clang against the compile DB.
    # `bazel_targets` auto-generates the DB from bazel (build+aquery); otherwise a
    # pre-existing `compile_commands` path is used.
    if (cfg.bazel_targets or cfg.compile_commands) and (reindex or not p.scip_cpp.exists()):
        if cfg.bazel_targets:
            typer.echo(f"generating C++ compile DB from bazel ({cfg.bazel_targets}); "
                       f"first run does a full build to materialize headers ...")
            cc = bazel_cc.generate_compile_db(
                acq.repo_dir, cfg.bazel_targets, p.cache / "scip" / f"{slug}.compile_commands.json")
        else:
            cc = Path(cfg.compile_commands)
            if not cc.is_absolute():
                cc = acq.repo_dir / cc
        typer.echo(f"indexing C++ with scip-clang ({cc}) ...")
        scip_index.run_clang_indexer(acq.repo_dir, cc, p.scip_cpp,
                                     scip_clang_bin=_scip_clang_bin())
    # TS/JS, Go, Rust — SCIP indexers installed ON DEMAND (not by setup-vendor). When one of
    # these is present, ask the user to install its indexer, then run it; skip if declined.
    for key in lang_mod.AUTO_RUN:
        if key not in langs:
            continue
        lang = lang_mod.LANGS[key]
        out = lang_mod.scip_path(p.cache, slug, key)
        if not (reindex or not out.exists()):
            continue
        if not lang_mod.ensure_indexer(lang, auto=install_indexers):
            continue
        typer.echo(f"indexing {lang.label} with {lang.bin} ...")
        try:
            lang.run(acq.repo_dir, out)
        except Exception as e:  # one language failing shouldn't abort the others
            typer.echo(f"  {lang.label} indexing failed: {e}", err=True)
    graph = _graph(p)
    typer.echo(f"graph: {len(graph)} symbols")

    state = state_mod.load_state(p.state)
    ag = _derive_agenda(graph, cfg, state, agenda_override=agenda_mode)
    agenda_cfg, seedmap, agenda = ag.cfg, ag.seedmap, ag.concepts
    typer.echo(ag.summary())
    if ag.mode == "modules" and ag.defaulted:
        typer.echo("  (existing silo keeps module discovery; set `agenda: subsystems` in "
                   "config to plan by subsystem)")
    agenda_file = _write_agenda_file(p, ag, graph)
    if agenda_file:
        typer.echo(subsystems_mod.render_agenda(ag.subsystems, graph, slug))
        typer.echo(f"proposed agenda written → {agenda_file}. Review it before synthesizing: "
                   f"drop entries with `agenda_exclude:`, add/rename with "
                   f"`- **<slug>** — seeds: (subsystem: <prefix>)`, then re-run prepare.")

    hashes = diff.current_hashes(graph, acq.repo_dir)
    plan = diff.compute_plan(graph, acq.repo_dir, state, agenda_cfg, hashes)
    typer.echo(plan.render())

    todo = set(plan.todo)
    # The host wiki's shared concept vocabulary (wiki/concepts/) — handed to synthesis so
    # pages self-tag with `concepts:`, which Stage 7 connect resolves as authoritative.
    vocab = connect_mod.load_vocabulary(p.wiki, "concepts")
    built = 0
    for concept in agenda:
        if concept.slug not in todo:
            continue
        text, subgraph = packet.build_packet(
            graph, acq.repo_dir, slug, acq.commit, concept, cfg.tests, _today(),
            seed_monikers=seedmap.get(concept.slug), focus=cfg.synthesis_focus, vocab=vocab,
            scope=ag.scopes.get(concept.slug, ""),
            scope_symbols=ag.scope_sets.get(concept.slug),
        )
        pkt = packet.write_packet(p.cache, slug, concept.slug, text, subgraph)
        typer.echo(f"  packet → {pkt.name}  ({len(subgraph)} symbols)")
        built += 1
    if built == 0:
        typer.echo("nothing to build (converged).")
    else:
        typer.echo(f"\nWrote {built} packet(s). Now run agent synthesis, then `wikify finalize {slug}`.")

    # Doc worklist: glob the project's own docs (cfg.docs) for the last synthesis
    # step (doc-concept extraction, skills/prompts/ingest-docs.md). The docs stay in
    # place; we only record which to process, relative to the repo root.
    docs = _find_docs(acq.repo_dir, cfg.docs)
    if docs:
        manifest = p.cache / "docs" / f"{slug}.txt"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("\n".join(docs) + "\n", encoding="utf-8")
        typer.echo(f"docs: {len(docs)} project doc(s) to ingest → {manifest} "
                   f"(run the doc-concept step, then finalize)")


@app.command()
def finalize(
    slug: str,
    repo: str = typer.Option(None, help="Source path or git URL (overrides config)."),
    root: Path = typer.Option(Path("."), help="Project root."),
    fix: bool = typer.Option(False, help="Auto-repair deterministically-fixable lint errors first."),
) -> None:
    """Stage 6: lint the agent-written pages, assemble the index, update state."""
    p, cfg = _load(root, slug)
    if cfg.source_type == "docs":
        _finalize_docs(p, cfg, repo)
        return
    if not _scip_indexes(p):
        typer.echo(f"error: no SCIP index for {slug}; run `wikify prepare {slug}` first", err=True)
        raise typer.Exit(2)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=cfg.ref, mode=cfg.acquire)
    graph = _graph(p)

    # Stage 6b FIRST — emit module catalogs (the symbol homes). Citations resolve
    # against their frontmatter `symbols` map, so catalogs must exist before lint.
    # Source links default to a path relative to each catalog page (local repo);
    # cfg.source_url overrides with a base URL, or "" disables them.
    catalogued, catalog_paths = coverage_mod.emit_catalogs(
        graph, p.wiki_slug, repo_dir=acq.repo_dir, source_url=cfg.source_url,
        collapse=cfg.coverage_collapse, exclude=cfg.coverage_exclude)
    typer.echo(f"catalog: wrote {len(catalog_paths)} module page(s)")

    if fix:
        edits, report_lint = fix_mod.fix_silo(p.wiki_slug, graph, p.cache, slug)
        typer.echo(f"fix: applied {edits} repair(s); "
                   f"{len(report_lint.errors)} error(s) remain")
    else:
        report_lint = lint.lint_silo(p.wiki_slug, graph, p.cache, slug)
    # Doc-derived concepts (doc-concepts/, from the doc-ingest step) — light gate:
    # their catalog citations must resolve (rule 1), no subgraph/uncited gates.
    doc_report = lint.lint_doc_concepts(p.wiki_slug, graph)
    report_lint = lint.LintReport(report_lint.errors + doc_report.errors)
    if not report_lint.ok:
        typer.echo(f"\nLINT FAILED ({len(report_lint.errors)} error(s)):", err=True)
        for e in report_lint.errors:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)
    typer.echo("lint: OK — every citation resolves.")

    # Update reconcile state from the actual concept pages on disk.
    state = state_mod.load_state(p.state)
    state_mod.set_ref(state, acq.commit)
    state_mod.set_symbols(state, diff.current_hashes(graph, acq.repo_dir))
    concept_status: list[tuple[str, str]] = []
    for page in sorted((p.wiki_slug / "concepts").glob("*.md")):
        cited = sorted(lint.page_citations(page))
        state_mod.record_page(state, page.stem, cited, acq.commit)
        concept_status.append((page.stem, "fresh"))
    state_mod.save_state(p.state, state)

    report = coverage_mod.compute_report(graph, p.wiki_slug, catalogued=catalogued)
    typer.echo(report.render())

    scip_tool = "scip-python"
    assemble.write_repo_index(
        p.wiki_slug, slug, acq.commit, scip_tool, concept_status, _today(), report=report
    )
    # Top catalog of code wikis, written into the configured base (wiki/code/ by default,
    # or wiki/ when wiki_subdir=""). Leaves a curated wiki/index.md untouched when subdir'd.
    assemble.write_top_index(
        p.wiki_base, [d.name for d in p.wiki_base.iterdir() if d.is_dir()], _today())
    rel = f"{p.wiki_subdir}/{slug}" if p.wiki_subdir else slug
    typer.echo(f"assembled wiki/{rel}/index.md  (commit {acq.commit[:10]})")
    # The overview is the silo's front door: the host index links it (skill register
    # step) and `connect` discovers silos by its presence. It is written last (skill
    # step 3), so a partial run must still finalize — warn, never fail.
    if not (p.wiki_slug / "overview.md").exists():
        typer.echo(f"warning: no overview.md at wiki/{rel}/ — the silo is unreachable from "
                   f"the host index and invisible to `wikify connect` until it exists; write "
                   f"it (skill step 3, prompts/overview.md) and re-run finalize.", err=True)
    else:
        for target in _overview_link_warnings(p.wiki_slug):
            typer.echo(f"warning: overview.md links to a page that does not exist: {target}",
                       err=True)


@app.command(name="lint")
def lint_cmd(
    slug: str,
    root: Path = typer.Option(Path("."), help="Project root."),
    fix: bool = typer.Option(False, help="Auto-repair deterministically-fixable errors in place."),
) -> None:
    """Re-run the citation linter alone (Stage 6 gate); ``--fix`` auto-repairs first."""
    p, _cfg = _load(root, slug)
    graph = _graph(p)
    if fix:
        edits, report = fix_mod.fix_silo(p.wiki_slug, graph, p.cache, slug)
        typer.echo(f"fix: applied {edits} repair(s)")
    else:
        report = lint.lint_silo(p.wiki_slug, graph, p.cache, slug)
    if report.ok:
        typer.echo("lint: OK")
        return
    for e in report.errors:
        typer.echo(f"  {e}", err=True)
    raise typer.Exit(1)


@app.command()
def coverage(
    slug: str,
    root: Path = typer.Option(Path("."), help="Project root."),
    emit: bool = typer.Option(False, help="Write/refresh catalog pages."),
) -> None:
    """Report whole-repo coverage (set-difference over the SCIP symbol table)."""
    p, _cfg = _load(root, slug)
    if not p.scip.exists():
        typer.echo(f"error: no SCIP index at {p.scip}; run `wikify prepare {slug}` first", err=True)
        raise typer.Exit(2)
    graph = _graph(p)
    catalogued: set[str] = set()
    if emit:
        catalogued, paths = coverage_mod.emit_catalogs(graph, p.wiki_slug)
        typer.echo(f"catalog: wrote {len(paths)} module page(s)")
    else:
        # Treat already-written catalog pages' documentable set as represented.
        catalogued = set(coverage_mod.documentable_symbols(graph)) if (p.wiki_slug / "catalog").is_dir() else set()
    typer.echo(coverage_mod.compute_report(graph, p.wiki_slug, catalogued=catalogued).render())


@app.command()
def verify(
    slug: str,
    page: str = typer.Option(None, help="Dump claims for one concept (stem or filename)."),
    root: Path = typer.Option(Path("."), help="Project root."),
    all_claims: bool = typer.Option(False, "--all", help="Ignore cached holds: full worklist."),
    record: Path = typer.Option(None, help="With --page: the reviewer's STRICT JSON verdicts "
                                          "file to memoize (prompts/verify.md output)."),
    repo: str = typer.Option(None, help="Source path or git URL (overrides config)."),
) -> None:
    """List the load-bearing claims to adversarially verify (worklist for the
    verifier agent in .agents/skills/wikify-ingest-repo/prompts/verify.md).
    Deterministic; runs no model.

    Incremental (§10.4): verdicts recorded with ``--record`` are memoized per claim on
    its prose + the body hashes of the symbols it cites, and dropped from later
    worklists while both are unchanged (plus a deterministic re-sample). ``--all``
    forces the full list. Without a cached SCIP index the cache is bypassed."""
    p, cfg = _load(root, slug)
    pages = sorted((p.wiki_slug / "concepts").glob("*.md"))
    if page:
        pages = [x for x in pages if page in (x.stem, x.name)]
    if record and not page:
        typer.echo("error: --record needs --page", err=True)
        raise typer.Exit(2)

    # Evidence hashes for the cache: the current graph's symbol body-shas at the pin.
    hashes: dict[str, str] | None = None
    ref = cfg.ref or ""
    if _scip_indexes(p):
        acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=cfg.ref, mode=cfg.acquire)
        ref = acq.commit
        hashes = diff.current_hashes(_graph(p), acq.repo_dir)
    else:
        typer.echo("note: no SCIP index cached; verdict cache bypassed (run `wikify prepare` "
                   "to enable incremental verify)", err=True)

    total = to_verify_total = 0
    for pg in pages:
        claims = verify_mod.load_bearing_claims(pg)
        total += len(claims)
        cpath = verify_mod.cache_path(p.cache, slug, pg.stem)
        cache = verify_mod.load_cache(cpath)

        if record:
            data = json.loads(Path(record).read_text(encoding="utf-8"))
            n, unmatched = verify_mod.record_verdicts(
                cache, pg, claims, data.get("verdicts", []), hashes or {}, ref, _today())
            verify_mod.save_cache(cpath, cache)
            n_ref = sum(1 for v in data.get("verdicts", []) if v.get("refuted"))
            typer.echo(f"{pg.stem}: recorded {n} verdict(s) ({n_ref} refuted) → {cpath}")
            if unmatched:
                typer.echo(f"  unmatched claim_line(s), not recorded: {unmatched} "
                           f"(use the L<n> numbers from the worklist)", err=True)
            continue

        if hashes is None:
            wl = verify_mod.Worklist(to_verify=list(claims))
            wl.reason = {c.id: "no cache" for c in claims}
        else:
            wl = verify_mod.plan_worklist(pg, claims, cache, hashes, ref, force=all_claims)
        to_verify_total += len(wl.to_verify)
        parts = [f"{len(wl.to_verify)} to verify"]
        if wl.cached:
            parts.append(f"{len(wl.cached)} cached hold(s)")
        if wl.invalid:
            parts.append(f"{len(wl.invalid)} cited code changed")
        if wl.resampled:
            parts.append(f"{len(wl.resampled)} re-sampled")
        if wl.refuted:
            parts.append(f"{len(wl.refuted)} still refuted")
        typer.echo(f"{pg.stem}: {len(claims)} claim(s) — " + ", ".join(parts))
        if page:
            for c in wl.to_verify:
                cites = f"  [{len(c.citations)} cite]" if c.citations else ""
                why = wl.reason.get(c.id, "")
                tag = f" ({why})" if why and why != "new" else ""
                typer.echo(f"  L{c.line} [{c.section}]{cites}{tag} {c.text[:88]}")
    if not record:
        typer.echo(f"\ntotal: {to_verify_total} to verify of {total} load-bearing claim(s) "
                   f"across {len(pages)} page(s)")


@app.command()
def connect(
    apply: str = typer.Option("", help="Comma-separated concept keys to connect (wire inline)."),
    refresh: bool = typer.Option(False, help="Re-apply all already-connected concepts."),
    exclude: str = typer.Option("", help="Comma-separated 'repo/rel-path' matches to drop."),
    root: Path = typer.Option(Path("."), help="Project root."),
    vocab: str = typer.Option("concepts", help="Wiki subdir holding the concept vocabulary."),
) -> None:
    """Stage 7 — connect ingested silos on the concept axis, inline (no model, no side-table).

    With no options it **proposes**: prints which vocabulary concepts (``wiki/<vocab>/*.md``)
    have candidate implementations across the silos, and which are already connected — a human
    then picks. ``--apply <keys>`` wires those concepts inline and bidirectionally: a
    ``## In this wiki's repos`` block on each concept page linking down to every implementation,
    and an up-link on each silo page. ``--refresh`` re-applies the already-connected set (after a
    new ingest). Links live in regenerable ``connect:auto`` blocks; hand prose is never touched."""
    wiki = root / "wiki"
    if not wiki.is_dir():
        typer.echo(f"error: no wiki/ at {wiki}", err=True)
        raise typer.Exit(2)
    keys = [k.strip() for k in apply.split(",") if k.strip()]
    if refresh:
        keys = sorted(set(keys) | set(connect_mod.connected_keys(wiki, vocab)))
    if not keys:
        typer.echo(connect_mod.compute_report(wiki, vocab))
        return
    exc = {e.strip() for e in exclude.split(",") if e.strip()}
    counts = connect_mod.apply_connections(wiki, keys, vocab, exclude=exc)
    for key in sorted(counts):
        typer.echo(f"connected {key}: {counts[key]} implementation link(s)")
    typer.echo(f"wired {len(counts)} concept(s) inline (concept pages ↔ silo pages).")


@app.command()
def plan(
    slug: str,
    ref: str = typer.Option(None, help="Pinned commit/tag."),
    repo: str = typer.Option(None, help="Source path or git URL."),
    root: Path = typer.Option(Path("."), help="Project root."),
) -> None:
    """Dry-run: print the reconcile delta against the DERIVED agenda, emit nothing.

    Reuses the cached SCIP index — a dry-run never triggers indexing (prepare owns
    that, including the sharded path for large repos)."""
    p, cfg = _load(root, slug)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=ref or cfg.ref, mode=cfg.acquire)
    if not _scip_indexes(p):
        typer.echo(f"error: no SCIP index for {slug}; run `wikify prepare {slug}` first", err=True)
        raise typer.Exit(2)
    graph = _graph(p)
    state = state_mod.load_state(p.state)
    ag = _derive_agenda(graph, cfg, state)
    typer.echo(ag.summary())
    hashes = diff.current_hashes(graph, acq.repo_dir)
    typer.echo(diff.compute_plan(graph, acq.repo_dir, state, ag.cfg, hashes).render())


@app.command()
def agenda(
    slug: str,
    root: Path = typer.Option(Path("."), help="Project root."),
    max_subsystems: int = typer.Option(0, "--max", help="Cap the proposal (0 = config/default)."),
    write: bool = typer.Option(True, help="Write .cache/plan/<slug>.agenda.md."),
) -> None:
    """Propose the subsystem agenda (table of contents) from the cached index; no packets.

    The planner's proposal — directory-shaped subsystems ranked by external fan-in,
    each with its entry points — for the user to confirm, trim (`agenda_exclude:`) or
    extend (`seeds: (subsystem: <prefix>)`) before `prepare` builds packets."""
    p, cfg = _load(root, slug)
    if not _scip_indexes(p):
        typer.echo(f"error: no SCIP index for {slug}; run `wikify prepare {slug}` first", err=True)
        raise typer.Exit(2)
    graph = _graph(p)
    subs = subsystems_mod.discover_subsystems(
        graph,
        max_subsystems=max_subsystems or cfg.agenda_max or subsystems_mod.DEFAULT_MAX_SUBSYSTEMS,
        exclude_globs=cfg.agenda_exclude,
    )
    text = subsystems_mod.render_agenda(subs, graph, slug)
    typer.echo(text)
    if write:
        out = p.cache / "plan" / f"{slug}.agenda.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"written → {out}")


if __name__ == "__main__":
    app()
