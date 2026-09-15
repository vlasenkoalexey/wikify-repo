# Prose budget: two tiers and a per-unit rule, not a per-repo cap

Status: realized in wikify-repo 0.3.0 (2026-09-15). Companion to `catalog-index.md`
(which records the open item this page resolves). Decisions-log entry "Prose volume is a
per-unit rule" in `design.md`; mechanics in `implementation.md` section 10.19.
The subsystem planner (section 10.11) is unchanged in how it forms and ranks units; this page
changes how many of them become pages, and adds a second, cheaper page tier.

## Problem

Prose does not grow with the repo. Measured on three silos:

| silo                    | documentable symbols | concept pages | symbols cited by a concept page |
|-------------------------|----------------------|---------------|---------------------------------|
| torch_tpu               | 13,335               | 27            | 765 (5.7 percent)               |
| PyTorch, Python shards  | 192,586              | 24            | 459 (0.2 percent)               |
| PyTorch, demo wiki      | 87,136               | 24            | 480 (0.6 percent)               |

Cause: `DEFAULT_MAX_SUBSYSTEMS = 24` in `subsystems.py` caps the agenda per repo
(`agenda_max` overrides it), at parity with the older module-centrality cap. It is a cost
control: a concept page costs about 55k output tokens and 2 to 3 minutes of agent time plus
verification, so every repo gets the same bill. On PyTorch that is 24 pages spread over
eleven configured shards, about two per subtree. A second, independent problem on the
PyTorch silo: it was planned by the legacy module-centrality agenda, and its pages are hub
headers (`c10/util/Exception.h`, `Half.h`, `BFloat16.h`, `intrusive_ptr.h`), the failure the
subsystem planner was built to fix. Fixing the count before the ranking would multiply the
wrong pages.

## What the planner already knows

Running `discover_subsystems` uncapped on both indexes:

```
repo                      units   modules covered   modules/unit p50   ext fan-in p50   deep pages today
torch_tpu                 80      437               3                  3                27
PyTorch, Python shards    145     1,404             9                  34               24
```

Units per area: PyTorch distributed 35, inductor 23, ao 11, nn 9, dynamo 5, fx 3; torch_tpu
ops 45 (kernel families of two or three files), internal 19. The cap discards 56 of 80 units
on torch_tpu and 121 of 145 on PyTorch.

## Decision

### The hierarchy every silo gets

```
overview.md            what the repo is, the areas, which page answers which question
areas/<area>.md        one per top-level area of the planner's tree: purpose, the units inside
                       with their entry points, how they connect; small units are sections here
concepts/<unit>.md     deep mechanism pages, today's format, for units that pass the rule
catalog/index.md       the module map (deterministic; see catalog-index.md) and the symbol index
```

Area pages are mostly deterministic (modules, entry points, fan-in and the unit list come
from the planner) plus two or three synthesized paragraphs, about a fifth of a mechanism
page's cost. They are the missing middle layer: in the 149-session corpus agents opened
overview, index and log pages 411 times, and today the next hop from there is a deep page or
nothing.

### The rule for a deep page

A unit gets a deep mechanism page when either holds:

- it is mechanism-sized: at least 5 modules, a quarter of the planner's split bucket
  (`max_modules` is 20), so the unit has enough files to hold an internal mechanism;
- or it is depended upon: at least 20 distinct symbols outside the unit reference something
  inside it, so it is an API surface the rest of the repo enters through.

Every other unit is a section of its area page. Floor: if fewer than 8 units qualify, the
top 8 by rank are taken, so a tiny repo still gets its main mechanisms. Ceiling:
`agenda_max`, opt-in, applied after ranking. `agenda_exclude` keeps its meaning.

### The bill is printed before synthesis

`prepare` renders the agenda with each unit marked deep or area and the clause that decided
it, and prints the estimated cost at the measured rate (deep page: about 55k output tokens
and 2.5 minutes of agent time; area page: a fifth of that). The maintainer trims with
`agenda_exclude` or `agenda_max` with the price of each cut visible.

## What the rule produces

