"""The catalog as an index (docs/catalog-index.md): the sharded TSV symbol/edge index, the
module map, the catalog tiers, and the content/hygiene fixes shipped with them.

Deterministic: hand-built graphs, no scip-python.
"""

from pathlib import Path

from wikify import coverage, lint, relink, scip_index, scip_pb2, source
from wikify.graph import Symbol, SymbolGraph

PKG = "scip-python python demo 0.0.0"
CLS = f"{PKG} `demo.models`/Transformer#"
FWD = f"{PKG} `demo.models`/Transformer#forward()."
ATTN = f"{PKG} `demo.models`/Attention#"
STEP = f"{PKG} `demo.train.loop`/train_step()."
PRIV = f"{PKG} `demo.util`/_A."
PRIV2 = f"{PKG} `demo.util`/_B."


def _graph():
    g = SymbolGraph()
    g.add_symbol(Symbol(moniker=CLS, kind="Class", suffix="Type", name="Transformer",
                        def_path="demo/models.py", def_line=10,
                        documentation="```python\nclass Transformer:\n```\nThe core decoder stack."))
    g.add_symbol(Symbol(moniker=FWD, kind="Method", suffix="Method", name="forward",
                        def_path="demo/models.py", def_line=20,
                        signature="def forward(self, x: int | None):"))
    g.add_symbol(Symbol(moniker=ATTN, kind="Class", suffix="Type", name="Attention",
                        def_path="demo/models.py", def_line=40))
    g.add_symbol(Symbol(moniker=STEP, kind="Method", suffix="Method", name="train_step",
                        def_path="demo/train/loop.py", def_line=5))
    g.add_symbol(Symbol(moniker=PRIV, kind="Term", suffix="Term", name="_A", def_path="demo/util.py", def_line=1))
    g.add_symbol(Symbol(moniker=PRIV2, kind="Term", suffix="Term", name="_B", def_path="demo/util.py", def_line=2))
    g.add_edge(STEP, FWD)        # train_step calls forward
    g.add_edge(FWD, ATTN)        # forward uses Attention
    return g


def _cite_page(wiki: Path):
    (wiki / "concepts").mkdir(parents=True, exist_ok=True)
    (wiki / "concepts" / "training.md").write_text(
        "# T\n## Mechanism (step-by-step)\n"
        '1. step [`train_step`](../catalog/demo/train/loop.md#train_step "demo/train/loop.py:L6")\n',
        encoding="utf-8")


# --------------------------------------------------------------------------- #
# symbol index + edges
# --------------------------------------------------------------------------- #
def _rows(path: Path) -> list[list[str]]:
    return [l.split("\t") for l in path.read_text().splitlines() if not l.startswith("#")]


def test_index_rows_shards_header_and_columns(tmp_path):
    g = _graph()
    wiki = tmp_path / "demo"
    _cite_page(wiki)
    indexed, paths = coverage.emit_symbol_index(g, wiki, hashes={FWD: "abcd"}, slug="demo", ref="deadbeefcafe")
    assert indexed == set(coverage.documentable_symbols(g))
    # one shard per two-level directory prefix (demo/ and demo/train/ here), edges beside it
    assert (wiki / "catalog" / "symbols" / "demo.tsv") in paths
    assert (wiki / "catalog" / "symbols" / "demo-train.tsv") in paths
    assert (wiki / "catalog" / "edges" / "demo.tsv") in paths
    text = (wiki / "catalog" / "symbols" / "demo.tsv").read_text()
    head = [l for l in text.splitlines() if l.startswith("#")]
    assert head[0].startswith("# wikify symbol index: demo @ deadbeefca, shard demo, 5 symbols")
    assert head[1] == "# columns: " + "\t".join(coverage.INDEX_COLUMNS)
    assert "grep -P '^" in head[2] and "catalog/symbols/*.tsv" in head[2]
    assert "catalog/edges/*.tsv" in head[3] and "sort -t$'\\t' -k5 -nr | head" in head[4]
    rows = {r[0]: r for f in (wiki / "catalog" / "symbols").glob("*.tsv") for r in _rows(f)}
    fwd = rows["demo/models#Transformer.forward"]
    assert fwd[1:5] == ["demo/models.py", "21", "method", str(g.importance(FWD))]
    assert fwd[5] == "abcd"                     # hash column from the map given
    assert fwd[6] == "1"                        # one caller (train_step)
    assert rows["demo/train/loop#train_step"][7] == "training"   # cited by the concept page
    assert rows["demo/models#Transformer"][3] == "class"
    assert rows["demo/util#_A"][3] == "value" and rows["demo/util#_A"][5] == ""
    # rows sorted by path then line
    anchors = [r[0] for r in _rows(wiki / "catalog" / "symbols" / "demo.tsv")]
    assert anchors.index("demo/models#Transformer") < anchors.index("demo/models#Transformer.forward")
    edges = _rows(wiki / "catalog" / "edges" / "demo.tsv")
    assert ["demo/models#Transformer.forward", "demo/train/loop#train_step"] in edges
    assert ["demo/models#Attention", "demo/models#Transformer.forward"] in edges


