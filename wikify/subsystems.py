"""Stage 5 agenda, subsystem tier: the table of contents is a plan over the module tree.

Design decision 8 says the comprehension unit is the *derived cluster*, not the file.
The first realization (``discover.py``) ranked single modules by fan-in. On real repos
that surfaces hub headers (string formatting, status builders, macros) because
centrality rewards what everything depends on, while the subsystems people ask about
(the compilation cache, the distributed backend, the compile backend) got no mechanism
page at all. This module derives **subsystems** instead:

1. Take the documentable symbols' definition files (library only: tests, examples and
   vendored code are excluded by ``discover.DEFAULT_EXCLUDES``), find the umbrella
   package they share, and split the directory tree top-down until every node holds at
   most ``max_modules`` modules. Children smaller than ``min_modules`` fold back into
   their parent's own module group, so the tree does not fragment into one-file pages.
2. For each subsystem compute its symbol set, internal edges, and **external fan-in**:
   the distinct library symbols outside it that reference something inside it, i.e.
   how much the rest of the repo depends on it.
3. **Entry points** are the inside symbols ranked by distinct external callers (the
   API surface the rest of the repo enters through); **hubs** are the inside symbols
   by importance. Seeds are entry points first, then hubs, capped.
4. Rank by ``fanin_external * 2 + internal_edges`` and cap the agenda.

Pure Python, deterministic, no model. Synthesis (LLM) still writes one page per
packet; the packet is built around the subsystem's seeds by ``packet.gather_subgraph``
and carries a ``## Scope`` block naming the subsystem's modules and entry points. The
ingest skill shows the rendered agenda to the user *before* synthesis; curation is
config-driven: ``agenda_exclude`` globs drop entries, and a ``## Concepts`` entry with
``seeds: (subsystem: <prefix>)`` adds or renames one. Module-level centrality
(``discover.discover_concepts``) remains available as ``agenda: modules``.
"""

from __future__ import annotations

import fnmatch
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import coverage
from .discover import DEFAULT_EXCLUDES, _excluded, _library_nodes, label_propagation
from .graph import SymbolGraph

# The planner also drops per-file test modules (``foo_test.cc``, ``bar_test.py``,
# ``conftest.py``): they would count as modules in the tree split and their symbols
# could surface as hubs. discover.DEFAULT_EXCLUDES only covers test *directories*.
PLANNER_EXCLUDES = DEFAULT_EXCLUDES + ("_test.", "_tests.", "conftest.py")

DEFAULT_MAX_MODULES = 20      # split a directory whose subtree holds more modules than this
DEFAULT_MIN_MODULES = 2       # a child smaller than this folds into its parent's group
DEFAULT_MIN_SYMBOLS = 8       # drop subsystems with fewer documentable symbols (keep >= 1)
DEFAULT_MAX_SUBSYSTEMS = 24   # agenda cap (parity with discover.discover_concepts max_deep)
DEFAULT_SEEDS = 8             # seeds per subsystem handed to packet.gather_subgraph
FLAT_SPLIT_MIN_MODULES = 3    # a reference community must span this many modules to be a unit


@dataclass
class Subsystem:
    """One planned page: a directory-shaped unit with its API surface and hubs."""

    slug: str
    prefix: str                                           # repo-relative dir; "" = root
    modules: list[str]                                    # def files, sorted
    symbols: list[str] = field(default_factory=list)      # documentable monikers inside
    class_count: int = 0
    internal_edges: int = 0
    fanin_external: int = 0                               # distinct outside callers
    fanout_external: int = 0                              # distinct outside callees
    entry_points: list[str] = field(default_factory=list) # monikers, most external callers first
    hubs: list[str] = field(default_factory=list)         # monikers, by importance
    seeds: list[str] = field(default_factory=list)        # entry points then hubs, capped

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)

    @property
    def score(self) -> int:
        """Interactions the rest of the repo has with this unit plus its own cohesion."""
        return self.fanin_external * 2 + self.internal_edges

    @property
    def title(self) -> str:
        return self.prefix or "(repo root)"


# --------------------------------------------------------------------------- #
# Tree split
# --------------------------------------------------------------------------- #
def _umbrella(modules: list[str]) -> str:
    """Longest directory prefix (whole components) shared by every module path."""
    if not modules:
        return ""
    parts = [m.split("/")[:-1] for m in modules]
    common: list[str] = []
    for segs in zip(*parts):
        if all(s == segs[0] for s in segs):
            common.append(segs[0])
        else:
            break
    return "/".join(common)


