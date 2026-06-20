"""Stage 2 — structural diff & reconcile scoping (implementation.md §5.5).

Hash each symbol's (signature + body) and compare to the recorded state to find
changed monikers; any page citing a changed symbol is stale. Concerns in the
config with no page are to be built. Pure Python, drives idempotent reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import source, state as state_mod
from .config import RepoConfig
from .graph import SymbolGraph


@dataclass
class Plan:
    build: list[str] = field(default_factory=list)      # concerns with no page yet
    rebuild: list[str] = field(default_factory=list)    # stale pages (cited symbol changed)
    leave: list[str] = field(default_factory=list)      # fresh pages
    changed_symbols: int = 0
    removed_symbols: int = 0

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
        lines.append(
            f"  symbols      : {self.changed_symbols} changed, {self.removed_symbols} removed"
        )
        if self.is_noop:
            lines.append("  => no-op (converged)")
        return "\n".join(lines)


def current_hashes(graph: SymbolGraph, repo_root: str | Path) -> dict[str, str]:
    return {m: source.body_hash(repo_root, s) for m, s in graph.symbols.items()}


def compute_plan(
    graph: SymbolGraph,
    repo_root: str | Path,
    state: dict,
    config: RepoConfig,
    hashes: dict[str, str] | None = None,
) -> Plan:
    hashes = hashes if hashes is not None else current_hashes(graph, repo_root)
    old = state.get("symbols", {})
    changed = {m for m, h in hashes.items() if old.get(m) != h}
    removed = {m for m in old if m not in hashes}
    invalidating = changed | removed

    plan = Plan(changed_symbols=len(changed), removed_symbols=len(removed))
    for concern in config.concerns:
        name = concern.slug
        if not state_mod.has_page(state, name):
            plan.build.append(name)
            continue
        cited = set(state_mod.page_cited(state, name))
        if cited & invalidating:
            plan.rebuild.append(name)
        else:
            plan.leave.append(name)
    return plan
