"""Stage 2 — structural diff & reconcile scoping (implementation.md §5.5).

Hash each symbol's (signature + body) and compare to the recorded state to find
changed monikers; any page citing a changed symbol is stale. Concepts in the
config with no page are to be built. Pure Python, drives idempotent reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import source, state as state_mod
from .config import RepoConfig
from .coverage import qualified_name
from .graph import SymbolGraph


@dataclass
class Plan:
    build: list[str] = field(default_factory=list)      # concepts with no page yet
    rebuild: list[str] = field(default_factory=list)    # stale pages (cited symbol changed)
    leave: list[str] = field(default_factory=list)      # fresh pages
    relink: list[str] = field(default_factory=list)     # fresh pages whose citations moved
    changed_symbols: int = 0
    removed_symbols: int = 0
    moves: dict[str, str] = field(default_factory=dict)      # old moniker → new (same for path moves)
    old_paths: dict[str, str] = field(default_factory=dict)  # old moniker → old def file
    new_paths: dict[str, str] = field(default_factory=dict)  # new moniker → new def file

    @property
    def is_noop(self) -> bool:
        return not self.build and not self.rebuild

    @property
    def todo(self) -> list[str]:
        return self.build + self.rebuild

    def render(self) -> str:
        lines = ["Reconcile plan:"]
        lines.append(f"  will build   : {', '.join(self.build) or '(none)'}")
        lines.append(f"  will rebuild : {', '.join(self.rebuild) or '(none)'}  (stale)")
        lines.append(f"  will leave   : {', '.join(self.leave) or '(none)'}  (fresh)")
        if self.moves:
            lines.append(f"  will relink  : {', '.join(self.relink) or '(none)'}  "
                         f"({len(self.moves)} symbol(s) moved, citations rewritten, no rebuild)")
        lines.append(
            f"  symbols      : {self.changed_symbols} changed, {self.removed_symbols} removed"
            + (f", {len(self.moves)} moved" if self.moves else "")
        )
        if self.is_noop:
            lines.append("  => no-op (converged)")
        return "\n".join(lines)


def current_hashes(graph: SymbolGraph, repo_root: str | Path) -> dict[str, str]:
    return {m: source.body_hash(repo_root, s) for m, s in graph.symbols.items()}


def current_paths(graph: SymbolGraph) -> dict[str, str]:
    return {m: s.def_path for m, s in graph.symbols.items() if s.def_path}


def detect_moves(
    old_hashes: dict[str, str],
    old_paths: dict[str, str],
    new_hashes: dict[str, str],
    new_paths: dict[str, str],
) -> dict[str, str]:
    """Symbols that moved rather than changed (design.md "Moves are relinked, not rebuilt").

    Two cases, both requiring an unchanged body hash:
    - **path move**: the same moniker now lives in a different file (C++ monikers are
      namespace-based, so a moved file keeps them) → ``old → old``;
    - **rename**: a moniker disappeared and one appeared with the same qualified name
      and the same body hash (Python monikers embed the module, so a moved file renames
      every symbol in it) → ``old → new``, accepted only when the match is one-to-one.
    Anything ambiguous is left to the ordinary changed/removed path (a rebuild)."""
    moves: dict[str, str] = {}
    for m, h in old_hashes.items():
        if m in new_hashes and new_hashes[m] == h:
            op, np_ = old_paths.get(m), new_paths.get(m)
            if op and np_ and op != np_:
                moves[m] = m
    removed = [m for m in old_hashes if m not in new_hashes]
    added = [m for m in new_hashes if m not in old_hashes]
    if removed and added:
        def key(m: str, h: str) -> tuple[str, str]:
            return (qualified_name(m), h)
        by_key_old: dict[tuple[str, str], list[str]] = {}
        for m in removed:
            by_key_old.setdefault(key(m, old_hashes[m]), []).append(m)
        by_key_new: dict[tuple[str, str], list[str]] = {}
        for m in added:
            by_key_new.setdefault(key(m, new_hashes[m]), []).append(m)
        for k, olds in by_key_old.items():
            news = by_key_new.get(k, [])
            if len(olds) == 1 and len(news) == 1:
                moves[olds[0]] = news[0]
    return moves


def compute_plan(
    graph: SymbolGraph,
    repo_root: str | Path,
    state: dict,
    config: RepoConfig,
    hashes: dict[str, str] | None = None,
) -> Plan:
    hashes = hashes if hashes is not None else current_hashes(graph, repo_root)
    old = state.get("symbols", {})
    old_paths = state.get("paths", {}) or {}
    new_paths = current_paths(graph)
    moves = detect_moves(old, old_paths, hashes, new_paths) if old_paths else {}
    changed = {m for m, h in hashes.items() if old.get(m) != h and m not in moves.values()}
    removed = {m for m in old if m not in hashes and m not in moves}
    invalidating = changed | removed

    plan = Plan(changed_symbols=len(changed), removed_symbols=len(removed), moves=moves,
                old_paths={m: old_paths[m] for m in moves if m in old_paths},
                new_paths={n: new_paths[n] for n in moves.values() if n in new_paths})
    for concept in config.concepts:
        name = concept.slug
        if not state_mod.has_page(state, name):
            plan.build.append(name)
            continue
        cited = set(state_mod.page_cited(state, name))
        if cited & invalidating:
            plan.rebuild.append(name)
        elif cited & set(moves):
            plan.relink.append(name)
        else:
            plan.leave.append(name)
    return plan
