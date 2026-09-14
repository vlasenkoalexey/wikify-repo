"""Discovery derives the concept agenda from topology (no model, no scip-python).

Build a tiny graph where one module is clearly more central, and assert it is
ranked first, auto-seeded, and that excluded (test/experiment) modules are skipped.
"""

from wikify import discover
from wikify.graph import Symbol, SymbolGraph

PKG = "scip-python python demo 0.0.0"


def _g():
    g = SymbolGraph()
    # central module: core.py with a class referenced a lot
    core = f"{PKG} `demo.core`/Engine#"
    core_run = f"{PKG} `demo.core`/Engine#run()."
    util = f"{PKG} `demo.util`/helper()."
    test = f"{PKG} `demo.tests.test_core`/test_engine()."
    for m, name, path, suf in [
        (core, "Engine", "demo/core.py", "Type"),
        (core_run, "run", "demo/core.py", "Method"),
        (util, "helper", "demo/util.py", "Method"),
        (test, "test_engine", "demo/tests/test_core.py", "Method"),
    ]:
        g.add_symbol(Symbol(moniker=m, kind="x", suffix=suf, name=name, def_path=path))
    # make Engine highly referenced (many callers) → high centrality
    for i in range(30):
        caller = f"{PKG} `demo.mod{i}`/use()."
        g.add_symbol(Symbol(moniker=caller, kind="x", suffix="Method", name="use",
                            def_path=f"demo/mod{i}.py"))
        g.add_edge(caller, core)
    return g, core


def test_central_module_ranked_first_and_seeded():
    g, core = _g()
    specs = discover.discover_concepts(g, min_importance=1)
    assert specs, "expected at least one discovered concept"
    top = specs[0]
    assert top.module == "demo/core.py"
    assert core in top.seeds          # auto-seeded from highest-centrality symbol
    assert top.importance >= 30


def test_excludes_tests_and_low_importance():
    g, _ = _g()
    specs = discover.discover_concepts(g, min_importance=1)
    modules = {s.module for s in specs}
    assert "demo/tests/test_core.py" not in modules  # excluded by DEFAULT_EXCLUDES


def test_slugs_are_unique_and_readable():
    g, _ = _g()
    specs = discover.discover_concepts(g, min_importance=1)
    slugs = [s.slug for s in specs]
    assert len(slugs) == len(set(slugs))
    assert "demo-core" in slugs  # demo/core.py → "demo-core" (no umbrella to drop)


def test_exclude_patterns_match_on_path_segments_not_substrings():
    """A directory pattern must not match a word that merely *ends* with it.

    ``"test/"`` is a substring of ``agent/backtest/``, so a plain ``in`` test
    classified an entire backtest tree as test code and dropped it from the
    agenda. Directory patterns therefore anchor to a path-segment boundary;
    file-prefix patterns like ``"/test_"`` keep substring semantics.
    """
    excluded = discover._excluded
    D = discover.DEFAULT_EXCLUDES

    # real source trees whose names merely contain an excluded word
    assert not excluded("agent/backtest/factor_costs.py", D)
    assert not excluded("agent/backtest/engines/options_portfolio.py", D)
    assert not excluded("pkg/latest/snapshot.py", D)
    assert not excluded("pkg/contest/entry.py", D)

    # genuine test / vendored / example trees still excluded
    assert excluded("test/foo.py", D)
    assert excluded("tests/foo.py", D)
    assert excluded("agent/tests/test_x.py", D)
    assert excluded("pkg/examples/demo.py", D)
    assert excluded("pkg/scripts/run.py", D)
    assert excluded("pkg/third_party/x.py", D)
    assert excluded("pkg/vendor/x.py", D)
    assert excluded("pkg/node_modules/x.js", D)

    # file-prefix pattern is not a directory pattern and still matches anywhere
    assert excluded("src/test_helper.py", D)