def test_index_full_profile_adds_signature_and_doc(tmp_path):
    g = _graph()
    wiki = tmp_path / "demo"
    coverage.emit_symbol_index(g, wiki, profile="full")
    text = (wiki / "catalog" / "symbols" / "demo.tsv").read_text()
    assert "# columns: " + "\t".join(coverage.INDEX_FULL_COLUMNS) in text
    rows = {r[0]: r for r in _rows(wiki / "catalog" / "symbols" / "demo.tsv")}
    assert rows["demo/models#Transformer.forward"][8] == "def forward(self, x: int | None):"  # pipes are fine in TSV
    assert rows["demo/models#Transformer"][9] == "The core decoder stack."


def test_index_rewrite_drops_stale_shards(tmp_path):
    g = _graph()
    wiki = tmp_path / "demo"
    (wiki / "catalog" / "symbols").mkdir(parents=True)
    (wiki / "catalog" / "symbols" / "gone.tsv").write_text("# stale\n")
    coverage.emit_symbol_index(g, wiki)
    assert not (wiki / "catalog" / "symbols" / "gone.tsv").exists()


def test_index_anchor_matches_citation_grammar():
    ref = coverage.catalog_ref("demo/train/loop.py", STEP)
    assert ref == "../catalog/demo/train/loop.md#train_step"
    assert coverage.index_anchor("demo/train/loop.py", STEP) == "demo/train/loop#train_step"


# --------------------------------------------------------------------------- #
# module map + tiers
# --------------------------------------------------------------------------- #
def test_map_lists_every_module_with_entry_points_and_citing_pages(tmp_path):
    g = _graph()
    wiki = tmp_path / "demo"
    _cite_page(wiki)
    text = coverage.render_map(g, wiki, source_base="https://x/blob/abc", slug="demo", ref="deadbeef")
    assert text.startswith("---\ntitle: 'Module map: demo'\ntype: catalog-map")
    assert "## `demo`" in text
    assert "| [`demo/train/loop.py`](https://x/blob/abc/demo/train/loop.py) | 1 | `train_step` | [training](../concepts/training.md) |" in text
    assert "| [`demo/models.py`](https://x/blob/abc/demo/models.py) | 3 | `forward`, " in text   # most callers first
    paged = coverage.render_map(g, wiki, pages=True)
    assert "[`demo/models.py`](demo/models.md)" in paged


def test_catalog_modes(tmp_path):
    g = _graph()
    idx, paths = coverage.emit_catalogs(g, tmp_path / "i", mode="index")
    assert idx == set(coverage.documentable_symbols(g)) and paths == []
    assert not (tmp_path / "i" / "catalog").exists()
    _, paths = coverage.emit_catalogs(g, tmp_path / "a", mode="anchors")
    assert paths and all("Collapsed catalog" in p.read_text() for p in paths)
    _, paths = coverage.emit_catalogs(g, tmp_path / "f", mode="full")
    assert any("## Classes" in p.read_text() for p in paths)
    import pytest
    with pytest.raises(ValueError):
        coverage.emit_catalogs(g, tmp_path / "x", mode="nope")


# --------------------------------------------------------------------------- #
# hygiene + content fixes
# --------------------------------------------------------------------------- #
def test_symbol_base_never_swallows_a_leading_underscore():
    """Both symbols start with ``_``: commonprefix would absorb it and show ``A.``/``B.``."""
    import yaml
    g = _graph()
    page = coverage.render_catalog(g, "demo/util.py", [PRIV, PRIV2], covered={})
    fm = yaml.safe_load(page.split("---")[1])
    assert fm["symbol_base"].endswith("`demo.util`/")
    assert fm["symbols"] == {"_A": "_A.", "_B": "_B."}


def test_positional_only_marker_and_source_signature():
    s = Symbol(moniker=STEP, kind="Method", suffix="Method", name="jax_op",
               signature="def jax_op(\n  name,\n  fn=None,\n  ,\n  *,\n  mesh=None\n):")
    assert coverage._clean_sig(s) == "def jax_op(name, fn=None, /, *, mesh=None):"
    c = Symbol(moniker="cxx . . $ X#", kind="Class", suffix="Type", name="X", sig_from_source="class X final")
    assert coverage._clean_sig(c) == "class X final"
    assert c.display_signature == "class X final"


