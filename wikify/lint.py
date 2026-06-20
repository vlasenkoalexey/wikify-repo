"""Stage 6 — the citation linter (implementation.md §5.3). The hallucination floor.

Hard, deterministic gate. For each concern page it enforces:
  1. Every link to ``symbols/*.md`` points to an existing stub whose frontmatter
     ``moniker`` resolves in the silo's SCIP graph. Dead link = FAIL.
  2. In "## Entry points" and "## Mechanism (step-by-step)", every list item
     carries ≥1 symbol citation or an L2 evidence link — unless it is inside a
     ``> [!inferred]`` block. Uncited assertion there = FAIL.
  3. No symbol cited that is absent from this concern's packet subgraph
     (catches invented symbols). = FAIL.

Checkable without NLP because rules 2–3 are scoped to named sections and list
items, not arbitrary prose.
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


def _frontmatter_moniker(stub_path: Path) -> str | None:
    try:
        text = stub_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return None
    val = fm.get("moniker")
    return str(val) if val else None


def _is_symbol_link(target: str) -> bool:
    return "symbols/" in target and target.endswith(".md")


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

        # rule 1 & 3: validate every symbol/evidence link on this line
        for label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            stub = (page_path.parent / target).resolve()
            if not stub.exists():
                errors.append(LintError(rel, i, 1, f"dead citation → {target} (no stub)"))
                continue
            moniker = _frontmatter_moniker(stub)
            if moniker is None or moniker not in graph:
                errors.append(
                    LintError(rel, i, 1, f"stub {target} moniker does not resolve in SCIP index")
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
    """Resolve the monikers a concern page cites (via its stub frontmatter)."""
    monikers: set[str] = set()
    for line in page_path.read_text(encoding="utf-8").splitlines():
        for _label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            stub = (page_path.parent / target).resolve()
            if stub.exists():
                m = _frontmatter_moniker(stub)
                if m:
                    monikers.add(m)
    return monikers


def lint_stub(stub_path: Path, graph: SymbolGraph) -> list[LintError]:
    moniker = _frontmatter_moniker(stub_path)
    if moniker is None:
        return [LintError(stub_path.name, 1, 1, "stub missing frontmatter moniker")]
    if moniker not in graph:
        return [LintError(stub_path.name, 1, 1, f"stub moniker not in SCIP index: {moniker}")]
    return []


def lint_silo(
    wiki_slug_dir: str | Path,
    graph: SymbolGraph,
    cache_dir: str | Path,
    slug: str,
) -> LintReport:
    """Lint every concern page + stub in a silo."""
    wiki_slug_dir = Path(wiki_slug_dir)
    errors: list[LintError] = []

    for stub in sorted((wiki_slug_dir / "symbols").glob("*.md")):
        errors.extend(lint_stub(stub, graph))

    for page in sorted((wiki_slug_dir / "concerns").glob("*.md")):
        concern_slug = page.stem
        subgraph = packet.read_subgraph(cache_dir, slug, concern_slug)
        errors.extend(lint_page(page, graph, subgraph))

    return LintReport(errors)
