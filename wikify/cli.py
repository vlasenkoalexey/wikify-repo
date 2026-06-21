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
from dataclasses import replace
from pathlib import Path

import typer

from . import (
    acquire,
    assemble,
    bazel_cc,
    coverage as coverage_mod,
    diff,
    discover,
    fix as fix_mod,
    lint,
    packet,
    scip_index,
    state as state_mod,
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
        self.wiki_slug = self.wiki / slug


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
    return p, load_config(p.config)


def _source(cfg: RepoConfig, repo: str | None) -> str:
    src = repo or cfg.repo
    if not src:
        typer.echo("error: no repo source (pass --repo or set 'repo:' in config)", err=True)
        raise typer.Exit(2)
    return src


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


def _graph(p: Paths):
    """Build the graph, merging the C++ index (scip-clang) when present."""
    indexes = [scip_index.parse_index(p.scip)] if p.scip.exists() else []
    if p.scip_cpp.exists():
        indexes.append(scip_index.parse_index(p.scip_cpp))
    return scip_index.build_graph(*indexes)


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
) -> None:
    """Stages 0-4: acquire, index, build graph, emit packets, print the plan."""
    p, cfg = _load(root, slug)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=ref or cfg.ref)
    typer.echo(f"acquired {slug} @ {acq.commit[:10]}  ({acq.repo_dir})")

    langs = cfg.languages or ["python"]
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
    graph = _graph(p)
    typer.echo(f"graph: {len(graph)} symbols")

    # Agenda is DERIVED (decision 8): discovery ranks modules by centrality and
    # auto-seeds concepts; config concepts override/extend on slug collision.
    discovered = discover.discover_concepts(graph)
    seedmap = {d.slug: d.seeds for d in discovered}
    cfg_slugs = {c.slug for c in cfg.concepts}
    agenda = [Concept(slug=d.slug) for d in discovered if d.slug not in cfg_slugs] + cfg.concepts
    agenda_cfg = replace(cfg, concepts=agenda)
    typer.echo(f"agenda: {len(discovered)} discovered + {len(cfg.concepts)} config = {len(agenda)} concepts")

    state = state_mod.load_state(p.state)
    hashes = diff.current_hashes(graph, acq.repo_dir)
    plan = diff.compute_plan(graph, acq.repo_dir, state, agenda_cfg, hashes)
    typer.echo(plan.render())

    todo = set(plan.todo)
    built = 0
    for concept in agenda:
        if concept.slug not in todo:
            continue
        text, subgraph = packet.build_packet(
            graph, acq.repo_dir, slug, acq.commit, concept, cfg.tests, _today(),
            seed_monikers=seedmap.get(concept.slug),
        )
        pkt = packet.write_packet(p.cache, slug, concept.slug, text, subgraph)
        typer.echo(f"  packet → {pkt.name}  ({len(subgraph)} symbols)")
        built += 1
    if built == 0:
        typer.echo("nothing to build (converged).")
    else:
        typer.echo(f"\nWrote {built} packet(s). Now run agent synthesis, then `wikify finalize {slug}`.")


@app.command()
def finalize(
    slug: str,
    repo: str = typer.Option(None, help="Source path or git URL (overrides config)."),
    root: Path = typer.Option(Path("."), help="Project root."),
    fix: bool = typer.Option(False, help="Auto-repair deterministically-fixable lint errors first."),
) -> None:
    """Stage 6: lint the agent-written pages, assemble the index, update state."""
    p, cfg = _load(root, slug)
    if not p.scip.exists() and not p.scip_cpp.exists():
        typer.echo(f"error: no SCIP index for {slug}; run `wikify prepare {slug}` first", err=True)
        raise typer.Exit(2)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=cfg.ref)
    graph = _graph(p)

    # Stage 6b FIRST — emit module catalogs (the symbol homes). Citations resolve
    # against their frontmatter `symbols` map, so catalogs must exist before lint.
    catalogued, catalog_paths = coverage_mod.emit_catalogs(graph, p.wiki_slug)
    typer.echo(f"catalog: wrote {len(catalog_paths)} module page(s)")

    if fix:
        edits, report_lint = fix_mod.fix_silo(p.wiki_slug, graph, p.cache, slug)
        typer.echo(f"fix: applied {edits} repair(s); "
                   f"{len(report_lint.errors)} error(s) remain")
    else:
        report_lint = lint.lint_silo(p.wiki_slug, graph, p.cache, slug)
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
    assemble.write_top_index(p.wiki, [d.name for d in p.wiki.iterdir() if d.is_dir()], _today())
    typer.echo(f"assembled wiki/{slug}/index.md  (commit {acq.commit[:10]})")


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
) -> None:
    """List the load-bearing claims to adversarially verify (worklist for the
    verifier agent in skills/prompts/verify.md). Deterministic; runs no model."""
    p, _cfg = _load(root, slug)
    pages = sorted((p.wiki_slug / "concepts").glob("*.md"))
    if page:
        pages = [x for x in pages if page in (x.stem, x.name)]
    total = 0
    for pg in pages:
        claims = verify_mod.load_bearing_claims(pg)
        total += len(claims)
        typer.echo(f"{pg.stem}: {len(claims)} claim(s)")
        if page:
            for c in claims:
                cites = f"  [{len(c.citations)} cite]" if c.citations else ""
                typer.echo(f"  L{c.line} [{c.section}]{cites} {c.text[:88]}")
    typer.echo(f"\ntotal: {total} load-bearing claim(s) across {len(pages)} page(s)")


@app.command()
def plan(
    slug: str,
    ref: str = typer.Option(None, help="Pinned commit/tag."),
    repo: str = typer.Option(None, help="Source path or git URL."),
    root: Path = typer.Option(Path("."), help="Project root."),
) -> None:
    """Dry-run: print the reconcile delta, emit nothing."""
    p, cfg = _load(root, slug)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=ref or cfg.ref)
    if not p.scip.exists():
        scip_index.run_indexer(acq.repo_dir, p.scip, project_name=slug)
    graph = _graph(p)
    state = state_mod.load_state(p.state)
    typer.echo(diff.compute_plan(graph, acq.repo_dir, state, cfg).render())


if __name__ == "__main__":
    app()
