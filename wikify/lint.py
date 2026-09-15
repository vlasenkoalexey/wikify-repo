"""Stage 6 — the citation linter (implementation.md §5.3). The hallucination floor.

Hard, deterministic gate. For each concept page it enforces:
  1. Every symbol citation is a link into a module **catalog** with an anchor
     (``../catalog/<module>.md#<anchor>``) that resolves, via the catalog's
     frontmatter ``symbols`` map, to a moniker present in the silo's SCIP graph.
     Dead/unresolvable citation = FAIL.
  2. In "## Entry points" and "## Mechanism (step-by-step)", every list item
     carries ≥1 symbol citation or an L2 evidence link — unless it is inside a
     ``> [!inferred]`` block. Uncited assertion there = FAIL.
  3. No symbol cited that is absent from this concept's packet subgraph
     (catches invented symbols). = FAIL.

Symbols live in their module catalog, not in per-symbol stubs — citations are catalog
anchors. Since 0.3 (catalog-index.md) the resolution table is the GRAPH
(``coverage.symbol_index``: module from the link path + qualified name), so lint works in
every catalog tier — full pages, anchor-only pages, or no pages at all. The catalog page's
front-matter map is only a fallback for callers that have no graph in hand. A citation may
carry a link title (``"path:Lnn"``, the source location); it is stripped, never resolved.
Checkable without NLP because rules 2–3 are scoped to named sections and list items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import packet
from .graph import SymbolGraph

# Per-graph resolution tables (``coverage.symbol_index``), built once per graph object.
_INDEX_CACHE: dict[int, tuple[SymbolGraph, dict[tuple[str, str], str]]] = {}

_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
_CITED_SECTIONS = ("Entry points", "Mechanism")  # prefix match on heading text


@dataclass
class LintError:
    page: str
    line: int
    rule: int
    message: str

    def __str__(self) -> str:
        return f"{self.page}:{self.line} [rule {self.rule}] {self.message}"


@dataclass
class LintReport:
    errors: list[LintError]

    @property
    def ok(self) -> bool:
        return not self.errors


def _frontmatter_dict(page_path: Path) -> dict:
    try:
        text = page_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def strip_title(target: str) -> str:
    """Drop a markdown link title: ``x.md#A "torch/a.py:L12"`` -> ``x.md#A``."""
    return target.split(None, 1)[0] if " " in target else target


def _is_symbol_link(target: str) -> bool:
    """A symbol citation is a catalog link carrying an anchor."""
    target = strip_title(target)
    path = target.split("#", 1)[0]
    return "catalog/" in path and path.endswith(".md") and "#" in target


def _graph_index(graph: SymbolGraph) -> dict[tuple[str, str], str]:
    hit = _INDEX_CACHE.get(id(graph))
    if hit is not None and hit[0] is graph:
        return hit[1]
    from . import coverage
    idx = coverage.symbol_index(graph)
    _INDEX_CACHE[id(graph)] = (graph, idx)
    return idx


def _resolve_citation(page_path: Path, target: str, graph: SymbolGraph | None = None) -> str | None:
    """Resolve a ``../catalog/<module>.md#anchor`` citation → moniker (or None).

    With ``graph``: the module is the link path after ``catalog/`` and the anchor is the
    qualified name — resolved in the graph's symbol index, no file read, any catalog tier.
    Without a graph (a caller that only has the page): fall back to the catalog page's
    front-matter ``symbols`` map, reconstructing ``symbol_base + suffix``."""
    target = strip_title(target)
    path, _, anchor = target.partition("#")
    if graph is not None:
        i = path.find("catalog/")
        rel = path[i + len("catalog/"):] if i >= 0 else path
        return _graph_index(graph).get((rel, anchor))
    catalog_page = (page_path.parent / path).resolve()
    fm = _frontmatter_dict(catalog_page)
    syms = fm.get("symbols") or {}
    if anchor not in syms:
        return None
    return f"{fm.get('symbol_base', '')}{syms[anchor]}"


def _is_evidence_link(target: str) -> bool:
    return target.endswith(".md") and ("tests/" in target or "sources/" in target)