def _split(
    prefix: str, mods: list[str], max_modules: int, min_modules: int
) -> list[tuple[str, list[str]]]:
    """Split ``prefix``'s subtree until every group holds <= ``max_modules`` modules.

    Files directly in a split directory stay together as that directory's own group
    (the "core" of the subsystem); tiny children fold into that group."""
    if len(mods) <= max_modules:
        return [(prefix, sorted(mods))]
    plen = len(prefix) + 1 if prefix else 0
    direct: list[str] = []
    children: dict[str, list[str]] = defaultdict(list)
    for m in mods:
        rest = m[plen:]
        if "/" in rest:
            children[rest.split("/", 1)[0]].append(m)
        else:
            direct.append(m)
    kept: list[tuple[str, list[str]]] = []
    for name in sorted(children):
        cm = children[name]
        if len(cm) < min_modules:
            direct.extend(cm)
        else:
            kept.append((f"{prefix}/{name}" if prefix else name, cm))
    if not kept:                       # flat directory: nothing to split into
        return [(prefix, sorted(mods))]
    out: list[tuple[str, list[str]]] = []
    if direct:
        out.append((prefix, sorted(direct)))
    for cp, cm in kept:
        out.extend(_split(cp, cm, max_modules, min_modules))
    return out


def _stem(path: str) -> str:
    """``torch_tpu/common/compilation_cache.h`` → ``compilation_cache``."""
    name = path.rsplit("/", 1)[-1]
    name = name.split(".", 1)[0]
    return name


def _split_flat(
    graph: SymbolGraph,
    prefix: str,
    files: list[str],
    mods: dict[str, list[str]],
    max_modules: int,
) -> list[tuple[str, list[str]]]:
    """A flat directory over budget cannot be split by the tree, so split it by the
    reference graph: label-propagation communities over the directory's own symbols,
    each module assigned its dominant community, communities spanning >=
    ``FLAT_SPLIT_MIN_MODULES`` modules become units named ``<dir>::<stem>`` after the
    cluster's LARGEST module (by documentable symbols — utilities are small, the
    substantive module is big; naming by importance would pick the status/error
    helper everyone calls). The rest stay as the directory's own group. Deterministic.
    Falls back to the flat group when clustering finds nothing to separate."""
    if len(files) <= max_modules:
        return [(prefix, sorted(files))]
    allowed = {m for f in files for m in mods[f]}
    label = label_propagation(graph, allowed=allowed)
    mod_label: dict[str, str | None] = {}
    for f in files:
        c = Counter(label[m] for m in mods[f] if m in label)
        mod_label[f] = c.most_common(1)[0][0] if c else None
    groups: dict[str | None, list[str]] = defaultdict(list)
    for f, l in mod_label.items():
        groups[l].append(f)
    units: list[tuple[str, list[str]]] = []
    rest: list[str] = []
    for l, fs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        if l is None or len(fs) < FLAT_SPLIT_MIN_MODULES:
            rest.extend(fs)
            continue
        biggest = max(fs, key=lambda f: (len(mods[f]), max(graph.importance(m) for m in mods[f]), f))
        units.append((f"{prefix}::{_stem(biggest)}", sorted(fs)))
    if len(units) < 2 and not (units and rest):
        return [(prefix, sorted(files))]          # nothing separable: keep the flat unit
    out: list[tuple[str, list[str]]] = []
    if rest:
        out.append((prefix, sorted(rest)))
    out.extend(units)
    return out


def _slug(prefix: str, umbrella: str) -> str:
    rel = prefix[len(umbrella):].strip("/") if umbrella and prefix.startswith(umbrella) else prefix
    rel = rel.replace("::", "/")                # community unit: <dir>::<stem> → <dir>/<stem>
    parts = [p.strip("_").replace(".", "-").lower() for p in rel.split("/") if p and p != "__init__"]
    parts = [p for p in parts if p]
    return "-".join(parts) or "core"


def _matches(prefix: str, globs: list[str]) -> bool:
    """Does an ``agenda_exclude`` glob name this unit? ``dir`` matches the unit itself,
    ``dir/*`` its children; a community unit ``dir::stem`` matches on ``dir``."""
    cands = {prefix, prefix.split("::", 1)[0]}
    for g in globs:
        g = g.rstrip("/")
        if not g:
            continue
        for c in cands:
            if c == g or fnmatch.fnmatch(c, g) or fnmatch.fnmatch(c, g + "/*"):
                return True
    return False


