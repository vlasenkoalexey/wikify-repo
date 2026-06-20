"""Stage 1 — run scip-python, parse the ``.scip`` index, build the SymbolGraph.

Deterministic, no model calls. The risky part is ``derive_edges``: SCIP has no
"call" role, so callers/callees are approximated by **reference scoping** — a
reference to in-repo symbol ``S`` occurring inside the body (enclosing range) of
definition ``F`` yields the edge ``F → S``. Symbol-accurate (the frontend bound
the name to the right symbol) but reference-based, not true call resolution.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import scip_pb2
from .graph import Symbol, SymbolGraph
from .monikers import parse_symbol

# SCIP role bits
_DEFINITION = scip_pb2.SymbolRole.Definition
_IMPORT = scip_pb2.SymbolRole.Import

# enum number → name, e.g. 17 → "Function"
_KIND_NAME = {v.number: v.name for v in scip_pb2.SymbolInformation.Kind.DESCRIPTOR.values}

Range = tuple[int, int, int, int]  # (start_line, start_char, end_line, end_char)

# Descriptor suffixes that denote function-local symbols, not citable nodes.
_LOCALISH_SUFFIXES = {"Parameter", "TypeParameter", "Meta"}


# --------------------------------------------------------------------------- #
# Running the indexer
# --------------------------------------------------------------------------- #
def run_indexer(
    project_dir: str | Path,
    output_path: str | Path,
    project_name: str | None = None,
    project_version: str = "0.0.0",
) -> Path:
    """Invoke ``scip-python index`` on ``project_dir`` → ``output_path`` (.scip)."""
    project_dir = Path(project_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    name = project_name or project_dir.name
    cmd = [
        "scip-python", "index",
        "--project-name", name,
        "--project-version", project_version,
        "--output", str(output_path.resolve()),
        ".",
    ]
    proc = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"scip-python failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return output_path


def parse_index(scip_path: str | Path) -> "scip_pb2.Index":
    index = scip_pb2.Index()
    index.ParseFromString(Path(scip_path).read_bytes())
    return index


# --------------------------------------------------------------------------- #
# Range helpers (handle typed oneof + deprecated packed-int encodings)
# --------------------------------------------------------------------------- #
def _occ_range(occ) -> Range | None:
    which = occ.WhichOneof("typed_range")
    if which == "single_line_range":
        r = occ.single_line_range
        return (r.line, r.start_character, r.line, r.end_character)
    if which == "multi_line_range":
        r = occ.multi_line_range
        return (r.start_line, r.start_character, r.end_line, r.end_character)
    rng = list(occ.range)
    if len(rng) == 3:
        return (rng[0], rng[1], rng[0], rng[2])
    if len(rng) == 4:
        return (rng[0], rng[1], rng[2], rng[3])
    return None


def _occ_enclosing(occ) -> Range | None:
    which = occ.WhichOneof("typed_enclosing_range")
    if which == "single_line_enclosing_range":
        r = occ.single_line_enclosing_range
        return (r.line, r.start_character, r.line, r.end_character)
    if which == "multi_line_enclosing_range":
        r = occ.multi_line_enclosing_range
        return (r.start_line, r.start_character, r.end_line, r.end_character)
    er = list(occ.enclosing_range)
    if len(er) == 3:
        return (er[0], er[1], er[0], er[2])
    if len(er) == 4:
        return (er[0], er[1], er[2], er[3])
    return None


def _contains(span: Range, point: tuple[int, int]) -> bool:
    """Is (line, char) within half-open span [start, end)?"""
    sl, sc, el, ec = span
    start, end = (sl, sc), (el, ec)
    return start <= point < end


def _span_size(span: Range) -> tuple[int, int]:
    sl, sc, el, ec = span
    return (el - sl, ec - sc)


def _signature(si) -> str:
    """Best-effort signature: first fenced code block in the documentation."""
    for doc in si.documentation:
        if "```" in doc:
            inside = doc.split("```", 2)
            if len(inside) >= 2:
                body = inside[1]
                if "\n" in body:
                    body = body.split("\n", 1)[1]
                return body.strip()
    return ""


# --------------------------------------------------------------------------- #
# Graph construction
# --------------------------------------------------------------------------- #
def build_graph(index) -> SymbolGraph:
    g = SymbolGraph()

    # 1) Nodes: every global SymbolInformation defined across all documents.
    for doc in index.documents:
        for si in doc.symbols:
            if si.symbol.startswith("local ") or si.symbol in g.symbols:
                continue
            ps = parse_symbol(si.symbol)
            if not ps.descriptors:
                continue
            name, suffix = ps.terminal
            # Drop locals: parameters, type-parameters, and meta descriptors are
            # not citable mechanism symbols (context-sherpa pruning rule, §5.1).
            if suffix in _LOCALISH_SUFFIXES:
                continue
            sym = Symbol(
                moniker=si.symbol,
                kind=_KIND_NAME.get(si.kind, "UnspecifiedKind"),
                suffix=suffix,
                name=name,
                documentation="\n".join(si.documentation),
                signature=_signature(si),
            )
            for rel in si.relationships:
                if rel.is_implementation:
                    sym.relationships.append((rel.symbol, "is_implementation"))
                if rel.is_type_definition:
                    sym.relationships.append((rel.symbol, "is_type_definition"))
            g.add_symbol(sym)

    # 2) Per-document occurrence pass: definition locations + reference edges.
    for doc in index.documents:
        _process_document(g, doc)
    return g


def _process_document(g: SymbolGraph, doc) -> None:
    # Collect definition occurrences of in-repo symbols, with their body spans.
    defs: list[tuple[str, Range, Range | None]] = []  # (moniker, def_range, enclosing)
    refs: list[tuple[str, Range, bool]] = []          # (moniker, ref_range, is_import)

    for occ in doc.occurrences:
        sym = occ.symbol
        if not sym or sym.startswith("local ") or sym not in g.symbols:
            continue
        rng = _occ_range(occ)
        if rng is None:
            continue
        is_def = bool(occ.symbol_roles & _DEFINITION)
        if is_def:
            defs.append((sym, rng, _occ_enclosing(occ)))
        else:
            refs.append((sym, rng, bool(occ.symbol_roles & _IMPORT)))

    # Resolve a body span for each definition: prefer the SCIP enclosing range;
    # else fall back to [def start, next def start) in document order (§5.1).
    defs_sorted = sorted(defs, key=lambda d: (d[1][0], d[1][1]))
    starts = [d[1][:2] for d in defs_sorted]
    spans: list[tuple[str, Range]] = []
    for idx, (moniker, def_rng, encl) in enumerate(defs_sorted):
        # Record the definition location on the node (first occurrence wins).
        node = g.symbols[moniker]
        if node.def_path is None:
            node.def_path = doc.relative_path
            node.def_line = def_rng[0]
            node.enclosing = encl
        if encl is not None:
            body = encl
        else:
            nxt = starts[idx + 1] if idx + 1 < len(starts) else None
            end = nxt if nxt is not None else (def_rng[0] + 1_000_000, 0)
            body = (def_rng[0], def_rng[1], end[0], end[1])
        spans.append((moniker, body))

    # For each reference, find the innermost enclosing definition F → edge F→S.
    for sym, ref_rng, is_import in refs:
        g.ref_count[sym] = g.ref_count.get(sym, 0) + 1
        g.refs.setdefault(sym, []).append((doc.relative_path, ref_rng[0]))
        if is_import:
            continue  # imports are not calls
        point = (ref_rng[0], ref_rng[1])
        enclosing: tuple[str, Range] | None = None
        for moniker, span in spans:
            if _contains(span, point):
                if enclosing is None or _span_size(span) < _span_size(enclosing[1]):
                    enclosing = (moniker, span)
        if enclosing is not None:
            g.add_edge(enclosing[0], sym)


def index_repo(
    project_dir: str | Path,
    output_path: str | Path,
    project_name: str | None = None,
) -> SymbolGraph:
    """End-to-end: run the indexer, parse, build the graph."""
    run_indexer(project_dir, output_path, project_name=project_name)
    return build_graph(parse_index(output_path))
