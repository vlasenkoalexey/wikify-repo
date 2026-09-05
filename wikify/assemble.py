"""Stage 6 — assemble the per-repo and top-level catalogs (implementation.md §6).

Writes ``wiki/<slug>/index.md`` (the per-repo catalog, carrying the single
ingested commit SHA in frontmatter, per the design's per-repo pinning rule) and
the top-level ``wiki/index.md``. Pure Python; runs after lint passes.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

# A concept page cites catalog anchors: ``../catalog/<module path>.md#Symbol``. The
# directory of the most-cited module is the page's *area* (§10.11 "Front door").
_CATALOG_LINK_RE = re.compile(r"\]\((?:\.\./)+catalog/([^)#]+)\.md(?:#[^)]*)?\)")
GROUP_MIN_CONCEPTS = 6      # group the concept table by area only past this many pages
CROSS_CUTTING = "(cross-cutting)"


def _frontmatter(page: Path) -> dict:
    try:
        text = page.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def page_description(page: Path) -> str:
    """The page's one-line ``description:`` frontmatter (borrowed from openwiki/OKF: the
    index is built from it, so an agent can pick a page without opening it). '' if absent."""
    d = _frontmatter(page).get("description") or ""
    return " ".join(str(d).split())


def page_area(page: Path) -> str:
    """The source directory this page's catalog citations mostly point into, '' if none.
    Derived from the page itself, so it works for every page ever written (planned or not)."""
    try:
        text = page.read_text(encoding="utf-8")
    except OSError:
        return ""
    dirs: Counter = Counter()
    for m in _CATALOG_LINK_RE.finditer(text):
        path = m.group(1)
        dirs[path.rsplit("/", 1)[0] if "/" in path else ""] += 1
    if not dirs:
        return ""
    return max(dirs.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _concepts_table(wiki_slug_dir: Path, concept_status: list[tuple[str, str]]) -> str:
    """The concept table: one row per page with its area + description; grouped into
    per-area sections once a silo has enough pages for a flat list to stop being readable."""
    cdir = wiki_slug_dir / "concepts"
    rows = []
    for c, status in concept_status:
        page = cdir / f"{c}.md"
        rows.append((c, status, page_area(page) or CROSS_CUTTING, page_description(page)))
    areas = {r[2] for r in rows}
    out: list[str] = []
    if len(areas) >= 2 and len(rows) >= GROUP_MIN_CONCEPTS:
        out += ["## Concepts (deep)",
                "Grouped by the source area each page's citations point into.", ""]
        by: dict[str, list] = defaultdict(list)
        for r in rows:
            by[r[2]].append(r)
        for area in sorted(by, key=lambda a: (-len(by[a]), a)):
            out += [f"### `{area}`", "| Concept | Description | Status |", "|---|---|---|"]
            out += [f"| [{c}](concepts/{c}.md) | {_cell(d)} | {st} |" for c, st, _, d in by[area]]
            out.append("")
    else:
        out += ["## Concepts (deep)", "| Concept | Area | Description | Status |", "|---|---|---|---|"]
        out += [f"| [{c}](concepts/{c}.md) | `{a}` | {_cell(d)} | {st} |" for c, st, a, d in rows]
        out.append("")
    return "\n".join(out)


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

    # Light tier (decision 8 mid-band): surface any areas/ community-annotation
    # pages, so a light-tier ingest (e.g. xla) isn't misrepresented as "0 concepts".
    area_pages = sorted((wiki_slug_dir / "areas").glob("*.md"))
    concepts_section = ""
    if concept_status:
        concepts_section = _concepts_table(wiki_slug_dir, concept_status)
    if area_pages:
        rows_a = "\n".join(f"- [{p.stem}](areas/{p.name})" for p in area_pages)
        concepts_section += (
            "\n## Areas (light tier — community annotation)\n"
            "Cluster-level orientation over the library (cheaper than deep concept "
            "pages; diagrams optional).\n" + rows_a + "\n"
        )
    if not concept_status and not area_pages:
        concepts_section = "## Concepts\n_(none synthesized; see `catalog/` for the structural index)_\n"

    # Doc-derived concepts (from the doc-ingest step) — extracted from the project's
    # own docs and grounded to the catalog; kept separate from code concepts.
    doc_concept_pages = sorted((wiki_slug_dir / "doc-concepts").glob("*.md"))
    if doc_concept_pages:
        def _doc_row(pg: Path) -> str:
            d = page_description(pg)
            return f"- [{pg.stem}](doc-concepts/{pg.name})" + (f" — {d}" if d else "")
        rows_d = "\n".join(_doc_row(p) for p in doc_concept_pages)
        concepts_section += (
            "\n## Doc-derived concepts\n"
            "Concepts extracted from the project's own docs (README / `docs/`), "
            "grounded to the symbol catalog. The source docs stay in place.\n"
            + rows_d + "\n"
        )

    # Front door: the synthesized overview page (skills/prompts/overview.md), when
    # it exists, is what a newcomer should read first.
    overview_section = ""
    if (wiki_slug_dir / "overview.md").exists():
        blurb = page_description(wiki_slug_dir / "overview.md") or (
            "the whole system in one page (main concepts + core diagrams + a map of the wiki)")
        overview_section = f"\n**Start here → [Overview](overview.md)** — {blurb}\n"

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


_CONNECT_UP_MARK = "<!-- connect:up:begin -->"


def _connection_status(silo_dir: Path) -> str:
    """Derived from the pages themselves (no side-file): a silo is connected iff
    any of its concept pages carries a Stage-7 up-link block."""
    cdir = silo_dir / "concepts"
    if not cdir.is_dir():
        return "standalone"
    n = sum(
        1 for p in sorted(cdir.glob("*.md"))
        if _CONNECT_UP_MARK in p.read_text(encoding="utf-8", errors="replace")
    )
    return f"connected ({n} concept{'s' if n != 1 else ''})" if n else "standalone"


def write_top_index(wiki_dir: str | Path, slugs: list[str], date: str) -> Path:
    wiki_dir = Path(wiki_dir)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"| {s} | [{s}]({s}/index.md) | {_connection_status(wiki_dir / s)} |"
        for s in sorted(slugs)
    )
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
