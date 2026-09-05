"""Diagram checks (§10.17): a Mermaid structural floor and a lint-checked legend.

Mermaid's grammar lives only in JavaScript, so this is deliberately a *heuristic floor*
(the four general rules oh-my-mermaid's validator ships, ported) — warnings, never a gate:

- a fence must start with a known diagram type (``flowchart TD``, ``sequenceDiagram``, ...);
- brackets balance per line (quoted label text excluded);
- a flowchart has a readable number of nodes (<= ``MAX_NODES``) and parses to at least one;
- an empty fence is reported.

The **legend** is what makes a diagram usable by a reader: under a flowchart fence a
``Legend:`` list maps each node id (or label) to the symbol it stands for as an ordinary
catalog citation, or to ``(concept)`` for an abstraction. Legend links are gated by the
existing citation rules (1: anchor resolves, 3: inside the packet), so nothing new can be
cited through a legend; this module only checks *coverage*: every node has a legend entry and
every entry names a node. Fences without a legend are counted, not listed, so silos written
before legends existed stay quiet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_NODES = 20
DIAGRAM_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram-v2", "stateDiagram",
    "erDiagram", "gantt", "pie", "journey", "gitGraph", "mindmap", "timeline", "quadrantChart",
    "xychart-beta", "block-beta", "sankey-beta", "C4Context", "C4Container", "C4Component",
    "requirementDiagram", "zenuml", "packet-beta", "architecture-beta", "kanban",
)
_FENCE_RE = re.compile(r"^```mermaid[^\n]*\n(.*?)^```", re.S | re.M)
_KEYWORDS = {"subgraph", "end", "classDef", "class", "style", "linkStyle", "click", "direction",
             "flowchart", "graph", "LR", "RL", "TD", "TB", "BT"}
_NODE_DEF_RE = re.compile(
    r'(?<![\w"])([A-Za-z_][\w.-]*)\s*(\[\[|\[\(|\(\(|\(\[|\[/|\[\\|\{\{|\[|\(|\{|>)\s*"?([^"\]\)\}\|]*?)"?\s*(?:\]\]|\)\]|\)\)|\]\)|/\]|\\\]|\}\}|\]|\)|\})')
_LEGEND_ITEM_RE = re.compile(r"^\s*[-*]\s+`([^`]+)`\s*(?:[—:-]\s*(.*))?$")
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


@dataclass
class Fence:
    line: int            # 1-based line of the opening fence
    body: str
    kind: str = ""       # first non-directive line's diagram type ('' if none)
    end_line: int = 0    # 1-based line of the closing fence


@dataclass
class Legend:
    entries: list[tuple[str, str | None]] = field(default_factory=list)   # (name, link target|None)


def _strip_quotes(s: str) -> str:
    return re.sub(r'"[^"]*"', '""', s)


def fences(text: str) -> list[Fence]:
    out: list[Fence] = []
    for m in _FENCE_RE.finditer(text):
        start = text.count("\n", 0, m.start()) + 1
        body = m.group(1)
        end = start + body.count("\n") + 1
        out.append(Fence(line=start, body=body, kind=_diagram_type(body), end_line=end))
    return out


def _content_lines(body: str) -> list[str]:
    lines: list[str] = []
    in_fm = False
    for i, raw in enumerate(body.splitlines()):
        s = raw.strip()
        if i == 0 and s == "---":
            in_fm = True
            continue
        if in_fm:
            if s == "---":
                in_fm = False
            continue
        if not s or s.startswith("%%"):
            continue
        lines.append(s)
    return lines


def _diagram_type(body: str) -> str:
    lines = _content_lines(body)
    if not lines:
        return ""
    first = lines[0]
    for t in DIAGRAM_TYPES:
        if first == t or first.startswith(t + " ") or first.startswith(t + "\n"):
            return t
    return ""


def flowchart_nodes(body: str) -> tuple[set[str], dict[str, str]]:
    """``(node ids, {id: label})`` for a flowchart/graph body. Heuristic: label-bearing node
    definitions first, then bare ids around edge operators on non-directive lines."""
    ids: set[str] = set()
    labels: dict[str, str] = {}
    for line in _content_lines(body)[1:]:
        head = line.split()[0] if line.split() else ""
        if head in ("subgraph", "end", "classDef", "class", "style", "linkStyle", "click", "direction"):
            continue
        for m in _NODE_DEF_RE.finditer(line):
            nid, label = m.group(1), m.group(3).strip()
            if nid not in _KEYWORDS:
                ids.add(nid)
                if label:
                    labels[nid] = label
        stripped = _strip_quotes(line)
        stripped = re.sub(r"\|[^|]*\|", " ", stripped)                  # edge labels
        stripped = re.sub(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|>[^\]]*\]", " ", stripped)  # node labels
        stripped = re.sub(r"<?[-=.]{2,}[>xo]?|~{3,}|&", " ", stripped)  # arrows and joins
        for tok in stripped.split():
            if re.fullmatch(r"[A-Za-z_][\w.-]*", tok) and tok not in _KEYWORDS:
                ids.add(tok)
    return ids, labels


def legend_after(text: str, fence: Fence) -> Legend | None:
    """The ``Legend:`` list following a fence (blank line or heading ends it)."""
    lines = text.splitlines()
    i = fence.end_line               # 0-based index of the line after the closing fence
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower().rstrip(":") == "legend":
        i += 1
    entries: list[tuple[str, str | None]] = []
    while i < len(lines):
        m = _LEGEND_ITEM_RE.match(lines[i])
        if not m:
            break
        rest = m.group(2) or ""
        link = _LINK_RE.search(rest)
        entries.append((m.group(1).strip(), link.group(1) if link else None))
        i += 1
    return Legend(entries) if entries else None


def _norm(s: str) -> str:
    return " ".join(s.replace('"', "").split()).lower()


def check_page(page_path: str | Path) -> tuple[list[str], int, int]:
    """``(warnings, fences_seen, flowcharts_without_legend)`` for one page."""
    page_path = Path(page_path)
    text = page_path.read_text(encoding="utf-8", errors="replace")
    name = page_path.name
    warnings: list[str] = []
    no_legend = 0
    fs = fences(text)
    for f in fs:
        where = f"{name}:L{f.line}"
        content = _content_lines(f.body)
        if not content:
            warnings.append(f"{where}: empty mermaid fence")
            continue
        if not f.kind:
            warnings.append(f"{where}: no diagram type on the first line (got {content[0][:40]!r})")
            continue
        for ln in content:
            s = _strip_quotes(ln)
            if s.count("[") != s.count("]") or s.count("(") != s.count(")") or s.count("{") != s.count("}"):
                warnings.append(f"{where}: unbalanced brackets: {ln[:80]}")
                break
        if f.kind in ("flowchart", "graph"):
            ids, labels = flowchart_nodes(f.body)
            if not ids:
                warnings.append(f"{where}: flowchart with no recognizable nodes")
            elif len(ids) > MAX_NODES:
                warnings.append(f"{where}: {len(ids)} nodes (max {MAX_NODES} for a readable diagram; split it)")
            legend = legend_after(text, f)
            if legend is None:
                no_legend += 1
                continue
            names = {_norm(n) for n, _ in legend.entries}
            known = {_norm(i) for i in ids} | {_norm(l) for l in labels.values()}
            missing = sorted(i for i in ids if _norm(i) not in names and _norm(labels.get(i, "")) not in names)
            orphans = sorted(n for n, _ in legend.entries if _norm(n) not in known)
            if missing:
                warnings.append(f"{where}: legend misses node(s): {', '.join(missing[:8])}"
                                + (f" (+{len(missing) - 8})" if len(missing) > 8 else ""))
            if orphans:
                warnings.append(f"{where}: legend names no node in the diagram: {', '.join(orphans[:8])}")
    return warnings, len(fs), no_legend


def check_silo(silo_dir: str | Path) -> tuple[list[str], int, int, int]:
    """``(warnings, pages_with_fences, fences, flowcharts_without_legend)`` over concepts/."""
    warnings: list[str] = []
    pages = fences_total = no_legend = 0
    for page in sorted((Path(silo_dir) / "concepts").glob("*.md")):
        w, n, nl = check_page(page)
        warnings += w
        if n:
            pages += 1
        fences_total += n
        no_legend += nl
    return warnings, pages, fences_total, no_legend
