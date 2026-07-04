"""Stage 7 — multi-repo connection (design.md "Stage 7 — Multi-repo connection").

A single ingest produces a **silo** (``wiki/<subdir>/<slug>/`` with ``overview.md`` +
``concepts/`` + ``catalog/``). With several silos in one wiki, the value is the
*cross-repo* view: which repos implement the same concept (splash attention, remat,
sharding). connect wires that **inline, as a normal wiki** — no side-table, no new
page type — through the concept pages the host wiki already curates:

    wiki/concepts/<key>.md   ←→   each repo's silo concept page(s) for <key>

The wiki-level concept page links **down** to every repo's implementation; each silo
page links **up** to the concept. The links live in delimited ``connect:auto`` blocks so
re-running regenerates them without touching hand-written prose (like coverage catalogs).

This module is pure Python, no model call. It (1) **proposes** candidates — a silo page
matches a vocabulary key by an explicit ``concepts:`` frontmatter tag (authoritative) or a
name/token heuristic (a candidate) — and (2) **applies** the links for the concepts a human
chose at the connection phase. *Which* concepts to connect is a human decision (selective by
design — connecting everything to everything drowns the pages); the link insertion is
mechanical. The vocabulary is the host wiki's ``wiki/concepts/`` filenames, never wikify's.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Tokens too generic to carry correspondence signal (split from concept keys / page ids).
_STOP = {
    "the", "of", "a", "an", "and", "or", "to", "for", "in", "on", "with", "by",
    "py", "src", "lib", "common", "utils", "util", "base", "core", "impl", "internal",
}
_MIN_PREFIX = 4  # a page token matches a key token if one is a prefix of the other, ≥ this long

# Delimited, regenerable blocks — connect owns the text between them; everything else is
# hand-written and never touched.
_DOWN_BEGIN = "<!-- connect:auto:begin -->"
_DOWN_END = "<!-- connect:auto:end -->"
_UP_BEGIN = "<!-- connect:up:begin -->"
_UP_END = "<!-- connect:up:end -->"


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens, split on any non-alnum, stopwords dropped."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOP}


def _token_matches(key_tok: str, page_toks: set[str]) -> bool:
    """A key token is present in a page's tokens: exact, or prefix-share ≥ _MIN_PREFIX
    (so ``remat`` ↔ ``rematerialization``, ``shard`` ↔ ``sharding``)."""
    if key_tok in page_toks:
        return True
    for pt in page_toks:
        n = min(len(key_tok), len(pt))
        if n >= _MIN_PREFIX and key_tok[:n] == pt[:n]:
            return True
    return False


# --------------------------------------------------------------------------- #
# Silo + vocabulary discovery
# --------------------------------------------------------------------------- #
@dataclass
class SiloPage:
    """One silo concept page — the grounding target a vocabulary key resolves to."""

    repo: str            # silo slug (the dir name under wiki/<subdir>/)
    path: Path           # absolute path to the concept .md
    rel_from_wiki: str   # path relative to the wiki root (for links)
    title: str
    tags: list[str]      # explicit `concepts:` frontmatter (authoritative correspondence)
    tokens: set[str] = field(default_factory=set)  # name/id tokens (heuristic candidates)


@dataclass
class Match:
    repo: str
    path: Path
    rel_from_wiki: str
    title: str
    confidence: str      # "tag" (explicit) | "name" (heuristic candidate)


def _frontmatter_span(text: str) -> tuple[dict, int]:
    """Return (frontmatter dict, body_start_index). body_start is the char offset just
    after the closing ``---`` (0 if there is no frontmatter)."""
    if not text.startswith("---"):
        return {}, 0
    end = text.find("\n---", 3)
    if end == -1:
        return {}, 0
    try:
        fm = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        fm = {}
    body_start = text.find("\n", end + 1)
    body_start = len(text) if body_start == -1 else body_start + 1
    return (fm if isinstance(fm, dict) else {}), body_start


def load_vocabulary(wiki_dir: str | Path, vocab_subdir: str = "concepts") -> list[str]:
    """The host wiki's controlled concept vocabulary — stems of ``wiki/<vocab_subdir>/*.md``
    (skipping ``index``/``_``-prefixed housekeeping pages). Empty if the dir is absent."""
    vdir = Path(wiki_dir) / vocab_subdir
    if not vdir.is_dir():
        return []
    return sorted(
        p.stem for p in vdir.glob("*.md")
        if p.stem != "index" and not p.stem.startswith("_")
    )


def discover_silos(wiki_dir: str | Path, vocab_subdir: str = "concepts") -> list[SiloPage]:
    """Every silo concept page in the wiki. A **silo** is any directory holding an
    ``overview.md`` and a ``concepts/`` subdir (layout-agnostic — ``wiki/code/<slug>`` and
    ``wiki/codebases/<slug>`` alike). Excludes the curated top-level vocabulary dir."""
    wiki_dir = Path(wiki_dir)
    vocab_dir = (wiki_dir / vocab_subdir).resolve()
    pages: list[SiloPage] = []
    for overview in wiki_dir.rglob("overview.md"):
        silo = overview.parent
        cdir = silo / "concepts"
        if not cdir.is_dir() or cdir.resolve() == vocab_dir:
            continue
        repo = silo.name
        for page in sorted(cdir.glob("*.md")):
            text = page.read_text(encoding="utf-8", errors="replace")
            fm, _ = _frontmatter_span(text)
            title = str(fm.get("title") or page.stem)
            raw_tags = fm.get("concepts") or []
            if isinstance(raw_tags, str):
                raw_tags = [raw_tags]
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
            id_ = str(fm.get("concept") or "")
            toks = _tokens(page.stem) | _tokens(id_) | _tokens(title)
            pages.append(SiloPage(
                repo=repo, path=page,
                rel_from_wiki=str(page.relative_to(wiki_dir)),
                title=title, tags=tags, tokens=toks,
            ))
    return pages


# --------------------------------------------------------------------------- #
# Correspondence candidates (the proposal)
# --------------------------------------------------------------------------- #
def _page_matches_key(page: SiloPage, key: str) -> str | None:
    """Correspondence confidence of ``page`` to vocabulary ``key`` or None. ``"tag"`` if an
    explicit ``concepts:`` entry equals the key; else ``"name"`` if *all* of the key's
    significant tokens appear in the page's tokens."""
    if key in page.tags:
        return "tag"
    key_toks = _tokens(key)
    if key_toks and all(_token_matches(kt, page.tokens) for kt in key_toks):
        return "name"
    return None


