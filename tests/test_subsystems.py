"""The subsystem planner derives a subsystem-shaped agenda from the module tree (no model).

A synthetic repo under an umbrella package ``demo/``: a ``core`` subsystem everyone
enters through ``Engine.run``, a ``util`` subsystem with one hub helper, a flat ``ops``
directory of 31 kernels (bigger than ``max_modules`` but unsplittable), and a test
driver that must neither appear nor count toward fan-in.
"""

from wikify import subsystems
from wikify.graph import Symbol, SymbolGraph

PKG = "scip-python python demo 0.0.0"


def _sym(g, moniker, name, path, suffix="Method", kind="Function"):
    g.add_symbol(Symbol(moniker=moniker, kind=kind, suffix=suffix, name=name, def_path=path))
    return moniker


def _g():
    g = SymbolGraph()
    engine = _sym(g, f"{PKG} `demo.core.engine`/Engine#", "Engine", "demo/core/engine.py", "Type", "Class")
    run = _sym(g, f"{PKG} `demo.core.engine`/Engine#run().", "run", "demo/core/engine.py")
    step = _sym(g, f"{PKG} `demo.core.engine`/Engine#step().", "step", "demo/core/engine.py")
    state = _sym(g, f"{PKG} `demo.core.state`/State#", "State", "demo/core/state.py", "Type", "Class")
    load = _sym(g, f"{PKG} `demo.core.state`/State#load().", "load", "demo/core/state.py")
    fmt = _sym(g, f"{PKG} `demo.util.fmt`/fmt().", "fmt", "demo/util/fmt.py")
    log = _sym(g, f"{PKG} `demo.util.log`/log().", "log", "demo/util/log.py")
    g.add_edge(run, step)      # internal to core
    g.add_edge(run, load)      # internal to core
    g.add_edge(run, fmt)       # core -> util
    ops = []
    for i in range(31):
        m = _sym(g, f"{PKG} `demo.ops.op{i}`/apply().", "apply", f"demo/ops/op{i}.py")
        g.add_edge(m, run)     # every kernel enters core through Engine.run
        ops.append(m)
    test = _sym(g, f"{PKG} `demo.tests.test_engine`/test_run().", "test_run", "demo/tests/test_engine.py")
    g.add_edge(test, run)      # excluded: must not count as external fan-in
    return g, dict(engine=engine, run=run, step=step, state=state, load=load, fmt=fmt, log=log, ops=ops)


def test_tree_split_yields_directory_shaped_subsystems():
    g, _ = _g()
    subs = subsystems.discover_subsystems(g, min_symbols=1)
    by_slug = {s.slug: s for s in subs}
    # umbrella "demo" is stripped: slugs are core/util/ops, not demo-core/...
    assert set(by_slug) == {"core", "util", "ops"}
    assert by_slug["core"].prefix == "demo/core"
    assert sorted(by_slug["core"].modules) == ["demo/core/engine.py", "demo/core/state.py"]
    # flat ops/ exceeds max_modules but cannot split further: stays one unit
    assert len(by_slug["ops"].modules) == 31
    # tests are excluded from modules entirely
    assert not any("tests/" in m for s in subs for m in s.modules)


def test_ranking_entry_points_and_excluded_callers():
    g, s = _g()
    subs = subsystems.discover_subsystems(g, min_symbols=1)
    core = next(x for x in subs if x.slug == "core")
    assert subs[0].slug == "core"                       # highest external fan-in
    assert core.fanin_external == 31                    # 31 kernels; the test driver is NOT counted
    assert core.internal_edges == 2                     # run->step, run->load
    assert core.entry_points[0] == s["run"]             # the API surface: most external callers
    assert core.seeds[0] == s["run"]                    # entry points seed first
    assert s["engine"] in core.hubs or s["engine"] in core.seeds
    ops = next(x for x in subs if x.slug == "ops")
    assert ops.fanin_external == 0 and ops.entry_points == []
    assert subs[-1].slug == "ops"


