"""The citation linter is the hallucination floor — prove it FAILS on bad input.

Deterministic (no scip-python): build a tiny graph by hand, write stub + page
files, and assert each lint rule (§5.3) fires.
"""

from pathlib import Path

from wikify import lint
from wikify.graph import Symbol, SymbolGraph

MONIKER = "scip-python python demo 0.0.0 `demo`/foo()."
OTHER = "scip-python python demo 0.0.0 `demo`/bar()."


def _graph():
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=MONIKER, kind="Function", suffix="Method", name="foo"))
    g.add_symbol(Symbol(moniker=OTHER, kind="Function", suffix="Method", name="bar"))
    return g


def _write_stub(symbols_dir: Path, slug: str, moniker: str) -> None:
    symbols_dir.mkdir(parents=True, exist_ok=True)
    (symbols_dir / f"{slug}.md").write_text(
        f'---\ntitle: "x"\ntype: symbol\nmoniker: "{moniker}"\n---\n# x\n',
        encoding="utf-8",
    )


def _page(concerns_dir: Path, body: str) -> Path:
    concerns_dir.mkdir(parents=True, exist_ok=True)
    p = concerns_dir / "c.md"
    p.write_text(body, encoding="utf-8")
    return p


CLEAN = """\
# C
## Entry points
- [`foo`](../symbols/foo.md) — the entry.
## Mechanism (step-by-step)
1. does a thing [extracted → `foo`](../symbols/foo.md)
"""


def test_clean_page_passes(tmp_path):
    _write_stub(tmp_path / "symbols", "foo", MONIKER)
    page = _page(tmp_path / "concerns", CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert errors == [], errors


def test_rule1_dead_citation(tmp_path):
    # link points to a stub that does not exist
    page = _page(tmp_path / "concerns", CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 1 for e in errors)


def test_rule1_unresolvable_moniker(tmp_path):
    _write_stub(tmp_path / "symbols", "foo", "scip-python python demo 0.0.0 `demo`/ghost().")
    page = _page(tmp_path / "concerns", CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 1 for e in errors)


def test_rule2_uncited_mechanism_item(tmp_path):
    _write_stub(tmp_path / "symbols", "foo", MONIKER)
    body = CLEAN + "2. an uncited assertion with no link\n"
    page = _page(tmp_path / "concerns", body)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert any(e.rule == 2 for e in errors)


def test_rule2_inferred_block_is_exempt(tmp_path):
    _write_stub(tmp_path / "symbols", "foo", MONIKER)
    body = CLEAN + "\n> [!inferred]\n> a guess, no citation needed here\n"
    page = _page(tmp_path / "concerns", body)
    errors = lint.lint_page(page, _graph(), subgraph={MONIKER})
    assert errors == [], errors


def test_rule3_out_of_subgraph(tmp_path):
    # cite `foo` (resolves in graph) but it is NOT in this concern's subgraph
    _write_stub(tmp_path / "symbols", "foo", MONIKER)
    page = _page(tmp_path / "concerns", CLEAN)
    errors = lint.lint_page(page, _graph(), subgraph={OTHER})
    assert any(e.rule == 3 for e in errors)
