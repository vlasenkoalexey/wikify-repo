"""CLI orchestration tests — the wiring between stages, not the stages themselves.

Pins the contracts the module tests can't see:
  * ``prepare`` builds the DERIVED agenda (discovery ∪ config) and writes packets;
  * ``plan`` models the same run: same derived agenda, never triggers indexing;
  * ``finalize`` emits catalogs BEFORE lint (citations resolve against catalog
    frontmatter, so the order is load-bearing).

Runs without scip-python: the checked-in ``fixtures/callgraph/callgraph.scip``
is pre-seeded into ``.cache/scip/`` as if prepare had already indexed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wikify import coverage as coverage_mod
from wikify import scip_index
from wikify.cli import app

FIXTURE = Path(__file__).parent / "fixtures" / "callgraph"
SLUG = "mathlib"

runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@test", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _make_project(tmp_path, agenda_line: str):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIXTURE / "mathlib.py", src / "mathlib.py")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")

    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / f"{SLUG}.md").write_text(
        f"---\nslug: {SLUG}\nrepo: {src}\n{agenda_line}---\n\n# mathlib\n\n## Concepts\n"
        "- **compute-pipeline** — seeds: `compute`\n",
        encoding="utf-8",
    )
    scip = root / ".cache" / "scip" / f"{SLUG}.scip"
    scip.parent.mkdir(parents=True)
    shutil.copy(FIXTURE / "callgraph.scip", scip)
    return root


@pytest.fixture()
def project(tmp_path):
    """A project root + a committed source repo, index pre-seeded (no scip-python).
    Pinned to the legacy module-centrality agenda so the packet set stays exact."""
    return _make_project(tmp_path, "agenda: modules\n")


@pytest.fixture()
def project_planned(tmp_path):
    """Same project, opted into the subsystem planner (§10.11)."""
    return _make_project(tmp_path, "agenda: subsystems\n")


@pytest.fixture()
def project_unset(tmp_path):
    """Same project with no ``agenda:`` key — exercises the fresh/existing default rule."""
    return _make_project(tmp_path, "")


def _prepare(root: Path):
    return runner.invoke(
        app, ["prepare", SLUG, "--root", str(root), "--no-reindex"])


def test_prepare_writes_packet_for_config_concept(project):
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    assert "agenda:" in res.output
    pkts = sorted((project / ".cache" / "packets" / SLUG).glob("*.md"))
    assert [p.stem for p in pkts] == ["compute-pipeline"]
    subgraph = (pkts[0].parent / "compute-pipeline.subgraph.txt").read_text()
    assert "compute" in subgraph


def test_plan_models_prepare_agenda(project):
    """Regression: ``plan`` once diffed the raw config only (no discovery), so its
    delta diverged from what ``prepare`` actually built. Both must report the
    same derived agenda and the same to-build set."""
    plan_res = runner.invoke(app, ["plan", SLUG, "--root", str(project)])
    assert plan_res.exit_code == 0, plan_res.output
    prep_res = _prepare(project)
    assert prep_res.exit_code == 0, prep_res.output

    def agenda_line(out: str) -> str:
        return next(l for l in out.splitlines() if l.startswith("agenda:"))

    assert agenda_line(plan_res.output) == agenda_line(prep_res.output)
    assert "compute-pipeline" in plan_res.output


def test_plan_is_a_dry_run_never_indexes(project, tmp_path):
    """Without a cached index, ``plan`` must refuse (exit 2) — not silently run
    the (non-sharded) indexer the way prepare would."""
    shutil.rmtree(project / ".cache")
    res = runner.invoke(app, ["plan", SLUG, "--root", str(project)])
    assert res.exit_code == 2
    assert "run `wikify prepare" in res.output
    assert not (project / ".cache" / "scip" / f"{SLUG}.scip").exists()


def test_finalize_emits_catalogs_before_lint(project):
    """A concept page cites ``../catalog/<module>.md#<anchor>`` — with no catalog
    on disk yet. finalize must emit catalogs first so lint can resolve it."""
    res = _prepare(project)
    assert res.exit_code == 0, res.output

    graph = scip_index.build_graph(
        scip_index.parse_index(project / ".cache" / "scip" / f"{SLUG}.scip"))
    compute = graph.find("compute")[0]
    ref = coverage_mod.catalog_ref(graph.symbols[compute].def_path, compute)

    concepts = project / "wiki" / "code" / SLUG / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "compute-pipeline.md").write_text(
        "---\ntitle: compute pipeline\n---\n\n# Compute pipeline\n\n"
        "## Mechanism (step-by-step)\n\n"
        f"1. [`compute`]({ref}) drives the pipeline.\n",
        encoding="utf-8",
    )
    assert not (concepts.parent / "catalog").exists()

    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "lint: OK" in res.output
    assert (concepts.parent / "catalog").is_dir()
    assert (concepts.parent / "index.md").exists()


def test_top_index_reports_connection_status(project):
    """The top catalog's Connection column is derived from the silo pages: a
    concept page carrying a connect up-link block flips it from standalone."""
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    concepts = project / "wiki" / "code" / SLUG / "concepts"
    concepts.mkdir(parents=True)
    page = concepts / "compute-pipeline.md"
    page.write_text("---\ntitle: t\n---\n\n# t\n", encoding="utf-8")

    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    top = (project / "wiki" / "code" / "index.md").read_text()
    assert f"| {SLUG} |" in top and "standalone" in top

    page.write_text(
        page.read_text() + "\n<!-- connect:up:begin -->\nup\n<!-- connect:up:end -->\n",
        encoding="utf-8")
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    top = (project / "wiki" / "code" / "index.md").read_text()
    assert "connected (1 concept)" in top


def test_finalize_lint_gate_fails_on_dead_citation(project):
    """The citation linter is a hard build gate: a citation whose anchor does not
    resolve in any catalog must fail finalize (exit 1)."""
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    concepts = project / "wiki" / "code" / SLUG / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "compute-pipeline.md").write_text(
        "---\ntitle: compute pipeline\n---\n\n"
        "## Mechanism (step-by-step)\n\n"
        "1. [`ghost`](../catalog/mathlib.md#Ghost.method) does not exist.\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 1
    assert "LINT FAILED" in res.output


def test_prepare_subsystems_mode_plans_scopes_and_writes_agenda(project_planned):
    """Planner mode: the agenda is the subsystem table of contents; every planned packet
    carries a ``## Scope`` block; the proposal is persisted for the skill to show."""
    res = _prepare(project_planned)
    assert res.exit_code == 0, res.output
    assert "concepts (subsystems)" in res.output
    assert "Proposed agenda" in res.output
    pkts = sorted((project_planned / ".cache" / "packets" / SLUG).glob("*.md"))
    assert [p.stem for p in pkts] == ["compute-pipeline", "core"]   # planned unit + config concept
    core = (project_planned / ".cache" / "packets" / SLUG / "core.md").read_text()
    assert "## Scope" in core and "Subsystem `(repo root)`" in core
    assert "## Scope" not in (project_planned / ".cache" / "packets" / SLUG / "compute-pipeline.md").read_text()
    assert (project_planned / ".cache" / "plan" / f"{SLUG}.agenda.md").exists()


