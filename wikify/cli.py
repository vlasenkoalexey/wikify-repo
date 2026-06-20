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
from pathlib import Path

import typer

from . import acquire, assemble, diff, lint, packet, scip_index, state as state_mod
from .config import RepoConfig, load_config

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
        self.state = state_mod.state_path(self.cache, slug)
        self.wiki = root / "wiki"
        self.wiki_slug = self.wiki / slug


def _today() -> str:
    return datetime.date.today().isoformat()


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


def _graph(p: Paths):
    return scip_index.build_graph(scip_index.parse_index(p.scip))


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

    if reindex or not p.scip.exists():
        typer.echo("indexing with scip-python ...")
        scip_index.run_indexer(acq.repo_dir, p.scip, project_name=slug)
    graph = _graph(p)
    typer.echo(f"graph: {len(graph)} symbols")

    state = state_mod.load_state(p.state)
    hashes = diff.current_hashes(graph, acq.repo_dir)
    plan = diff.compute_plan(graph, acq.repo_dir, state, cfg, hashes)
    typer.echo(plan.render())

    todo = set(plan.todo)
    built = 0
    for concern in cfg.concerns:
        if concern.slug not in todo:
            continue
        text, subgraph = packet.build_packet(
            graph, acq.repo_dir, slug, acq.commit, concern, cfg.tests, _today()
        )
        pkt = packet.write_packet(p.cache, slug, concern.slug, text, subgraph)
        typer.echo(f"  packet → {pkt}  ({len(subgraph)} symbols)")
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
) -> None:
    """Stage 6: lint the agent-written pages, assemble the index, update state."""
    p, cfg = _load(root, slug)
    if not p.scip.exists():
        typer.echo(f"error: no SCIP index at {p.scip}; run `wikify prepare {slug}` first", err=True)
        raise typer.Exit(2)
    acq = acquire.acquire(_source(cfg, repo), slug, p.raw, ref=cfg.ref)
    graph = _graph(p)

    report = lint.lint_silo(p.wiki_slug, graph, p.cache, slug)
    if not report.ok:
        typer.echo(f"\nLINT FAILED ({len(report.errors)} error(s)):", err=True)
        for e in report.errors:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)
    typer.echo("lint: OK — every citation resolves.")

    # Update reconcile state from the actual pages.
    state = state_mod.load_state(p.state)
    state_mod.set_ref(state, acq.commit)
    state_mod.set_symbols(state, diff.current_hashes(graph, acq.repo_dir))
    concern_status: list[tuple[str, str]] = []
    for concern in cfg.concerns:
        page = p.wiki_slug / "concerns" / f"{concern.slug}.md"
        if page.exists():
            cited = sorted(lint.page_citations(page))
            state_mod.record_page(state, concern.slug, cited, acq.commit)
            concern_status.append((concern.slug, "fresh"))
        else:
            concern_status.append((concern.slug, "missing"))
    state_mod.save_state(p.state, state)

    scip_tool = "scip-python"
    assemble.write_repo_index(p.wiki_slug, slug, acq.commit, scip_tool, concern_status, _today())
    assemble.write_top_index(p.wiki, [d.name for d in p.wiki.iterdir() if d.is_dir()], _today())
    typer.echo(f"assembled wiki/{slug}/index.md  (commit {acq.commit[:10]})")


@app.command(name="lint")
def lint_cmd(
    slug: str,
    root: Path = typer.Option(Path("."), help="Project root."),
) -> None:
    """Re-run the citation linter alone (Stage 6 gate)."""
    p, _cfg = _load(root, slug)
    graph = _graph(p)
    report = lint.lint_silo(p.wiki_slug, graph, p.cache, slug)
    if report.ok:
        typer.echo("lint: OK")
        return
    for e in report.errors:
        typer.echo(f"  {e}", err=True)
    raise typer.Exit(1)


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
