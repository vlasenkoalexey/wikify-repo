"""Symbol citations: the two link forms and the key they share (docs/citations.md).

A citation names one row of the symbol index by its **key**, ``<module>#<QualifiedName>``
(column 1 of ``catalog/symbols.tsv``; ``<module>`` is the catalog path without ``.md``).
Two forms carry it:

- **source** (0.4, whenever the silo has a ``source_url``): the href is the symbol's source
  line at the pinned commit, the title is the key::

      [`GetOrCompile`](https://github.com/o/r/blob/<sha>/src/cache.cc#L791 "src/cache.cc#Cache.GetOrCompile")

  A reader with repository access lands on the code; a reader without it (a private repo)
  still has the key, which is one ``grep -P '^<key>\\t'`` away from path, line, signature
  and doc in the shipped index.
- **catalog** (0.3 and silos with no ``source_url``): ``../catalog/<module>.md#<QualifiedName>``,
  optionally titled ``"path:Lnn"``. The catalog page exists only in the ``anchors`` and
  ``full`` tiers.

Every consumer (lint, fix, verify, OKF, relink, coverage) resolves through ``key_of``, so
both forms resolve identically and a silo can hold a mix while it migrates. ``finalize``
rewrites every citation to the source form from the index it just wrote; ``wikify
source-links`` does the same from an already-shipped index, with no SCIP index at hand.
"""

from __future__ import annotations

import re
from pathlib import Path

# ``[label](target)``; the target may carry a quoted title.
LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_TITLED = re.compile(r'^(\S+)\s+"([^"]*)"\s*$')
_KEY = re.compile(r"^[^\s#]+#[^\s#]+$")


def split(target: str) -> tuple[str, str]:
    """``href "title"`` -> (href, title); title is '' when absent."""
    m = _TITLED.match(target.strip())
    return (m.group(1), m.group(2)) if m else (target.strip(), "")


def key_of(target: str) -> tuple[str, str] | None:
    """The ``(catalog rel .md path, qualified name)`` a citation names, or None when the
    link is not a symbol citation. Same tuple shape as ``coverage.symbol_index`` keys."""
    href, title = split(target)
    if href.startswith(("http://", "https://")):
        if not _KEY.match(title):
            return None
        module, _, anchor = title.partition("#")
        return f"{module}.md", anchor
    path, sep, anchor = href.partition("#")
    i = path.find("catalog/")
    if i < 0 or not sep or not anchor or not path.endswith(".md"):
        return None
    return path[i + len("catalog/"):], anchor


def key_text(key: tuple[str, str]) -> str:
    """``(rel.md, anchor)`` -> the index key ``rel#anchor``."""
    rel, anchor = key
    return f"{rel[:-3] if rel.endswith('.md') else rel}#{anchor}"


def source_target(source_url: str, path: str, line: int, key: str) -> str:
    """The source-form link target for one symbol (``line`` is 1-based)."""
    return f'{source_url.rstrip("/")}/{path}#L{line} "{key}"'


def read_index(catalog_dir: str | Path) -> dict[str, tuple[str, int]]:
    """``key -> (path, 1-based line)`` from a shipped symbol index: ``symbols.tsv``, or
    ``symbols/*.tsv`` when sharded. Header (``#``) lines are skipped."""
    d = Path(catalog_dir)
    files = ([d / "symbols.tsv"] if (d / "symbols.tsv").exists() else []) + sorted(
        (d / "symbols").glob("*.tsv"))
    rows: dict[str, tuple[str, int]] = {}
    for f in files:
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln or ln.startswith("#"):
                continue
            cols = ln.split("\t")
            if len(cols) >= 3 and cols[2].isdigit():
                rows[cols[0]] = (cols[1], int(cols[2]))
    return rows


def to_source_text(text: str, rows: dict[str, tuple[str, int]], source_url: str) -> tuple[str, int]:
    """Rewrite every symbol citation in ``text`` whose key is in ``rows`` to the source form
    at its current path and line. Unknown keys are left as written (the linter reports them);
    a citation already in its current source form is unchanged, so a second pass is a no-op."""
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        label, target = m.group(1), m.group(2)
        key = key_of(target)
        if key is None:
            return m.group(0)
        k = key_text(key)
        if k not in rows:
            return m.group(0)
        path, line = rows[k]
        new = source_target(source_url, path, line, k)
        if new == target.strip():
            return m.group(0)
        n += 1
        return f"[{label}]({new})"

    return LINK.sub(sub, text), n


# Pages that carry citations: everything a silo's agents write. The generated index, log,
# change pages and catalog are tool-owned and never cite.
_SKIP_DIRS = ("catalog", "changes")
_SKIP_FILES = ("index.md", "log.md")


def citing_pages(wiki_slug_dir: str | Path) -> list[Path]:
    root = Path(wiki_slug_dir)
    out = []
    for f in sorted(root.rglob("*.md")):
        rel = f.relative_to(root)
        if rel.parts[0] in _SKIP_DIRS or (len(rel.parts) == 1 and rel.name in _SKIP_FILES):
            continue
        out.append(f)
    return out


def to_source_silo(wiki_slug_dir: str | Path, source_url: str) -> dict[str, int]:
    """Rewrite a silo's citations to the source form from its shipped index. Returns
    ``{"pages": pages changed, "links": links rewritten}``."""
    rows = read_index(Path(wiki_slug_dir) / "catalog")
    pages = links = 0
    for f in citing_pages(wiki_slug_dir):
        text = f.read_text(encoding="utf-8")
        new, n = to_source_text(text, rows, source_url)
        if n:
            f.write_text(new, encoding="utf-8")
            pages += 1
            links += n
    return {"pages": pages, "links": links}
