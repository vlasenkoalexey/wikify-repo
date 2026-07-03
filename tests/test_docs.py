"""Docs mode (source_type: docs) — pinning tests for the deterministic half:
anchor adapters (markdown + HTML), enumeration, the src: citation gate, and coverage.
"""

from __future__ import annotations

from pathlib import Path

from wikify import docs
from wikify.config import load_config


def test_markdown_anchors_atx_and_setext():
    text = "# Top Title\n\n## Configuration\n\nSet-ext Head\n----\n```\n## not-a-heading\n```\n"
    a = docs._markdown_anchors(text)
    assert "top-title" in a and "configuration" in a and "set-ext-head" in a
    assert "not-a-heading" not in a          # fenced code is skipped


def test_html_anchors_headings_and_ids():
    html = '<h1>Getting Started</h1><div id="install">x</div><h2>API Reference</h2>'
    a = docs._html_anchors(html)
    assert "getting-started" in a and "api-reference" in a and "install" in a


def test_enumerate_and_build_doc_map(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Readme\n## Usage\n")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n## Config\n")
    (tmp_path / "third_party").mkdir()
    (tmp_path / "third_party" / "vendor.md").write_text("# skip me\n")

    found = docs.enumerate_docs(tmp_path, None)
    assert "README.md" in found and "docs/guide.md" in found
    assert "third_party/vendor.md" not in found      # vendored is skipped

    dm = docs.build_doc_map(tmp_path, found)
    assert dm["README.md"].anchors == {"readme", "usage"}
    assert dm["docs/guide.md"].page_slug == "docs-guide"


def _write(page: Path, body: str):
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(body, encoding="utf-8")


def test_lint_docs_passes_and_fails(tmp_path):
    doc_map = {
        "README.md": docs.DocInfo("README.md", {"usage"}),
        "guide.md": docs.DocInfo("guide.md", {"config"}),
    }
    wiki = tmp_path / "wiki"
    _write(wiki / "topics" / "good.md",
           "See [usage](src:README.md#usage) and [cfg](src:guide.md#config).")
    rep = docs.lint_docs(wiki, doc_map)
    assert rep.ok, [str(e) for e in rep.errors]

    _write(wiki / "topics" / "bad.md",
           "Bad doc [x](src:missing.md#y) and bad anchor [z](src:guide.md#nope).")
    rep2 = docs.lint_docs(wiki, doc_map)
    msgs = " ".join(str(e) for e in rep2.errors)
    assert not rep2.ok
    assert "no such doc" in msgs and "no such section" in msgs


def test_docs_coverage_set_difference(tmp_path):
    doc_map = {
        "a.md": docs.DocInfo("a.md", {"h"}),
        "b.md": docs.DocInfo("b.md", set()),
        "c.md": docs.DocInfo("c.md", set()),
    }
    wiki = tmp_path / "wiki"
    _write(wiki / "sources" / "a.md", "summary of a")               # a represented (has page)
    _write(wiki / "topics" / "t.md", "mentions [b](src:b.md)")      # b represented (cited)
    cov = docs.docs_coverage(doc_map, wiki)
    assert cov.total == 3
    assert cov.covered == {"a.md", "b.md"}
    assert cov.uncovered == ["c.md"]                                 # c silently missing → flagged


def test_config_source_type_and_doc_globs(tmp_path):
    cfg = tmp_path / "s.md"
    cfg.write_text('---\nslug: s\nsource_type: docs\ndoc_globs:\n  - "docs/**/*.md"\n---\n## Concepts\n')
    c = load_config(cfg)
    assert c.source_type == "docs" and c.doc_globs == ["docs/**/*.md"]
    cfg.write_text("---\nslug: s\n---\n## Concepts\n")
    assert load_config(cfg).source_type == "code"      # default unchanged
