"""Deterministic AST fallback indexer for files pyright cannot analyze.

Some source files crash scip-python's type evaluator (pyright hits an unbounded
``RangeError: Maximum call stack size exceeded`` in its constraint solver on very
large / deeply-generic classes — e.g. ``torch/_tensor.py`` defining ``Tensor``,
``torch/overrides.py``). When such a file is the ``--target-only`` target, the
index for it is **never emitted** — the symbol vanishes entirely, even though
thousands of other files reference it.

This module recovers those symbols WITHOUT a type checker: it parses each missing
file with Python's ``ast`` and emits a synthetic SCIP index whose monikers match
scip-python's scheme exactly, so the recovered definitions unify with the existing
references (callers populate by moniker join). It is deterministic and crash-proof
— enumeration, not traversal — and even recovers the authored docstrings.

Limitation: no outbound edges (no type resolution), so these symbols have no
callees of their own; but inbound references from pyright-indexed files connect,
and coverage/catalogs/citations all work. This is the symbol-recovery floor for
the (rare) files a type checker chokes on.
"""

from __future__ import annotations

import ast
from pathlib import Path

from . import scip_pb2

_DEFINITION = scip_pb2.SymbolRole.Definition
_Kind = scip_pb2.SymbolInformation.Kind


def module_path(rel_path: str) -> str:
    """``torch/optim/adam.py`` → ``torch.optim.adam``; ``a/b/__init__.py`` → ``a.b``."""
    p = Path(rel_path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def synthesize_index(
    repo_dir: str | Path,
    rel_paths: list[str],
    project_name: str,
    project_version: str = "0.0.0",
) -> "scip_pb2.Index":
    """Build a SCIP ``Index`` for ``rel_paths`` (repo-relative) via AST parsing.

    Files that don't exist or don't parse are skipped (best-effort recovery)."""
    repo_dir = Path(repo_dir)
    index = scip_pb2.Index()
    index.metadata.tool_info.name = "wikify-ast-fallback"
    prefix = f"scip-python python {project_name} {project_version} "
    for rel in rel_paths:
        f = repo_dir / rel
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, ValueError):
            continue
        doc = _document(rel, tree, prefix)
        if doc.symbols:
            index.documents.append(doc)
    return index


def _document(rel: str, tree: ast.Module, prefix: str) -> "scip_pb2.Document":
    mod = module_path(rel)
    doc = scip_pb2.Document(relative_path=rel, language="Python")
    base = f"{prefix}`{mod}`/"

    def emit(name_path: str, node, kind, sig: str) -> None:
        moniker = base + name_path
        si = scip_pb2.SymbolInformation(symbol=moniker, kind=kind)
        si.documentation.append(f"```python\n{sig}\n```")
        ds = ast.get_docstring(node)
        if ds:
            si.documentation.append(ds)
        doc.symbols.append(si)
        # Definition occurrence at the symbol's name token (0-based line).
        line = node.lineno - 1
        col = _name_col(node)
        occ = scip_pb2.Occurrence(symbol=moniker, symbol_roles=_DEFINITION)
        occ.range.extend([line, col, col + len(_simple_name(node))])
        doc.occurrences.append(occ)

    def walk_class_body(cls: ast.ClassDef, scope: str) -> None:
        for child in cls.body:
            if isinstance(child, ast.ClassDef):
                np = f"{scope}{child.name}#"
                emit(np, child, _Kind.Class, _class_sig(child))
                walk_class_body(child, np)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                emit(f"{scope}{child.name}().", child, _Kind.Method, _func_sig(child))
            else:
                for tgt in _assign_targets(child):
                    _emit_attr(doc, base, f"{scope}{tgt}.", child)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            np = f"{node.name}#"
            emit(np, node, _Kind.Class, _class_sig(node))
            walk_class_body(node, np)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            emit(f"{node.name}().", node, _Kind.Function, _func_sig(node))
        else:
            for tgt in _assign_targets(node):
                _emit_attr(doc, base, f"{tgt}.", node)
    return doc


def _emit_attr(doc, base: str, name_path: str, node) -> None:
    moniker = base + name_path
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=moniker, kind=_Kind.Variable))
    occ = scip_pb2.Occurrence(symbol=moniker, symbol_roles=_DEFINITION)
    occ.range.extend([node.lineno - 1, node.col_offset, node.col_offset])
    doc.occurrences.append(occ)


def _assign_targets(node) -> list[str]:
    """Simple ``name =``/``name: T =`` target names (module/class-level vars)."""
    out: list[str] = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        out.append(node.target.id)
    return out


def _simple_name(node) -> str:
    return getattr(node, "name", "")


def _name_col(node) -> int:
    """Column of the name token after the ``class ``/``def ``/``async def `` keyword."""
    kw = "class " if isinstance(node, ast.ClassDef) else (
        "async def " if isinstance(node, ast.AsyncFunctionDef) else "def ")
    return node.col_offset + len(kw)


def _class_sig(node: ast.ClassDef) -> str:
    bases = [ast.unparse(b) for b in node.bases]
    bases += [f"{kw.arg}={ast.unparse(kw.value)}" for kw in node.keywords if kw.arg]
    inner = f"({', '.join(bases)})" if bases else ""
    return f"class {node.name}{inner}:"


def _func_sig(node) -> str:
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{node.name}({ast.unparse(node.args)}):"
