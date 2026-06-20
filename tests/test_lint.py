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


def test_rule1_anchor_not_in_catalog(tmp_path):
    _write_catalog(tmp_path, {"bar": OTHER})  # 'foo' anchor missing
    page = _page(tmp_path, CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 1 for e in errors)


def test_rule1_resolves_to_unknown_moniker(tmp_path):
    _write_catalog(tmp_path, {"foo": "scip-python python demo 0.0.0 `demo`/ghost()."})
    page = _page(tmp_path, CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 1 for e in errors)


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


def test_page_citations_resolves_via_catalog(tmp_path):
    _write_catalog(tmp_path, {"foo": MONIKER})
    page = _page(tmp_path, CLEAN)
    assert lint.page_citations(page) == {MONIKER}
