"""coverage_collapse / coverage_exclude — trim model-zoo & test bloat while keeping
citations resolvable. Collapse keeps the frontmatter symbol map (rule-1 resolution)
but omits the member body; exclude drops the page entirely (uncited noise only)."""

from __future__ import annotations

from wikify import coverage
from wikify.config import load_config


def test_glob_any_spans_slashes():
    assert coverage._glob_any("easydel/modules/gemma4/modeling_gemma4.py", ["easydel/modules/*"])
    assert coverage._glob_any("a/b/models/x.py", ["*/models/*"])
    assert not coverage._glob_any("easydel/layers/attention.py", ["easydel/modules/*"])


def _tiny_graph():
    from wikify.graph import Symbol, SymbolGraph
    g = SymbolGraph()
    base = "scip-python python demo 0.0.0 `demo.models.big`/"
    cls = base + "Big#"
    meth = base + "Big#run()."
    g.add_symbol(Symbol(moniker=cls, kind="Class", suffix="Type", name="Big",
                        def_path="demo/models/big.py", def_line=0))
    g.add_symbol(Symbol(moniker=meth, kind="Method", suffix="Method", name="run",
                        def_path="demo/models/big.py", def_line=4, documentation="runs it"))
    return g


def test_collapse_keeps_symbol_map_drops_body(tmp_path):
    g = _tiny_graph()
    # full page: has the ## Classes body
    cat, _ = coverage.emit_catalogs(g, tmp_path / "full")
    full = (tmp_path / "full" / "catalog" / "demo" / "models" / "big.md").read_text()
    assert "## Classes" in full and "run" in full

    # collapsed page: frontmatter symbol map present, body omitted
    cat2, _ = coverage.emit_catalogs(g, tmp_path / "coll", collapse=["demo/models/*"])
    coll = (tmp_path / "coll" / "catalog" / "demo" / "models" / "big.md").read_text()
    assert "Collapsed catalog" in coll
    assert "## Classes" not in coll                 # body dropped
    assert "symbols:" in coll and "Big" in coll     # symbol map kept → citations resolve
    assert "runs it" not in coll                     # member docstring/detail omitted
    assert cat2 == cat                              # same monikers still catalogued


def test_exclude_drops_page(tmp_path):
    g = _tiny_graph()
    cat, paths = coverage.emit_catalogs(g, tmp_path / "ex", exclude=["demo/models/*"])
    assert not (tmp_path / "ex" / "catalog" / "demo" / "models" / "big.md").exists()
    assert cat == set()                             # nothing catalogued (dropped)


def test_config_parses_trim_keys(tmp_path):
    cfg = tmp_path / "s.md"
    cfg.write_text('---\nslug: s\ncoverage_collapse:\n  - "x/modules/*"\n'
                   'coverage_exclude:\n  - "**test**"\n---\n## Concepts\n')
    c = load_config(cfg)
    assert c.coverage_collapse == ["x/modules/*"] and c.coverage_exclude == ["**test**"]