def lint_page(
    page_path: Path,
    graph: SymbolGraph,
    subgraph: set[str],
) -> list[LintError]:
    errors: list[LintError] = []
    rel = page_path.name
    text = page_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    section = ""
    in_inferred = False
    # group multi-line list items for rule 2
    pending: list[tuple[int, str]] = []  # (start_line, accumulated text) -> validate

    def flush_item(item_lines: list[str], start: int) -> None:
        if not item_lines:
            return
        body = "\n".join(item_lines)
        cited = any(
            _is_symbol_link(t) or _is_evidence_link(t) for _, t in _LINK.findall(body)
        )
        if not cited:
            errors.append(
                LintError(rel, start, 2, f"uncited item in '## {section}': "
                          f"{item_lines[0].strip()[:60]!r}")
            )

    cur_item: list[str] = []
    cur_start = 0

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # section tracking
        if stripped.startswith("## "):
            if cur_item:
                flush_item(cur_item, cur_start)
                cur_item = []
            section = stripped[3:].strip()
            in_inferred = False
            continue

        # inferred-block tracking
        if "[!inferred]" in line:
            in_inferred = True
        elif in_inferred and not stripped.startswith(">") and stripped:
            in_inferred = False

        # rule 1 & 3: validate every symbol citation (catalog anchor) on this line
        for label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            moniker = _resolve_citation(page_path, target, graph)
            if moniker is None:
                errors.append(
                    LintError(rel, i, 1, f"dead citation → {strip_title(target)} (no such symbol in the index)")
                )
                continue
            if moniker not in graph:
                errors.append(
                    LintError(rel, i, 1, f"citation {target} resolves to a moniker not in the SCIP index")
                )
                continue
            if subgraph and moniker not in subgraph:
                errors.append(
                    LintError(rel, i, 3, f"cited symbol outside packet subgraph: `{label}`")
                )

        # rule 2: in cited sections, group list items and require a citation
        in_cited_section = section.startswith(_CITED_SECTIONS)
        if in_cited_section and not in_inferred:
            if _LIST_ITEM.match(line):
                if cur_item:
                    flush_item(cur_item, cur_start)
                cur_item = [line]
                cur_start = i
            elif cur_item and (stripped == "" or stripped.startswith("#")):
                flush_item(cur_item, cur_start)
                cur_item = []
            elif cur_item:
                cur_item.append(line)  # continuation
        elif cur_item:
            flush_item(cur_item, cur_start)
            cur_item = []

    if cur_item:
        flush_item(cur_item, cur_start)
    return errors


def page_citations(page_path: Path, graph: SymbolGraph | None = None) -> set[str]:
    """Resolve the monikers a concept page cites (graph index when given, else catalog
    front matter)."""
    monikers: set[str] = set()
    for line in page_path.read_text(encoding="utf-8").splitlines():
        for _label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            m = _resolve_citation(page_path, target, graph)
            if m:
                monikers.add(m)
    return monikers


def lint_rule1_dir(wiki_slug_dir: str | Path, graph: SymbolGraph, subdir: str) -> LintReport:
    """Rule 1 only over every page in ``<silo>/<subdir>``: each catalog citation must
    resolve to a real symbol. No subgraph gate (rule 3), no uncited-item gate (rule 2)."""
    errors: list[LintError] = []
    d = Path(wiki_slug_dir) / subdir
    if not d.is_dir():
        return LintReport(errors)
    for page in sorted(d.glob("*.md")):
        for i, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            for _label, target in _LINK.findall(line):
                if not _is_symbol_link(target):
                    continue
                moniker = _resolve_citation(page, target, graph)
                if moniker is None:
                    errors.append(LintError(page.name, i, 1,
                                  f"dead citation → {strip_title(target)} (no such symbol in the index)"))
                elif moniker not in graph:
                    errors.append(LintError(page.name, i, 1,
                                  f"citation {target} resolves to a moniker not in the SCIP index"))
    return LintReport(errors)


def lint_doc_concepts(wiki_slug_dir: str | Path, graph: SymbolGraph) -> LintReport:
    """Light lint for doc-derived concept pages (`doc-concepts/`): every catalog
    citation must resolve to a real symbol (rule 1). These come from a project doc,
    not a SCIP packet, so the subgraph gate (rule 3) and uncited-item gate (rule 2)
    do NOT apply — a doc-concept may state prose freely; it just may not cite a
    symbol that doesn't exist."""
    return lint_rule1_dir(wiki_slug_dir, graph, "doc-concepts")


def lint_areas(wiki_slug_dir: str | Path, graph: SymbolGraph) -> LintReport:
    """Area pages (`areas/`, prose-budget.md): rule 1 only, like doc-concepts — their
    citations come from the planner scaffold, not a packet subgraph."""
    return lint_rule1_dir(wiki_slug_dir, graph, "areas")


def lint_silo(
    wiki_slug_dir: str | Path,
    graph: SymbolGraph,
    cache_dir: str | Path,
    slug: str,
) -> LintReport:
    """Lint every concept page in a silo (citations resolve into module catalogs)."""
    wiki_slug_dir = Path(wiki_slug_dir)
    errors: list[LintError] = []
    for page in sorted((wiki_slug_dir / "concepts").glob("*.md")):
        concept_slug = page.stem
        subgraph = packet.read_subgraph(cache_dir, slug, concept_slug)
        errors.extend(lint_page(page, graph, subgraph))
    return LintReport(errors)
