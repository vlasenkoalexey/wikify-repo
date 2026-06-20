"""Stage 4 (Phase-1 subset) — static evidence from TESTS only.

"Tests as spec": a test function that, in its body, references in-repo symbols
is treated as pinning their intended behavior. We reuse the SCIP reference edges
already in the graph — a test ``T`` whose callees intersect a concept's subgraph
exercises those symbols. No execution, no model.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from .graph import SymbolGraph


@dataclass
class TestEvidence:
    moniker: str
    name: str
    path: str | None
    line: int | None
    exercises: list[str]  # subgraph monikers referenced from the test body


def _matches_globs(path: str, globs: list[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.replace("**/", "")):
            return True
    return False


def is_test_path(path: str | None, test_globs: list[str]) -> bool:
    if not path:
        return False
    if test_globs and _matches_globs(path, test_globs):
        return True
    # Fallback heuristic when no globs configured.
    return "test" in path.lower()


def collect_tests(
    graph: SymbolGraph,
    test_globs: list[str],
    subgraph: set[str],
) -> list[TestEvidence]:
    """Tests whose body exercises any symbol in ``subgraph`` (asserts → symbols)."""
    out: list[TestEvidence] = []
    for moniker, sym in graph.symbols.items():
        if not sym.is_callable or not is_test_path(sym.def_path, test_globs):
            continue
        exercised = sorted(graph.callees(moniker) & subgraph)
        if exercised:
            out.append(
                TestEvidence(
                    moniker=moniker,
                    name=sym.name,
                    path=sym.def_path,
                    line=sym.def_line,
                    exercises=exercised,
                )
            )
    out.sort(key=lambda t: (t.path or "", t.line or 0))
    return out
