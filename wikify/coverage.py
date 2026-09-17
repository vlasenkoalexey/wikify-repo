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

import fnmatch
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


# ``../catalog/<module>.md#<anchor>`` with an optional link title (``"path:Lnn"``, the
# source location packets add since 0.3) — the title is display metadata, never resolved.
_CATALOG_LINK = re.compile(r"\]\(\.\./catalog/([^)#\s]+\.md)#([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def symbol_index(graph: SymbolGraph) -> dict[tuple[str, str], str]:
    """``(catalog page path, anchor) -> moniker`` for every documentable symbol: the
    citation resolution table, derived from the graph alone (language-agnostic — the
    link IS ``catalog_rel_path(def_path)``). The linter, coverage, relink and the OKF
    stamper all resolve through this; catalog pages are a rendering of it, not its source.
    On an anchor collision inside one module the higher-importance symbol wins, matching
    ``symbol_anchor_map``."""
    index: dict[tuple[str, str], str] = {}
    docs = documentable_symbols(graph)
    for m in sorted(docs, key=lambda x: graph.importance(x)):   # low first: high overwrites
        index[(catalog_rel_path(docs[m].def_path), qualified_name(m))] = m
    return index


def covered_monikers(graph: SymbolGraph, wiki_slug_dir: str | Path) -> dict[str, str]:
    """Map each concept-cited moniker → the concept page slug that cites it.

    Resolves citations against the GRAPH (module from the link path + qualified-name
    match), not the catalog files — so it works while catalogs are being generated.
    """
    index = symbol_index(graph)

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
    raw = sym.display_signature
    if not raw:
        return ""
    body = [ln for ln in raw.splitlines() if not ln.strip().startswith("@")]
    # scip-python renders the positional-only marker ``/`` as a bare ``,`` line; restore it
    # (it used to flatten to ``fn=None,, *``).
    body = ["/," if ln.strip() == "," else ln for ln in body]
    s = re.sub(r"\s+", " ", " ".join(ln.strip() for ln in body)).strip()
    s = s.replace("( ", "(").replace(" )", ")").replace(" ,", ",")
    return s.replace(",,", ", /,")


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
    # commonprefix is character-wise: when every symbol on a page starts with ``_`` the
    # underscore joins the base and the map shows ``INTERNAL_PREFIX`` for ``_INTERNAL_PREFIX``.
    # Cut back to the last descriptor boundary so a suffix always starts at a name.
    cut = max(base.rfind("/"), base.rfind("#"), base.rfind(" "))
    base = base[:cut + 1] if cut >= 0 else ""
    return base, {a: m[len(base):] for a, m in anchor_map.items()}


def render_catalog(
    graph: SymbolGraph,
    module_path: str,
    monikers: list[str],
    covered: dict[str, str],
    source_base: str | None = None,
    collapse: bool = False,
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
    if collapse:
        # Pointer-only page: the frontmatter symbol map above still resolves every
        # citation (linter rule 1), but the detailed member body is omitted. Used by
        # `coverage_collapse` to keep model-zoo / boilerplate modules citeable without
        # the per-member bulk. Follow the source link for full detail.
        a(f"> **Collapsed catalog** ({len(monikers)} symbols) — anchors above resolve for "
          "citations; detailed member listing omitted (`coverage_collapse`). See the source "
          "link above, or the curated codebase page, for depth.")
        return "\n".join(lines) + "\n"

    def _link_targets(targets: list[str], cap: int = 40) -> str:
        """Render in-repo edge targets, ranked by importance, linked to their catalog.

        Test/example/vendored callers are filtered out (they're noise and bury the
        real users), and the rest are ranked by centrality so the cap keeps the
        load-bearing callers, not an alphabetical slice. Hidden counts are reported
        (no silent truncation)."""
        # Only documentable targets (classes / functions / terms): a namespace or
        # macro moniker has no catalog anchor and its "home" file may have no page
        # at all, so linking it would leave a dead link on every page that uses it.
        items = [(graph.symbols[t], t) for t in targets
                 if t in graph.symbols and graph.symbols[t].suffix in DOCUMENTABLE_SUFFIXES]
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


def _glob_any(path: str, patterns) -> bool:
    """True if ``path`` matches any glob in ``patterns`` (``*`` spans ``/``, so
    ``easydel/modules/*`` matches ``easydel/modules/gemma4/modeling_gemma4.py``)."""
    return any(fnmatch.fnmatch(path, p) for p in (patterns or ()))


CATALOG_MODES = ("index", "anchors", "full")


def emit_catalogs(
    graph: SymbolGraph,
    wiki_slug_dir: str | Path,
    repo_dir: str | Path | None = None,
    source_url: str | None = None,
    collapse: list[str] | None = None,
    exclude: list[str] | None = None,
    mode: str = "full",
) -> tuple[set[str], list[Path]]:
    """Write one catalog page per in-repo module. Returns (catalogued monikers, paths).

    Every documentable symbol ends up on its module's catalog page, so the
    returned set is exactly the documentable set — the whole-repo guarantee.

    ``mode`` (config ``catalog:``, ``catalog-index.md``): ``full`` renders today's pages;
    ``anchors`` renders every page collapsed (front-matter map + source link, no body);
    ``index`` writes no pages at all — the symbol index (``emit_symbol_index``) is the
    catalog, and every documentable symbol counts as represented by it.

    Source links: ``source_url`` (a base URL, e.g. github ``…/blob/<commit>``) is
    used verbatim; otherwise, if ``repo_dir`` is given, each page links to the local
    source via a path **relative to that page** (never an absolute path — a leading
    ``/`` in markdown means repo-root, which would be a broken link). ``source_url=""``
    disables links."""
    if mode not in CATALOG_MODES:
        raise ValueError(f"catalog mode must be one of {CATALOG_MODES}, got {mode!r}")
    wiki_slug_dir = Path(wiki_slug_dir)
    catalog_dir = wiki_slug_dir / "catalog"
    repo_abs = Path(repo_dir).resolve() if repo_dir else None
    docs = documentable_symbols(graph)
    if mode == "index":
        return set(docs), []
    covered = covered_monikers(graph, wiki_slug_dir)
    modules = by_module(docs)

    catalogued: set[str] = set()
    written: list[Path] = []
    for module_path, monikers in sorted(modules.items()):
        # `exclude` drops a module entirely (no page) — use ONLY for uncited noise
        # (tests/vendored) since a dropped symbol can't be cited. `collapse` keeps the
        # citeable symbol map but omits the member body (model zoos / boilerplate).
        if _glob_any(module_path, exclude):
            continue
        out = catalog_dir / catalog_rel_path(module_path)
        if source_url == "":
            base = None
        elif source_url:
            base = source_url
        elif repo_abs is not None:
            # relative path from THIS page's directory to the source repo root.
            base = os.path.relpath(repo_abs, out.parent.resolve()).replace(os.sep, "/")
        else:
            base = None
        text = render_catalog(graph, module_path, monikers, covered, source_base=base,
                              collapse=(mode == "anchors") or _glob_any(module_path, collapse))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        written.append(out)
        catalogued.update(monikers)
    return catalogued, written


# --------------------------------------------------------------------------- #
# The symbol index (catalog-index.md): the catalog as data, pages as a rendering
# --------------------------------------------------------------------------- #
INDEX_COLUMNS = ("anchor", "path", "line", "kind", "rank", "hash", "callers", "pages")
INDEX_FULL_COLUMNS = INDEX_COLUMNS + ("sig", "doc")
INDEX_PROFILES = ("nav", "full")


def index_anchor(module_path: str, moniker: str) -> str:
    """The row key of the symbol index: the citation target with ``catalog/`` and ``.md``
    stripped — ``torch_tpu/eager/op_dispatcher.h#DispatchOp``. Same anchor grammar as
    ``catalog_ref``, so a citation and a row agree by construction."""
    return f"{catalog_rel_path(module_path)[:-3]}#{qualified_name(moniker)}"


SHARD_DEPTH = 2
SINGLE_SHARD = "all"


def _shard_of(def_path: str, depth: int = SHARD_DEPTH) -> str:
    """Shard key of a definition path: its first ``depth`` directory components
    (``torch_tpu/eager``), fewer when the file sits higher; ``(root)`` for a bare file. A
    single umbrella package (``torch/``, ``torch_tpu/``) would otherwise be one shard.
    ``depth=0`` puts everything in one shard (``symbols.tsv`` / ``edges.tsv``)."""
    if depth <= 0:
        return SINGLE_SHARD
    parts = def_path.split("/")
    dirs = parts[:-1]
    if not dirs:
        return "(root)"
    return "/".join(dirs[:depth])


def _shard_file(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "root"


def shard_paths(shard: str) -> tuple[str, str]:
    """Silo-relative paths of a shard's two files. Depth 0 is the two-file layout."""
    if shard == SINGLE_SHARD:
        return "catalog/symbols.tsv", "catalog/edges.tsv"
    f = _shard_file(shard)
    return f"catalog/symbols/{f}.tsv", f"catalog/edges/{f}.tsv"


def index_groups(graph: SymbolGraph, depth: int = SHARD_DEPTH):
    """The index's row groups and edge sets, shared by the emitter and the map.

    Returns ``(docs, rows_by_shard, edges_by_shard)`` where a row is
    ``(anchor, winner_moniker, [monikers], {caller monikers})``. One row per ANCHOR: C++
    overloads (and any same-named symbols in one module) share a qualified name; the row
    belongs to the highest-importance moniker — the same rule the citation resolver
    (``symbol_index``) applies — and its callers are the union over the group, so nothing
    that calls any overload goes missing."""
    docs = documentable_symbols(graph)
    groups: dict[str, list[str]] = defaultdict(list)
    for m, sym in docs.items():
        groups[index_anchor(sym.def_path, m)].append(m)
    rows_by_shard: dict[str, list[tuple]] = defaultdict(list)
    edges_by_shard: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for anchor, ms in groups.items():
        ms.sort(key=lambda x: (-graph.importance(x), x))
        winner = ms[0]
        callers = {c for m in ms for c in graph.callers(m) if c in docs}
        shard = _shard_of(docs[winner].def_path, depth)
        rows_by_shard[shard].append((anchor, winner, ms, callers))
        for c in callers:
            edges_by_shard[shard].add((anchor, index_anchor(docs[c].def_path, c)))
    return docs, rows_by_shard, edges_by_shard


def _kind_label(sym: Symbol, moniker: str) -> str:
    if sym.suffix == "Type":
        return "class"
    owner = _owner_class(moniker)
    if sym.suffix == "Method":
        return "method" if owner else "function"
    return "member" if owner else "value"


def _cell(text: str) -> str:
    """One TSV cell: no tabs, no newlines."""
    return " ".join(str(text).split())


def emit_symbol_index(
    graph: SymbolGraph,
    wiki_slug_dir: str | Path,
    hashes: dict[str, str] | None = None,
    profile: str = "nav",
    slug: str = "",
    ref: str = "",
    depth: int = SHARD_DEPTH,
) -> tuple[set[str], list[Path]]:
    """Write the symbol index: ``catalog/symbols/<top-level-dir>.tsv`` (one row per
    documentable symbol, sorted by path) and ``catalog/edges/<top-level-dir>.tsv`` (one
    caller edge per line, complete, unfiltered), each with a header that teaches the
    columns and the three grep recipes. Returns (indexed monikers, written paths).

    Columns: ``anchor path line kind rank hash callers pages`` and, in the ``full``
    profile, ``sig doc``. ``rank`` is ``graph.importance``; ``hash`` is the body hash from
    ``hashes`` (state or ``diff.current_hashes``); ``pages`` are the concept pages citing the
    symbol. One row per anchor (``index_groups``). Sharded by ``depth`` path components so a
    scoped grep is the normal query and an accidental Read is bounded; ``depth=0`` writes
    the two-file layout ``catalog/symbols.tsv`` + ``catalog/edges.tsv``. Deterministic, no
    model; linear in symbols."""
    if profile not in INDEX_PROFILES:
        raise ValueError(f"index profile must be one of {INDEX_PROFILES}, got {profile!r}")
    wiki_slug_dir = Path(wiki_slug_dir)
    docs, rows_by_shard, edges_by_shard = index_groups(graph, depth)
    covered = covered_monikers(graph, wiki_slug_dir)
    pages_of: dict[str, list[str]] = defaultdict(list)
    for m, page in covered.items():
        pages_of[m].append(page)
    hashes = hashes or {}
    cols = INDEX_FULL_COLUMNS if profile == "full" else INDEX_COLUMNS

    def _row(anchor: str, winner: str, ms: list[str], callers: set[str]) -> list[str]:
        sym = docs[winner]
        pages = {pg for m in ms for pg in pages_of.get(m, [])}
        row = [anchor, sym.def_path, str((sym.def_line or 0) + 1),
               _kind_label(sym, winner), str(max(graph.importance(m) for m in ms)),
               hashes.get(winner, ""), str(len(callers)), ";".join(sorted(pages))]
        if profile == "full":
            row += [_cell(_clean_sig(sym)), _cell(sym.doc_summary)]
        return row

    # Clear both layouts so a depth change never leaves stale files behind.
    cat = wiki_slug_dir / "catalog"
    cat.mkdir(parents=True, exist_ok=True)
    for d in (cat / "symbols", cat / "edges"):
        if d.is_dir():
            for stale in d.glob("*.tsv"):
                stale.unlink()
    for f in (cat / "symbols.tsv", cat / "edges.tsv"):
        if f.exists():
            f.unlink()
    sym_glob = "catalog/symbols.tsv" if depth <= 0 else "catalog/symbols/*.tsv"
    edge_glob = "catalog/edges.tsv" if depth <= 0 else "catalog/edges/*.tsv"
    at = f"{slug or 'repo'}" + (f" @ {ref[:10]}" if ref else "")
    written: list[Path] = []
    for shard, groups in sorted(rows_by_shard.items()):
        rows = sorted((_row(*g) for g in groups), key=lambda r: (r[1], int(r[2]), r[0]))
        n_syms = sum(len(g[2]) for g in groups)
        example, ex_path = _example_anchor(rows)
        name = example.split("#", 1)[1].rsplit(".", 1)[-1]      # bare name: a method's last segment
        area = ex_path.rsplit("/", 1)[0] if "/" in ex_path else ""
        where = "" if shard == SINGLE_SHARD else f", shard {shard}"
        folded = f" ({n_syms} symbols; overloads share a row)" if n_syms != len(rows) else ""
        head = [
            f"# wikify symbol index: {at}{where}, {len(rows)} rows{folded}. Grep it by anchor; never read it whole.",
            "# columns: " + "\t".join(cols),
            f"# one symbol:  grep -P '^{example}\\t' {sym_glob}",
            f"# by name:     grep -P '[#.]{name}\\t' {sym_glob} | sort -t$'\\t' -k5 -nr | head -20   (matches Class.{name} too)",
            f"# by area:     grep -P '^{area}/.*#{name}\\t' {sym_glob} | head -20" if area else
            f"# by area:     grep -P '^<dir>/.*#{name}\\t' {sym_glob} | head -20",
            f"# one module:  awk -F'\\t' '$2==\"{ex_path}\"' {sym_glob} | cut -f1,3,4 | head -40",
            f"# to prose:    grep -P '^{example}\\t' {sym_glob} | cut -f1,8   (column 8: concept pages citing it)",
            f"# callers:     column 7 is the count; the list lives in {edge_glob}, see its header (count, narrow, head)",
        ]
        out = wiki_slug_dir / shard_paths(shard)[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(head + ["\t".join(r) for r in rows]) + "\n", encoding="utf-8")
        written.append(out)
    for shard, edge_set in sorted(edges_by_shard.items()):
        edges = sorted(edge_set)
        where = "" if shard == SINGLE_SHARD else f", shard {shard}"
        ex_callee, ex_dir = _example_edge(edges)
        head = [
            f"# wikify edge list: {at}{where}, {len(edges)} caller edges. Grep it by anchor; never read it whole.",
            "# columns: callee\tcaller   (one edge per line). Hubs have hundreds of callers and a search tool",
            "#          shows about 50 lines, so: count, then narrow, then head.",
            f"# count first:   grep -c -P '^{ex_callee}\\t' {edge_glob}",
            f"# by caller dir: grep -P '^{ex_callee}\\t' {edge_glob} | cut -f2 | cut -d/ -f1-2 | sort | uniq -c",
            f"# who calls X:   grep -P '^{ex_callee}\\t{ex_dir}' {edge_glob} | cut -f2 | head -20   (narrowed to one directory)",
            f"# what X calls:  grep -P '\\t{ex_callee}$' {edge_glob} | cut -f1 | head -20",
        ]
        out = wiki_slug_dir / shard_paths(shard)[1]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(head + [f"{a}\t{b}" for a, b in edges]) + "\n", encoding="utf-8")
        written.append(out)
    return set(docs), written


def _example_anchor(rows: list[list[str]]) -> tuple[str, str]:
    """The anchor the header recipes demonstrate: the highest-ranked row with a moderate
    caller count (10 to 300) so the examples show a real, bounded result; else the top row."""
    def _callers(r: list[str]) -> int:
        try:
            return int(r[6])
        except (ValueError, IndexError):
            return 0
    mid = [r for r in rows if 10 <= _callers(r) <= 300]
    pick = max(mid or rows, key=lambda r: (int(r[4]), r[0]))
    return pick[0], pick[1]


def _example_edge(edges: list[tuple[str, str]]) -> tuple[str, str]:
    """(callee, caller directory prefix) for the edge-file recipes: the callee with the most
    edges in a moderate band (10 to 300), and the directory most of its callers live in."""
    from collections import Counter
    per = Counter(a for a, _ in edges)
    mid = [a for a, n in per.items() if 10 <= n <= 300]
    callee = max(mid or per, key=lambda a: (per[a], a))
    dirs = Counter("/".join(b.split("/")[:2]) + "/" for a, b in edges if a == callee and "/" in b)
    top = dirs.most_common(1)[0][0] if dirs else ""
    return callee, top


def index_summary(graph: SymbolGraph, depth: int = SHARD_DEPTH) -> tuple[int, int, int]:
    """(symbols, rows, edges) the index holds — for messages, without re-reading files."""
    docs, rows, edges = index_groups(graph, depth)
    return len(docs), sum(len(v) for v in rows.values()), sum(len(v) for v in edges.values())


def render_map(
    graph: SymbolGraph,
    wiki_slug_dir: str | Path,
    source_base: str | None = None,
    pages: bool = False,
    slug: str = "",
    ref: str = "",
    purposes: dict[str, str] | None = None,
    depth: int = SHARD_DEPTH,
) -> str:
    """The module map, ``catalog/index.md``: the index files with their sizes, then every
    module with its symbol count, its top entry points by rank, and the concept pages that
    cite into it, grouped by shard so each section links the TSV files that hold its rows.
    Deterministic; ``purposes`` (module -> one line) is an optional synthesized layer merged
    in when present. ``pages=True`` links each module to its catalog page."""
    wiki_slug_dir = Path(wiki_slug_dir)
    docs, rows_by_shard, edges_by_shard = index_groups(graph, depth)
    # Sections are always by directory (SHARD_DEPTH levels), whatever the file layout: with
    # the joined layout the whole repo would otherwise be one table.
    _, rows_by_dir, edges_by_dir = index_groups(graph, SHARD_DEPTH)
    covered = covered_monikers(graph, wiki_slug_dir)
    modules = by_module(docs)
    purposes = purposes or {}
    by_top: dict[str, list[str]] = defaultdict(list)
    for mp in modules:
        by_top[_shard_of(mp, SHARD_DEPTH)].append(mp)
    n_rows = sum(len(v) for v in rows_by_shard.values())
    n_edges = sum(len(v) for v in edges_by_shard.values())

    def _links(section: str) -> str:
        """The file(s) holding this section's rows, with the section's own counts."""
        shard = section if depth > 0 else SINGLE_SHARD
        sp, ep = shard_paths(shard)
        sp, ep = sp[len("catalog/"):], ep[len("catalog/"):]          # relative to catalog/
        return (f"[`{sp}`]({sp}) ({len(rows_by_dir.get(section, []))} rows), "
                f"[`{ep}`]({ep}) ({len(edges_by_dir.get(section, []))} edges)")

    lines: list[str] = []
    a = lines.append
    a("---")
    a(f"title: 'Module map: {slug or 'repo'}'")
    a("type: catalog-map")
    a("provenance: extracted")
    a("---")
    a(f"# Module map: {slug or 'repo'}" + (f" @ {ref[:10]}" if ref else ""))
    a("")
    sym_glob = "symbols.tsv" if depth <= 0 else "symbols/*.tsv"
    edge_glob = "edges.tsv" if depth <= 0 else "edges/*.tsv"
    a(f"{len(modules)} modules, {len(docs)} documentable symbols, {n_rows} index rows "
      f"(overloads share a row), {n_edges} caller edges. The index is tab-separated; grep it by "
      f"anchor, never read a file whole. Columns: anchor, path, line, kind, rank, hash, callers, "
      f"citing pages, then signature and doc line in the full profile. Hubs have hundreds of "
      f"callers and a search tool shows about 50 lines: count first, narrow by directory, end "
      f"every listing with `head`.")
    if source_base and source_base.startswith(("http://", "https://")):
        a("Source links below are permalinks into the repository at the pin, for readers with "
          "access to it; do not fetch them. The index rows carry the path, line, signature and "
          "doc line, and the citation's link title carries the source location.")
    a("")
    a("```")
    a(f"grep -P '^<path>#<Name>\\t' catalog/{sym_glob}                            # one symbol's row")
    a(f"grep -P '[#.]<Name>\\t' catalog/{sym_glob} | sort -t$'\\t' -k5 -nr | head -20   # by bare name, best first")
    a(f"grep -P '^<dir>/.*#<Name>\\t' catalog/{sym_glob} | head -20                    # a name within an area")
    a(f"awk -F'\\t' '$2==\"<path>\"' catalog/{sym_glob} | cut -f1,3,4 | head -40         # everything a module defines")
    a(f"grep -c -P '^<path>#<Name>\\t' catalog/{edge_glob}                         # how many callers (count first)")
    a(f"grep -P '^<path>#<Name>\\t' catalog/{edge_glob} | cut -f2 | cut -d/ -f1-2 | sort | uniq -c   # callers by directory")
    a(f"grep -P '^<path>#<Name>\\t<dir>/' catalog/{edge_glob} | cut -f2 | head -20      # callers in one directory")
    a(f"grep -P '\\t<path>#<Name>$' catalog/{edge_glob} | cut -f1 | head -20           # what it calls")
    a("```")
    a("")
    a("## Index files")
    a("")
    a("| Shard | Symbols | Edges |")
    a("|---|---|---|")
    for shard in sorted(rows_by_shard):
        sp, ep = shard_paths(shard)
        sp, ep = sp[len("catalog/"):], ep[len("catalog/"):]
        a(f"| `{shard}` | [`{sp}`]({sp}) — {len(rows_by_shard[shard])} rows | "
          f"[`{ep}`]({ep}) — {len(edges_by_shard.get(shard, []))} edges |")
    a("")
    a("Below, one row per module: entry points are the symbols with the most callers; *cited by* "
      "lists the concept pages that cite into the module.")
    a("")
    for top in sorted(by_top):
        a(f"## `{top}`")
        a("")
        a(f"Index: {_links(top)}")
        a("")
        a("| Module | Symbols | Entry points | Cited by |")
        a("|---|---|---|---|")
        for mp in sorted(by_top[top]):
            ms = modules[mp]
            ranked = sorted(ms, key=lambda m: (-len(graph.callers(m)), -graph.importance(m), m))
            eps = ", ".join(f"`{graph.symbols[m].name}`" for m in ranked[:3])
            cited = sorted({covered[m] for m in ms if m in covered})
            cite_txt = ", ".join(f"[{c}](../concepts/{c}.md)" for c in cited)
            if pages:
                link = f"[`{mp}`]({catalog_rel_path(mp)})"
            elif source_base:
                link = f"[`{mp}`]({_src_link(source_base, mp)})"
            else:
                link = f"`{mp}`"
            purpose = purposes.get(mp, "")
            cell = f"{link} — {purpose}" if purpose else link
            a(f"| {cell} | {len(ms)} | {eps} | {cite_txt} |")
        a("")
    return "\n".join(lines) + "\n"
