"""SymbolGraph — the L1 grounding model (implementation.md §5.1).

This is the *citation namespace*: every symbol here has a stable SCIP moniker
that wiki pages cite and the linter resolves against. The graph is built by
``scip_index.py`` from a parsed ``.scip`` index; nothing here calls a model.

Callers/callees are a **reference-scoping heuristic**, not true call resolution
(SCIP has no "call" role). See ``scip_index.derive_edges`` for the derivation
and the doc note: stubs say "calls/refs", not "calls".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Symbol:
    """One global, in-repo symbol (a node in the citation namespace)."""

    moniker: str                       # full SCIP symbol string (authoritative id)
    kind: str                          # SCIP Kind name, e.g. "Function", "Class"
    suffix: str                        # terminal descriptor suffix: Method/Type/Term...
    name: str                          # terminal descriptor name, e.g. "compute"
    def_path: str | None = None        # repo-relative path of the definition
    def_line: int | None = None        # 0-based start line of the definition
    enclosing: tuple[int, int, int, int] | None = None  # def's enclosing range
    signature: str = ""
    documentation: str = ""
    relationships: list[tuple[str, str]] = field(default_factory=list)
    # ^ (target_moniker, rel_kind) for is_implementation / is_type_definition

    @property
    def is_callable(self) -> bool:
        return self.kind in _CALLABLE_KINDS or self.suffix == "Method"


_CALLABLE_KINDS = {
    "Function",
    "Method",
    "Constructor",
    "AbstractMethod",
    "StaticMethod",
    "ClassMethod",
    "PureVirtualMethod",
    "MethodReceiver",
}


class SymbolGraph:
    """In-repo symbol graph: nodes + reference-derived edges.

    Edges are directed ``F -> S`` meaning "the body of F contains a reference to
    in-repo symbol S" (an approximation of a call). ``callers``/``callees`` are
    the two adjacency views; ``ref_count`` counts reference occurrences for the
    importance rank.
    """

    def __init__(self) -> None:
        self.symbols: dict[str, Symbol] = {}
        self._callees: dict[str, set[str]] = {}     # F -> {S, ...}
        self._callers: dict[str, set[str]] = {}     # S -> {F, ...}
        self.ref_count: dict[str, int] = {}         # moniker -> reference count
        self.refs: dict[str, list[tuple[str, int]]] = {}  # moniker -> [(path, line)]

    # -- construction -------------------------------------------------------

    def add_symbol(self, sym: Symbol) -> None:
        self.symbols[sym.moniker] = sym
        self._callees.setdefault(sym.moniker, set())
        self._callers.setdefault(sym.moniker, set())
        self.ref_count.setdefault(sym.moniker, 0)
        self.refs.setdefault(sym.moniker, [])

    def add_edge(self, caller: str, callee: str) -> None:
        """Record that ``caller``'s body references in-repo symbol ``callee``."""
        if caller not in self.symbols or callee not in self.symbols:
            return
        self._callees[caller].add(callee)
        self._callers[callee].add(caller)

    # -- queries ------------------------------------------------------------

    def callees(self, moniker: str) -> set[str]:
        return self._callees.get(moniker, set())

    def callers(self, moniker: str) -> set[str]:
        return self._callers.get(moniker, set())

    def importance(self, moniker: str) -> int:
        """context-sherpa rank: outbound*5 + ref_count*2 (no clustering)."""
        outbound = len(self._callees.get(moniker, ()))
        return outbound * 5 + self.ref_count.get(moniker, 0) * 2

    def find(self, name: str) -> list[str]:
        """Monikers whose terminal descriptor name == ``name`` (test/debug helper)."""
        return [m for m, s in self.symbols.items() if s.name == name]

    def __len__(self) -> int:
        return len(self.symbols)

    def __contains__(self, moniker: str) -> bool:
        return moniker in self.symbols
