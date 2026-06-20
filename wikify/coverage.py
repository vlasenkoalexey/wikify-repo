"""Stage 6b — structural coverage: every module catalogued (whole-repo guarantee).

WHY THIS EXISTS
---------------
Concern synthesis (Stage 5) is **concern-driven and top-down by design** — it
only documents the concerns it is given. That is correct for *depth* (it avoids
shallow file-by-file summaries), but on its own it silently drops whole
subsystems: a hand-authored concern list that omits ``models/`` produces a wiki
where the models simply do not appear, even though SCIP indexed every one of
their symbols.

The naive fix — "traverse the call graph from the entry points and document what
you reach" — does NOT work, because the architecturally load-bearing edges are
*dynamic by design*: a model is invoked as ``model_parts[0](inputs)`` through
``nn.Module.__call__``, which leaves **no static call edge**. Traversal dies at
that seam, and a per-file "is this connected?" check would false-flag the model
files as dead code (the same blind spot that makes name-based call graphs wrong).

THE MECHANISM
-------------
Coverage is a **set-difference over the SCIP symbol table, NOT a graph walk**.
SCIP already enumerated every symbol, so we never rely on reachability to *find*
code — only on enumeration:

  1. ``documentable_symbols`` — every in-repo class/function/method/term in the
     graph (SCIP found them all).
  2. ``covered_monikers`` — the symbols cited by a concern page.
  3. difference ⇒ *catalog-only* symbols; emit one generated catalog page per
     module so nothing is unrepresented. Deterministic, no LLM.

This guarantees **every module is represented** (the whole-repo guarantee) while
keeping concern pages for *depth*. It does NOT create the missing dynamic edges
(trainer→model) or unify cross-model concepts (the N ``Attention`` classes) —
those are separate, optional operations. Coverage ≠ connection: enumeration
sidesteps dynamic dispatch precisely because it never asks about connectivity.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import lint
from .graph import Symbol, SymbolGraph
from .monikers import parse_symbol

# Descriptor suffixes that denote a documentable, citable symbol (vs locals,
# parameters, type-params, already dropped upstream). Type = class, Method =
# function/method, Term = module-level value / attribute.
DOCUMENTABLE_SUFFIXES = {"Type", "Method", "Term"}


# --------------------------------------------------------------------------- #
# Enumeration (the set, not the walk)
# --------------------------------------------------------------------------- #
def documentable_symbols(graph: SymbolGraph) -> dict[str, Symbol]:
    """Every in-repo symbol worth representing in the wiki (has a def + is citable)."""
    return {
        m: s
        for m, s in graph.symbols.items()
        if s.def_path is not None and s.suffix in DOCUMENTABLE_SUFFIXES
    }


def class_symbols(graph: SymbolGraph) -> dict[str, Symbol]:
    """In-repo class definitions only (suffix == Type)."""
    return {
        m: s
        for m, s in graph.symbols.items()
        if s.def_path is not None and s.suffix == "Type"
    }


def covered_monikers(wiki_slug_dir: str | Path) -> dict[str, str]:
    """Map each concern-cited moniker → the concern page slug that cites it."""
    covered: dict[str, str] = {}
    concerns = Path(wiki_slug_dir) / "concerns"
    if not concerns.is_dir():
        return covered
    for page in sorted(concerns.glob("*.md")):
        for moniker in lint.page_citations(page):
            covered.setdefault(moniker, page.stem)
    return covered


def by_module(symbols: dict[str, Symbol]) -> dict[str, list[str]]:
    """Group documentable monikers by their definition file (the module)."""
    mods: dict[str, list[str]] = defaultdict(list)
    for moniker, sym in symbols.items():
        mods[sym.def_path].append(moniker)
    return dict(mods)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
@dataclass
class CoverageReport:
    total: int = 0
    covered: int = 0          # cited by a concern page (deep)
    catalog_only: int = 0     # represented only in a generated catalog (shallow)
    modules: int = 0
    classes_total: int = 0
    classes_represented: int = 0
    uncovered_examples: list[str] = field(default_factory=list)

    @property
    def represented(self) -> int:
        return self.covered + self.catalog_only

    @property
    def pct_deep(self) -> float:
        return 100.0 * self.covered / self.total if self.total else 0.0

    @property
    def pct_represented(self) -> float:
        return 100.0 * self.represented / self.total if self.total else 0.0

    def render(self) -> str:
        lines = ["Coverage report:"]
        lines.append(f"  documentable symbols : {self.total}  across {self.modules} modules")
        lines.append(f"  deep (concern pages) : {self.covered}  ({self.pct_deep:.1f}%)")
        lines.append(f"  catalog-only         : {self.catalog_only}")
        lines.append(f"  represented total    : {self.represented}  ({self.pct_represented:.1f}%)")
        lines.append(f"  classes              : {self.classes_represented}/{self.classes_total} represented")
        if self.uncovered_examples:
            lines.append(f"  NOT represented (sample): {', '.join(self.uncovered_examples)}")
        return "\n".join(lines)


def compute_report(
    graph: SymbolGraph,
    wiki_slug_dir: str | Path,
    catalogued: set[str] | None = None,
) -> CoverageReport:
    """Classify every documentable symbol as covered / catalog-only / unrepresented.

    ``catalogued`` is the set of monikers that have (or will have) a catalog page;
    pass the planned set to report post-catalog coverage. If None, only concern
    coverage counts (pre-catalog state).
    """
    docs = documentable_symbols(graph)
    covered = covered_monikers(wiki_slug_dir)
    catalogued = catalogued if catalogued is not None else set()

    rep = CoverageReport(total=len(docs), modules=len(by_module(docs)))
    unrepresented: list[str] = []
    for moniker, sym in docs.items():
        if moniker in covered:
            rep.covered += 1
        elif moniker in catalogued:
            rep.catalog_only += 1
        else:
            unrepresented.append(sym.name or moniker)

    classes = class_symbols(graph)
    rep.classes_total = len(classes)
    rep.classes_represented = sum(
        1 for m in classes if m in covered or m in catalogued
    )
    rep.uncovered_examples = sorted(unrepresented)[:10]
    return rep


# --------------------------------------------------------------------------- #
# Catalog page generation (deterministic, grounded, no LLM)
# --------------------------------------------------------------------------- #
def catalog_rel_path(module_path: str) -> str:
    """Map a module file path to its catalog page path (mirrors the source tree)."""
    p = module_path
    if p.endswith(".py"):
        p = p[:-3]
    return f"{p}.md"


def _owner_class(moniker: str) -> str | None:
    """Name of the enclosing class for a method/term, or None if module-level."""
    ps = parse_symbol(moniker)
    types = [name for name, suf in ps.descriptors if suf == "Type"]
    if not ps.descriptors:
        return None
    terminal_suffix = ps.descriptors[-1][1]
    if terminal_suffix == "Type":
        return None  # the symbol IS a class
    return types[-1] if types else None


def _sig1(sym: Symbol) -> str:
    return sym.signature.splitlines()[0].strip() if sym.signature else ""


def _rel_catalog_link(from_module: str, to_module: str) -> str:
    """Relative link from one module's catalog page to another's."""
    from_page = catalog_rel_path(from_module)
    to_page = catalog_rel_path(to_module)
    from_dir = posixpath.dirname(from_page)
    return posixpath.relpath(to_page, from_dir or ".")


def class_connections(
    graph: SymbolGraph, class_moniker: str, member_monikers: list[str]
) -> tuple[list[str], list[str]]:
    """Roll member edges up to the class → (uses, used_by) target monikers.

    A class *uses* another in-repo symbol if the class itself or any of its
    members (methods AND fields — so ``self.attention = Attention(...)`` counts)
    references it. This absorbs SCIP's member-granular reference scoping and
    yields true class-to-class edges. Self-references are excluded.
    """
    group = set(member_monikers) | {class_moniker}
    uses: set[str] = set()
    used_by: set[str] = set()
    for m in group:
        uses |= graph.callees(m)
        used_by |= graph.callers(m)
    uses -= group
    used_by -= group
    return sorted(uses), sorted(used_by)


def _rel_names(sym: Symbol) -> list[str]:
    out: list[str] = []
    for target, _kind in sym.relationships:
        nm = parse_symbol(target).terminal[0]
        if nm:
            out.append(nm)
    return out


def render_catalog(
    graph: SymbolGraph,
    module_path: str,
    monikers: list[str],
    covered: dict[str, str],
) -> str:
    """Render one module's catalog page from the graph (no synthesis)."""
    symbols = {m: graph.symbols[m] for m in monikers}
    # Partition into classes, their members, and module-level defs.
    classes: dict[str, str] = {}          # class_name -> moniker
    members: dict[str, list[str]] = defaultdict(list)  # class_name -> [member monikers]
    module_level: list[str] = []
    for m in monikers:
        sym = symbols[m]
        if sym.suffix == "Type":
            classes[sym.name] = m
            continue
        owner = _owner_class(m)
        if owner is not None:
            members[owner].append(m)
        else:
            module_level.append(m)

    def _cov_tag(moniker: str) -> str:
        concern = covered.get(moniker)
        return f" — documented in [{concern}](../" + "../" * (module_path.count("/")) + \
               f"concerns/{concern}.md)" if concern else ""

    lines: list[str] = []
    a = lines.append
    title = module_path
    a("---")
    a(f'title: "Module: {title}"')
    a("type: catalog")
    a("provenance: extracted")
    a(f"module: {module_path}")
    a("status: fresh")
    a("---")
    a(f"# Module: `{module_path}`")
    a("")
    a("Generated structural catalog (no synthesis). Every entry is grounded in the "
      "SCIP index; intra-module calls/refs are reference-scoped. Symbols documented "
      "by a concern page link to it; the rest are catalogued here for coverage.")
    a("")

    def _link_targets(targets: list[str], cap: int = 40) -> str:
        """Render in-repo edge targets, classes first, linked to their catalog page."""
        items = [(graph.symbols[t], t) for t in targets if t in graph.symbols]
        items.sort(key=lambda it: (it[0].suffix != "Type", it[0].name or ""))
        out: list[str] = []
        for sym, _t in items[:cap]:
            if sym.def_path:
                rel = _rel_catalog_link(module_path, sym.def_path)
                out.append(f"[`{sym.name}`]({rel})")
            else:
                out.append(f"`{sym.name}`")
        more = f" (+{len(items) - cap} more)" if len(items) > cap else ""
        return ", ".join(out) + more if out else "(none in-repo)"

    if classes:
        a("## Classes")
        for cname in sorted(classes):
            cm = classes[cname]
            csym = symbols[cm]
            rels = _rel_names(csym)
            base = f"  ·  implements/extends {', '.join(sorted(set(rels)))}" if rels else ""
            a(f"### `{cname}`{base}")
            loc = f"{csym.def_path}:{(csym.def_line or 0) + 1}"
            a(f"- def: `{loc}`{_cov_tag(cm)}")
            if _sig1(csym):
                a(f"- signature: `{_sig1(csym)}`")
            meth = sorted(symbols[mm].name for mm in members.get(cname, []))
            if meth:
                a(f"- members: {', '.join(f'`{x}`' for x in meth)}")
            uses, used_by = class_connections(graph, cm, members.get(cname, []))
            if uses:
                a(f"- uses (calls/refs, reference-scoped): {_link_targets(uses)}")
            if used_by:
                a(f"- used by: {_link_targets(used_by)}")
            a("")

    funcs = [m for m in module_level if symbols[m].suffix == "Method"]
    terms = [m for m in module_level if symbols[m].suffix == "Term"]
    if funcs:
        a("## Functions")
        for m in sorted(funcs, key=lambda x: symbols[x].name):
            sym = symbols[m]
            loc = f"{sym.def_path}:{(sym.def_line or 0) + 1}"
            sig = f"  `{_sig1(sym)}`" if _sig1(sym) else ""
            a(f"- `{sym.name}` — `{loc}`{sig}{_cov_tag(m)}")
        a("")
    if terms:
        a("## Module values")
        for m in sorted(terms, key=lambda x: symbols[x].name):
            sym = symbols[m]
            loc = f"{sym.def_path}:{(sym.def_line or 0) + 1}"
            a(f"- `{sym.name}` — `{loc}`{_cov_tag(m)}")
        a("")

    return "\n".join(lines) + "\n"


def emit_catalogs(
    graph: SymbolGraph,
    wiki_slug_dir: str | Path,
) -> tuple[set[str], list[Path]]:
    """Write one catalog page per in-repo module. Returns (catalogued monikers, paths).

    Every documentable symbol ends up on its module's catalog page, so the
    returned set is exactly the documentable set — the whole-repo guarantee.
    """
    wiki_slug_dir = Path(wiki_slug_dir)
    catalog_dir = wiki_slug_dir / "catalog"
    docs = documentable_symbols(graph)
    covered = covered_monikers(wiki_slug_dir)
    modules = by_module(docs)

    catalogued: set[str] = set()
    written: list[Path] = []
    for module_path, monikers in sorted(modules.items()):
        text = render_catalog(graph, module_path, monikers, covered)
        out = catalog_dir / catalog_rel_path(module_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        written.append(out)
        catalogued.update(monikers)
    return catalogued, written
