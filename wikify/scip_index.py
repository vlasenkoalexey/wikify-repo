"""Stage 1 — run scip-python, parse the ``.scip`` index, build the SymbolGraph.

Deterministic, no model calls. The risky part is ``derive_edges``: SCIP has no
"call" role, so callers/callees are approximated by **reference scoping** — a
reference to in-repo symbol ``S`` occurring inside the body (enclosing range) of
definition ``F`` yields the edge ``F → S``. Symbol-accurate (the frontend bound
the name to the right symbol) but reference-based, not true call resolution.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import ast_fallback, scip_pb2
from .graph import Symbol, SymbolGraph, devirtualize

# scip-python is a Node CLI; pyright OOMs at Node's 4 GB default heap on large
# repos (e.g. pytorch). Give it a generous ceiling — Node only allocates as
# needed, so a high ceiling is safe on small machines too. Override via env.
_NODE_HEAP_MB = os.environ.get("WIKIFY_NODE_HEAP_MB", "16384")
from .monikers import parse_symbol

# SCIP role bits
_DEFINITION = scip_pb2.SymbolRole.Definition
_IMPORT = scip_pb2.SymbolRole.Import

# enum number → name, e.g. 17 → "Function"
_KIND_NAME = {v.number: v.name for v in scip_pb2.SymbolInformation.Kind.DESCRIPTOR.values}

Range = tuple[int, int, int, int]  # (start_line, start_char, end_line, end_char)

# Descriptor suffixes that denote function-local symbols, not citable nodes.
_LOCALISH_SUFFIXES = {"Parameter", "TypeParameter", "Meta"}

# Terminal descriptor suffix → a reasonable Kind name, used when a symbol has a
# definition occurrence but no SymbolInformation (pyright dropped it on a partial
# type-check failure). We recover the node from the occurrence; the kind is the
# best we can infer from the moniker shape alone.
_SUFFIX_KIND = {
    "Type": "Class",
    "Method": "Method",
    "Term": "Term",
    "Namespace": "Namespace",
    "Macro": "Macro",
}


# --------------------------------------------------------------------------- #
# Running the indexer
# --------------------------------------------------------------------------- #
def run_indexer(
    project_dir: str | Path,
    output_path: str | Path,
    project_name: str | None = None,
    project_version: str = "0.0.0",
    target_only: str | None = None,
    heap_mb: str | int | None = None,
) -> Path:
    """Invoke ``scip-python index`` on ``project_dir`` → ``output_path`` (.scip).

    ``target_only`` limits the *emitted* index to that repo-relative path (the
    rest of the repo is still analyzed for type/import resolution, so monikers
    stay globally correct) — the unit of sharding in ``run_indexer_sharded``.
    """
    project_dir = Path(project_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    name = project_name or project_dir.name
    cmd = [
        "scip-python", "index",
        "--project-name", name,
        "--project-version", project_version,
        "--output", str(output_path.resolve()),
    ]
    if target_only:
        cmd += ["--target-only", target_only]
    cmd.append(".")
    # Raise Node's heap ceiling so pyright doesn't OOM on large repos.
    env = dict(os.environ)
    node_opts = env.get("NODE_OPTIONS", "")
    if "max-old-space-size" not in node_opts:
        heap = str(heap_mb) if heap_mb else _NODE_HEAP_MB
        env["NODE_OPTIONS"] = f"{node_opts} --max-old-space-size={heap}".strip()
    proc = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, env=env)
    # scip-python returns nonzero when pyright hits type-check errors (or a
    # RangeError in a *dependency* file) even though it emitted a complete index
    # for the target. Treat a non-empty written index as success; only fail when
    # nothing usable was produced.
    if not _has_documents(output_path):
        raise RuntimeError(
            f"scip-python failed ({proc.returncode}), no index emitted:"
            f"\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return output_path


def _has_documents(scip_path: Path) -> bool:
    """True if ``scip_path`` exists and parses to a non-empty SCIP index."""
    try:
        return scip_path.exists() and len(parse_index(scip_path).documents) > 0
    except Exception:
        return False


def run_indexer_sharded(
    project_dir: str | Path,
    output_path: str | Path,
    targets: list[str],
    project_name: str | None = None,
    project_version: str = "0.0.0",
    max_parallel: int = 8,
    heap_mb: str | int | None = None,
) -> Path:
    """Index ``targets`` concurrently (one scip-python per ``--target-only``), then
    merge the shards into a single ``output_path`` (.scip).

    This is how we index repos too large for one pyright process: each shard has a
    bounded working set (the target subtree + its lazy type deps), so memory stays
    flat while wall-clock divides by ``max_parallel``. SCIP monikers are global, so
    the union is exact; only each document's ``relative_path`` (emitted relative to
    its target) is repaired back to repo-relative at merge time."""
    project_dir = Path(project_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shard_dir = Path(tempfile.mkdtemp(prefix="wikify-shards-"))

    def _one(item: tuple[int, str]) -> tuple[str, Path] | None:
        idx, target = item
        shard_out = shard_dir / f"shard-{idx:03d}.scip"
        try:
            run_indexer(project_dir, shard_out, project_name=project_name,
                        project_version=project_version, target_only=target,
                        heap_mb=heap_mb)
        except RuntimeError:
            return None  # shard emitted nothing usable; skip, don't sink the run
        return (target, shard_out)

    shards: list[tuple[str, Path]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as ex:
        for res in ex.map(_one, list(enumerate(targets))):
            if res is not None:
                shards.append(res)
    if not shards:
        raise RuntimeError("all index shards failed; no documents emitted")

    merged = _merge_shards_index(project_dir, shards)

    # AST fallback: any target .py file pyright never emitted (a single-file shard
    # that crashed, or a file inside a dir shard that pyright excluded/choked on)
    # is recovered deterministically from source, so its symbols stay citable.
    emitted = {d.relative_path for d in merged.documents}
    missing = _missing_target_files(project_dir, targets, emitted)
    if missing:
        fb = ast_fallback.synthesize_index(
            project_dir, missing, project_name or project_dir.name, project_version)
        merged.documents.extend(fb.documents)

    output_path.write_bytes(merged.SerializeToString())
    return output_path


def _merge_shards_index(
    project_dir: str | Path,
    shards: list[tuple[str, Path]],
) -> "scip_pb2.Index":
    """Union shard ``.scip`` files into one Index, repairing target-relative paths.

    ``shards`` is ``[(target, shard_scip_path)]``. Each shard emits its target
    files relative to the target dir (repaired here to repo-relative) plus a few
    *dependency spillover* files already repo-relative; spillover files overlap
    across shards and are emitted **partially** (only referenced symbols), so we
    keep, per path, the document with the most symbols — the owning shard's
    complete copy beats any partial spillover. Feeding one document per path to
    ``build_graph`` also keeps occurrence/ref counts from being double-tallied."""
    project_dir = Path(project_dir)
    best: dict[str, "scip_pb2.Document"] = {}
    metadata = None
    for target, shard_path in shards:
        idx = parse_index(shard_path)
        if metadata is None:
            metadata = idx.metadata
        # Files for a dir target are emitted relative to the dir; for a file
        # target, relative to the file's parent dir. Spillover deps are already
        # repo-relative (the join below won't exist on disk, so we keep them).
        base = target if (project_dir / target).is_dir() else str(Path(target).parent)
        for doc in idx.documents:
            _repair_doc_path(doc, project_dir, base)
            prev = best.get(doc.relative_path)
            if prev is None or len(doc.symbols) > len(prev.symbols):
                best[doc.relative_path] = doc

    merged = scip_pb2.Index()
    if metadata is not None:
        merged.metadata.CopyFrom(metadata)
    for path in sorted(best):
        merged.documents.append(best[path])
    return merged


def _repair_doc_path(doc, project_dir: Path, base: str) -> None:
    r"""Restore a shard document's ``relative_path`` to repo-relative, in place.

    scip-python emits target files relative to the ``--target-only`` dir and
    spillover dependency files relative to *some* ancestor (yielding ``../``
    prefixes whose meaning depends on the shard's target depth). We try the
    target-join first; if that doesn't land on a real file, we fall back to the
    authoritative module path encoded in a symbol's moniker (``\`a.b.c\`/X#`` →
    ``a/b/c.py``). Without this, an unrepaired ``../fx/node.py`` becomes a second,
    broken-keyed copy of ``torch/fx/node.py`` and pollutes discovery."""
    if (project_dir / doc.relative_path).exists() and not doc.relative_path.startswith(".."):
        return
    if base not in ("", "."):
        cand = os.path.normpath(os.path.join(base, doc.relative_path))
        if (project_dir / cand).exists():
            doc.relative_path = cand
            return
    moniker_path = _path_from_moniker(doc, project_dir)
    if moniker_path is not None:
        doc.relative_path = moniker_path


def _path_from_moniker(doc, project_dir: Path) -> str | None:
    r"""Derive a repo-relative file path from a document's symbol monikers.

    Uses the module Namespace descriptor (``\`torch.fx.node\``) → ``torch/fx/node``
    and probes ``.py`` / ``/__init__.py``. Authoritative when the emitted path is
    ambiguous (``../`` spillover)."""
    for si in doc.symbols:
        if si.symbol.startswith("local "):
            continue
        ps = parse_symbol(si.symbol)
        ns = [name for name, suf in ps.descriptors if suf == "Namespace"]
        if not ns:
            continue
        rel = ns[0].replace(".", "/")
        for cand in (f"{rel}.py", f"{rel}/__init__.py"):
            if (project_dir / cand).exists():
                return cand
    return None


def merge_shards(
    project_dir: str | Path,
    shards: list[tuple[str, Path]],
    output_path: str | Path,
) -> Path:
    """Union shard ``.scip`` files into ``output_path`` (path-writing wrapper)."""
    merged = _merge_shards_index(project_dir, shards)
    output_path = Path(output_path)
    output_path.write_bytes(merged.SerializeToString())
    return output_path


def _missing_target_files(
    project_dir: str | Path,
    targets: list[str],
    emitted: set[str],
) -> list[str]:
    """Repo-relative ``.py`` files under ``targets`` that ``emitted`` doesn't cover."""
    project_dir = Path(project_dir)
    expected: set[str] = set()
    for target in targets:
        tp = project_dir / target
        if tp.is_dir():
            for f in tp.rglob("*.py"):
                expected.add(f.relative_to(project_dir).as_posix())
        elif target.endswith(".py"):
            expected.add(target)
    return sorted(expected - emitted)


