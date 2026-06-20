"""Devirtualization (CHA) + relevance-bounded subgraph sizing.

Two process improvements:
  - `graph.devirtualize` adds base→override / class→subclass edges from SCIP
    `is_implementation`, crossing the dynamic-dispatch seam (e.g. nn.Module.forward
    → a subclass forward) that reference scoping can't see.
  - `packet.gather_subgraph` fills a fixed budget by *relevance* (importance ÷
    distance from a seed), not BFS discovery order, so a hub keeps its load-bearing
    collaborators instead of an arbitrary first-N.
"""

from wikify import packet
from wikify.graph import Symbol, SymbolGraph, devirtualize


def _sym(g, moniker, name, rels=None):
    s = Symbol(moniker=moniker, kind="Method", suffix="Method", name=name,
               def_path="m.py", def_line=1)
    if rels:
        s.relationships.extend(rels)
    g.add_symbol(s)
    return moniker


# --- devirtualization ------------------------------------------------------

def test_devirtualize_adds_base_to_override_edge():
    g = SymbolGraph()
    base = _sym(g, "`m`/Module#forward().", "forward")
    override = _sym(g, "`m`/Transformer#forward().", "forward",
                    rels=[(base, "is_implementation")])
    n = devirtualize(g)
    assert n == 1
    assert override in g.callees(base)        # reaching the base reaches the impl
    assert base in g.callers(override)
    assert g.is_virtual(base, override)       # labelled as a CHA edge, not a ref


def test_devirtualize_connects_a_caller_through_the_base():
    """A trainer that references the base forward now reaches the real override."""
    g = SymbolGraph()
    base = _sym(g, "`m`/Module#forward().", "forward")
    override = _sym(g, "`m`/Transformer#forward().", "forward",
                    rels=[(base, "is_implementation")])
    trainer = _sym(g, "`m`/Trainer#step().", "step")
    g.add_edge(trainer, base)                 # static ref: trainer -> base.forward
    devirtualize(g)
    reach = packet.gather_subgraph(g, [trainer])
    assert override in reach                  # the override is now reachable


def test_devirtualize_ignores_dangling_or_non_impl_relationships():
    g = SymbolGraph()
    s = _sym(g, "`m`/A#f().", "f",
             rels=[("`m`/Ghost#f().", "is_implementation"),   # target absent
                   ("`m`/A#f().", "is_type_definition")])     # wrong kind
    assert devirtualize(g) == 0


# --- relevance-bounded subgraph -------------------------------------------

def test_subgraph_keeps_high_importance_over_low_within_budget():
    g = SymbolGraph()
    seed = _sym(g, "`m`/seed().", "seed")
    # a "hub" callee referenced by many => high importance, and a "leaf" referenced
    # by none => low importance. With budget 2 (seed + 1), the hub must win.
    hub = _sym(g, "`m`/hub().", "hub")
    leaf = _sym(g, "`m`/leaf().", "leaf")
    g.add_edge(seed, hub)
    g.add_edge(seed, leaf)
    for i in range(5):                        # 5 extra referrers of hub => importance
        r = _sym(g, f"`m`/r{i}().", f"r{i}")
        g.add_edge(r, hub)
    chosen = packet.gather_subgraph(g, [seed], max_nodes=2)
    assert seed in chosen and hub in chosen and leaf not in chosen


def test_subgraph_always_keeps_seeds_and_respects_budget():
    g = SymbolGraph()
    seeds = [_sym(g, f"`m`/s{i}().", f"s{i}") for i in range(4)]
    for i in range(20):
        c = _sym(g, f"`m`/c{i}().", f"c{i}")
        g.add_edge(seeds[0], c)
    chosen = packet.gather_subgraph(g, seeds, max_nodes=6)
    assert len(chosen) == 6
    assert all(s in chosen for s in seeds)    # seeds are never dropped