```
                       units   modules>=5   fanin>=20   either (deep pages)   today
torch_tpu              80      25           13          27                    27
PyTorch, Python shards 145     105          91          124                   24
```

```
                        torch_tpu                      PyTorch shards
area pages              6                              about 10
deep pages              27 (unchanged)                 124 (from 24)
clean ingest, agent     about 1.2 h (today plus areas) about 5.5 h
output tokens           about 1.7M                     about 7.5M
incremental             pages whose citations moved    same; per release, not nightly
```

## Why this shape

- **A per-unit test, not a page count.** The count falls out of the repo instead of being
  chosen. A fixed number is right for one repo size only; 24 starves PyTorch and would
  bloat a 30-module library if raised.
- **Over the planner's tree, not over index shards.** Shards exist to keep scip-python
  inside memory; C++ has none and small repos have none. The planner's units are
  directory-shaped, ranked by external fan-in, and already reviewed in the agenda step.
- **Two clauses, or-ed.** A size-only rule drops the units readers ask about most:
  `ops/macros` (4 modules, 638 external referrers), `eager::tensor_to_buffer` (4, 531),
  and on PyTorch `_prims_common` (2, 904), `_inductor::sizevars` (3, 318), `_logging`
  (4, 212), `_decomp` (3, 203). A fan-in-only rule (20 or more) gives torch_tpu 13 pages
  and rewards hubs again.
- **Not a share rule.** "Units holding 90 percent of the total score" gives 17 on
  torch_tpu and 68 on PyTorch and is dominated by hubs (`error_utils` alone has a fan-in
  of 1,939): rewarding what everything depends on is the failure the planner was built to
  avoid.
- **Not symbol density.** A kernel directory and a compiler-pass directory of equal size do
  not deserve equal prose; the fan-in ranking already encodes that difference.
- **Why 5 and 20.** Five modules is a quarter of the split bucket, the smallest group that
  reliably has an internal mechanism rather than a list of siblings. Twenty external
  referrers is above torch_tpu's 75th percentile (9) and below PyTorch's median (34), so on
  a small repo only the true API surfaces cross it and on a large one most units do, which
  is the intended behaviour: prose grows with how interconnected the code is, not with its
  byte count. Both are defaults to revisit after one PyTorch ingest under the rule.
- **The floor and the ceiling.** The floor protects repos where no unit reaches 5 modules.
  The ceiling stays opt-in because the bill, not a silent cap, is what should make a
  maintainer trim; a full PyTorch index with C++ would land in the hundreds of pages, and
  that should be a decision with a price attached.
- **Fast-moving repos.** More pages means more rebuilds on `--ref`: a week of torch_tpu
  drift touched 16 of 27 pages. A repo ingested nightly should keep its deep count modest
  and lean on area pages, which change rarely; release-cadence repos can afford the full
  count. The bill makes this trade visible too.
- **Coverage is chosen twice on large repos, deliberately.** First by `index_shards`,
  which is where a maintainer says which subtrees exist for the wiki at all, then by the
  agenda with its bill.

## What does not change

Unit formation and ranking (`subsystems.py` split, flat split by reference community,
`fanin_external * 2 + internal_edges`), packets, the synthesis prompt for deep pages, verify,
lint, `changes/`, the catalog and coverage floor. `agenda: modules` (legacy) keeps its own cap.

## Order of work

1. Re-plan the PyTorch silo with `agenda: subsystems`; its current 24 pages are the wrong
   pages before they are too few.
2. Implement the rule, the floor and the bill in the planner and the agenda render.
3. Add the area tier: scaffold from the planner, a short synthesis prompt, linked from the
   overview and listed by `assemble`.
4. One PyTorch ingest under the rule; adjust the two thresholds from what the agenda shows;
   then make it the default for new silos.

## Sources

Planner runs on 2026-09-15 over the torch_tpu index (Python and C++) and the PyTorch
Python-shard index of the private autoresearch wiki; per-page costs from the measured clean
torch_tpu ingestion of 2026-09-05; page-open counts from the survey wiki's
`wiki/notes/catalog-usage-at-query-time.md`.
