"""Read source snippets / body hashes for symbols (used by packet + diff).

A symbol's body span comes from its SCIP enclosing range when available, else a
single-line fallback at the definition line. Pure file IO; no model, no SCIP.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .graph import Symbol


def _read_lines(repo_root: Path, rel_path: str) -> list[str] | None:
    p = repo_root / rel_path
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def body_span(sym: Symbol) -> tuple[int, int] | None:
    """0-based [start_line, end_line] inclusive for the symbol's definition body."""
    if sym.def_line is None:
        return None
    if sym.enclosing is not None:
        sl, _sc, el, _ec = sym.enclosing
        return (sl, el)
    return (sym.def_line, sym.def_line)


def read_snippet(repo_root: str | Path, sym: Symbol, max_lines: int = 60) -> str:
    """Return the source text of ``sym``'s definition body (capped)."""
    if sym.def_path is None:
        return ""
    lines = _read_lines(Path(repo_root), sym.def_path)
    if lines is None:
        return ""
    span = body_span(sym)
    if span is None:
        return ""
    start, end = span
    end = min(end, start + max_lines - 1, len(lines) - 1)
    return "\n".join(lines[start : end + 1])


def body_hash(repo_root: str | Path, sym: Symbol) -> str:
    """Stable hash of (signature + body source) for reconcile diffing (§5.5)."""
    snippet = read_snippet(repo_root, sym, max_lines=10_000)
    payload = f"{sym.signature}\n{snippet}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]
