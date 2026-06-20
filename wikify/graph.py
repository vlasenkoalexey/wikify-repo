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

    @property
    def docstring(self) -> str:
        """Author-written prose from ``documentation``, signature code-fence stripped.

        scip-python stores the signature as a leading ```python fenced block inside
        ``documentation``; the docstring prose follows. We keep only the non-fenced
        parts — the author's own description (L2 authored evidence, decision 8).
        """
        doc = self.documentation
        if "```" in doc:
            doc = "".join(doc.split("```")[0::2])  # keep parts outside fences
        result = _clean_doc(doc).strip()
        # scip-clang emits this literal placeholder for undocumented C++ symbols.
        if result.rstrip(".").strip().lower() == "no documentation available":
            return ""
        return result

    @property
    def doc_summary(self) -> str:
        """First non-empty line of the docstring (the summary), or ''."""
        for line in self.docstring.splitlines():
            line = line.strip()
            if line:
                return line
        return ""


def _clean_doc(text: str) -> str:
    """Undo scip-python's markdown escaping for readable inline display."""
    return (
        text.replace("&nbsp;", " ")
        .replace("\\_", "_")
        .replace("\\*", "*")
        .replace("\\`", "`")
        .replace("\\#", "#")
    )


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
        # Edges added by devirtualization (CHA), not by reference scoping — kept
        # separate so they can be labelled "(virtual)" and audited.
        self.virtual_edges: set[tuple[str, str]] = set()

    # -- construction -------------------------------------------------------

    def add_symbol(self, sym: Symbol) -> None:
        self.symbols[sym.moniker] = sym
        self._callees.setdefault(sym.moniker, set())
        self._callers.setdefault(sym.moniker, set())
        self.ref_count.setdefault(sym.moniker, 0)
        self.refs.setdefault(sym.moniker, [])

    def add_edge(self, caller: str, callee: str, virtual: bool = False) -> None:
        """Record that ``caller``'s body references in-repo symbol ``callee``.

        ``virtual`` marks a devirtualization (CHA) edge — a *potential* dynamic
        dispatch (base → override), not a static reference."""
        if caller not in self.symbols or callee not in self.symbols:
            return
        self._callees[caller].add(callee)
        self._callers[callee].add(caller)
        if virtual:
            self.virtual_edges.add((caller, callee))

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

    def is_virtual(self, caller: str, callee: str) -> bool:
        return (caller, callee) in self.virtual_edges

    def __len__(self) -> int:
        return len(self.symbols)

    def __contains__(self, moniker: str) -> bool:
        return moniker in self.symbols


def devirtualize(graph: "SymbolGraph") -> int:
    """Class Hierarchy Analysis: cross the dynamic-dispatch seam the reference
    call graph cannot see. Returns the number of virtual edges added.

    A call like ``model_parts[0](x)`` reaches ``nn.Module.__call__`` → the *base*
    ``forward``, but the real work is in an override (``Transformer.forward``) that
    no static reference edge points to — so traversal from a trainer dies at the
    base. SCIP records ``is_implementation`` on each override (``S implements T``);
    we add the edge ``T → S`` (base → override) so reaching the base also reaches
    its implementations, and likewise ``BaseClass → Subclass``. This is the
    "connection" op that coverage (a set-difference) deliberately leaves undone."""
    added = 0
    for moniker, sym in graph.symbols.items():
        for target, kind in sym.relationships:
            if kind != "is_implementation" or target not in graph.symbols:
                continue
            if moniker not in graph.callees(target):
                graph.add_edge(target, moniker, virtual=True)
                added += 1
    return added