def build_index(silos: list[SiloPage], vocab: list[str]) -> dict[str, list[Match]]:
    """Invert (vocabulary × silo pages) → ``concept key → [Match]`` (tag before name, then
    by repo/path). Only keys with ≥1 candidate implementation are returned. This is the
    *proposal* — nothing is written until a human picks which keys to connect."""
    index: dict[str, list[Match]] = {}
    for key in vocab:
        hits: list[Match] = []
        for page in silos:
            conf = _page_matches_key(page, key)
            if conf:
                hits.append(Match(page.repo, page.path, page.rel_from_wiki, page.title, conf))
        if hits:
            hits.sort(key=lambda m: (m.confidence != "tag", m.repo, m.rel_from_wiki))
            index[key] = hits
    return index


# --------------------------------------------------------------------------- #
# Applying the links (inline, bidirectional, regenerable)
# --------------------------------------------------------------------------- #
def _replace_block(text: str, begin: str, end: str, block: str | None) -> str:
    """Replace the ``begin…end`` delimited block with ``block`` (None → remove it). If the
    markers are absent and ``block`` is given, the caller decides placement — this only
    swaps an existing block; returns text unchanged when absent and block is None."""
    lo = text.find(begin)
    if lo == -1:
        return text
    hi = text.find(end, lo)
    if hi == -1:
        return text
    hi_end = hi + len(end)
    # swallow a trailing newline after the end marker to avoid blank accumulation
    if text[hi_end:hi_end + 1] == "\n":
        hi_end += 1
    replacement = "" if block is None else block + "\n"
    return text[:lo] + replacement + text[hi_end:]


def _relpath(from_wiki_rel: str, to_wiki_rel: str) -> str:
    """A markdown link from the page at ``wiki/<from_wiki_rel>`` to ``wiki/<to_wiki_rel>``."""
    return os.path.relpath(to_wiki_rel, str(Path(from_wiki_rel).parent))


def _down_block(key: str, hits: list[Match], concept_rel: str) -> str:
    """The ``## In this wiki's repos`` block for a concept page: links down to each
    implementation, grouped by repo, most-cited repos first."""
    lines = [_DOWN_BEGIN, "## In this wiki's repos",
             f"Grounded implementations of **{key}** across the ingested repos "
             "(generated by `wikify connect` — do not hand-edit inside this block):", ""]
    by_repo: dict[str, list[Match]] = {}
    for m in hits:
        by_repo.setdefault(m.repo, []).append(m)
    for repo in sorted(by_repo, key=lambda r: (-len(by_repo[r]), r)):
        for m in by_repo[repo]:
            link = _relpath(concept_rel, m.rel_from_wiki)
            lines.append(f"- [{repo}]({link}) — {m.title}")
    lines.append(_DOWN_END)
    return "\n".join(lines)


def _up_block(page_rel: str, keys: list[str], vocab_subdir: str) -> str:
    """The one-line up-link block for a silo page: the cross-repo concept(s) it's part of."""
    links = ", ".join(
        f"[{k}]({_relpath(page_rel, f'{vocab_subdir}/{k}.md')})" for k in sorted(keys)
    )
    return f"{_UP_BEGIN}\n> **Cross-repo concept:** part of {links} across this wiki's repos.\n{_UP_END}"


def _insert_after_frontmatter_h1(text: str, block: str) -> str:
    """Insert ``block`` right after the frontmatter and the first ``# H1`` heading (so an
    up-link sits at the top of the readable body), else after frontmatter, else prepend."""
    _, body_start = _frontmatter_span(text)
    head, body = text[:body_start], text[body_start:]
    lines = body.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            rest = "".join(lines[i + 1:])
            return head + "".join(lines[:i + 1]) + "\n" + block + "\n" + rest.lstrip("\n")
    return head + block + "\n\n" + body.lstrip("\n")


