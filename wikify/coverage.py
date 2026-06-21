"""Stage 6b — structural coverage: every module catalogued (whole-repo guarantee).

WHY THIS EXISTS
---------------
Concept synthesis (Stage 5) is **concept-driven and top-down by design** — it
only documents the concepts it is given. That is correct for *depth* (it avoids
shallow file-by-file summaries), but on its own it silently drops whole
subsystems: a hand-authored concept list that omits ``models/`` produces a wiki
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
  2. ``covered_monikers`` — the symbols cited by a concept page.
  3. difference ⇒ *catalog-only* symbols; emit one generated catalog page per
     module so nothing is unrepresented. Deterministic, no LLM.

This guarantees **every module is represented** (the whole-repo guarantee) while
keeping concept pages for *depth*. It does NOT create the missing dynamic edges
(trainer→model) or unify cross-model concepts (the N ``Attention`` classes) —
those are separate, optional operations. Coverage ≠ connection: enumeration
sidesteps dynamic dispatch precisely because it never asks about connectivity.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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


_CATALOG_LINK = re.compile(r"\]\(\.\./catalog/([^)#]+\.md)#([^)\s]+)\)")


def covered_monikers(graph: SymbolGraph, wiki_slug_dir: str | Path) -> dict[str, str]:
    """Map each concept-cited moniker → the concept page slug that cites it.

    Resolves citations against the GRAPH (module from the link path + qualified-name
    match), not the catalog files — so it works while catalogs are being generated.
    """
    # reverse index keyed by the catalog page path (language-agnostic: works for
    # .py/.h/.cpp alike, since the link IS catalog_rel_path(def_path)).
    index: dict[tuple[str, str], str] = {}
    for m, s in documentable_symbols(graph).items():
        index[(catalog_rel_path(s.def_path), qualified_name(m))] = m

    covered: dict[str, str] = {}
    concepts = Path(wiki_slug_dir) / "concepts"
    if not concepts.is_dir():
        return covered
    for page in sorted(concepts.glob("*.md")):
        for catalog_rel, anchor in _CATALOG_LINK.findall(page.read_text(encoding="utf-8")):
            moniker = index.get((catalog_rel, anchor))
            if moniker:
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
    covered: int = 0          # cited by a concept page (deep)
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
        lines.append(f"  deep (concept pages) : {self.covered}  ({self.pct_deep:.1f}%)")
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
    pass the planned set to report post-catalog coverage. If None, only concept
    coverage counts (pre-catalog state).
    """
    docs = documentable_symbols(graph)
    covered = covered_monikers(graph, wiki_slug_dir)
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


_ANCHOR_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def qualified_name(moniker: str) -> str:
    """Anchor for a symbol within its module catalog: descriptor names after the
    namespace, joined by '.' (e.g. ``Trainer.train_step``). Pure function of the
    moniker, so packet citations and catalog frontmatter always agree.

    The result is **link-safe** (no spaces/special chars): C++ monikers from
    scip-clang can contain ``$``/spaces, which would break a markdown ``#anchor``."""
    ps = parse_symbol(moniker)
    if ps.is_local:
        base = f"local-{ps.local_id}"
    else:
        names = [n for n, suf in ps.descriptors if suf != "Namespace" and n]
        base = ".".join(names) if names else moniker
    return _ANCHOR_UNSAFE.sub("-", base).strip("-") or "sym"


def catalog_ref(module_path: str, moniker: str) -> str:
    """Citation target a concept page uses (``concepts/`` → ``../catalog/…#anchor``)."""
    return f"../catalog/{catalog_rel_path(module_path)}#{qualified_name(moniker)}"


def symbol_anchor_map(graph: SymbolGraph, monikers: list[str]) -> dict[str, str]:
    """{anchor → moniker} for a module's symbols (the linter's resolution table).

    On the rare anchor collision (two symbols, same qualified name in one module),
    keep the higher-importance moniker — deterministic, resolution stays valid."""
    out: dict[str, str] = {}
    for m in sorted(monikers, key=lambda x: -graph.importance(x)):
        out.setdefault(qualified_name(m), m)
    return out


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


def _clean_sig(sym: Symbol) -> str:
    """The real `def …`/`class …` line — decorator lines stripped, collapsed to one
    line (scip-python stores the signature as a multi-line fenced block whose first
    line is often a `@decorator`, which is why catalogs used to show `@…`)."""
    if not sym.signature:
        return ""
    body = [ln for ln in sym.signature.splitlines() if not ln.strip().startswith("@")]
    s = re.sub(r"\s+", " ", " ".join(ln.strip() for ln in body)).strip()
    return s.replace("( ", "(").replace(" )", ")").replace(" ,", ",")


