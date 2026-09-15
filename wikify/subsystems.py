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
4. Rank by ``fanin_external * 2 + internal_edges``. Then the **prose budget**
   (``prose-budget.md``): a unit gets a deep mechanism page when it has at least
   ``DEEP_MIN_MODULES`` modules or at least ``DEEP_MIN_FANIN`` outside referrers; every
   other unit is a section of its top-level **area page**. A floor keeps the top
   ``DEEP_FLOOR`` units deep on tiny repos; ``agenda_max`` is an opt-in ceiling. The
   agenda render prints the bill so trimming is a priced decision.

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
DEFAULT_MAX_SUBSYSTEMS = 24   # legacy cap (``agenda: modules`` parity); an opt-in ceiling here
DEFAULT_SEEDS = 8             # seeds per subsystem handed to packet.gather_subgraph
FLAT_SPLIT_MIN_MODULES = 3    # a reference community must span this many modules to be a unit

# Prose budget (prose-budget.md): the per-unit deep-page rule, its floor, and the rate the
# bill is printed at (measured on the torch_tpu clean ingest, 2026-09-05).
DEEP_MIN_MODULES = 5          # a quarter of the split bucket: enough files to hold a mechanism
DEEP_MIN_FANIN = 20           # distinct outside referrers: an API surface the repo enters through
DEEP_FLOOR = 8                # tiny repos still get their top units as deep pages
DEEP_PAGE_MINUTES = 2.5       # agent time per deep page, synthesis + verification share
DEEP_PAGE_TOKENS = 55_000     # output tokens per deep page
AREA_PAGE_FRACTION = 0.2      # an area page costs about a fifth of a deep page
TIER_DEEP = "deep"
TIER_AREA = "area"


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
    tier: str = TIER_DEEP                                 # "deep" (own page) | "area" (section)
    reason: str = ""                                      # the clause that decided the tier
    area: str = ""                                        # top-level area prefix ("" = root)

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


def _area_of(prefix: str, umbrella: str) -> str:
    """The top-level area a unit belongs to: the first path component under the umbrella
    (a community unit ``dir::stem`` belongs to ``dir``'s area). The umbrella's own direct
    group is the root area (``umbrella`` itself)."""
    d = prefix.split("::", 1)[0]
    if umbrella and (d == umbrella or d.startswith(umbrella + "/")):
        rel = d[len(umbrella):].strip("/")
    else:
        rel = d
    first = rel.split("/", 1)[0] if rel else ""
    if not first:
        return umbrella
    return f"{umbrella}/{first}" if umbrella else first


def area_slug(area: str, umbrella: str) -> str:
    """File stem of an area page: the area's path under the umbrella, ``root`` for the
    umbrella itself."""
    rel = area[len(umbrella):].strip("/") if umbrella and area.startswith(umbrella) else area
    parts = [p.strip("_").replace(".", "-").lower() for p in rel.split("/") if p]
    return "-".join(p for p in parts if p) or "root"


def assign_tiers(
    subs: list[Subsystem],
    deep_min_modules: int = DEEP_MIN_MODULES,
    deep_min_fanin: int = DEEP_MIN_FANIN,
    floor: int = DEEP_FLOOR,
    ceiling: int | None = None,
) -> list[Subsystem]:
    """The prose-budget rule, in place. ``subs`` must be ranked (best first).

    deep  = at least ``deep_min_modules`` modules OR at least ``deep_min_fanin`` outside
            referrers; else area. Fewer than ``floor`` deep → the top-ranked area units are
            promoted (reason ``floor``). ``ceiling`` (``agenda_max``) demotes deep units past
            it in rank order (reason ``ceiling``). Every unit keeps its ``reason``."""
    for s in subs:
        if len(s.modules) >= deep_min_modules:
            s.tier, s.reason = TIER_DEEP, f"modules>={deep_min_modules}"
        elif s.fanin_external >= deep_min_fanin:
            s.tier, s.reason = TIER_DEEP, f"fanin>={deep_min_fanin}"
        else:
            s.tier, s.reason = TIER_AREA, "small unit"
    n_deep = sum(1 for s in subs if s.tier == TIER_DEEP)
    if n_deep < floor:
        for s in subs:
            if n_deep >= floor:
                break
            if s.tier == TIER_AREA:
                s.tier, s.reason = TIER_DEEP, "floor"
                n_deep += 1
    if ceiling is not None:
        seen = 0
        for s in subs:
            if s.tier != TIER_DEEP:
                continue
            seen += 1
            if seen > ceiling:
                s.tier, s.reason = TIER_AREA, "ceiling"
    return subs


def deep_units(subs: list[Subsystem]) -> list[Subsystem]:
    return [s for s in subs if s.tier == TIER_DEEP]


