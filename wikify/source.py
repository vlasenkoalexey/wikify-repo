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


_SIG_MAX_LINES = 12


def read_signature(repo_root: str | Path, sym: Symbol) -> str:
    """A one-line declaration read from the source at the definition line.

    For languages whose indexer emits no signature (scip-clang for C++): take the definition
    line and the lines after it up to the first ``{`` or ``;`` (or a balanced ``)`` once the
    parameter list opened), prepend an immediately preceding ``template <...>`` line, drop
    trailing ``{``/``;``, collapse whitespace. Bounded to ``_SIG_MAX_LINES`` lines; returns
    '' when the file or line is missing. Deterministic, no model."""
    if sym.def_path is None or sym.def_line is None:
        return ""
    lines = _read_lines(Path(repo_root), sym.def_path)
    if lines is None or sym.def_line >= len(lines):
        return ""
    start = sym.def_line
    if sym.suffix == "Term":                        # a field / enumerator / constant: its own line
        text = lines[start].split("//", 1)[0].strip()
        return " ".join(text.rstrip(",;{").split())
    if start > 0 and lines[start - 1].strip().startswith("template"):
        start -= 1
    out: list[str] = []
    depth = 0
    opened = False
    done = False
    for ln in lines[start:start + _SIG_MAX_LINES]:
        text = ln.split("//", 1)[0].rstrip()
        for ch in text:
            if ch == "(":
                depth += 1
                opened = True
            elif ch == ")":
                depth -= 1
            elif ch in "{;" and depth <= 0:
                text = text[:text.index(ch)]
                done = True
                break
        out.append(text.strip())
        if done or (opened and depth <= 0 and text.rstrip().endswith(")")):
            break
    sig = " ".join(x for x in out if x)
    sig = " ".join(sig.split())
    return sig.replace("( ", "(").replace(" )", ")").replace(" ,", ",").strip()


def fill_signatures(graph, repo_root: str | Path, suffixes: tuple[str, ...] = (".h", ".hpp", ".hh", ".cc", ".cpp", ".cxx", ".c", ".cu")) -> int:
    """Set ``sig_from_source`` on every symbol that has no indexer signature and is defined in
    a file with one of ``suffixes``. Returns the number filled. Never touches ``signature``."""
    n = 0
    for sym in graph.symbols.values():
        if sym.signature or sym.sig_from_source or not sym.def_path:
            continue
        if sym.suffix not in ("Type", "Method", "Term"):    # namespaces, macros: no row
            continue
        if not sym.def_path.endswith(suffixes):
            continue
        sig = read_signature(repo_root, sym)
        if sig:
            sym.sig_from_source = sig
            n += 1
    return n
