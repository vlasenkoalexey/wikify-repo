"""Reconcile state — the idempotency ledger (implementation.md §5.5).

Persists ``{ref, symbols, pages}`` to ``.cache/state/<slug>.json`` so ``ingest``
converges to {pinned commit × concept set}: re-running with no source/config
change is a no-op, and a change rebuilds only the delta.

- ``symbols`` maps each moniker → a body-sha; comparing against a fresh index
  yields the set of *changed* monikers.
- ``pages`` maps each concept → the monikers it ``cited`` (so any page whose
  cited set intersects the changed set is stale) plus the ``built_ref`` it was
  built at.

This is pure bookkeeping: nothing here calls a model.
"""

from __future__ import annotations

import json
from pathlib import Path


def state_path(cache_dir: str | Path, slug: str) -> Path:
    """Return the on-disk path of the state file: ``<cache_dir>/state/<slug>.json``."""
    return Path(cache_dir) / "state" / f"{slug}.json"


def _empty_state() -> dict:
    """A fresh, never-built state with all top-level keys present."""
    return {"ref": None, "symbols": {}, "pages": {}}


def load_state(path: str | Path) -> dict:
    """Load the state dict, or a fresh empty state if the file does not exist.

    All three top-level keys (``ref``, ``symbols``, ``pages``) are guaranteed
    present even if the on-disk file is partial.
    """
    path = Path(path)
    if not path.exists():
        return _empty_state()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    state = _empty_state()
    state.update(data)
    # Guard against null-valued keys in a partial/hand-edited file.
    if state.get("symbols") is None:
        state["symbols"] = {}
    if state.get("pages") is None:
        state["pages"] = {}
    return state


def save_state(path: str | Path, state: dict) -> None:
    """Write ``state`` as pretty-printed JSON (indent=2, sorted keys), making parents."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def set_symbols(state: dict, symbols: dict[str, str]) -> None:
    """Replace the moniker → body-sha map."""
    state["symbols"] = dict(symbols)


def set_ref(state: dict, ref: str) -> None:
    """Record the pinned commit the state corresponds to."""
    state["ref"] = ref


def record_page(state: dict, concept: str, cited: list[str], built_ref: str) -> None:
    """Record a built page: its (deduped, sorted) cited monikers and build ref."""
    state.setdefault("pages", {})[concept] = {
        "cited": sorted(set(cited)),
        "built_ref": built_ref,
    }


def page_cited(state: dict, concept: str) -> list[str]:
    """Return the cited monikers for ``concept`` (empty list if no such page)."""
    return list(state.get("pages", {}).get(concept, {}).get("cited", []))


def has_page(state: dict, concept: str) -> bool:
    """Whether a page has been recorded for ``concept``."""
    return concept in state.get("pages", {})
