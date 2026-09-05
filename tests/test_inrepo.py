"""In-repo layout (§10.15): `wikify init` inside a code repository, then the ordinary
pipeline with no slug — wiki at <repo>/wiki/, cache at .wikify/, no raw/, no top index —
while the host-wiki layout keeps working untouched (tests/test_cli.py)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wikify import coverage as coverage_mod
from wikify import scip_index
from wikify.cli import WIKIFY_BEGIN, WIKIFY_END, app

FIXTURE = Path(__file__).parent / "fixtures" / "callgraph"
runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@test", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    """A committed code repo named mathlib (so the fixture index's slug matches)."""
    r = tmp_path / "mathlib"
    r.mkdir()
    shutil.copy(FIXTURE / "mathlib.py", r / "mathlib.py")
    (r / "README.md").write_text("# mathlib\n\nA tiny library.\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def test_init_is_idempotent_and_injects_instructions(repo):
    res = runner.invoke(app, ["init", "--root", str(repo)])
    assert res.exit_code == 0, res.output
    cfg = (repo / "wikify.md").read_text()
    assert "slug: mathlib" in cfg and "in_repo: true" in cfg and "## Concepts" in cfg
    assert ".wikify/" in (repo / ".gitignore").read_text().splitlines()
    for f in ("CLAUDE.md", "AGENTS.md"):
        text = (repo / f).read_text()
        assert text.count(WIKIFY_BEGIN) == 1 and text.count(WIKIFY_END) == 1
        assert "`wiki/overview.md`" in text and "wikify-ingest-repo" in text
    snapshot = {f: (repo / f).read_text() for f in ("wikify.md", ".gitignore", "CLAUDE.md", "AGENTS.md")}
    res = runner.invoke(app, ["init", "--root", str(repo)])
    assert res.exit_code == 0, res.output
    assert "unchanged wikify block" in res.output and "exists" in res.output
    assert {f: (repo / f).read_text() for f in snapshot} == snapshot
    # an existing CLAUDE.md keeps its own content; the block is replaced in place
    (repo / "CLAUDE.md").write_text("# My rules\n\nBe terse.\n\n" + WIKIFY_BEGIN + "\nOLD\n" + WIKIFY_END + "\n\n## After\n")
    res = runner.invoke(app, ["init", "--root", str(repo), "--wiki-dir", "docs/wiki"])
    text = (repo / "CLAUDE.md").read_text()
    assert text.startswith("# My rules") and text.endswith("## After\n") and "OLD" not in text
    assert "`docs/wiki/overview.md`" in text


def test_init_refuses_inside_a_host_wiki_project(tmp_path):
    host = tmp_path / "host"
    (host / "config").mkdir(parents=True)
    (host / "config" / "x.md").write_text("---\nslug: x\n---\n## Concepts\n")
    res = runner.invoke(app, ["init", "--root", str(host)])
    assert res.exit_code == 2 and "host-wiki project" in res.output


def test_pipeline_without_slug_builds_a_flat_silo(repo):
    assert runner.invoke(app, ["init", "--root", str(repo)]).exit_code == 0
    scip = repo / ".wikify" / "scip" / "mathlib.scip"
    scip.parent.mkdir(parents=True)
    shutil.copy(FIXTURE / "callgraph.scip", scip)

    res = runner.invoke(app, ["prepare", "--root", str(repo), "--no-reindex"])
    assert res.exit_code == 0, res.output
    assert "concepts (subsystems)" in res.output and "warning: working tree" not in res.output
    pkts = sorted(p.stem for p in (repo / ".wikify" / "packets" / "mathlib").glob("*.md"))
    assert pkts == ["core"]
    assert not (repo / "raw").exists() and not (repo / ".cache").exists()
    # the README is a doc to ingest; the wiki dir and cache never are
    assert "docs: 1 project doc(s)" in res.output

    graph = scip_index.build_graph(scip_index.parse_index(scip))
    compute = graph.find("compute")[0]
    ref = coverage_mod.catalog_ref(graph.symbols[compute].def_path, compute)
    (repo / "wiki" / "concepts").mkdir(parents=True)
    (repo / "wiki" / "concepts" / "core.md").write_text(
        f"---\ntitle: core\ntype: concept\n---\n\n# core\n\n## Overview\nAll of it: [`compute`]({ref}).\n")
    (repo / "wiki" / "overview.md").write_text("---\ntitle: mathlib overview\ntype: overview\n---\n# mathlib\n\nSee [core](concepts/core.md).\n")

    res = runner.invoke(app, ["finalize", "--root", str(repo)])
    assert res.exit_code == 0, res.output
    assert "lint: OK" in res.output and "assembled wiki/index.md" in res.output
    wiki = repo / "wiki"
    assert (wiki / "catalog" / "mathlib.md").exists() and (wiki / "log.md").exists()
    assert not (wiki / "code").exists() and not (wiki / "mathlib").exists()
    idx = (wiki / "index.md").read_text()
    assert "# mathlib internals wiki" in idx and 'okf_version: "0.2"' in idx   # the silo index, not a top catalog
    # catalog source links are relative into the repo itself and resolve
    cat = (wiki / "catalog" / "mathlib.md").read_text()
    import re
    m = re.search(r"\]\(([^)]*mathlib\.py)#L\d+\)", cat)
    assert m and (wiki / "catalog" / m.group(1)).resolve() == (repo / "mathlib.py").resolve()
    assert "generated: {by: wikify/" in (wiki / "concepts" / "core.md").read_text()

    # slug given must match; a wrong one is refused
    assert runner.invoke(app, ["plan", "mathlib", "--root", str(repo)]).exit_code == 0
    res = runner.invoke(app, ["plan", "other", "--root", str(repo)])
    assert res.exit_code == 2 and "is for 'mathlib'" in res.output

    # dirty tree → warning, not an error
    (repo / "mathlib.py").write_text((repo / "mathlib.py").read_text() + "\n# scratch\n")
    res = runner.invoke(app, ["plan", "--root", str(repo)])
    assert res.exit_code == 0 and "uncommitted changes" in res.output


def test_host_mode_still_requires_a_slug(tmp_path):
    res = runner.invoke(app, ["plan", "--root", str(tmp_path)])
    assert res.exit_code == 2 and "run `wikify init`" in res.output