def test_agenda_command_proposes_without_building(project_planned):
    res = runner.invoke(app, ["agenda", SLUG, "--root", str(project_planned)])
    assert res.exit_code == 0, res.output
    assert "| 1 | core |" in res.output
    assert (project_planned / ".cache" / "plan" / f"{SLUG}.agenda.md").exists()
    assert not (project_planned / ".cache" / "packets").exists()


def test_agenda_default_fresh_is_subsystems_existing_is_modules(project_unset):
    """No ``agenda:`` key: a fresh silo plans by subsystem; once state records pages, the
    same config keeps module discovery (no surprise rebuilds) until told otherwise."""
    from wikify import state as state_mod
    res = runner.invoke(app, ["plan", SLUG, "--root", str(project_unset)])
    assert res.exit_code == 0, res.output
    assert "concepts (subsystems)" in res.output
    st = state_mod.state_path(project_unset / ".cache", SLUG)
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text('{"ref": "x", "symbols": {}, "pages": {"old": {"cited": [], "built_ref": "x"}}}')
    res = runner.invoke(app, ["plan", SLUG, "--root", str(project_unset)])
    assert res.exit_code == 0, res.output
    assert "concepts (modules)" in res.output
    # explicit CLI override wins over the default rule
    res = runner.invoke(app, ["prepare", SLUG, "--root", str(project_unset), "--no-reindex",
                              "--agenda", "subsystems"])
    assert res.exit_code == 0, res.output
    assert "concepts (subsystems)" in res.output


