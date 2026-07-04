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


@pytest.fixture()
def project(tmp_path):
    """A project root + a committed source repo, index pre-seeded (no scip-python)."""
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIXTURE / "mathlib.py", src / "mathlib.py")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")

    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / f"{SLUG}.md").write_text(
        f"---\nslug: {SLUG}\nrepo: {src}\n---\n\n# mathlib\n\n## Concepts\n"
        "- **compute-pipeline** — seeds: `compute`\n",
        encoding="utf-8",
    )
    scip = root / ".cache" / "scip" / f"{SLUG}.scip"
    scip.parent.mkdir(parents=True)
    shutil.copy(FIXTURE / "callgraph.scip", scip)
    return root


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
