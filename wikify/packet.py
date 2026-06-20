"""Build synthesis packets — the Python → LLM interface (implementation.md §5.4).

A packet is ONE markdown file per concept at ``.cache/packets/<slug>/<concept>.md``.
It carries the seeds, the implementing subgraph (symbols + callers/callees), the
relevant source snippets, the test evidence, and the page template + citation
rules. The agent reads ONLY the packet and writes one mechanism page.

Determinism boundary: packet building is pure Python. The subgraph it pins is
also written as a sidecar ``<concept>.subgraph.txt`` so the linter can enforce
"no symbol cited outside this packet's subgraph" (§5.3 rule 3).
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from . import coverage, evidence, source
from .config import Concept
from .graph import SymbolGraph

MAX_SUBGRAPH = 50
SNIPPET_LINES = 50


# --------------------------------------------------------------------------- #
# Seed resolution + subgraph traversal
# --------------------------------------------------------------------------- #
def _seed_tail(token: str) -> str:
    """Last identifier of a seed token like ``Trainer::step`` or ``a.b.c``."""
    for sep in ("::", "#", "/", "."):
        token = token.replace(sep, ".")
    return token.strip().split(".")[-1].strip("`() ")


def resolve_seeds(graph: SymbolGraph, seeds: list[str]) -> tuple[list[str], list[str]]:
    """Map seed tokens to monikers. Returns (resolved_monikers, unresolved_tokens)."""
    resolved: list[str] = []
    unresolved: list[str] = []
    for token in seeds:
        tail = _seed_tail(token)
        matches = graph.find(tail)
        # If the seed named a container (e.g. Trainer::step), prefer monikers
        # whose string also mentions the container segment.
        container = _seed_container(token)
        if container:
            narrowed = [m for m in matches if container in m]
            if narrowed:
                matches = narrowed
        if matches:
            resolved.extend(matches)
        else:
            unresolved.append(token)
    # de-dup, keep order
    seen: set[str] = set()
    ordered = [m for m in resolved if not (m in seen or seen.add(m))]
    return ordered, unresolved


def _seed_container(token: str) -> str | None:
    for sep in ("::", "#"):
        if sep in token:
            return token.split(sep)[0].strip("`() ").split(".")[-1]
    return None


def auto_seeds(graph: SymbolGraph, n: int = 8) -> list[str]:
    """Discovery fallback: the n highest-importance callable symbols."""
    callables = [m for m, s in graph.symbols.items() if s.is_callable]
    callables.sort(key=graph.importance, reverse=True)
    return callables[:n]


def gather_subgraph(
    graph: SymbolGraph, seeds: list[str], max_nodes: int = MAX_SUBGRAPH
) -> list[str]:
    """BFS over callees from the seeds (plus seeds' direct callers for context)."""
    result: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque(seeds)
    for s in seeds:  # one hop of callers gives entry-point context
        for caller in sorted(graph.callers(s)):
            queue.append(caller)
    while queue and len(result) < max_nodes:
        m = queue.popleft()
        if m in visited or m not in graph.symbols:
            continue
        visited.add(m)
        result.append(m)
        for callee in sorted(graph.callees(m)):
            if callee not in visited:
                queue.append(callee)
    return result


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _short(moniker: str, graph: SymbolGraph) -> str:
    return graph.symbols[moniker].name or moniker


def _kind(sym) -> str:
    """Display kind: scip-python often leaves Kind unspecified; fall back to the
    descriptor suffix (e.g. Method/Type/Term), which the frontend always sets."""
    if sym.kind and sym.kind != "UnspecifiedKind":
        return sym.kind
    return sym.suffix or "symbol"


def build_packet(
    graph: SymbolGraph,
    repo_root: str | Path,
    slug: str,
    ref: str,
    concept: Concept,
    test_globs: list[str],
    date: str,
    seed_monikers: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Render the packet markdown and return (text, subgraph_monikers).

    ``seed_monikers`` (from discovery) are used directly; otherwise the concept's
    seed tokens are resolved by name."""
    if seed_monikers:
        seeds = [m for m in seed_monikers if m in graph.symbols]
        unresolved = []
    else:
        seeds, unresolved = resolve_seeds(graph, concept.seeds)
    if not seeds:
        seeds = auto_seeds(graph)
        seed_note = "(discover: top-importance symbols — no seeds resolved)"
    else:
        seed_note = ", ".join(f"`{n}`" for n in sorted({_short(s, graph) for s in seeds}))
        if unresolved:
            seed_note += f"  (unresolved seed tokens: {', '.join(unresolved)})"

    subgraph = gather_subgraph(graph, seeds)
    subset = set(subgraph)
    tests = evidence.collect_tests(graph, test_globs, subset)

    lines: list[str] = []
    a = lines.append
    a(f"# Packet: {concept.slug}  (repo {slug} @ {ref})")
    a("")
    a("## Seeds")
    a(seed_note)
    a("")
    a("## Subgraph")
    a("Cite ONLY these symbols. Each: moniker · signature · def · calls/refs.")
    a("")
    for m in subgraph:
        sym = graph.symbols[m]
        a(f"### `{sym.name}`  ({_kind(sym)})")
        a(f"- moniker: `{m}`")
        if sym.def_path:
            a(f"- cite: [`{sym.name}`]({coverage.catalog_ref(sym.def_path, m)})")
        else:
            a("- cite: (external symbol — no catalog home; do not cite)")
        if sym.signature:
            a(f"- signature: `{sym.signature}`")
        if sym.doc_summary:
            # Author's docstring — citable L2 evidence; prefer quoting over guessing.
            a(f"- doc (author intent, L2): {sym.doc_summary}")
        loc = f"{sym.def_path}:{(sym.def_line or 0) + 1}" if sym.def_path else "(unknown)"
        a(f"- def: {loc}")
        callees = sorted(_short(c, graph) for c in graph.callees(m) & subset)
        callers = sorted(_short(c, graph) for c in graph.callers(m) & subset)
        a(f"- calls/refs: {', '.join(callees) if callees else '(none in subgraph)'}")
        a(f"- called by: {', '.join(callers) if callers else '(none in subgraph)'}")
        a("")

    a("## Source")
    for m in subgraph:
        sym = graph.symbols[m]
        snippet = source.read_snippet(repo_root, sym, max_lines=SNIPPET_LINES)
        if not snippet:
            continue
        a(f"### `{sym.name}` — {sym.def_path}:{(sym.def_line or 0) + 1}")
        a("```python")
        a(snippet)
        a("```")
        a("")

    a("## Evidence")
    if tests:
        a("Tests exercising subgraph symbols (assert → symbols):")
        a("")
        a("| Test | Defined | Exercises |")
        a("|---|---|---|")
        for t in tests:
            ex = ", ".join(f"`{_short(m, graph)}`" for m in t.exercises)
            loc = f"{t.path}:{(t.line or 0) + 1}" if t.path else ""
            a(f"| `{t.name}` | {loc} | {ex} |")
    else:
        a("(no tests in the configured test paths reference this subgraph)")
    a("")

    a("## Template + rules")
    a(_TEMPLATE_RULES.format(concept=concept.slug, date=date))

    return "\n".join(lines) + "\n", subgraph


def write_packet(
    cache_dir: str | Path, slug: str, concept_slug: str, text: str, subgraph: list[str]
) -> Path:
    out_dir = Path(cache_dir) / "packets" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    pkt = out_dir / f"{concept_slug}.md"
    pkt.write_text(text, encoding="utf-8")
    (out_dir / f"{concept_slug}.subgraph.txt").write_text(
        "\n".join(subgraph) + "\n", encoding="utf-8"
    )
    return pkt


def read_subgraph(cache_dir: str | Path, slug: str, concept_slug: str) -> set[str]:
    p = Path(cache_dir) / "packets" / slug / f"{concept_slug}.subgraph.txt"
    if not p.exists():
        return set()
    return {ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


_TEMPLATE_RULES = """\
Write ONE file: `wiki/<slug>/concepts/{concept}.md`. You do NOT create any symbol
stubs — every symbol already has a home in its module catalog. To cite a symbol,
copy its `cite:` link from the Subgraph above VERBATIM (it points into the
generated catalog, e.g. `[`Sym`](../catalog/<module>.md#Sym)`). The linter
resolves each citation against the catalog's symbol table, so the link must match.

HARD RULES:
- Use ONLY symbols from the Subgraph above. Never name a symbol not listed there.
  If you need a missing one, say so in Open questions — do not invent it.
- In "## Entry points" and "## Mechanism (step-by-step)", EVERY bullet/step must
  carry a symbol citation: paste the symbol's `cite:` link, optionally tagged
  `[extracted → `Sym`](../catalog/...#Sym)`. Uncited claims there fail the lint.
- Any claim you cannot ground in a cited symbol or an Evidence item goes inside a
  `> [!inferred]` blockquote — never stated as fact.
- Prefer the author's `doc (author intent, L2)` lines over guessing; you may quote
  them. "## Dynamics (design intent)" uses tests/source/docstrings only; never
  claim runtime behavior. Do not write an "Observed dynamics" section.

PAGE TEMPLATE:
---
title: <concept title>
type: concept
provenance: mixed
concept: {concept}
updated: {date}
status: fresh
---
# <concept title>
<one-line scope>
## Entry points
- <cite link> — what it is, when it's hit.
## Mechanism (step-by-step)
1. <step> [extracted → `Sym`](../catalog/<module>.md#Sym)
## Key data structures
## Dynamics (design intent)
## Edge cases
## Open questions
## See also
"""