def test_no_split_when_under_budget_collapses_to_core():
    g, _ = _g()
    subs = subsystems.discover_subsystems(g, max_modules=100, min_symbols=1)
    assert [s.slug for s in subs] == ["core"]
    assert subs[0].prefix == "demo"
    assert len(subs[0].modules) == 35


def test_min_symbols_keeps_at_least_the_top_unit():
    g, _ = _g()
    subs = subsystems.discover_subsystems(g, min_symbols=1000)
    assert len(subs) == 1 and subs[0].slug == "core"


def test_exclude_globs_and_cap():
    g, _ = _g()
    subs = subsystems.discover_subsystems(g, min_symbols=1, exclude_globs=["demo/ops"])
    assert "ops" not in {s.slug for s in subs}
    subs = subsystems.discover_subsystems(g, min_symbols=1, exclude_globs=["demo/o*"])
    assert "ops" not in {s.slug for s in subs}
    assert len(subsystems.discover_subsystems(g, min_symbols=1, max_subsystems=2)) == 2


def test_subsystem_for_prefix_is_the_config_seed_form():
    g, s = _g()
    sub = subsystems.subsystem_for_prefix(g, "demo/core/", slug="engine-core")
    assert sub is not None and sub.slug == "engine-core"
    assert sorted(sub.modules) == ["demo/core/engine.py", "demo/core/state.py"]
    assert sub.seeds[0] == s["run"]
    assert subsystems.subsystem_for_prefix(g, "demo/nowhere") is None


def test_render_agenda_and_scope():
    g, _ = _g()
    subs = subsystems.discover_subsystems(g, min_symbols=1)
    text = subsystems.render_agenda(subs, g, "demo")
    assert "| 1 | core | `demo/core` |" in text
    assert "`run`" in text                                # entry point named
    assert "agenda_exclude" in text                       # curation instructions
    scope = subsystems.render_scope(subs[0], g)
    assert scope.startswith("Subsystem `demo/core`")
    assert "`demo/core/engine.py`" in scope and "Entry points" in scope


def test_flat_directory_over_budget_splits_by_reference_community():
    """A flat dir with more modules than the budget cannot split by tree; two clearly
    separate reference clusters must become two units named after their top module."""
    g = SymbolGraph()
    def cluster(tag, n):
        syms = []
        for i in range(n):
            m = _sym(g, f"{PKG} `demo.flat.{tag}{i}`/{tag}_fn{i}().", f"{tag}_fn{i}", f"demo/flat/{tag}{i}.py")
            syms.append(m)
        for i in range(1, n):                  # everyone in the cluster references member 0
            g.add_edge(syms[i], syms[0])
        return syms
    a = cluster("alpha", 12)
    b = cluster("beta", 12)
    subs = subsystems.discover_subsystems(g, max_modules=20, min_symbols=1)
    slugs = sorted(s.slug for s in subs)
    # umbrella is demo/flat itself, so the unit slug is just the naming module's stem
    assert len(slugs) == 2 and slugs[0].startswith("alpha") and slugs[1].startswith("beta"), slugs
    assert all("::" in s.prefix for s in subs)
    assert sorted(len(s.modules) for s in subs) == [12, 12]
    # agenda_exclude on the directory drops both community units
    assert subsystems.discover_subsystems(g, max_modules=20, min_symbols=1, exclude_globs=["demo/flat"]) == []


def test_test_files_are_not_modules_or_seeds():
    g, s = _g()
    _sym(g, f"{PKG} `demo.core.engine_test`/test_engine().", "test_engine", "demo/core/engine_test.py")
    subs = subsystems.discover_subsystems(g, min_symbols=1)
    core = next(x for x in subs if x.slug == "core")
    assert "demo/core/engine_test.py" not in core.modules
    assert not any(graph_name.endswith("test_engine().") for graph_name in core.seeds)
