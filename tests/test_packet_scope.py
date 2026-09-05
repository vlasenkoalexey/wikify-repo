"""The packet budget belongs to the unit (§10.3). A repo-wide helper module with the
highest importance in the graph must not crowd a subsystem's own members out of its
packet; without a scope the legacy behaviour is byte-for-byte unchanged."""

from wikify import packet
from wikify.graph import Symbol, SymbolGraph

PKG = "scip-python python demo 0.0.0"


def _sym(g, moniker, name, path, refs=0):
    g.add_symbol(Symbol(moniker=moniker, kind="Function", suffix="Method", name=name, def_path=path))
    g.ref_count[moniker] = refs
    return moniker


def _g():
    g = SymbolGraph()
    helpers = [_sym(g, f"{PKG} `demo.util.err`/h{i}().", f"h{i}", "demo/util/err.py", refs=500) for i in range(6)]
    members = [_sym(g, f"{PKG} `demo.unit.m{i}`/f{i}().", f"f{i}", f"demo/unit/m{i}.py", refs=3) for i in range(20)]
    # every member calls every helper (the repo-wide status/error pattern)
    for m in members:
        for h in helpers:
            g.add_edge(m, h)
    # the seeds reach only the first 5 members; the rest are members no seed reaches
    for i in range(1, 5):
        g.add_edge(members[0], members[i])
    return g, helpers, members


def test_without_scope_helpers_win_the_budget():
    g, helpers, members = _g()
    sg = packet.gather_subgraph(g, [members[0]], max_nodes=12)
    assert sg[0] == members[0]
    assert sum(m in helpers for m in sg) == 6           # all six helpers admitted
    assert sum(m in members for m in sg) <= 6


def test_scope_reserves_budget_and_caps_outside_modules():
    g, helpers, members = _g()
    scope = set(members)
    sg = packet.gather_subgraph(g, [members[0]], max_nodes=12, scope=scope)
    assert sg[0] == members[0]
    assert len(sg) == 12
    assert sum(m in members for m in sg) >= 9            # 0.75 reserve, then backfill
    assert sum(m in helpers for m in sg) <= 2            # per-outside-module cap
    # members the seeds never reach are still candidates
    unreached = set(members[5:])
    assert unreached & set(sg)


def test_scope_none_is_legacy_behaviour():
    g, helpers, members = _g()
    assert packet.gather_subgraph(g, [members[0]], max_nodes=12) == \
        packet.gather_subgraph(g, [members[0]], max_nodes=12, scope=None)


def test_scope_smaller_than_reserve_backfills_with_outside():
    """A tiny unit: members first, capped outside context, then the cap is relaxed so
    the budget still fills — nothing is crowded out once members are exhausted."""
    g, helpers, members = _g()
    scope = set(members[:3])
    sg = packet.gather_subgraph(g, [members[0]], max_nodes=12, scope=scope)
    # reachable candidates: seed + 2 members + 6 helpers + 2 reached non-members = 11
    assert len(sg) == 11
    assert sg[:3] == [members[0]] + sorted(members[1:3])       # members precede context
    assert sum(m in helpers for m in sg) == 6                  # cap relaxed once members ran out