def test_signature_prefers_the_implementation_over_the_first_overload():
    si = scip_pb2.SymbolInformation(symbol=STEP)
    si.documentation.append("```python\n@overload\ndef f(\n  x: None = None\n): ...\n```")
    si.documentation.append("```python\ndef f(\n  x=None\n): # -> int\n```")
    assert scip_index._signature(si).startswith("def f(\n  x=None")
    only = scip_pb2.SymbolInformation(symbol=STEP)
    only.documentation.append("```python\n@overload\ndef g(): ...\n```")
    assert scip_index._signature(only).startswith("@overload")      # nothing better: keep it


def test_read_signature_from_cpp_source(tmp_path):
    src = tmp_path / "op.h"
    src.write_text("// comment\ntemplate <int kArity, int kNumOutputs = 1>\n"
                   "absl::StatusOr<Out<kNumOutputs>> DispatchOp(\n"
                   "    Builder<kArity> op_builder,  // trailing comment\n"
                   "    const Inputs<kArity>& inputs) {\n  body();\n}\n"
                   "enum class OpName {\n  kA,\n};\n")
    fn = Symbol(moniker="cxx . . $ DispatchOp(1).", kind="Function", suffix="Method", name="DispatchOp",
                def_path="op.h", def_line=2)
    assert source.read_signature(tmp_path, fn) == (
        "template <int kArity, int kNumOutputs = 1> absl::StatusOr<Out<kNumOutputs>> DispatchOp("
        "Builder<kArity> op_builder, const Inputs<kArity>& inputs)")
    en = Symbol(moniker="cxx . . $ OpName#", kind="Enum", suffix="Type", name="OpName", def_path="op.h", def_line=7)
    assert source.read_signature(tmp_path, en) == "enum class OpName"
    g = SymbolGraph(); g.add_symbol(fn); g.add_symbol(en)
    py = Symbol(moniker=STEP, kind="Method", suffix="Method", name="s", def_path="s.py", def_line=0)
    g.add_symbol(py)
    assert source.fill_signatures(g, tmp_path) == 2
    assert fn.signature == "" and fn.sig_from_source.startswith("template")   # hash input untouched
    assert py.sig_from_source == ""                                          # python untouched


def test_inherited_enum_comments_are_dropped_but_own_comments_kept():
    idx = scip_pb2.Index()
    doc = scip_pb2.Document(relative_path="op.h")
    base = "cxx . . $ OpName#"
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=base, documentation=["Identifies ops."]))
    for k in ("kA", "kB", "kC"):
        doc.symbols.append(scip_pb2.SymbolInformation(symbol=f"{base}{k}.", documentation=["go/keep-sorted start"]))
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=f"{base}kMax.", documentation=["Not a real op."]))
    idx.documents.append(doc)
    g = scip_index.build_graph(idx)
    assert g.symbols[f"{base}kA."].documentation == ""
    assert g.symbols[f"{base}kMax."].documentation == "Not a real op."
    assert g.symbols[base].documentation == "Identifies ops."


def test_build_graph_only_paths_drops_untracked_documents():
    idx = scip_pb2.Index()
    for path, sym in (("pkg/a.py", f"{PKG} `pkg.a`/f()."), ("pkg/.ipynb_checkpoints/a-checkpoint.py", f"{PKG} `pkg.a-checkpoint`/f().")):
        d = scip_pb2.Document(relative_path=path)
        d.symbols.append(scip_pb2.SymbolInformation(symbol=sym))
        idx.documents.append(d)
    g = scip_index.build_graph(idx, only_paths={"pkg/a.py"})
    assert f"{PKG} `pkg.a`/f()." in g.symbols and f"{PKG} `pkg.a-checkpoint`/f()." not in g.symbols


def test_file_level_shard_with_empty_path_is_repaired(tmp_path):
    """A ``--target-only <file>`` shard emits its own document with relative_path "" — the
    old test (``project_dir / ""`` exists) returned early and filed the module under ""."""
    repo = tmp_path / "repo"
    (repo / "torch_tpu").mkdir(parents=True)
    (repo / "torch_tpu" / "_versioned_so_loader.py").write_text("x = 1\n")
    doc = scip_pb2.Document(relative_path="")
    doc.symbols.append(scip_pb2.SymbolInformation(symbol="scip-python python t 0.0.0 `torch_tpu._versioned_so_loader`/install_hook()."))
    scip_index._repair_doc_path(doc, repo, "torch_tpu")
    assert doc.relative_path == "torch_tpu/_versioned_so_loader.py"


