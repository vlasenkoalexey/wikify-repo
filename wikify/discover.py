"""Stage 5 agenda — derive concepts from topology (design decision 8).

The comprehension agenda is **computed from the code's own structure, not authored**.
A concept unit is a *module* (the def file); its importance is the aggregate
centrality (inbound fan-in) of its symbols; the deep tier is the top units by
importance, excluding the experiment/test/script tail. Each concept is auto-seeded
from its highest-centrality symbols — no hand-seeding.

Pure Python, no model: this only *selects and seeds*; synthesis (LLM) writes the
pages for the units this picks.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import coverage
from .graph import Symbol, SymbolGraph

# Modules under these prefixes are coverage-only (catalog), never deep concepts.
# Covers both torchtitan ("tests/") and xla ("test/", "examples/") conventions.
DEFAULT_EXCLUDES = (
    "experiments/", "tests/", "/tests/", "test/", "/test/", "/test_",
    "examples/", "example/", "scripts/", "benchmarks/", "benchmark/",
)


@dataclass
class DiscoveredConcept:
    slug: str
    module: str                       # def-file path
    importance: int
    seeds: list[str] = field(default_factory=list)   # monikers (auto)
    symbol_count: int = 0
    class_count: int = 0


def _excluded(path: str, excludes: tuple[str, ...]) -> bool:
    return any(e in path for e in excludes)


def _concept_slug(module: str) -> str:
    """Readable concept slug from a module path (drop top package + extension)."""
    p = module[:-3] if module.endswith(".py") else module
    parts = [seg for seg in p.split("/") if seg and seg != "__init__"]
    if parts and parts[0] in ("torchtitan", "src"):  # drop the umbrella package
        parts = parts[1:]
    # collapse a trailing repeated leaf like models/llama3/model/model -> models-llama3-model
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    return "-".join(parts) or "root"


def module_importance(graph: SymbolGraph) -> dict[str, dict]:
    """Per-module aggregate stats: fan-in (centrality), symbol/class counts, seeds."""
    docs = coverage.documentable_symbols(graph)
    mods: dict[str, dict] = defaultdict(
        lambda: {"fanin": 0, "syms": 0, "classes": 0, "ranked": []}
    )
    for m, s in docs.items():
        info = mods[s.def_path]
        fanin = len(graph.callers(m))
        info["fanin"] += fanin
        info["syms"] += 1
        if s.suffix == "Type":
            info["classes"] += 1
        info["ranked"].append((graph.importance(m), fanin, m))
    return mods


def discover_concepts(
    graph: SymbolGraph,
    max_deep: int = 24,
    min_importance: int = 25,
    seeds_per_concept: int = 4,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
) -> list[DiscoveredConcept]:
    """Top modules by centrality → deep concept specs, auto-seeded. Deterministic."""
    mods = module_importance(graph)
    specs: list[DiscoveredConcept] = []
    for module, info in mods.items():
        if _excluded(module, excludes) or info["fanin"] < min_importance:
            continue
        ranked = sorted(info["ranked"], reverse=True)  # by (importance, fanin)
        seeds = [m for _imp, _fan, m in ranked[:seeds_per_concept]]
        specs.append(
            DiscoveredConcept(
                slug=_concept_slug(module),
                module=module,
                importance=info["fanin"],
                seeds=seeds,
                symbol_count=info["syms"],
                class_count=info["classes"],
            )
        )
    specs.sort(key=lambda c: -c.importance)
    # de-dup slugs (distinct modules can collapse to the same slug)
    seen: set[str] = set()
    deduped: list[DiscoveredConcept] = []
    for c in specs:
        slug = c.slug
        n = 2
        while slug in seen:
            slug = f"{c.slug}-{n}"
            n += 1
        c.slug = slug
        seen.add(slug)
        deduped.append(c)
    return deduped[:max_deep]


# --------------------------------------------------------------------------- #
# Light tier (decision 8 mid-band): community detection + Pareto selection.
# "Top ~20% of communities cover ~80% of interactions." Cheaper than concept
# pages: cluster-granular, no source read, diagrams optional. Deterministic.
# --------------------------------------------------------------------------- #
@dataclass
class Community:
    label: str
    members: list[str] = field(default_factory=list)      # monikers
    internal_edges: int = 0
    boundary_edges: int = 0                                # edges to other communities
    top_members: list[str] = field(default_factory=list)  # by centrality
    modules: list[str] = field(default_factory=list)      # def files represented
    neighbors: list[str] = field(default_factory=list)     # labels of connected communities

    @property
    def interactions(self) -> int:
        return self.internal_edges + self.boundary_edges


def _undirected_adj(graph: SymbolGraph, allowed: set[str] | None = None) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for m in graph.symbols:
        if allowed is not None and m not in allowed:
            continue
        for c in graph.callees(m):
            if allowed is not None and c not in allowed:
                continue
            adj[m].add(c)
            adj[c].add(m)
    return adj


def _library_nodes(graph: SymbolGraph, excludes: tuple[str, ...]) -> set[str]:
    """In-repo nodes that are NOT test/example/benchmark — the library proper."""
    return {
        m for m, s in graph.symbols.items()
        if s.def_path and not _excluded(s.def_path, excludes)
    }


def label_propagation(
    graph: SymbolGraph, iterations: int = 10, allowed: set[str] | None = None
) -> dict[str, str]:
    """Deterministic label-propagation community detection over the ref graph.

    ``allowed`` restricts clustering to a node subset (e.g. library-only, so test
    code never pulls library symbols into a test community). Each node adopts the
    most frequent label among its neighbours (ties → smallest label, keep own).
    Fixed sorted iteration order makes it reproducible. Returns moniker → label."""
    adj = _undirected_adj(graph, allowed=allowed)
    nodes = sorted(n for n in adj if adj[n])
    label = {n: n for n in nodes}
    for _ in range(iterations):
        changed = False
        for n in nodes:
            counts = Counter(label[x] for x in adj[n])
            counts[label[n]] += 0  # ensure own label present
            best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if best != label[n]:
                label[n] = best
                changed = True
        if not changed:
            break
    return label


def _excluded_fraction(graph: SymbolGraph, members: list[str], excludes: tuple[str, ...]) -> float:
    paths = [graph.symbols[m].def_path or "" for m in members]
    n = sum(1 for p in paths if _excluded(p, excludes))
    return n / len(paths) if paths else 1.0


def detect_communities(
    graph: SymbolGraph,
    min_size: int = 4,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
) -> list[Community]:
    """Cluster the graph and summarise each community (edges, top members, modules).

    Clustering runs over the **library subgraph only** (test/example/benchmark
    nodes are removed first), so test drivers never pull library symbols into a
    test-labelled community — same rule as the concept tier, applied pre-cluster."""
    allowed = _library_nodes(graph, excludes)
    label = label_propagation(graph, allowed=allowed)
    groups: dict[str, list[str]] = defaultdict(list)
    for n, l in label.items():
        groups[l].append(n)

    comms: list[Community] = []
    for l, members in groups.items():
        if len(members) < min_size:
            continue
        mset = set(members)
        internal = boundary = 0
        nbr: Counter = Counter()
        for m in members:
            for c in graph.callees(m):
                if c in mset:
                    internal += 1
                elif c in label:
                    boundary += 1
                    nbr[label[c]] += 1
        top = sorted(members, key=graph.importance, reverse=True)[:8]
        modules = sorted({graph.symbols[m].def_path for m in members if graph.symbols[m].def_path})
        comms.append(Community(
            label=l, members=members, internal_edges=internal, boundary_edges=boundary,
            top_members=top, modules=modules[:6],
            neighbors=[lbl for lbl, _ in nbr.most_common(4)],
        ))
    comms.sort(key=lambda c: -c.interactions)
    return comms


def pareto_communities(graph: SymbolGraph, cover: float = 0.8, min_size: int = 4) -> list[Community]:
    """The smallest set of top communities whose interactions cover ``cover`` of all."""
    comms = detect_communities(graph, min_size=min_size)
    total = sum(c.interactions for c in comms) or 1
    out, acc = [], 0
    for c in comms:
        out.append(c)
        acc += c.interactions
        if acc / total >= cover:
            break
    return out
