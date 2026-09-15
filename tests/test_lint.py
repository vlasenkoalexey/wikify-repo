"""The citation linter is the hallucination floor — prove it FAILS on bad input.

Deterministic (no scip-python): build a tiny graph + a catalog page (with the
frontmatter anchor→moniker map) by hand, then assert each lint rule (§5.3) fires.
Citations are catalog anchors (`../catalog/<module>.md#Anchor`), not stubs.
"""

from pathlib import Path

import yaml

from wikify import coverage, lint
from wikify.graph import Symbol, SymbolGraph

MONIKER = "scip-python python demo 0.0.0 `demo`/foo()."
OTHER = "scip-python python demo 0.0.0 `demo`/bar()."


def _graph():
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=MONIKER, kind="Function", suffix="Method", name="foo",
                        def_path="demo.py", def_line=1))
    g.add_symbol(Symbol(moniker=OTHER, kind="Function", suffix="Method", name="bar",
                        def_path="demo.py", def_line=5))
    return g


def _write_catalog(wiki_slug: Path, anchors: dict[str, str]) -> None:
    """Write a catalog page whose frontmatter `symbols` map resolves anchors."""
    cat = wiki_slug / "catalog" / "demo.md"
    cat.parent.mkdir(parents=True, exist_ok=True)
    fm = {"title": "Module: demo.py", "type": "catalog", "symbols": anchors}
    cat.write_text("---\n" + yaml.safe_dump(fm) + "---\n# demo\n", encoding="utf-8")


def _page(wiki_slug: Path, body: str) -> Path:
    d = wiki_slug / "concepts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "c.md"
    p.write_text(body, encoding="utf-8")
    return p


# foo is anchored "foo" in catalog/demo.md
CLEAN = """\
# C
## Entry points
- [`foo`](../catalog/demo.md#foo) — the entry.
## Mechanism (step-by-step)
1. does a thing [extracted → `foo`](../catalog/demo.md#foo)
"""


def test_clean_page_passes(tmp_path):
    _write_catalog(tmp_path, {"foo": MONIKER})
    page = _page(tmp_path, CLEAN)
    assert lint.lint_page(page, _graph(), subgraph={MONIKER}) == []


def test_rule1_anchor_not_in_index(tmp_path):
    """Resolution is through the graph's symbol index: an anchor naming no symbol in the
    module is dead, whatever a catalog page on disk says (there need not be one)."""
    page = _page(tmp_path, CLEAN.replace("#foo", "#ghost"))
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 1 and "ghost" in e.message for e in errors)


def test_rule1_wrong_module_path_is_dead(tmp_path):
    """The module comes from the link path: a real symbol cited under another module's
    catalog path does not resolve."""
    page = _page(tmp_path, CLEAN.replace("../catalog/demo.md#foo", "../catalog/other.md#foo"))
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 1 for e in errors)


def test_citations_resolve_without_any_catalog_page(tmp_path):
    """Catalog tier ``index``: no ``catalog/*.md`` exists; lint still resolves."""
    page = _page(tmp_path, CLEAN)
    assert not (tmp_path / "catalog").exists()
    assert lint.lint_page(page, _graph(), subgraph={MONIKER}) == []
    assert lint.page_citations(page, _graph()) == {MONIKER}


def test_link_title_is_tolerated_and_stripped(tmp_path):
    """A citation may carry the source location as its link title (packets add it);
    it is display metadata and never part of the anchor."""
    titled = CLEAN.replace("(../catalog/demo.md#foo)", '(../catalog/demo.md#foo "demo.py:L2")')
    page = _page(tmp_path, titled)
    assert lint.lint_page(page, _graph(), subgraph={MONIKER}) == []
    assert lint.page_citations(page, _graph()) == {MONIKER}
    assert lint.strip_title('../catalog/demo.md#foo "demo.py:L2"') == "../catalog/demo.md#foo"


def test_rule2_uncited_mechanism_item(tmp_path):
    _write_catalog(tmp_path, {"foo": MONIKER})
    page = _page(tmp_path, CLEAN + "2. an uncited assertion with no link\n")
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 2 for e in errors)


def test_rule2_inferred_block_is_exempt(tmp_path):
    _write_catalog(tmp_path, {"foo": MONIKER})
    page = _page(tmp_path, CLEAN + "\n> [!inferred]\n> a guess, no citation needed here\n")
    assert lint.lint_page(page, _graph(), subgraph={MONIKER}) == []


def test_rule3_out_of_subgraph(tmp_path):
    _write_catalog(tmp_path, {"foo": MONIKER})
    page = _page(tmp_path, CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={OTHER})  # foo not in subgraph
    assert any(e.rule == 3 for e in errors)


def test_page_citations_falls_back_to_catalog_front_matter_without_graph(tmp_path):
    _write_catalog(tmp_path, {"foo": MONIKER})
    page = _page(tmp_path, CLEAN)
    assert lint.page_citations(page) == {MONIKER}