# --------------------------------------------------------------------------- #
# Stats + seeds
# --------------------------------------------------------------------------- #
def _fill(graph: SymbolGraph, sub: Subsystem, library: set[str], seeds_per: int) -> Subsystem:
    inside = set(sub.symbols)
    ext_callers: set[str] = set()
    ext_callees: set[str] = set()
    per_entry: dict[str, set[str]] = defaultdict(set)
    internal = 0
    for m in sub.symbols:
        for c in graph.callees(m):
            if c in inside:
                internal += 1
            elif c in library:
                ext_callees.add(c)
        for c in graph.callers(m):
            if c not in inside and c in library:
                ext_callers.add(c)
                per_entry[m].add(c)
    sub.internal_edges = internal
    sub.fanin_external = len(ext_callers)
    sub.fanout_external = len(ext_callees)
    sub.class_count = sum(1 for m in sub.symbols if graph.symbols[m].suffix == "Type")

    def _prefer(m: str) -> int:   # callables and types before plain terms; operators last
        s = graph.symbols[m]
        if s.name.startswith("operator"):
            return 2
        return 0 if (s.is_callable or s.suffix == "Type") else 1

    entry = [m for m in sub.symbols if per_entry[m]]
    entry.sort(key=lambda m: (-len(per_entry[m]), _prefer(m), -graph.importance(m), m))
    sub.entry_points = entry[:seeds_per]
    hubs = sorted(sub.symbols, key=lambda m: (_prefer(m), -graph.importance(m), m))
    sub.hubs = hubs[:seeds_per]
    seen: set[str] = set()
    sub.seeds = [m for m in sub.entry_points + sub.hubs if not (m in seen or seen.add(m))][:seeds_per]
    return sub


def _modules(graph: SymbolGraph, excludes: tuple[str, ...]) -> dict[str, list[str]]:
    """Documentable symbols grouped by definition file, library modules only."""
    docs = coverage.documentable_symbols(graph)
    mods: dict[str, list[str]] = defaultdict(list)
    for m, s in docs.items():
        if s.def_path and not _excluded(s.def_path, excludes):
            mods[s.def_path].append(m)
    return mods


def discover_subsystems(
    graph: SymbolGraph,
    max_modules: int = DEFAULT_MAX_MODULES,
    min_modules: int = DEFAULT_MIN_MODULES,
    min_symbols: int = DEFAULT_MIN_SYMBOLS,
    max_subsystems: int = DEFAULT_MAX_SUBSYSTEMS,
    seeds_per: int = DEFAULT_SEEDS,
    excludes: tuple[str, ...] = PLANNER_EXCLUDES,
    exclude_globs: list[str] | None = None,
) -> list[Subsystem]:
    """Plan the agenda: directory-shaped subsystems, ranked, seeded. Deterministic."""
    mods = _modules(graph, excludes)
    if not mods:
        return []
    library = _library_nodes(graph, excludes)
    umbrella = _umbrella(sorted(mods))
    groups: list[tuple[str, list[str]]] = []
    for prefix, files in _split(umbrella, sorted(mods), max_modules, min_modules):
        groups.extend(_split_flat(graph, prefix, files, mods, max_modules))
    subs: list[Subsystem] = []
    for prefix, files in groups:
        if exclude_globs and _matches(prefix, exclude_globs):
            continue
        symbols = [m for f in files for m in mods[f]]
        sub = Subsystem(slug=_slug(prefix, umbrella), prefix=prefix, modules=files, symbols=symbols)
        subs.append(_fill(graph, sub, library, seeds_per))
    subs.sort(key=lambda s: (-s.score, -s.symbol_count, s.prefix))
    kept = [s for s in subs if s.symbol_count >= min_symbols] or subs[:1]
    # de-dup slugs (distinct prefixes can collapse to one slug)
    seen: set[str] = set()
    out: list[Subsystem] = []
    for s in kept:
        slug, n = s.slug, 2
        while slug in seen:
            slug = f"{s.slug}-{n}"
            n += 1
        s.slug = slug
        seen.add(slug)
        out.append(s)
    return out[:max_subsystems]


