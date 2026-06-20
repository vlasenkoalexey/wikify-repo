"""Stage 6 — assemble the per-repo and top-level catalogs (implementation.md §6).

Writes ``wiki/<slug>/index.md`` (the per-repo catalog, carrying the single
ingested commit SHA in frontmatter, per the design's per-repo pinning rule) and
the top-level ``wiki/index.md``. Pure Python; runs after lint passes.
"""

from __future__ import annotations

from pathlib import Path


def write_repo_index(
    wiki_slug_dir: str | Path,
    slug: str,
    ref: str,
    scip_tool: str,
    concern_status: list[tuple[str, str]],  # (concern_slug, status)
    date: str,
) -> Path:
    wiki_slug_dir = Path(wiki_slug_dir)
    wiki_slug_dir.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"| {c} | [{c}](concerns/{c}.md) | {status} |" for c, status in concern_status
    )
    text = f"""---
slug: {slug}
commit: {ref}
scip_tool: {scip_tool}
updated: {date}
---

# {slug} internals wiki

Generated, grounded wiki. Start from a concern; drill into cited symbols.
The commit pin above is the single source version for every page in this silo.

## Concerns
| Concern | Page | Status |
|---|---|---|
{rows}

## Provenance
`extracted` = from SCIP / source. `inferred` = LLM judgment, treat as such.
Design-intent dynamics are labeled; none are runtime-measured (no L4 pass run).
Callers/callees are reference-scoped (SCIP has no call role), labeled "calls/refs".
"""
    out = wiki_slug_dir / "index.md"
    out.write_text(text, encoding="utf-8")
    return out


def write_top_index(wiki_dir: str | Path, slugs: list[str], date: str) -> Path:
    wiki_dir = Path(wiki_dir)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| {s} | [{s}]({s}/index.md) | standalone |" for s in sorted(slugs))
    text = f"""---
title: wikify — top-level catalog
updated: {date}
---

# Wikify — repository wikis

| Repo | Wiki | Connection |
|---|---|---|
{rows}
"""
    out = wiki_dir / "index.md"
    out.write_text(text, encoding="utf-8")
    return out
