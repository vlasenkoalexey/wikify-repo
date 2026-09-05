"""Moves are relinked, not rebuilt (wikify.diff.detect_moves + wikify.relink).

A symbol whose body did not change but whose file (C++: same moniker) or moniker
(Python: module embedded) did is a move: the pages citing it stay true, only their
catalog links move. Ambiguous matches fall back to the ordinary rebuild path."""

import json
from pathlib import Path

from wikify import diff, relink, state as state_mod, verify
from wikify.config import Concept, RepoConfig
from wikify.graph import Symbol, SymbolGraph

PY = "scip-python python demo 0.0.0"


def _sym(g, moniker, name, path):
    g.add_symbol(Symbol(moniker=moniker, kind="Function", suffix="Method", name=name, def_path=path))
    return moniker


def test_detect_path_move_and_rename_only_when_unambiguous():
    old_h = {"cxx . . $ ns/Run().": "h1", f"{PY} `demo.old`/f().": "h2",
             f"{PY} `demo.a`/dup().": "h3", f"{PY} `demo.b`/dup().": "h3", f"{PY} `demo.gone`/x().": "h9"}
    old_p = {"cxx . . $ ns/Run().": "src/run.cc", f"{PY} `demo.old`/f().": "demo/old.py",
             f"{PY} `demo.a`/dup().": "demo/a.py", f"{PY} `demo.b`/dup().": "demo/b.py",
             f"{PY} `demo.gone`/x().": "demo/gone.py"}
    new_h = {"cxx . . $ ns/Run().": "h1", f"{PY} `demo.new`/f().": "h2",
             f"{PY} `demo.c`/dup().": "h3", f"{PY} `demo.d`/dup().": "h3"}
    new_p = {"cxx . . $ ns/Run().": "src/csrc/run.cc", f"{PY} `demo.new`/f().": "demo/new.py",
             f"{PY} `demo.c`/dup().": "demo/c.py", f"{PY} `demo.d`/dup().": "demo/d.py"}
    moves = diff.detect_moves(old_h, old_p, new_h, new_p)
    assert moves == {"cxx . . $ ns/Run().": "cxx . . $ ns/Run().",          # path move
                     f"{PY} `demo.old`/f().": f"{PY} `demo.new`/f()."}     # 1:1 rename
    # dup: two removed + two added with the same key → ambiguous → not a move
    assert not any("dup" in k for k in moves)
    # a body change is never a move
    assert diff.detect_moves({"m": "a"}, {"m": "x.py"}, {"m": "b"}, {"m": "y.py"}) == {}


def test_plan_relinks_instead_of_rebuilding():
    g = SymbolGraph()
    run = _sym(g, "cxx . . $ ns/Run().", "Run", "src/csrc/run.cc")
    other = _sym(g, "cxx . . $ ns/Other().", "Other", "src/other.cc")
    hashes = {run: "h1", other: "h2-changed"}
    state = state_mod.load_state(Path("/nonexistent"))
    state_mod.set_symbols(state, {run: "h1", other: "h2"})
    state_mod.set_paths(state, {run: "src/run.cc", other: "src/other.cc"})
    state_mod.record_page(state, "moved-page", [run], "r0")
    state_mod.record_page(state, "stale-page", [other], "r0")
    state_mod.record_page(state, "fresh-page", [], "r0")
    cfg = RepoConfig(slug="demo", concepts=[Concept("moved-page"), Concept("stale-page"),
                                            Concept("fresh-page"), Concept("new-page")])
    plan = diff.compute_plan(g, "/tmp", state, cfg, hashes)
    assert plan.relink == ["moved-page"] and plan.rebuild == ["stale-page"]
    assert plan.leave == ["fresh-page"] and plan.build == ["new-page"]
    assert plan.moves == {run: run} and plan.removed_symbols == 0
    assert "will relink" in plan.render() and "1 moved" in plan.render()
    # folding the move into state makes the next plan a no-op for that page
    state_mod.apply_moves(state, plan.moves, plan.new_paths)
    assert state["paths"][run] == "src/csrc/run.cc"
    plan2 = diff.compute_plan(g, "/tmp", state, cfg, hashes)
    assert plan2.relink == [] and "moved-page" in plan2.leave


def test_relink_text_rewrites_only_matching_targets():
    lmap = relink.link_map({"cxx . . $ ns/Run().": "cxx . . $ ns/Run()."},
                           {"cxx . . $ ns/Run().": "src/run.cc"}, {"cxx . . $ ns/Run().": "src/csrc/run.cc"})
    assert lmap == {("src/run.cc.md", "Run"): ("src/csrc/run.cc.md", "Run")}
    text = ("See [`Run`](../catalog/src/run.cc.md#Run) and [`Other`](../catalog/src/other.cc.md#Other) "
            "and [`Run`](../../catalog/src/run.cc.md#Run) and [docs](../doc-concepts/x.md).")
    out, n = relink.relink_text(text, lmap)
    assert n == 2
    assert "[`Run`](../catalog/src/csrc/run.cc.md#Run)" in out
    assert "[`Run`](../../catalog/src/csrc/run.cc.md#Run)" in out          # prefix preserved
    assert "[`Other`](../catalog/src/other.cc.md#Other)" in out and "[docs](../doc-concepts/x.md)" in out
    assert relink.relink_text(out, lmap) == (out, 0)                         # idempotent


def test_relink_silo_touches_pages_subgraphs_and_verify_cache(tmp_path):
    old, new = f"{PY} `demo.old`/f().", f"{PY} `demo.new`/f()."
    moves, op, np_ = {old: new}, {old: "demo/old.py"}, {new: "demo/new.py"}
    silo = tmp_path / "wiki" / "code" / "demo"
    (silo / "concepts").mkdir(parents=True)
    (silo / "doc-concepts").mkdir()
    (silo / "concepts" / "c.md").write_text("---\ntitle: c\n---\n[`f`](../catalog/demo/old.md#f) twice [`f`](../catalog/demo/old.md#f)\n")
    (silo / "doc-concepts" / "d.md").write_text("---\ntitle: d\n---\n[`f`](../catalog/demo/old.md#f)\n")
    cache = tmp_path / ".cache"
    (cache / "packets" / "demo").mkdir(parents=True)
    (cache / "packets" / "demo" / "c.subgraph.txt").write_text(f"{old}\n{PY} `demo.k`/g().\n")
    vc = verify.cache_path(cache, "demo", "c")
    verify.save_cache(vc, {"schema": verify.CACHE_SCHEMA, "claims": {"k1": {"refuted": False, "evidence": {old: "h2"}}}})
    counts = relink.relink_silo(silo, cache, "demo", moves, op, np_)
    assert counts == {"pages": 2, "links": 3, "subgraphs": 1, "verify_keys": 1}
    assert (silo / "concepts" / "c.md").read_text().count("../catalog/demo/new.md#f") == 2
    assert (cache / "packets" / "demo" / "c.subgraph.txt").read_text().startswith(new)
    assert verify.load_cache(vc)["claims"]["k1"]["evidence"] == {new: "h2"}
    assert relink.relink_silo(silo, cache, "demo", moves, op, np_) == {"pages": 0, "links": 0, "subgraphs": 0, "verify_keys": 0}


def test_prune_catalogs_removes_orphans_and_empty_dirs(tmp_path):
    cat = tmp_path / "catalog"
    (cat / "old").mkdir(parents=True)
    (cat / "old" / "m.md").write_text("x"); (cat / "keep.md").write_text("y")
    assert relink.prune_catalogs(cat, [cat / "keep.md"]) == 1
    assert (cat / "keep.md").exists() and not (cat / "old").exists()
