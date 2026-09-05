"""The per-repo index is the silo's navigation surface (§10.11 "Front door"): every concept
row carries the page's one-line ``description:`` and the source *area* its citations point
into; past a size threshold the table is grouped by area. All derived from the pages on
disk — no side file, so pages written before the field existed still render."""

from pathlib import Path

from wikify import assemble


def _page(dir_: Path, slug: str, area_path: str | None, description: str | None) -> None:
    fm = "---\ntitle: t\n" + (f"description: {description}\n" if description else "") + "---\n"
    cite = f"[`sym`](../catalog/{area_path}.md#sym)\n" if area_path else ""
    (dir_ / f"{slug}.md").write_text(fm + f"# {slug}\n\n{cite}", encoding="utf-8")


def _silo(tmp_path: Path) -> Path:
    silo = tmp_path / "wiki" / "code" / "demo"
    (silo / "concepts").mkdir(parents=True)
    return silo


def test_grouped_by_area_with_descriptions(tmp_path):
    silo = _silo(tmp_path)
    c = silo / "concepts"
    for i in range(4):
        _page(c, f"core-{i}", "demo/core/engine.py", f"Core page {i}.")
    _page(c, "util-a", "demo/util/fmt.py", "Formatting helpers.")
    _page(c, "util-b", "demo/util/log.py", None)          # no description → empty cell
    _page(c, "prose", None, "Cross-cutting | pipe.")       # no citations → cross-cutting
    status = [(p.stem, "fresh") for p in sorted(c.glob("*.md"))]
    out = assemble.write_repo_index(silo, "demo", "abc", "scip-python", status, "2026-09-05")
    text = out.read_text()
    assert "### `demo/core`" in text and "### `demo/util`" in text
    assert f"### `{assemble.CROSS_CUTTING}`" in text
    assert "| [core-1](concepts/core-1.md) | Core page 1. | fresh |" in text
    assert "| [util-b](concepts/util-b.md) |  | fresh |" in text
    assert "Cross-cutting \\| pipe." in text               # pipes escaped inside a cell
    # biggest area first
    assert text.index("### `demo/core`") < text.index("### `demo/util`")


def test_flat_table_for_small_silos(tmp_path):
    silo = _silo(tmp_path)
    c = silo / "concepts"
    _page(c, "a", "demo/core/engine.py", "A.")
    _page(c, "b", "demo/util/fmt.py", "B.")
    out = assemble.write_repo_index(silo, "demo", "abc", "scip-python",
                                    [("a", "fresh"), ("b", "fresh")], "2026-09-05")
    text = out.read_text()
    assert "### `" not in text
    assert "| [a](concepts/a.md) | `demo/core` | A. | fresh |" in text


def test_overview_and_doc_concept_descriptions(tmp_path):
    silo = _silo(tmp_path)
    (silo / "overview.md").write_text("---\ntitle: o\ndescription: Demo does X.\n---\n# o\n")
    (silo / "doc-concepts").mkdir()
    (silo / "doc-concepts" / "install.md").write_text("---\ntitle: i\ndescription: How to install.\n---\n")
    (silo / "doc-concepts" / "bare.md").write_text("---\ntitle: b\n---\n")
    out = assemble.write_repo_index(silo, "demo", "abc", "scip-python", [], "2026-09-05")
    text = out.read_text()
    assert "**Start here → [Overview](overview.md)** — Demo does X." in text
    assert "- [install](doc-concepts/install.md) — How to install." in text
    assert "- [bare](doc-concepts/bare.md)\n" in text


def test_page_area_picks_dominant_directory(tmp_path):
    pg = tmp_path / "p.md"
    pg.write_text("[a](../catalog/x/y/a.md#A) [b](../catalog/x/y/b.md#B) [c](../catalog/x/z/c.md#C)")
    assert assemble.page_area(pg) == "x/y"
    pg.write_text("[r](../catalog/root.md#R)")
    assert assemble.page_area(pg) == ""