def test_config_subsystem_seed_is_rederived_each_run(project):
    """``seeds: (subsystem: <prefix>)`` seeds a config concept from a directory (any mode)."""
    cfg = project / "config" / f"{SLUG}.md"
    cfg.write_text(cfg.read_text() + "- **whole-lib** — seeds: (subsystem: .)\n", encoding="utf-8")
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    pkt = project / ".cache" / "packets" / SLUG / "whole-lib.md"
    assert pkt.exists()
    assert "## Scope" in pkt.read_text()


def test_finalize_warns_without_overview_but_still_succeeds(project):
    """overview.md is the front door (host index + connect discovery). Missing → a warning
    on stderr, exit 0 (it is written last, so partial runs must finalize); present → silent."""
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    silo = project / "wiki" / "code" / SLUG
    (silo / "concepts").mkdir(parents=True)
    (silo / "concepts" / "compute-pipeline.md").write_text("---\ntitle: t\n---\n\n# t\n", encoding="utf-8")

    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "warning: no overview.md" in res.output
    assert (silo / "index.md").exists()

    (silo / "overview.md").write_text("---\ntitle: o\n---\n\n# overview\n", encoding="utf-8")
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "warning: no overview.md" not in res.output


def test_finalize_warns_on_dead_overview_links(project):
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    silo = project / "wiki" / "code" / SLUG
    (silo / "concepts").mkdir(parents=True)
    (silo / "concepts" / "compute-pipeline.md").write_text("---\ntitle: t\n---\n\n# t\n", encoding="utf-8")
    (silo / "overview.md").write_text(
        "---\ntitle: o\n---\n\n# o\n\n| Task | Start here |\n|---|---|\n"
        "| Run it | [nope](doc-concepts/missing.md) |\n| Compute | [ok](concepts/compute-pipeline.md#x) |\n"
        "See [ext](https://example.com) and ```[in code](fake.md)```\n", encoding="utf-8")
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "does not exist: doc-concepts/missing.md" in res.output
    assert "compute-pipeline.md" not in [l for l in res.output.splitlines() if "does not exist" in l][0]
    assert "fake.md" not in res.output and "example.com" not in res.output


def test_config_subsystem_entry_replaces_planned_unit(project_planned):
    """Renaming a planned unit via ``(subsystem: <prefix>)`` must not build it twice: the
    config entry covers the planned ``core`` unit (repo root), which is suppressed."""
    cfg = project_planned / "config" / f"{SLUG}.md"
    cfg.write_text(cfg.read_text() + "- **whole-lib** — seeds: (subsystem: .)\n", encoding="utf-8")
    res = _prepare(project_planned)
    assert res.exit_code == 0, res.output
    pkts = sorted(p.stem for p in (project_planned / ".cache" / "packets" / SLUG).glob("*.md"))
    assert pkts == ["compute-pipeline", "whole-lib"], pkts
    assert "## Scope" in (project_planned / ".cache" / "packets" / SLUG / "whole-lib.md").read_text()


def test_agenda_file_has_paste_ready_concepts_block(project_planned):
    res = runner.invoke(app, ["agenda", SLUG, "--root", str(project_planned)])
    assert res.exit_code == 0, res.output
    text = (project_planned / ".cache" / "plan" / f"{SLUG}.agenda.md").read_text()
    assert "## Concepts" in text
    assert "- **core** — seeds: (subsystem: .)" in text


def test_planned_packet_marks_outside_symbols(project_planned):
    res = _prepare(project_planned)
    assert res.exit_code == 0, res.output
    core = (project_planned / ".cache" / "packets" / SLUG / "core.md").read_text()
    assert "symbols below are inside this unit" in core
    # a single-module repo: everything is inside, nothing is marked outside
    assert "(outside this unit)" not in core.split("## Subgraph", 1)[1]


