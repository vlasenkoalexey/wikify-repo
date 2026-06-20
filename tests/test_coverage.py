"""Coverage = set-difference over the symbol table (no graph walk, no scip-python).

Build a tiny graph by hand, mark one symbol as concept-covered via a stub+page,
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
                        def_path="demo/models.py", def_line=10,
                        documentation="```python\nclass Transformer:\n```\nThe core decoder stack.\n\nMore detail."))
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


def _mark_covered(wiki_slug: Path, module: str, anchor: str):
    """Write a concept page citing a catalog anchor so that symbol is covered."""
    (wiki_slug / "concepts").mkdir(parents=True, exist_ok=True)
    cat = coverage.catalog_rel_path(module)
    (wiki_slug / "concepts" / "training.md").write_text(
        f"# Training\n## Mechanism (step-by-step)\n1. step [x](../catalog/{cat}#{anchor})\n",
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
    _mark_covered(wiki_slug, "demo/train.py", "train_step")  # anchor for M_FUNC
    catalogued, paths = coverage.emit_catalogs(g, wiki_slug)

    # Every documentable symbol is now represented (whole-repo guarantee).
    assert catalogued == set(coverage.documentable_symbols(g))
    rep = coverage.compute_report(g, wiki_slug, catalogued=catalogued)
    assert rep.total == 4
    assert rep.covered == 1            # train_step, via the concept page
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
    # Docstring summary (signature fence stripped) is rendered for Transformer.
    assert "The core decoder stack." in body
    assert "More detail." not in body  # only the summary line, not the full body


def test_catalog_cross_links_carry_symbol_anchors():
    """`uses`/`used by` links point at the symbol anchor, not the page top — so
    they navigate to the symbol and same-named targets render distinctly."""
    g = _graph()
    g.add_edge(M_METHOD, M_ATTN)   # Transformer.forward references Attention
    page = coverage.render_catalog(g, "demo/models.py",
                                   [M_CLASS, M_METHOD, M_ATTN], covered={})
    assert "[`Attention`](models.md#Attention)" in page   # anchored
    assert "[`Attention`](models.md)" not in page         # never anchorless


def test_docstring_strips_signature_fence():
    g = _graph()
    t = g.symbols[M_CLASS]
    assert t.doc_summary == "The core decoder stack."
    assert "class Transformer" not in t.docstring  # signature fence removed
