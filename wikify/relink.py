"""Relocation on file moves — rewrite citations instead of rebuilding pages (§10.13).

When ``diff.detect_moves`` finds symbols that moved (same body, new file or new moniker),
the pages citing them are still true; only their catalog links point at the old place.
This module rewrites those links mechanically — in the pages, in each page's packet
``.subgraph.txt`` (so lint rule 3 still holds), and in the verify cache (so recorded
holds carry over) — and ``state.apply_moves`` folds the move into state so the next
``prepare`` sees nothing to do. Nothing else in a page is touched; a link that does not
match an old target is left alone, which makes a second pass a no-op. The citation
linter remains the gate: a wrong rewrite would be a dead anchor at ``finalize``.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import verify as verify_mod
from .coverage import catalog_rel_path, qualified_name
from .lint import _LINK, _is_symbol_link

LinkMap = dict[tuple[str, str], tuple[str, str]]   # (old rel .md, old anchor) → (new rel .md, new anchor)


def link_map(moves: dict[str, str], old_paths: dict[str, str], new_paths: dict[str, str]) -> LinkMap:
    out: LinkMap = {}
    for old, new in moves.items():
        op, np_ = old_paths.get(old), new_paths.get(new)
        if not op or not np_:
            continue
        key = (catalog_rel_path(op), qualified_name(old))
        val = (catalog_rel_path(np_), qualified_name(new))
        if key != val:
            out[key] = val
    return out


def _split_target(target: str) -> tuple[str, str, str] | None:
    """``<prefix>catalog/<rel>.md#<anchor>`` → (prefix, rel.md, anchor)."""
    i = target.find("catalog/")
    if i < 0 or "#" not in target:
        return None
    path, _, anchor = target.partition("#")
    return target[:i], path[i + len("catalog/"):], anchor


def relink_text(text: str, lmap: LinkMap) -> tuple[str, int]:
    """Rewrite matching citation targets; everything else byte-identical."""
    if not lmap:
        return text, 0
    n = 0

    def sub(m):
        nonlocal n
        label, target = m.group(1), m.group(2)
        if _is_symbol_link(target):
            parts = _split_target(target)
            if parts and (parts[1], parts[2]) in lmap:
                rel, anchor = lmap[(parts[1], parts[2])]
                n += 1
                return f"[{label}]({parts[0]}catalog/{rel}#{anchor})"
        return m.group(0)

    return _LINK.sub(sub, text), n


def relink_silo(
    wiki_slug_dir: str | Path,
    cache_dir: str | Path,
    slug: str,
    moves: dict[str, str],
    old_paths: dict[str, str],
    new_paths: dict[str, str],
) -> dict[str, int]:
    """Apply a move set to a silo: pages (concepts + doc-concepts), packet subgraphs, and
    the verify cache. Returns counts. Idempotent."""
    wiki_slug_dir, cache_dir = Path(wiki_slug_dir), Path(cache_dir)
    lmap = link_map(moves, old_paths, new_paths)
    counts = {"pages": 0, "links": 0, "subgraphs": 0, "verify_keys": 0}
    for sub in ("concepts", "doc-concepts"):
        for page in sorted((wiki_slug_dir / sub).glob("*.md")):
            text = page.read_text(encoding="utf-8")
            new_text, n = relink_text(text, lmap)
            if n:
                page.write_text(new_text, encoding="utf-8")
                counts["pages"] += 1
                counts["links"] += n
    renames = {o: n for o, n in moves.items() if o != n}
    if renames:
        for sg in sorted((cache_dir / "packets" / slug).glob("*.subgraph.txt")):
            lines = sg.read_text(encoding="utf-8").splitlines()
            new_lines = [renames.get(l, l) for l in lines]
            if new_lines != lines:
                sg.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                counts["subgraphs"] += 1
        for vc in sorted((cache_dir / "verify" / slug).glob("*.json")):
            data = verify_mod.load_cache(vc)
            touched = 0
            for entry in data.get("claims", {}).values():
                ev = entry.get("evidence") or {}
                if any(k in renames for k in ev):
                    entry["evidence"] = {renames.get(k, k): v for k, v in ev.items()}
                    touched += 1
            if touched:
                verify_mod.save_cache(vc, data)
                counts["verify_keys"] += touched
    return counts


def prune_catalogs(catalog_dir: str | Path, keep: list[Path]) -> int:
    """Delete catalog pages for modules that no longer exist (a moved file otherwise leaves
    a stale page that old citations keep resolving against, and lint stays green)."""
    catalog_dir = Path(catalog_dir)
    if not catalog_dir.is_dir():
        return 0
    keep_set = {Path(k).resolve() for k in keep}
    removed = 0
    for page in sorted(catalog_dir.rglob("*.md")):
        if page.name.lower() in ("index.md", "readme.md"):
            continue
        if page.resolve() not in keep_set:
            page.unlink()
            removed += 1
    for d in sorted((d for d in catalog_dir.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    return removed
