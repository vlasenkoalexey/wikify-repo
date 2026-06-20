"""Coverage = set-difference over the symbol table (no graph walk, no scip-python).

Build a tiny graph by hand, mark one symbol as concern-covered via a stub+page,
and assert: every documentable symbol is enumerated, catalogs are emitted per
module, and the report classifies covered vs catalog-only correctly.
"""

from pathlib import Path

from wikify import coverage
from wikify.graph import Symbol, SymbolGraph

PKG = "scip-python python demo 0.0.0"
M_CLASS = f"{PKG} `demo.models`/Transformer#"
M_METHOD = f"{PKG} `demo.models`/Transformer#forward()."
M_ATTN = f"{PKG} `demo.models`/Attention#"
M_FUNC = f"{PKG} `demo.train`/train_step()."


def _graph():
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=M_CLASS, kind="Class", suffix="Type", name="Transformer",
                        def_path="demo/models.py", def_line=10))
    g.add_symbol(Symbol(moniker=M_METHOD, kind="Method", suffix="Method", name="forward",
                        def_path="demo/models.py", def_line=20))
    g.add_symbol(Symbol(moniker=M_ATTN, kind="Class", suffix="Type", name="Attention",
                        def_path="demo/models.py", def_line=40))
    g.add_symbol(Symbol(moniker=M_FUNC, kind="Method", suffix="Method", name="train_step",
                        def_path="demo/train.py", def_line=5))
    # an external symbol (no def_path) must be excluded from documentable set
    g.add_symbol(Symbol(moniker=f"{PKG} `torch.nn`/Module#", kind="Class", suffix="Type",
                        name="Module", def_path=None))
    return g


def _mark_covered(wiki_slug: Path, moniker: str, name: str):
    """Write a concern page + stub so `train_step` is concern-covered."""
    (wiki_slug / "symbols").mkdir(parents=True, exist_ok=True)
    (wiki_slug / "symbols" / f"{name}.md").write_text(
        f'---\nmoniker: "{moniker}"\n---\n# {name}\n', encoding="utf-8"
    )
    (wiki_slug / "concerns").mkdir(parents=True, exist_ok=True)
    (wiki_slug / "concerns" / "training.md").write_text(
        f"# Training\n## Mechanism (step-by-step)\n1. step [x](../symbols/{name}.md)\n",
        encoding="utf-8",
    )


def test_documentable_excludes_external():
    g = _graph()
    docs = coverage.documentable_symbols(g)
    assert M_CLASS in docs and M_FUNC in docs
    assert all("torch.nn" not in m for m in docs), "external symbol must be excluded"


def test_classes_enumerated():
    g = _graph()
    classes = coverage.class_symbols(g)
    assert set(s.name for s in classes.values()) == {"Transformer", "Attention"}


def test_report_classifies_covered_vs_catalog(tmp_path):
    g = _graph()
    wiki_slug = tmp_path / "demo"
    _mark_covered(wiki_slug, M_FUNC, "train_step")
    catalogued, paths = coverage.emit_catalogs(g, wiki_slug)

    # Every documentable symbol is now represented (whole-repo guarantee).
    assert catalogued == set(coverage.documentable_symbols(g))
    rep = coverage.compute_report(g, wiki_slug, catalogued=catalogued)
    assert rep.total == 4
    assert rep.covered == 1            # train_step, via the concern page
    assert rep.catalog_only == 3       # the rest
    assert rep.represented == 4
    assert rep.classes_represented == rep.classes_total == 2
    assert rep.uncovered_examples == []


def test_catalog_pages_mirror_source_tree(tmp_path):
    g = _graph()
    wiki_slug = tmp_path / "demo"
    coverage.emit_catalogs(g, wiki_slug)
    models = wiki_slug / "catalog" / "demo" / "models.md"
    train = wiki_slug / "catalog" / "demo" / "train.md"
    assert models.exists() and train.exists()
    body = models.read_text()
    # Both classes appear; forward is nested as a member of Transformer.
    assert "Transformer" in body and "Attention" in body
    assert "forward" in body