def test_verify_worklist_record_and_cached_holds(project):
    """verify → record the reviewer's JSON → verify again: recorded holds drop off the
    worklist; --all brings them back; a refuted claim stays until its prose changes."""
    import json as _json
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    graph = scip_index.build_graph(
        scip_index.parse_index(project / ".cache" / "scip" / f"{SLUG}.scip"))
    compute = graph.find("compute")[0]
    ref = coverage_mod.catalog_ref(graph.symbols[compute].def_path, compute)
    silo = project / "wiki" / "code" / SLUG
    (silo / "concepts").mkdir(parents=True)
    (silo / "concepts" / "compute-pipeline.md").write_text(
        "---\ntitle: t\n---\n\n# t\n\n## Overview\nThe pipeline is driven by "
        f"[`compute`]({ref}) once per call.\n\n## Mechanism (step-by-step)\n"
        f"1. [`compute`]({ref}) multiplies then squares.\n2. [`compute`]({ref}) returns a plain int.\n",
        encoding="utf-8")
    assert runner.invoke(app, ["finalize", SLUG, "--root", str(project)]).exit_code == 0

    res = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "3 claim(s) — 3 to verify" in res.output
    lines = [int(l.split()[0][1:]) for l in res.output.splitlines() if l.strip().startswith("L")]
    assert len(lines) == 3
    verdicts = project / "v.json"
    verdicts.write_text(_json.dumps({"page": "compute-pipeline.md", "verdicts": [
        {"claim_line": lines[0], "refuted": False},
        {"claim_line": lines[1], "refuted": False},
        {"claim_line": lines[2], "refuted": True, "note": "returns a float"},
        {"claim_line": 999, "refuted": False},
    ]}))
    res = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--record", str(verdicts),
                              "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "recorded 3 verdict(s) (1 refuted)" in res.output and "unmatched" in res.output

    res = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--root", str(project)])
    assert res.exit_code == 0, res.output
    line = next(l for l in res.output.splitlines() if l.startswith("compute-pipeline:"))
    assert "1 still refuted" in line
    assert "cached hold(s)" in line or "re-sampled" in line
    assert "(previously refuted, prose unchanged)" in res.output

    res = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--all", "--root", str(project)])
    assert "3 to verify" in res.output


def test_version_flag():
    from wikify import __version__
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0 and res.output.strip() == f"wikify {__version__}"


def test_finalize_stamps_okf_and_is_idempotent(project):
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    graph = scip_index.build_graph(
        scip_index.parse_index(project / ".cache" / "scip" / f"{SLUG}.scip"))
    compute = graph.find("compute")[0]
    ref = coverage_mod.catalog_ref(graph.symbols[compute].def_path, compute)
    silo = project / "wiki" / "code" / SLUG
    (silo / "concepts").mkdir(parents=True)
    page = silo / "concepts" / "compute-pipeline.md"
    page.write_text("---\ntitle: t\ntype: concept\nstatus: fresh\nupdated: 2026-09-05\n---\n\n# t\n\n"
                    f"## Overview\nDriven by [`compute`]({ref}).\n", encoding="utf-8")
    (silo / "overview.md").write_text("---\ntitle: o\ntype: overview\n---\n# o\n", encoding="utf-8")
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    text = page.read_text()
    assert "generated: {by: wikify/" in text and "status:" not in text
    assert "sources:\n  - {resource: " in text and "mathlib.py, title: mathlib.py}" in text
    assert "updated: 2026-09-05\n" in text                          # untouched
    assert text.endswith(f"## Overview\nDriven by [`compute`]({ref}).\n")
    assert "generated: {by: wikify/" in (silo / "overview.md").read_text()
    idx = (silo / "index.md").read_text()
    assert 'okf_version: "0.2"' in idx and "sources:\n  - {resource: " in idx and "mathlib @" in idx
    assert "warning: okf" not in res.output
    # second run: nothing changes
    before = page.read_text(), (silo / "overview.md").read_text()
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert (page.read_text(), (silo / "overview.md").read_text()) == before
    # a body edit refreshes generated.at only via body change (same day here: value equal or newer)
    gen_before = [l for l in before[0].splitlines() if l.startswith("generated:")][0]
    page.write_text(page.read_text() + "\nMore prose.\n", encoding="utf-8")
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert [l for l in page.read_text().splitlines() if l.startswith("generated:")][0] >= gen_before