def connected_keys(wiki_dir: str | Path, vocab_subdir: str = "concepts") -> list[str]:
    """Vocabulary keys already connected — their concept page carries a down-block. This is
    the connection state (the wiki pages themselves; no side-file), so a re-ingest can
    ``--refresh`` exactly what a human previously chose to connect."""
    vdir = Path(wiki_dir) / vocab_subdir
    if not vdir.is_dir():
        return []
    out = []
    for p in sorted(vdir.glob("*.md")):
        if _DOWN_BEGIN in p.read_text(encoding="utf-8", errors="replace"):
            out.append(p.stem)
    return out


def apply_connections(
    wiki_dir: str | Path,
    keys: list[str],
    vocab_subdir: str = "concepts",
    exclude: set[str] | None = None,
) -> dict[str, int]:
    """Wire the chosen ``keys`` inline and bidirectionally, idempotently. For each key:
    write the down-block into ``wiki/<vocab>/<key>.md``; then regenerate every silo page's
    up-block to list the connected keys it matches (removing it when it matches none).
    ``exclude`` drops specific ``repo/rel_from_wiki`` matches. Returns per-key link counts.

    Fully regenerable: existing ``connect:auto`` blocks are replaced, hand prose untouched."""
    wiki_dir = Path(wiki_dir)
    exclude = exclude or set()
    vocab = load_vocabulary(wiki_dir, vocab_subdir)
    silos = discover_silos(wiki_dir, vocab_subdir)
    index = build_index(silos, vocab)
    chosen = [k for k in keys if k in index]

    counts: dict[str, int] = {}
    # 1) down-blocks on the concept pages
    for key in chosen:
        hits = [m for m in index[key] if f"{m.repo}/{m.rel_from_wiki}" not in exclude]
        counts[key] = len(hits)
        cpage = wiki_dir / vocab_subdir / f"{key}.md"
        if not cpage.exists():
            continue
        concept_rel = f"{vocab_subdir}/{key}.md"
        block = _down_block(key, hits, concept_rel)
        text = cpage.read_text(encoding="utf-8")
        if _DOWN_BEGIN in text:
            text = _replace_block(text, _DOWN_BEGIN, _DOWN_END, block)
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
        cpage.write_text(text, encoding="utf-8")

    # 2) up-blocks on the silo pages — regenerate every page's block from the full
    #    connected set (so a page belonging to several connected concepts lists them all,
    #    and a page no longer matching any loses its block).
    connected = set(chosen) | set(connected_keys(wiki_dir, vocab_subdir))
    for page in silos:
        keys_here = sorted(
            k for k in connected
            if k in index
            and any(m.path == page.path and f"{m.repo}/{m.rel_from_wiki}" not in exclude
                    for m in index[k])
        )
        text = page.path.read_text(encoding="utf-8")
        if _UP_BEGIN in text:
            text = _replace_block(text, _UP_BEGIN, _UP_END,
                                  _up_block(page.rel_from_wiki, keys_here, vocab_subdir)
                                  if keys_here else None)
        elif keys_here:
            text = _insert_after_frontmatter_h1(
                text, _up_block(page.rel_from_wiki, keys_here, vocab_subdir))
        page.path.write_text(text, encoding="utf-8")

    return counts


# --------------------------------------------------------------------------- #
# The proposal report (stdout — nothing written)
# --------------------------------------------------------------------------- #
def compute_report(wiki_dir: str | Path, vocab_subdir: str = "concepts") -> str:
    """A one-screen proposal for ``wikify connect`` stdout: which concepts *could* be
    connected (candidates, most-implemented first) and which already are. Writes nothing —
    the human then picks keys for ``wikify connect --apply``."""
    wiki_dir = Path(wiki_dir)
    vocab = load_vocabulary(wiki_dir, vocab_subdir)
    silos = discover_silos(wiki_dir, vocab_subdir)
    index = build_index(silos, vocab)
    already = set(connected_keys(wiki_dir, vocab_subdir))
    n_repos = len({p.repo for p in silos})
    lines = [
        f"connect: {n_repos} silo(s), {len(silos)} concept page(s), "
        f"{len(vocab)} vocabulary concept(s); {len(index)} have candidate implementations.",
        "",
        "Pick which to connect:  wikify connect --apply <key1,key2,...>   "
        "(refresh all: --refresh)",
        "",
    ]
    for key in sorted(index, key=lambda k: (-len(index[k]), k)):
        repos = sorted({m.repo for m in index[key]})
        mark = "✓" if key in already else " "
        lines.append(f"  [{mark}] {key:26} {len(index[key]):2} impl  "
                     f"{len(repos)} repos: {', '.join(repos)}")
    if already:
        lines += ["", f"already connected ({len(already)}): {', '.join(sorted(already))} "
                  "— `--refresh` regenerates their links after a new ingest."]
    if not vocab:
        lines.append(f"  (no vocabulary — create wiki/{vocab_subdir}/*.md concept pages)")
    return "\n".join(lines)
