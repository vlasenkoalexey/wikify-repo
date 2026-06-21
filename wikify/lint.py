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

Symbols live in their module catalog (frontmatter ``symbols`` anchor→moniker map),
not in per-symbol stubs — citations are catalog anchors. Checkable without NLP
because rules 2–3 are scoped to named sections and list items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import packet
from .graph import SymbolGraph

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


def _is_symbol_link(target: str) -> bool:
    """A symbol citation is a catalog link carrying an anchor."""
    path = target.split("#", 1)[0]
    return "catalog/" in path and path.endswith(".md") and "#" in target


def _resolve_citation(page_path: Path, target: str) -> str | None:
    """Resolve a ``../catalog/<module>.md#anchor`` citation → moniker (or None).

    The catalog stores anchors → moniker *suffix* under a factored-out
    ``symbol_base`` (the common prefix); reconstruct ``base + suffix``. Falls back
    to the raw value when ``symbol_base`` is absent (older, uncompressed catalogs)."""
    path, _, anchor = target.partition("#")
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
            moniker = _resolve_citation(page_path, target)
            if moniker is None:
                errors.append(
                    LintError(rel, i, 1, f"dead citation → {target} (anchor not in catalog)")
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


def page_citations(page_path: Path) -> set[str]:
    """Resolve the monikers a concept page cites (via catalog anchor resolution)."""
    monikers: set[str] = set()
    for line in page_path.read_text(encoding="utf-8").splitlines():
        for _label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            m = _resolve_citation(page_path, target)
            if m:
                monikers.add(m)
    return monikers


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
