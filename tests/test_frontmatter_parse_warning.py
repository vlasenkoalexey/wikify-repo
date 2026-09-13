"""An unparseable front matter must be reported, not silently degraded.

Every front-matter reader in wikify falls back to an empty mapping when YAML raises, which
costs the page its ``description`` in the assembled index, its ``concepts:`` tag for
``connect`` and its ``aliases:`` for retrieval — all without a word on the console. These
guard the warning that makes that visible.
"""
from __future__ import annotations

from wikify import okf

NL = chr(10)

# `description` is a plain scalar containing a colon-space pair, which ends the whole
# document's front matter as far as YAML is concerned.
BAD = NL.join([
    "---",
    "title: t",
    "description: turns a price panel into {symbol: weight} targets",
    'concepts: ["NNFX Automation"]',
    "---",
    "",
    "# t",
    "",
])

GOOD = NL.join([
    "---",
    "title: t",
    'description: "turns a price panel into {symbol: weight} targets"',
    'concepts: ["NNFX Automation"]',
    "---",
    "",
    "# t",
    "",
])


def test_unparseable_frontmatter_degrades_silently_without_the_check():
    assert okf.frontmatter(BAD) == {}
    assert okf.frontmatter_error(BAD)


def test_warnings_reports_the_parse_failure(tmp_path):
    page = tmp_path / "bad.md"
    page.write_text(BAD, encoding="utf-8")
    warns = okf.warnings(page)
    assert any("does not parse as YAML" in w for w in warns), warns


def test_quoted_value_parses_and_warns_nothing(tmp_path):
    assert okf.frontmatter(GOOD)["concepts"] == ["NNFX Automation"]
    assert okf.frontmatter(GOOD)["description"] == "turns a price panel into {symbol: weight} targets"
    assert okf.frontmatter_error(GOOD) is None
    page = tmp_path / "good.md"
    page.write_text(GOOD, encoding="utf-8")
    assert not [w for w in okf.warnings(page) if "does not parse" in w]