def run_clang_indexer(
    project_root: str | Path,
    compile_commands_path: str | Path,
    output_path: str | Path,
    scip_clang_bin: str = "scip-clang",
) -> Path:
    """Invoke ``scip-clang`` against a ``compile_commands.json`` → ``output_path`` (.scip).

    C++ Stage-1 extraction (design §Stage 1, C++ path). scip-clang bundles its own
    clang, so it parses the C++ from the compile database; it must be invoked from
    the **project root** and needs a ``clang++`` resolvable for the resource dir.
    The emitted SCIP is the same format scip-python produces — `build_graph` reads
    it unchanged (proven: classes/methods/calls recovered from a C++ index)."""
    project_root = Path(project_root)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        scip_clang_bin,
        f"--compdb-path={Path(compile_commands_path).resolve()}",
        f"--index-output-path={output_path.resolve()}",
    ]
    # scip-clang invokes `clang++` (found via the compile DB) to determine the
    # resource directory. If no clang++ is on PATH (e.g. only g++ present), give
    # it one by symlinking a C++ compiler — keeps the C++ path working anywhere.
    env = dict(os.environ)
    if shutil.which("clang++") is None:
        cxx = shutil.which("c++") or shutil.which("g++") or shutil.which("gcc")
        if cxx:
            shim = Path(tempfile.mkdtemp(prefix="wikify-clangshim-"))
            (shim / "clang++").symlink_to(cxx)
            env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"scip-clang failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
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
def build_graph(*indexes) -> SymbolGraph:
    """Build one SymbolGraph from one OR MORE SCIP indexes.

    Multiple indexes (e.g. a scip-python index + a scip-clang index for a mixed
    Python/C++ repo) are **unioned** into a single graph — SCIP's stable monikers
    keep symbols distinct across languages, and the two-pass node/occurrence build
    is per-document, so it works uniformly regardless of source language."""
    g = SymbolGraph()

    # 1) Nodes: every global SymbolInformation across all documents of all indexes.
    for index in indexes:
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

    # 1.5) Recover orphan definitions: symbols with a Definition-role occurrence
    # but NO SymbolInformation. pyright drops the SymbolInformation when it fails
    # to fully type a symbol (e.g. a RangeError type-checking a huge class like
    # nn.Module or its dependencies), yet still records the definition occurrence.
    # Synthesizing the node here keeps such symbols citable/coverable — making
    # ingestion resilient to partial type-check failures on any repo. Done before
    # the edge pass so cross-document references to these symbols still resolve.
    for index in indexes:
        for doc in index.documents:
            for occ in doc.occurrences:
                sym = occ.symbol
                if (not occ.symbol_roles & _DEFINITION) or not sym or sym.startswith("local "):
                    continue
                if sym in g.symbols:
                    continue
                node = _synth_symbol(sym)
                if node is not None:
                    g.add_symbol(node)

    # 2) Per-document occurrence pass: definition locations + reference edges.
    for index in indexes:
        for doc in index.documents:
            _process_document(g, doc)

    # 3) Devirtualization (CHA): add base→override / class→subclass edges from
    # SCIP is_implementation relationships, so traversal crosses dynamic dispatch.
    devirtualize(g)
    return g


def _synth_symbol(moniker: str) -> Symbol | None:
    """Build a minimal Symbol from a moniker alone (no SymbolInformation).

    Used to recover orphan definitions (§build_graph step 1.5). The node carries
    the authoritative moniker, name and suffix; kind is inferred from the suffix;
    documentation/signature are empty (the source did have them — pyright just
    didn't emit them). Returns None for un-parseable or function-local symbols."""
    ps = parse_symbol(moniker)
    if not ps.descriptors:
        return None
    name, suffix = ps.terminal
    if suffix in _LOCALISH_SUFFIXES:
        return None
    return Symbol(
        moniker=moniker,
        kind=_SUFFIX_KIND.get(suffix, "UnspecifiedKind"),
        suffix=suffix,
        name=name,
        documentation="",
        signature="",
    )


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
