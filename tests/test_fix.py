"""`finalize --fix` auto-repair (wikify.fix).

Mirrors the lint test's hand-built fixture (tiny graph + catalog with the
frontmatter anchor→moniker map + a concept page) and proves each repair:
  - rule 1 (dead anchor)   → rewritten to the packet's correct catalog link
  - rule 3 (out of subgraph) → de-linked to plain code (never invented)
  - rule 2 (uncited step)  → links a citable symbol the step already names
and that an unfixable case (a step naming nothing citable) is left as a residual
error. The contract: --fix only removes a citation or swaps in the packet's own
verbatim link — it cannot manufacture grounding, so a fixed page passes lint.
"""

from pathlib import Path

import yaml

from wikify import fix, lint
from wikify.graph import Symbol, SymbolGraph

FOO = "scip-python python demo 0.0.0 `demo`/foo()."
BAR = "scip-python python demo 0.0.0 `demo`/bar()."  # exists but OUTSIDE subgraph
CITE_MAP = {"foo": "../catalog/demo.md#foo"}          # what the packet would carry


def _graph() -> SymbolGraph:
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=FOO, kind="Function", suffix="Method", name="foo",
                        def_path="demo.py", def_line=1))
    g.add_symbol(Symbol(moniker=BAR, kind="Function", suffix="Method", name="bar",
                        def_path="demo.py", def_line=5))
    return g


def _write_catalog(wiki_slug: Path) -> None:
    cat = wiki_slug / "catalog" / "demo.md"
    cat.parent.mkdir(parents=True, exist_ok=True)
    fm = {"title": "Module: demo.py", "type": "catalog",
          "symbols": {"foo": FOO, "bar": BAR}}
    cat.write_text("---\n" + yaml.safe_dump(fm) + "---\n# demo\n", encoding="utf-8")


def _page(wiki_slug: Path, body: str) -> Path:
    d = wiki_slug / "concepts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "c.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_rule1_dead_anchor_repaired_to_packet_link(tmp_path):
    _write_catalog(tmp_path)
    # right module, WRONG anchor (#wrongfoo); the packet knows the right one.
    page = _page(tmp_path, "# C\n## Entry points\n"
                 "- [`foo`](../catalog/demo.md#wrongfoo) — entry.\n")
    edits = fix.fix_page(page, _graph(), {FOO}, CITE_MAP)
    assert edits == 1
    assert "../catalog/demo.md#foo" in page.read_text()
    assert lint.lint_page(page, _graph(), {FOO}) == []


def test_rule3_out_of_subgraph_delinked(tmp_path):
    _write_catalog(tmp_path)
    # cites bar (resolves, but NOT in subgraph) in a non-cited section.
    page = _page(tmp_path, "# C\n## Overview\n"
                 "Uses [`bar`](../catalog/demo.md#bar) internally.\n")
    edits = fix.fix_page(page, _graph(), {FOO}, CITE_MAP)
    assert edits == 1
    txt = page.read_text()
    assert "](../catalog/demo.md#bar)" not in txt   # link removed
    assert "`bar`" in txt                           # prose kept as code
    assert lint.lint_page(page, _graph(), {FOO}) == []


def test_rule2_uncited_step_links_named_symbol(tmp_path):
    _write_catalog(tmp_path)
    page = _page(tmp_path, "# C\n## Mechanism (step-by-step)\n"
                 "1. The step calls `foo` to do the work.\n")
    assert any(e.rule == 2 for e in lint.lint_page(page, _graph(), {FOO}))  # broken first
    edits = fix.fix_page(page, _graph(), {FOO}, CITE_MAP)
    assert edits == 1
    assert "[`foo`](../catalog/demo.md#foo)" in page.read_text()
    assert lint.lint_page(page, _graph(), {FOO}) == []


def test_rule2_unfixable_when_no_citable_symbol_named(tmp_path):
    _write_catalog(tmp_path)
    page = _page(tmp_path, "# C\n## Mechanism (step-by-step)\n"
                 "1. The step calls `nothing_citable` here.\n")
    fix.fix_page(page, _graph(), {FOO}, CITE_MAP)
    residual = lint.lint_page(page, _graph(), {FOO})
    assert any(e.rule == 2 for e in residual)  # honestly left for a human

def test_empty_subgraph_page_is_left_untouched(tmp_path):
    """A page with no packet/subgraph isn't subgraph-checked by the linter, so --fix
    must NOT strip its (resolving) citations — the regression that damaged orphan
    pages."""
    _write_catalog(tmp_path)
    body = ("# C\n## Mechanism (step-by-step)\n"
            "1. step uses [`bar`](../catalog/demo.md#bar) and [`foo`](../catalog/demo.md#foo).\n")
    page = _page(tmp_path, body)
    edits = fix.fix_page(page, _graph(), subgraph=set(), cite_map={})
    assert edits == 0
    assert page.read_text() == body            # byte-for-byte untouched
    assert lint.lint_page(page, _graph(), subgraph=set()) == []


def test_clean_page_no_edits(tmp_path):
    _write_catalog(tmp_path)
    page = _page(tmp_path, "# C\n## Entry points\n"
                 "- [`foo`](../catalog/demo.md#foo) — entry.\n")
    assert fix.fix_page(page, _graph(), {FOO}, CITE_MAP) == 0
