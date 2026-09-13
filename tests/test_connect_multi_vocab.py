"""A host wiki may keep its concept vocabulary in several directories, reached with
``--vocab``. Applying one vocabulary must not delete the up-links another one wrote.

Regression: a vault whose concepts live under ``wiki/concepts/gex/`` and
``wiki/concepts/nnfx/`` lost every ``gex`` up-link the moment ``connect --vocab
concepts/nnfx --apply ...`` ran, because a page matching no key in the vocabulary being
applied had its block removed regardless of which vocabulary the block pointed at.
"""
from __future__ import annotations

from pathlib import Path

from wikify import connect

NL = chr(10)


def _page(path: Path, title: str, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NL.join(["---", f"title: {title}", "---", "", f"# {title}", "", body, ""]),
                    encoding="utf-8")


def _wiki(tmp_path: Path) -> Path:
    """A wiki with two vocabulary dirs and two silo pages, one tagged into each."""
    wiki = tmp_path / "wiki"
    _page(wiki / "concepts" / "gex" / "Call Wall.md", "Call Wall")
    _page(wiki / "concepts" / "nnfx" / "NNFX Automation.md", "NNFX Automation")

    for slug, tag, title in (("gexrepo", "Call Wall", "The corridor"),
                             ("opsrepo", "NNFX Automation", "The daily cycle")):
        silo = wiki / "code" / slug
        _page(silo / "overview.md", f"{slug} overview")
        (silo / "concepts").mkdir(parents=True, exist_ok=True)
        (silo / "concepts" / "page.md").write_text(
            NL.join(["---", f"title: {title}", f'concepts: ["{tag}"]', "---", "", f"# {title}", ""]),
            encoding="utf-8")
    return wiki


def test_applying_one_vocab_keeps_another_vocabs_up_links(tmp_path):
    wiki = _wiki(tmp_path)
    gex_page = wiki / "code" / "gexrepo" / "concepts" / "page.md"
    ops_page = wiki / "code" / "opsrepo" / "concepts" / "page.md"

    connect.apply_connections(wiki, ["Call Wall"], vocab_subdir="concepts/gex")
    assert "Call%20Wall.md" in gex_page.read_text(encoding="utf-8")

    # A second run over a different vocabulary: it must wire its own page and leave the
    # first vocabulary's block alone.
    connect.apply_connections(wiki, ["NNFX Automation"], vocab_subdir="concepts/nnfx")
    assert "NNFX%20Automation.md" in ops_page.read_text(encoding="utf-8")
    assert "Call%20Wall.md" in gex_page.read_text(encoding="utf-8"), (
        "applying the nnfx vocabulary deleted the gex up-link")


def test_page_dropped_from_its_own_vocab_still_loses_its_block(tmp_path):
    """The removal path still works within one vocabulary: untag the page, re-apply, block goes."""
    wiki = _wiki(tmp_path)
    gex_page = wiki / "code" / "gexrepo" / "concepts" / "page.md"

    connect.apply_connections(wiki, ["Call Wall"], vocab_subdir="concepts/gex")
    assert connect._UP_BEGIN in gex_page.read_text(encoding="utf-8")

    text = gex_page.read_text(encoding="utf-8").replace('concepts: ["Call Wall"]', "concepts: []")
    gex_page.write_text(text, encoding="utf-8")
    connect.apply_connections(wiki, ["Call Wall"], vocab_subdir="concepts/gex")
    assert connect._UP_BEGIN not in gex_page.read_text(encoding="utf-8")
