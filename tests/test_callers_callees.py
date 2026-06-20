"""Validate the SCIP-occurrence → callers/callees derivation (implementation.md §5.1).

This is the risky foundation everything downstream rests on: SCIP has no "call"
role, so edges are a reference-scoping heuristic. We index a fixture with a known
call structure and assert the derived edges match.
"""

import shutil
from pathlib import Path

import pytest

from wikify import scip_index

FIXTURE = Path(__file__).parent / "fixtures" / "callgraph"

pytestmark = pytest.mark.skipif(
    shutil.which("scip-python") is None, reason="scip-python not installed"
)


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("scip") / "callgraph.scip"
    return scip_index.index_repo(FIXTURE, out, project_name="callgraph")


def _one(graph, name):
    """Resolve the single in-repo moniker whose terminal descriptor is `name`."""
    monikers = graph.find(name)
    assert monikers, f"symbol {name!r} not found in graph"
    assert len(monikers) == 1, f"{name!r} ambiguous: {monikers}"
    return monikers[0]


def test_symbols_present(graph):
    for name in ("add", "mul", "square", "compute", "run"):
        assert graph.find(name), f"missing symbol {name}"


def test_callees(graph):
    compute = _one(graph, "compute")
    square = _one(graph, "square")
    add = _one(graph, "add")
    mul = _one(graph, "mul")

    assert graph.callees(compute) == {add, square}
    assert graph.callees(square) == {mul}
    assert graph.callees(add) == set()
    assert graph.callees(mul) == set()


def test_callers(graph):
    compute = _one(graph, "compute")
    square = _one(graph, "square")
    add = _one(graph, "add")
    mul = _one(graph, "mul")

    assert graph.callers(mul) == {square}
    assert graph.callers(add) == {compute}
    assert graph.callers(square) == {compute}


def test_method_body_scoping(graph):
    """A reference inside a method body must scope to the method, not the class."""
    run = _one(graph, "run")
    compute = _one(graph, "compute")
    assert compute in graph.callees(run)


def test_importance_rank(graph):
    """Importance = outbound*5 + ref_count*2; compute (2 callees) outranks add."""
    compute = _one(graph, "compute")
    add = _one(graph, "add")
    assert graph.importance(compute) > graph.importance(add)