def test_verify_record_stamps_verified_when_all_hold(project):
    import json as _json
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    graph = scip_index.build_graph(
        scip_index.parse_index(project / ".cache" / "scip" / f"{SLUG}.scip"))
    compute = graph.find("compute")[0]
    ref = coverage_mod.catalog_ref(graph.symbols[compute].def_path, compute)
    silo = project / "wiki" / "code" / SLUG
    (silo / "concepts").mkdir(parents=True)
    page = silo / "concepts" / "compute-pipeline.md"
    page.write_text(f"---\ntitle: t\n---\n\n# t\n\n## Overview\nDriven by [`compute`]({ref}).\n"
                    f"\n## Mechanism (step-by-step)\n1. [`compute`]({ref}) squares.\n", encoding="utf-8")
    assert runner.invoke(app, ["finalize", SLUG, "--root", str(project)]).exit_code == 0
    out = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--root", str(project)]).output
    lines = [int(l.split()[0][1:]) for l in out.splitlines() if l.strip().startswith("L")]
    v = project / "v.json"
    v.write_text(_json.dumps({"verdicts": [{"claim_line": n, "refuted": False} for n in lines]}))
    res = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--record", str(v), "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "verified: stamped wikify-verify/" in res.output
    assert "verified: [{by: wikify-verify/" in page.read_text()
    # one refuted → tool entry removed. The stamp added a front-matter line, so claim
    # lines moved: re-read the worklist (as the skill does) before recording again.
    out = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--all", "--root", str(project)]).output
    lines = [int(l.split()[0][1:]) for l in out.splitlines() if l.strip().startswith("L")]
    v.write_text(_json.dumps({"verdicts": [{"claim_line": lines[0], "refuted": True, "note": "no"}]}))
    res = runner.invoke(app, ["verify", SLUG, "--page", "compute-pipeline", "--record", str(v), "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "verified:" not in page.read_text()


def test_prepare_relinks_moved_module_and_finalize_prunes_stale_catalog(project):
    """Simulate a file move on the fixture: state says `compute` used to live in
    old/mathlib.py; a stale catalog page and a page citing it exist. prepare must relink
    the citation (no rebuild), and finalize must delete the stale catalog page."""
    from wikify import state as state_mod
    res = _prepare(project)
    assert res.exit_code == 0, res.output
    graph = scip_index.build_graph(
        scip_index.parse_index(project / ".cache" / "scip" / f"{SLUG}.scip"))
    compute = graph.find("compute")[0]
    new_ref = coverage_mod.catalog_ref(graph.symbols[compute].def_path, compute)
    old_ref = coverage_mod.catalog_ref("old/" + graph.symbols[compute].def_path, compute)
    silo = project / "wiki" / "code" / SLUG
    (silo / "concepts").mkdir(parents=True)
    page = silo / "concepts" / "compute-pipeline.md"
    page.write_text(f"---\ntitle: t\n---\n\n# t\n\n## Overview\nDriven by [`compute`]({new_ref}).\n")
    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    # Now recreate the world as it was BEFORE the move: the page cites the old catalog
    # path, a stale catalog copy still sits there (as a real move leaves behind), and
    # state remembers the old location.
    page.write_text(page.read_text().replace(new_ref, old_ref))
    old_cat = silo / "catalog" / "old" / "mathlib.md"
    old_cat.parent.mkdir(parents=True)
    old_cat.write_text((silo / "catalog" / "mathlib.md").read_text())
    st = state_mod.load_state(state_mod.state_path(project / ".cache", SLUG))
    st["paths"] = {m: "old/" + p for m, p in st["paths"].items()}
    state_mod.save_state(state_mod.state_path(project / ".cache", SLUG), st)

    res = _prepare(project)
    assert res.exit_code == 0, res.output
    assert "will relink  : compute-pipeline" in res.output and "relinked: 1 citation(s)" in res.output
    assert "will rebuild : (none)" in res.output
    assert new_ref in page.read_text() and old_ref not in page.read_text()
    res = _prepare(project)                                  # folded into state: no-op now
    assert "will relink" not in res.output and "will leave   : compute-pipeline" in res.output

    res = runner.invoke(app, ["finalize", SLUG, "--root", str(project)])
    assert res.exit_code == 0, res.output
    assert "removed 1 stale page(s)" in res.output and not old_cat.exists()