def test_relink_keeps_titles_out_of_the_anchor_key():
    lmap = {("old/m.md", "Sym"): ("new/m.md", "Sym")}
    text, n = relink.relink_text('see [`Sym`](../catalog/old/m.md#Sym "old/m.py:L3") and [x](../catalog/old/m.md#Sym)', lmap)
    assert n == 2 and text.count("../catalog/new/m.md#Sym") == 2
    assert lint._is_symbol_link('../catalog/old/m.md#Sym "old/m.py:L3"')


def test_overloads_share_one_row_with_callers_unioned(tmp_path):
    """Two C++ overloads have distinct monikers but one qualified name: one row (the
    higher-importance moniker), callers merged, edges deduplicated."""
    g = SymbolGraph()
    a = "cxx . . $ ns/Dispatch(aaaa)."
    b = "cxx . . $ ns/Dispatch(bbbb)."
    ca = "cxx . . $ ns/CallerA()."
    cb = "cxx . . $ ns/CallerB()."
    for m, name, line in ((a, "Dispatch", 1), (b, "Dispatch", 9), (ca, "CallerA", 20), (cb, "CallerB", 30)):
        g.add_symbol(Symbol(moniker=m, kind="Function", suffix="Method", name=name, def_path="ns/d.h", def_line=line))
    g.add_edge(ca, a); g.add_edge(cb, b); g.add_edge(cb, a)
    g.add_edge(a, ca); g.add_edge(a, cb)               # a has more outbound edges: higher importance
    coverage.emit_symbol_index(g, tmp_path)
    rows = [r for r in _rows(tmp_path / "catalog" / "symbols" / "ns.tsv") if r[0] == "ns/d.h#Dispatch"]
    assert len(rows) == 1 and rows[0][2] == "2" and rows[0][6] == "2"        # winner's line; 2 distinct callers
    edges = _rows(tmp_path / "catalog" / "edges" / "ns.tsv")
    assert sorted(e for e in edges if e[0] == "ns/d.h#Dispatch") == [["ns/d.h#Dispatch", "ns/d.h#CallerA"], ["ns/d.h#Dispatch", "ns/d.h#CallerB"]]


def test_shards_use_two_directory_levels():
    assert coverage._shard_of("torch_tpu/eager/op_dispatcher.h") == "torch_tpu/eager"
    assert coverage._shard_of("torch_tpu/_internal/compile/static.py") == "torch_tpu/_internal"
    assert coverage._shard_of("torch_tpu/loader.py") == "torch_tpu"
    assert coverage._shard_of("setup.py") == "(root)"
    assert coverage._shard_file("torch_tpu/eager") == "torch_tpu-eager"


def test_duplicate_symbol_information_merges_implementation_signature_and_docstring():
    idx = scip_pb2.Index()
    doc = scip_pb2.Document(relative_path="p.py")
    sym = f"{PKG} `p`/jax_op()."
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=sym, documentation=["```python\n@overload\ndef jax_op(\n  fn: None = None\n): ...\n```"]))
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=sym, documentation=["```python\n@overload\ndef jax_op(\n  fn: int\n): ...\n```"]))
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=sym, documentation=["```python\ndef jax_op(\n  fn=None\n):\n```", "Registers a JAX function."]))
    idx.documents.append(doc)
    g = scip_index.build_graph(idx)
    s = g.symbols[sym]
    assert s.signature.startswith("def jax_op(\n  fn=None")
    assert s.doc_summary == "Registers a JAX function."


def test_build_graph_repairs_empty_document_paths_from_monikers(tmp_path):
    (tmp_path / "torch_tpu").mkdir()
    (tmp_path / "torch_tpu" / "_loader.py").write_text("x = 1\n")
    idx = scip_pb2.Index()
    doc = scip_pb2.Document(relative_path="")
    sym = f"{PKG} `torch_tpu._loader`/install()."
    doc.symbols.append(scip_pb2.SymbolInformation(symbol=sym))
    occ = doc.occurrences.add(); occ.symbol = sym; occ.symbol_roles = 1; occ.range.extend([0, 0, 5])
    idx.documents.append(doc)
    g = scip_index.build_graph(idx, repair_root=tmp_path)
    assert g.symbols[sym].def_path == "torch_tpu/_loader.py"


def test_read_signature_member_is_its_own_line(tmp_path):
    (tmp_path / "e.h").write_text("enum class OpName {\n  kAbsOut,  // first\n  kAcosOut,\n};\n")
    t = Symbol(moniker="cxx . . $ OpName#kAbsOut.", kind="EnumMember", suffix="Term", name="kAbsOut", def_path="e.h", def_line=1)
    assert source.read_signature(tmp_path, t) == "kAbsOut"