def _params(sym: Symbol) -> str:
    """The parameter tuple from a callable's signature, e.g. ``(self, modules=None)``."""
    s = _clean_sig(sym)
    i = s.find("(")
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
    return ""


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


# Path segments whose symbols are TEST/example noise in uses/used-by lists — a
# class "used by" 1000 test fixtures tells you nothing about how the library works,
# and (alphabetically) buries the real callers. Dependencies (third_party/, vendor/)
# are NOT filtered: a vendored/3p symbol as a caller is a legitimate relationship.
_NOISE_SEGMENTS = {"test", "tests", "testing", "example", "examples",
                   "benchmark", "benchmarks"}


def _is_noise_path(def_path: str | None) -> bool:
    segs = (def_path or "").split("/")
    return any(s in _NOISE_SEGMENTS or s.startswith("test_") for s in segs)


def _src_link(source_base: str | None, path: str, line: int | None = None) -> str | None:
    """Permalink into the pinned source (``<base>/<path>#L<line>``), or None."""
    if not source_base:
        return None
    return f"{source_base.rstrip('/')}/{path}" + (f"#L{line}" if line else "")


def _compress_anchor_map(anchor_map: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Factor the common moniker prefix out of an anchor→moniker map.

    Returns ``(base, {anchor: suffix})`` where ``base + suffix`` reconstructs the
    full moniker. Every symbol in a catalog shares the scheme/project/version (and,
    for Python, the module namespace), so the common prefix is large; storing it
    once removes ~55 bytes × N of repetition per page."""
    monikers = list(anchor_map.values())
    if not monikers:
        return "", {}
    base = os.path.commonprefix(monikers)
    return base, {a: m[len(base):] for a, m in anchor_map.items()}


def render_catalog(
    graph: SymbolGraph,
    module_path: str,
    monikers: list[str],
    covered: dict[str, str],
    source_base: str | None = None,
) -> str:
    """Render one module's catalog page from the graph (no synthesis).

    ``source_base`` (e.g. ``https://github.com/org/repo/blob/<commit>``) makes the
    module header and every ``def:`` line a permalink into the pinned source."""
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
        concept = covered.get(moniker)
        return f" — documented in [{concept}](../" + "../" * (module_path.count("/")) + \
               f"concepts/{concept}.md)" if concept else ""

    def _loc(sym) -> str:
        """`def: file:line`, linked to the pinned source when ``source_base`` is set."""
        line = (sym.def_line or 0) + 1
        loc = f"{sym.def_path}:{line}"
        link = _src_link(source_base, sym.def_path, line)
        return f"[`{loc}`]({link})" if link else f"`{loc}`"

    def _loc_line(sym) -> str:
        """Compact source link `Lnnn` (the file is the catalog's module)."""
        line = (sym.def_line or 0) + 1
        link = _src_link(source_base, sym.def_path, line)
        return f"[`L{line}`]({link})" if link else f"`L{line}`"

    def _detail(sym, moniker: str) -> str:
        """One symbol's detail bullet: `name(params)` — Lnnn — docstring summary."""
        sig = f"`{sym.name}{_params(sym)}`" if sym.is_callable and _params(sym) else f"`{sym.name}`"
        doc = f" — {sym.doc_summary}" if sym.doc_summary else ""
        return f"{sig} — {_loc_line(sym)}{doc}{_cov_tag(moniker)}"

    lines: list[str] = []
    a = lines.append
    # Frontmatter carries the anchor→moniker map so the linter resolves citations.
    # Every Python moniker in one catalog shares the same prefix (scheme + project +
    # version + module namespace); factor it into `symbol_base` once so the map is
    # anchor→terminal, not 100 copies of the same 55-char prefix.
    base, suffixes = _compress_anchor_map(symbol_anchor_map(graph, monikers))
    fm = {
        "title": f"Module: {module_path}",
        "type": "catalog",
        "provenance": "extracted",
        "module": module_path,
        "status": "fresh",
        "symbol_base": base,
        "symbols": suffixes,
    }
    a("---")
    a(yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip())
    a("---")
    src = _src_link(source_base, module_path)
    header = f"[`{module_path}`]({src})" if src else f"`{module_path}`"
    a(f"# Module: {header}")
    a("")

    def _link_targets(targets: list[str], cap: int = 40) -> str:
        """Render in-repo edge targets, ranked by importance, linked to their catalog.

        Test/example/vendored callers are filtered out (they're noise and bury the
        real users), and the rest are ranked by centrality so the cap keeps the
        load-bearing callers, not an alphabetical slice. Hidden counts are reported
        (no silent truncation)."""
        items = [(graph.symbols[t], t) for t in targets if t in graph.symbols]
        kept = [it for it in items if not _is_noise_path(it[0].def_path)]
        hidden_tests = len(items) - len(kept)
        # importance first (central callers), classes before non-classes, then name.
        kept.sort(key=lambda it: (-graph.importance(it[1]), it[0].suffix != "Type",
                                  it[0].name or ""))
        out: list[str] = []
        for sym, _t in kept[:cap]:
            if sym.def_path:
                rel = _rel_catalog_link(module_path, sym.def_path)
                # Anchor disambiguates same-named symbols (two classes' `__call__`).
                out.append(f"[`{sym.name}`]({rel}#{qualified_name(_t)})")
            else:
                out.append(f"`{sym.name}`")
        notes = []
        if len(kept) > cap:
            notes.append(f"+{len(kept) - cap} more")
        if hidden_tests:
            notes.append(f"{hidden_tests} test-only")
        tail = f"  ({'; '.join(notes)})" if notes else ""
        if not out:
            return f"({hidden_tests} test-only callers)" if hidden_tests else "(none in-repo)"
        return ", ".join(out) + tail

    if classes:
        a("## Classes")
        for cname in sorted(classes):
            cm = classes[cname]
            csym = symbols[cm]
            rels = _rel_names(csym)
            base = f"  ·  implements/extends {', '.join(sorted(set(rels)))}" if rels else ""
            a(f"### `{cname}`{base}")
            a(f"- def: {_loc(csym)}{_cov_tag(cm)}")
            if csym.doc_summary:
                a(f"- doc: {csym.doc_summary}")
            if _clean_sig(csym):
                a(f"- signature: `{_clean_sig(csym)}`")
            # Members: public or documented → full detail (public-first); the rest
            # (undocumented dunder/private) → folded but present and linked. No caps —
            # a module's own contents are the deterministic content of the page.
            mem = [(symbols[mm], mm) for mm in members.get(cname, [])]
            detailed = [(s, m) for s, m in mem if not s.name.startswith("_") or s.doc_summary]
            det = {m for _s, m in detailed}
            folded = [(s, m) for s, m in mem if m not in det]
            detailed.sort(key=lambda it: (not it[0].is_callable, it[0].name))
            folded.sort(key=lambda it: it[0].name)
            if detailed:
                a("- members:")
                for s, m in detailed:
                    a(f"  - {_detail(s, m)}")
            if folded:
                fold = ", ".join(f"`{s.name}`{_loc_line(s)}" for s, _m in folded)
                a(f"- protocol/private: {fold}")
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
            a(f"- {_detail(symbols[m], m)}")
        a("")
    if terms:
        a("## Module values")
        for m in sorted(terms, key=lambda x: symbols[x].name):
            a(f"- {_detail(symbols[m], m)}")
        a("")

    return "\n".join(lines) + "\n"


def emit_catalogs(
    graph: SymbolGraph,
    wiki_slug_dir: str | Path,
    source_base: str | None = None,
) -> tuple[set[str], list[Path]]:
    """Write one catalog page per in-repo module. Returns (catalogued monikers, paths).

    Every documentable symbol ends up on its module's catalog page, so the
    returned set is exactly the documentable set — the whole-repo guarantee.
    ``source_base`` (when given) makes def locations link into the pinned source."""
    wiki_slug_dir = Path(wiki_slug_dir)
    catalog_dir = wiki_slug_dir / "catalog"
    docs = documentable_symbols(graph)
    covered = covered_monikers(graph, wiki_slug_dir)
    modules = by_module(docs)

    catalogued: set[str] = set()
    written: list[Path] = []
    for module_path, monikers in sorted(modules.items()):
        text = render_catalog(graph, module_path, monikers, covered, source_base=source_base)
        out = catalog_dir / catalog_rel_path(module_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        written.append(out)
        catalogued.update(monikers)
    return catalogued, written
