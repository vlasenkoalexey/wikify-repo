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


def test_catalog_frontmatter_factors_out_moniker_prefix(tmp_path):
    """The repeated scheme/project/version/namespace prefix is stored once as
    `symbol_base`, and the citation linter reconstructs base+suffix."""
    import yaml
    from wikify import lint
    g = _graph()
    page = coverage.render_catalog(g, "demo/models.py", [M_CLASS, M_METHOD, M_ATTN], covered={})
    fm = yaml.safe_load(page.split("---")[1])
    assert fm["symbol_base"] == "scip-python python demo 0.0.0 `demo.models`/"
    assert fm["symbols"]["Transformer"] == "Transformer#"           # suffix only
    assert "scip-python" not in fm["symbols"]["Transformer"]        # prefix factored out
    # round-trip: a concept page citing this catalog resolves to the full moniker.
    (tmp_path / "catalog/demo").mkdir(parents=True)
    (tmp_path / "catalog/demo/models.md").write_text(page)
    (tmp_path / "concepts").mkdir()
    cpage = tmp_path / "concepts/c.md"
    cpage.write_text("# C\n## Entry points\n- [`Transformer`](../catalog/demo/models.md#Transformer)\n")
    assert lint._resolve_citation(cpage, "../catalog/demo/models.md#Transformer") == M_CLASS


def test_used_by_excludes_tests_and_ranks_by_importance():
    g = SymbolGraph()
    C = f"{PKG} `demo.models`/Target#"
    A = f"{PKG} `demo.a`/AHi#"          # high importance (many outbound edges)
    B = f"{PKG} `demo.b`/BLo#"          # low importance
    T = f"{PKG} `demo.testing.t`/TFix#" # a test fixture → must be filtered
    for m, name, path in [(C, "Target", "demo/models.py"), (A, "AHi", "demo/a.py"),
                          (B, "BLo", "demo/b.py"), (T, "TFix", "demo/testing/t.py")]:
        g.add_symbol(Symbol(moniker=m, kind="Class", suffix="Type", name=name,
                            def_path=path, def_line=1))
    for caller in (A, B, T):
        g.add_edge(caller, C)           # all three "use" Target
    for i in range(5):                  # boost A's importance
        x = f"{PKG} `demo.a`/x{i}."
        g.add_symbol(Symbol(moniker=x, kind="Term", suffix="Term", name=f"x{i}",
                            def_path="demo/a.py", def_line=1))
        g.add_edge(A, x)
    page = coverage.render_catalog(g, "demo/models.py", [C], covered={})
    ub = next(l for l in page.splitlines() if l.startswith("- used by:"))
    assert "TFix" not in ub                        # test fixture excluded
    assert "1 test-only" in ub                     # ...and the omission noted
    assert ub.index("AHi") < ub.index("BLo")       # ranked by importance, not name


def test_docstring_extracted_into_catalog():
    """The author's docstring (parsed from scip `documentation`) reaches the page —
    on the class line, the method line, and a module-level function line."""
    g = SymbolGraph()
    C = f"{PKG} `demo.m`/Widget#"
    meth = f"{PKG} `demo.m`/Widget#render()."
    fn = f"{PKG} `demo.m`/build()."
    g.add_symbol(Symbol(moniker=C, kind="Class", suffix="Type", name="Widget",
                        def_path="demo/m.py", def_line=0,
                        documentation="```python\nclass Widget:\n```\nA drawable widget."))
    g.add_symbol(Symbol(moniker=meth, kind="Method", suffix="Method", name="render",
                        def_path="demo/m.py", def_line=5, signature="def render(self):",
                        documentation="```python\ndef render(self):\n```\nDraw the widget to the screen."))
    g.add_symbol(Symbol(moniker=fn, kind="Method", suffix="Method", name="build",
                        def_path="demo/m.py", def_line=9, signature="def build():",
                        documentation="```python\ndef build():\n```\nConstruct a default widget."))
    page = coverage.render_catalog(g, "demo/m.py", [C, meth, fn], covered={})
    assert "- doc: A drawable widget." in page           # class docstring
    assert "Draw the widget to the screen." in page      # method docstring (in members)
    assert "Construct a default widget." in page         # function docstring
    # and it really came from `documentation`, not invented
    assert g.symbols[meth].doc_summary == "Draw the widget to the screen."


def test_member_detail_promotes_documented_and_folds_plumbing():
    """A: public/documented members get full detail (name(params) — line — doc);
    undocumented dunder/private fold to a present-but-terse list."""
    g = SymbolGraph()
    C = f"{PKG} `demo.m`/C#"
    pub = f"{PKG} `demo.m`/C#append()."      # public method, documented
    dund_doc = f"{PKG} `demo.m`/C#__repr__()."  # dunder WITH doc → promoted
    dund = f"{PKG} `demo.m`/C#__init__()."    # dunder, no doc → folded
    priv = f"{PKG} `demo.m`/C#_modules."      # private, no doc → folded
    g.add_symbol(Symbol(moniker=C, kind="Class", suffix="Type", name="C",
                        def_path="demo/m.py", def_line=0,
                        signature="@final\nclass C(Base):"))   # decorator → stripped
    g.add_symbol(Symbol(moniker=pub, kind="Method", suffix="Method", name="append",
                        def_path="demo/m.py", def_line=9,
                        signature="@deco\ndef append(\n  self,\n  x\n):",
                        documentation="```python\n@deco\ndef append(self, x):\n```\nAdd x to the end."))
    g.add_symbol(Symbol(moniker=dund_doc, kind="Method", suffix="Method", name="__repr__",
                        def_path="demo/m.py", def_line=20, signature="def __repr__(self):",
                        documentation="```python\ndef __repr__(self):\n```\nCustom repr."))
    g.add_symbol(Symbol(moniker=dund, kind="Method", suffix="Method", name="__init__",
                        def_path="demo/m.py", def_line=4))
    g.add_symbol(Symbol(moniker=priv, kind="Term", suffix="Term", name="_modules",
                        def_path="demo/m.py", def_line=2))
    page = coverage.render_catalog(g, "demo/m.py", [C, pub, dund_doc, dund, priv],
                                   covered={}, source_base="https://x/blob/abc")
    # public method: name(params) + source line + doc
    assert "`append(self, x)` — [`L10`](https://x/blob/abc/demo/m.py#L10) — Add x to the end." in page
    assert "`__repr__(self)`" in page and "Custom repr." in page   # documented dunder promoted
    # undocumented plumbing folded, but present and linked
    assert "- protocol/private:" in page
    assert "`__init__`[`L5`]" in page and "`_modules`[`L3`]" in page
    # signature: decorator stripped (was the @deco wart)
    assert "- signature: `class C(Base):`" in page


def test_catalog_drops_boilerplate_paragraph():
    page = coverage.render_catalog(_graph(), "demo/models.py", [M_CLASS], covered={})
    assert "Generated structural catalog" not in page   # implied by type: catalog


def test_catalog_def_lines_link_to_source_when_base_given():
    base = "https://github.com/org/repo/blob/abc123"
    page = coverage.render_catalog(_graph(), "demo/models.py", [M_CLASS, M_FUNC],
                                   covered={}, source_base=base)
    # module header links to the file; class def links to the exact line (1-based).
    assert f"[`demo/models.py`]({base}/demo/models.py)" in page
    assert f"({base}/demo/models.py#L11)" in page        # Transformer def_line 10 → L11


def test_catalog_no_source_links_without_base():
    page = coverage.render_catalog(_graph(), "demo/models.py", [M_CLASS], covered={})
    assert "https://" not in page and "- def: `demo/models.py:11`" in page


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