def areas_of(subs: list[Subsystem]) -> dict[str, list[Subsystem]]:
    """Units grouped by area prefix, each group in rank order."""
    out: dict[str, list[Subsystem]] = defaultdict(list)
    for s in subs:
        out[s.area].append(s)
    return dict(out)


def estimate_bill(subs: list[Subsystem]) -> tuple[int, int, float, int]:
    """(deep pages, area pages, agent minutes, output tokens) at the measured rate."""
    n_deep = len(deep_units(subs))
    n_area = len(areas_of(subs)) if subs else 0
    units = n_deep + n_area * AREA_PAGE_FRACTION
    return n_deep, n_area, units * DEEP_PAGE_MINUTES, int(units * DEEP_PAGE_TOKENS)


def discover_subsystems(
    graph: SymbolGraph,
    max_modules: int = DEFAULT_MAX_MODULES,
    min_modules: int = DEFAULT_MIN_MODULES,
    min_symbols: int = DEFAULT_MIN_SYMBOLS,
    max_subsystems: int | None = None,
    seeds_per: int = DEFAULT_SEEDS,
    excludes: tuple[str, ...] = PLANNER_EXCLUDES,
    exclude_globs: list[str] | None = None,
    deep_min_modules: int = DEEP_MIN_MODULES,
    deep_min_fanin: int = DEEP_MIN_FANIN,
    floor: int = DEEP_FLOOR,
) -> list[Subsystem]:
    """Plan the agenda: directory-shaped subsystems, ranked, seeded, tiered. Deterministic.

    Returns EVERY unit (ranked best first) with ``tier`` set by ``assign_tiers``:
    ``deep`` units get their own mechanism page, ``area`` units become sections of their
    area page. ``max_subsystems`` is the opt-in ceiling on deep pages (config
    ``agenda_max``), never a truncation of the list."""
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
        s.area = _area_of(s.prefix, umbrella)
        out.append(s)
    return assign_tiers(out, deep_min_modules, deep_min_fanin, floor, max_subsystems)


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
        for s in discover_subsystems(graph, min_symbols=1, excludes=excludes,
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
    n_deep, n_area, minutes, tokens = estimate_bill(subs)
    a(f"{len(subs)} subsystem(s) over {n_mod} library module(s), ranked by external fan-in "
      f"and internal interactions. Tier `deep` = its own mechanism page; `area` = a section of "
      f"its area page (`areas/<area>.md`). Curate in `config/<slug>.md`: "
      f"drop with `agenda_exclude:` globs; cap deep pages with `agenda_max`; move the "
      f"thresholds with `agenda_deep_modules` / `agenda_deep_fanin`; add or rename with "
      f"`- **<slug>** — seeds: (subsystem: <prefix>)`.")
    a("")
    a(f"**Bill** at the measured rate ({DEEP_PAGE_MINUTES:g} min and {DEEP_PAGE_TOKENS // 1000}k output "
      f"tokens per deep page; an area page a fifth of that): **{n_deep} deep page(s) + {n_area} area "
      f"page(s) = about {minutes / 60:.1f} h of agent time and {tokens / 1e6:.1f}M output tokens** for a "
      f"clean ingest; an incremental run rebuilds only pages whose cited symbols changed.")
    a("")
    a("| # | slug | subsystem | modules | symbols | ext fan-in | internal | entry points | tier | why |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(subs, 1):
        eps = ", ".join(f"`{n}`" for n in _names(graph, s.entry_points, 4)) or "(none)"
        a(f"| {i} | {s.slug} | `{s.title}` | {len(s.modules)} | {s.symbol_count} | "
          f"{s.fanin_external} | {s.internal_edges} | {eps} | {s.tier} | {s.reason} |")
    a("")
    for s in subs:
        shown = ", ".join(f"`{m}`" for m in s.modules[:12])
        more = f" (+{len(s.modules) - 12} more)" if len(s.modules) > 12 else ""
        a(f"- **{s.slug}** — `{s.title}` ({s.tier}): {shown}{more}")
    a("")
    areas = areas_of(subs)
    if areas:
        a("## Areas (one page each, `areas/<area>.md`)")
        for area, units in sorted(areas.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            nd = sum(1 for u in units if u.tier == TIER_DEEP)
            a(f"- `{area or '(root)'}`: {len(units)} unit(s), {nd} deep, {len(units) - nd} as sections")
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
    for s in deep_units(subs):
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


# --------------------------------------------------------------------------- #
# Area pages (prose-budget.md): the middle prose tier, one page per top-level area
# --------------------------------------------------------------------------- #
AREA_AUTO_BEGIN = "<!-- area:auto:begin -->"
AREA_AUTO_END = "<!-- area:auto:end -->"
AREA_PROSE_PLACEHOLDER = "_(not yet synthesized — prompts/area.md writes this section)_"


def _cite(graph: SymbolGraph, m: str, depth: int = 1) -> str:
    """A catalog citation for a symbol from an area page (``areas/`` is one level down),
    carrying the source location as the link title."""
    s = graph.symbols[m]
    ref = coverage.catalog_ref(s.def_path, m)
    loc = f"{s.def_path}:L{(s.def_line or 0) + 1}"
    return f"[`{s.name}`]({ref} \"{loc}\")"


def render_area_block(area: str, units: list[Subsystem], graph: SymbolGraph) -> str:
    """The regenerable part of an area page: the units table and the small units'
    facts (modules, entry points as citations). Deterministic, no model."""
    lines: list[str] = []
    a = lines.append
    a(AREA_AUTO_BEGIN)
    a("## Units")
    a(f"{len(units)} planned unit(s) in `{area or '(root)'}`; deep units have their own mechanism "
      "page, the rest are sections below.")
    a("")
    a("| Unit | Modules | Symbols | Callers outside | Entry points | Page |")
    a("|---|---|---|---|---|---|")
    for u in units:
        eps = ", ".join(_cite(graph, m) for m in u.entry_points[:3]) or "(internal)"
        page = f"[{u.slug}](../concepts/{u.slug}.md)" if u.tier == TIER_DEEP else "section below"
        a(f"| `{u.title}` | {len(u.modules)} | {u.symbol_count} | {u.fanin_external} | {eps} | {page} |")
    a("")
    small = [u for u in units if u.tier != TIER_DEEP]
    if small:
        a("## Small units")
        for u in small:
            shown = ", ".join(f"`{m}`" for m in u.modules[:8])
            more = f" (+{len(u.modules) - 8} more)" if len(u.modules) > 8 else ""
            a(f"### `{u.title}`")
            a(f"- modules: {shown}{more}")
            eps = ", ".join(_cite(graph, m) for m in u.entry_points[:5])
            a(f"- entry points: {eps or '(none — internal unit)'}")
            hubs = ", ".join(_cite(graph, m) for m in u.hubs[:3] if m not in u.entry_points[:5])
            if hubs:
                a(f"- hubs: {hubs}")
            a("")
    a(AREA_AUTO_END)
    return "\n".join(lines)


def render_area_page(area: str, units: list[Subsystem], graph: SymbolGraph, slug: str,
                     date: str) -> str:
    """A fresh area page: front matter + prose placeholders + the auto block."""
    n_deep = sum(1 for u in units if u.tier == TIER_DEEP)
    n_mod = sum(len(u.modules) for u in units)
    desc = (f"Area {area or '(root)'} of {slug}: {len(units)} units over {n_mod} modules, "
            f"{n_deep} with their own mechanism page.")
    return "\n".join([
        "---",
        f"title: 'Area: {area or '(root)'}'",
        "type: area",
        f"area: '{area}'",
        f"description: '{desc}'",
        f"updated: {date}",
        "---",
        f"# Area: `{area or '(root)'}`",
        "",
        "## Purpose",
        AREA_PROSE_PLACEHOLDER,
        "",
        "## How the units connect",
        AREA_PROSE_PLACEHOLDER,
        "",
        render_area_block(area, units, graph),
        "",
    ])


def _replace_block(text: str, block: str) -> str:
    if AREA_AUTO_BEGIN in text and AREA_AUTO_END in text:
        head, rest = text.split(AREA_AUTO_BEGIN, 1)
        _old, tail = rest.split(AREA_AUTO_END, 1)
        return head + block + tail
    return text.rstrip("\n") + "\n\n" + block + "\n"


def write_area_pages(
    wiki_slug_dir, subs: list[Subsystem], graph: SymbolGraph, slug: str, date: str, umbrella: str,
) -> tuple[list, int]:
    """Write ``areas/<area>.md`` for every area (created with placeholders when absent; the
    auto block regenerated in place otherwise, prose outside it untouched). Returns
    (paths, created count). Idempotent."""
    from pathlib import Path
    adir = Path(wiki_slug_dir) / "areas"
    paths: list = []
    created = 0
    for area, units in sorted(areas_of(subs).items()):
        out = adir / f"{area_slug(area, umbrella)}.md"
        block = render_area_block(area, units, graph)
        if out.exists():
            text = out.read_text(encoding="utf-8")
            new = _replace_block(text, block)
            if new != text:
                out.write_text(new, encoding="utf-8")
        else:
            adir.mkdir(parents=True, exist_ok=True)
            out.write_text(render_area_page(area, units, graph, slug, date), encoding="utf-8")
            created += 1
        paths.append(out)
    return paths, created


def umbrella_of(graph: SymbolGraph, excludes: tuple[str, ...] = PLANNER_EXCLUDES) -> str:
    return _umbrella(sorted(_modules(graph, excludes)))
