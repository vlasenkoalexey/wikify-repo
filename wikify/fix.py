"""``finalize --fix`` / ``lint --fix`` — deterministic auto-repair of lint errors.

The citation linter (``lint.py``) is a hard gate; agent-written pages routinely
trip it in three mechanical ways that need no judgment to fix:

  - **rule 1** (dead anchor): right module, wrong anchor — e.g. ``#num_inputs``
    instead of ``#AutogradCompilerInstance.num_inputs``.
  - **rule 3** (symbol outside subgraph): a citation to a symbol that isn't
    citable from this packet.
  - **rule 2** (uncited Entry-points / Mechanism item): a step that names a
    citable symbol in backticks but never linked it.

All three are repaired against the **packet** (the ground truth of what's
citable): a name→correct-cite-link map drives both anchor repair and re-citation;
a symbol with no citable home is de-linked to plain code (never invented). This is
pure Python — it only ever *removes* a citation or swaps it for the packet's own
verbatim link, so it cannot manufacture grounding. Residual errors (a step that
names nothing citable) are left for a human/agent and reported.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import packet
from .graph import SymbolGraph
from .lint import (
    _LINK,
    _LIST_ITEM,
    _is_symbol_link,
    _resolve_citation,
    lint_page,
    lint_silo,
)

_BACKTICK = re.compile(r"`([^`]+)`")
_CITE_LINE = re.compile(r"^- cite:\s*\[([^\]]*)\]\(([^)]+)\)")


def _packet_cite_map(cache_dir: Path, slug: str, concept_slug: str) -> dict[str, str]:
    """Parse the packet's ``- cite:`` lines → {symbol name: correct catalog link}.

    Backticks are stripped from the label to key by bare symbol name. On a name
    collision (two overloads), last wins — any in-subgraph symbol of that name is a
    valid citation, and every packet cite is in-subgraph by construction."""
    pkt = Path(cache_dir) / "packets" / slug / f"{concept_slug}.md"
    out: dict[str, str] = {}
    try:
        lines = pkt.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        m = _CITE_LINE.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip().strip("`").strip()
        target = m.group(2).strip()
        if name and "catalog/" in target:
            out[name] = target
    return out


def _strip_ticks(label: str) -> str:
    return label.strip().strip("`").strip()


def fix_page(
    page_path: Path,
    graph: SymbolGraph,
    subgraph: set[str],
    cite_map: dict[str, str],
) -> int:
    """Repair fixable lint errors in ``page_path`` in place. Returns #edits made."""
    text = page_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    edits = 0

    # PASS 1 — per citation: repair (rule 1) or de-link (rule 3) any symbol link
    # that doesn't resolve into this packet's subgraph.
    for idx, line in enumerate(lines):
        for label, target in _LINK.findall(line):
            if not _is_symbol_link(target):
                continue
            moniker = _resolve_citation(page_path, target)
            # Mirror the linter's validity test EXACTLY: a citation is fine if it
            # resolves and is either in this subgraph or this page has no subgraph
            # to check against (rule 3 is skipped when subgraph is empty). Without
            # this guard, a page with no packet would have all its valid citations
            # stripped — the opposite of a fix.
            if moniker is not None and (not subgraph or moniker in subgraph):
                continue  # already valid
            name = _strip_ticks(label)
            old = f"[{label}]({target})"
            correct = cite_map.get(name)
            if correct and correct != target:
                lines[idx] = lines[idx].replace(old, f"[{label}]({correct})")
            else:
                lines[idx] = lines[idx].replace(old, label)  # de-link, keep prose
            edits += 1

    if edits:
        page_path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                             encoding="utf-8")

    # PASS 2 — rule 2: for each remaining uncited Entry-points/Mechanism item, link
    # a citable symbol it already names in backticks. Re-lint to locate the items
    # precisely (reusing the linter's own section/item grouping).
    rule2 = [e for e in lint_page(page_path, graph, subgraph) if e.rule == 2]
    if rule2:
        lines = page_path.read_text(encoding="utf-8").splitlines()
        for err in rule2:
            if _cite_item_mention(lines, err.line - 1, cite_map):
                edits += 1
        page_path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                             encoding="utf-8")
    return edits


def _cite_item_mention(lines: list[str], start: int, cite_map: dict[str, str]) -> bool:
    """Link the first backticked, citable, not-yet-linked symbol in the list item
    beginning at ``lines[start]``. Returns True if a link was inserted."""
    end = start + 1
    while end < len(lines):
        s = lines[end].strip()
        if s == "" or s.startswith("#") or _LIST_ITEM.match(lines[end]):
            break
        end += 1
    for j in range(start, end):
        line = lines[j]
        for m in _BACKTICK.finditer(line):
            # skip a token that is already a markdown link label: ``[`x`](...)``
            if m.start() > 0 and line[m.start() - 1] == "[":
                continue
            name = m.group(1).strip()
            target = cite_map.get(name)
            if target:
                lines[j] = line[:m.start()] + f"[`{name}`]({target})" + line[m.end():]
                return True
    return False


def fix_silo(
    wiki_slug_dir: str | Path,
    graph: SymbolGraph,
    cache_dir: str | Path,
    slug: str,
) -> tuple[int, "object"]:
    """Auto-repair every concept page, then re-lint. Returns (#edits, residual report)."""
    wiki_slug_dir = Path(wiki_slug_dir)
    cache_dir = Path(cache_dir)
    edits = 0
    for page in sorted((wiki_slug_dir / "concepts").glob("*.md")):
        concept_slug = page.stem
        subgraph = packet.read_subgraph(cache_dir, slug, concept_slug)
        cite_map = _packet_cite_map(cache_dir, slug, concept_slug)
        edits += fix_page(page, graph, subgraph, cite_map)
    report = lint_silo(wiki_slug_dir, graph, cache_dir, slug)
    return edits, report
