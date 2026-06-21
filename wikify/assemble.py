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
    concept_status: list[tuple[str, str]],  # (concept_slug, status)
    date: str,
    report=None,  # coverage.CoverageReport | None
) -> Path:
    wiki_slug_dir = Path(wiki_slug_dir)
    wiki_slug_dir.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"| {c} | [{c}](concepts/{c}.md) | {status} |" for c, status in concept_status
    )

    # Light tier (decision 8 mid-band): surface any areas/ community-annotation
    # pages, so a light-tier ingest (e.g. xla) isn't misrepresented as "0 concepts".
    area_pages = sorted((wiki_slug_dir / "areas").glob("*.md"))
    concepts_section = ""
    if concept_status:
        concepts_section = (
            "## Concepts (deep)\n| Concept | Page | Status |\n|---|---|---|\n" + rows + "\n"
        )
    if area_pages:
        rows_a = "\n".join(f"- [{p.stem}](areas/{p.name})" for p in area_pages)
        concepts_section += (
            "\n## Areas (light tier — community annotation)\n"
            "Cluster-level orientation over the library (cheaper than deep concept "
            "pages; diagrams optional).\n" + rows_a + "\n"
        )
    if not concept_status and not area_pages:
        concepts_section = "## Concepts\n_(none synthesized; see `catalog/` for the structural index)_\n"

    # Front door: the synthesized overview page (skills/prompts/overview.md), when
    # it exists, is what a newcomer should read first.
    overview_section = ""
    if (wiki_slug_dir / "overview.md").exists():
        overview_section = (
            "\n**Start here → [Overview](overview.md)** — the whole system in one "
            "page (main concepts + core diagrams + a map of the wiki).\n"
        )

    coverage_section = ""
    if report is not None:
        coverage_section = f"""
## Coverage
Two tiers: **concept pages** explain mechanisms deeply (selective); **module
catalogs** represent the rest so the whole repo is navigable. Coverage is a
set-difference over the SCIP symbol table, not a graph walk — every documentable
symbol is enumerated and represented.

- documentable symbols: **{report.total}** across {report.modules} modules
- deep (concept pages): **{report.covered}** ({report.pct_deep:.1f}%)
- catalog-only: **{report.catalog_only}**
- represented total: **{report.represented}** ({report.pct_represented:.1f}%)
- classes represented: **{report.classes_represented}/{report.classes_total}**

See [`catalog/`](catalog/) for the generated per-module structural index.
"""
    text = f"""---
slug: {slug}
commit: {ref}
scip_tool: {scip_tool}
updated: {date}
---

# {slug} internals wiki

Generated, grounded wiki. Start from a concept (or an area); drill into cited symbols.
The commit pin above is the single source version for every page in this silo.
{overview_section}
{concepts_section}{coverage_section}
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