def subsystem_for_prefix(
    graph: SymbolGraph,
    prefix: str,
    slug: str | None = None,
    seeds_per: int = DEFAULT_SEEDS,
    excludes: tuple[str, ...] = PLANNER_EXCLUDES,
) -> Subsystem | None:
    """One subsystem for a user-named directory (config ``seeds: (subsystem: <prefix>)``).

    Every library module at or under ``prefix`` is included, whatever the tree split
    would have done. Returns None when no documentable module lives there."""
    prefix = prefix.strip().strip("/")
    if prefix == ".":          # "." names the repo root, like ""
        prefix = ""
    if "::" in prefix:         # a community unit (``dir::stem``): re-derive the split
        for s in discover_subsystems(graph, max_subsystems=10**6, min_symbols=1, excludes=excludes,
                                     seeds_per=seeds_per):
            if s.prefix == prefix:
                if slug:
                    s.slug = slug
                return s
        return None
    mods = _modules(graph, excludes)
    files = sorted(f for f in mods if f == prefix or f.startswith(prefix + "/") or not prefix)
    if not files:
        return None
    symbols = [m for f in files for m in mods[f]]
    sub = Subsystem(slug=slug or _slug(prefix, ""), prefix=prefix, modules=files, symbols=symbols)
    return _fill(graph, sub, _library_nodes(graph, excludes), seeds_per)


# --------------------------------------------------------------------------- #
# Rendering (agenda file for the skill; scope block for the packet)
# --------------------------------------------------------------------------- #
def _short(graph: SymbolGraph, m: str) -> str:
    return graph.symbols[m].name if m in graph.symbols else m.rsplit("/", 1)[-1]


def _names(graph: SymbolGraph, monikers: list[str], n: int) -> list[str]:
    """First ``n`` distinct display names (a C++ class and its constructor share one)."""
    out: list[str] = []
    for m in monikers:
        name = _short(graph, m)
        if name not in out:
            out.append(name)
        if len(out) == n:
            break
    return out


def render_agenda(subs: list[Subsystem], graph: SymbolGraph, slug: str = "") -> str:
    """Markdown table + per-subsystem module lists: what the planner proposes to write."""
    lines: list[str] = []
    a = lines.append
    a(f"# Proposed agenda: {slug or 'repo'} (subsystem planner)")
    a("")
    n_mod = sum(len(s.modules) for s in subs)
    a(f"{len(subs)} subsystem(s) over {n_mod} library module(s), ranked by external fan-in "
      f"and internal interactions. One mechanism page per row. Curate in `config/<slug>.md`: "
      f"drop with `agenda_exclude:` globs; add or rename with "
      f"`- **<slug>** — seeds: (subsystem: <prefix>)`.")
    a("")
    a("| # | slug | subsystem | modules | symbols | ext fan-in | internal | entry points |")
    a("|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(subs, 1):
        eps = ", ".join(f"`{n}`" for n in _names(graph, s.entry_points, 4)) or "(none)"
        a(f"| {i} | {s.slug} | `{s.title}` | {len(s.modules)} | {s.symbol_count} | "
          f"{s.fanin_external} | {s.internal_edges} | {eps} |")
    a("")
    for s in subs:
        shown = ", ".join(f"`{m}`" for m in s.modules[:12])
        more = f" (+{len(s.modules) - 12} more)" if len(s.modules) > 12 else ""
        a(f"- **{s.slug}** — `{s.title}`: {shown}{more}")
    a("")
    # Topic titles are decided at confirmation time (openwiki plans titles first): a
    # paste-ready block whose only job is renaming the bold slug. A config subsystem
    # entry REPLACES the planned unit(s) under its prefix, so renaming never duplicates.
    a("## Concepts block (paste into `config/<slug>.md` to pin or rename)")
    a("Rename the bold slug to a topic name (prefer a key from the host `wiki/concepts/` "
      "vocabulary when the unit is an instance of one); keep the `(subsystem: ...)` clause as is. "
      "Delete lines you do not want. An entry replaces the planned unit(s) under its prefix.")
    a("")
    a("## Concepts")
    for s in subs:
        a(f"- **{s.slug}** — seeds: (subsystem: {s.prefix or '.'})")
    a("")
    return "\n".join(lines)


def render_scope(sub: Subsystem, graph: SymbolGraph) -> str:
    """The packet's ``## Scope`` block: the unit the page is about."""
    shown = ", ".join(f"`{m}`" for m in sub.modules[:15])
    more = f" (+{len(sub.modules) - 15} more)" if len(sub.modules) > 15 else ""
    eps = ", ".join(f"`{n}`" for n in _names(graph, sub.entry_points, 6)) or "(none — internal unit)"
    return "\n".join([
        f"Subsystem `{sub.title}` — {len(sub.modules)} module(s), {sub.symbol_count} documentable "
        f"symbol(s); {sub.fanin_external} outside symbol(s) depend on it.",
        f"Modules: {shown}{more}",
        f"Entry points (most external callers first): {eps}",
        "Write the page about how THIS subsystem works as a whole — its responsibilities, the "
        "mechanism that ties its modules together, and how the rest of the repo enters it — not "
        "about a single file. Hub utilities inside it are sections, not the subject.",
    ])
