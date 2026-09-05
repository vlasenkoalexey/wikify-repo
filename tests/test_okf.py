"""OKF v0.2 compatibility (wikify.okf): key-scoped front matter edits that preserve every
other line and are idempotent; the generated/verified/sources stamps; shape warnings."""

from pathlib import Path

from wikify import okf
from wikify.graph import Symbol, SymbolGraph

PAGE = """---
title: t
type: concept
concept: c
status: fresh
updated: 2026-07-04
---
# t

## Overview
Calls [`run`](../catalog/m.md#run) and [`run`](../catalog/m.md#run) then [`Tab`](../catalog/n.md#Tab).
"""


def test_set_keys_preserves_other_lines_and_is_idempotent():
    out = okf.set_keys(PAGE, {"generated": "generated: {by: wikify/0.2.0, at: 2026-09-05T00:00:00Z}"})
    assert out.startswith("---\ntitle: t\ntype: concept\nconcept: c\nstatus: fresh\nupdated: 2026-07-04\n"
                          "generated: {by: wikify/0.2.0, at: 2026-09-05T00:00:00Z}\n---\n# t\n")
    assert okf.set_keys(out, {"generated": "generated: {by: wikify/0.2.0, at: 2026-09-05T00:00:00Z}"}) == out
    # block value with indented continuation is removed as a unit
    blk = okf.set_keys(out, {"sources": "sources:\n  - {resource: a/b.py, title: a/b.py}"})
    assert "  - {resource: a/b.py" in blk
    assert okf.set_keys(blk, {"sources": None}) == out
    assert okf.body_sha(blk) == okf.body_sha(PAGE)          # front matter never touches the body


def test_generated_refreshes_only_when_asked():
    a = okf.stamp_generated(PAGE, "wikify/0.2.0", "2026-09-05T00:00:00Z", refresh=True)
    assert "generated: {by: wikify/0.2.0, at: 2026-09-05T00:00:00Z}" in a
    b = okf.stamp_generated(a, "wikify/0.2.0", "2026-09-06T00:00:00Z", refresh=False)
    assert b == a
    c = okf.stamp_generated(a, "wikify/0.2.0", "2026-09-06T00:00:00Z", refresh=True)
    assert "2026-09-06" in c


def test_verified_replaces_tool_entry_and_keeps_human():
    a = okf.stamp_verified(PAGE, "wikify-verify/0.2.0", "2026-09-05T00:00:00Z")
    assert "verified: [{by: wikify-verify/0.2.0, at: 2026-09-05T00:00:00Z}]" in a
    human = okf.set_keys(a, {"verified": "verified: [{by: wikify-verify/0.2.0, at: 2026-09-05T00:00:00Z}, "
                                          "{by: human:alekseyv, at: 2026-09-06T00:00:00Z}]"})
    b = okf.stamp_verified(human, "wikify-verify/0.3.0", "2026-09-07T00:00:00Z")
    assert b.count("wikify-verify/") == 1 and "wikify-verify/0.3.0" in b and "human:alekseyv" in b
    assert "2026-09-06T00:00:00Z" in b                       # datetime round-trips as ISO Z
    c = okf.stamp_verified(b, None, "2026-09-08T00:00:00Z")   # not all claims hold: tool entry out
    assert "wikify-verify" not in c and "human:alekseyv" in c
    assert "verified" not in okf.stamp_verified(a, None, "x")


def test_strip_invalid_status_keeps_okf_values():
    assert "status:" not in okf.strip_invalid_status(PAGE)
    ok = PAGE.replace("status: fresh", "status: deprecated")
    assert okf.strip_invalid_status(ok) == ok


def test_cited_files_counts_occurrences_and_sources_capped(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "m.md").write_text("---\nsymbol_base: 'p '\nsymbols:\n  run: 'run().'\n---\n")
    (tmp_path / "catalog" / "n.md").write_text("---\nsymbol_base: 'p '\nsymbols:\n  Tab: 'Tab#'\n---\n")
    (tmp_path / "concepts").mkdir()
    page = tmp_path / "concepts" / "c.md"
    page.write_text(PAGE)
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker="p run().", kind="Function", suffix="Method", name="run", def_path="src/a.py"))
    g.add_symbol(Symbol(moniker="p Tab#", kind="Class", suffix="Type", name="Tab", def_path="src/b.py"))
    files = okf.cited_files(page, g)
    assert files == [("src/a.py", 2), ("src/b.py", 1)]
    entries = okf.source_entries(files, "https://x/blob/sha")
    assert entries[0] == ("https://x/blob/sha/src/a.py", "src/a.py")
    assert okf.source_entries(files, None) == []
    assert len(okf.source_entries([(f"f{i}", 1) for i in range(30)], "b")) == okf.MAX_SOURCES
    assert okf.render_sources([]) is None
    assert okf.render_sources(entries).startswith("sources:\n  - {resource: https://x/blob/sha/src/a.py, title: src/a.py}")


def test_snapshot_resource():
    assert okf.snapshot_resource("https://x/org/r/blob/abc", "../raw") == "https://x/org/r/tree/abc"
    assert okf.snapshot_resource(None, "../raw/code/r") == "../raw/code/r"
    assert okf.snapshot_resource("", "../raw") is None


def test_warnings_shapes(tmp_path):
    p = tmp_path / "w.md"
    p.write_text("---\ntitle: t\ngenerated: {by: nobody, at: 2026-09-05}\n"
                 "verified: [{by: human:x, at: 2026-09-05T00:00:00Z}]\nstatus: fresh\n---\n")
    w = okf.warnings(p)
    assert any("generated.by" in x for x in w)
    assert any("generated.at" in x for x in w)
    assert any("status" in x for x in w)
    assert not any("verified" in x for x in w)
    p.write_text("---\ntitle: t\ngenerated: {by: wikify/0.2.0, at: 2026-09-05T00:00:00Z}\nstatus: stable\n---\n")
    assert okf.warnings(p) == []
