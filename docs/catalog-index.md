# Catalog as an index: design change and rationale

Status: realized in wikify-repo 0.3.0 (2026-09-15). It amends SCHEMA.md invariants 1
and 7, supersedes the decisions-log entry "The catalog is a navigation surface, not a
symbol dump" in `design.md`, and is specified in `implementation.md` section 10.18 (with
the small deviations recorded there). Nothing in the prose pipeline (packets, synthesis,
verify, changes) was touched.

## Summary

The module catalog was designed as a query-time navigation surface that also gates the
build. Measurements show the reverse: it is a build-time artifact (citation resolution,
coverage floor, per-symbol hashes) whose query-time form is wrong. Agents do not open
catalog pages when the source is on disk, and when the source is absent the pages help a
little only where they carry content (a signature, a caller list) that they mostly lack.
At PyTorch scale the catalog is 99 percent of a silo's bytes.

The change: keep everything the catalog computes, ship it as a greppable symbol index
(TSV, sharded by directory) plus one map page, resolve citations through the graph rather
than through page front matter, and make per-module pages an opt-in rendering for repos
whose source will not be on disk. No database, no query tool, no skill on the consumer
side: the files stay the interface.

## What the catalog is today

Stage 6b (`wikify/coverage.py`) emits one markdown page per source file. Each page has a
YAML front matter `symbols:` map (anchor to SCIP descriptor, prefix-factored into
`symbol_base:`) that the linter reads to resolve `../catalog/<module>.md#<Symbol>`
citations, then a body: module values, classes with per-member detail
(`name(params) - Lnnn - docstring line`), functions, and `uses` / `used by` lists that are
capped at 40, test-filtered and importance-ranked. The per-symbol body hashes that `verify`
and the incremental rebuild depend on live in `.cache/state/<slug>.json`, which is never
shipped.

Sizes on two real silos:

| silo                        | symbols  | catalog pages | catalog text | front matter only | concept pages |
|-----------------------------|----------|---------------|--------------|-------------------|---------------|
| torch_tpu (C++ and Python)  | 14,706   | 560           | 3.3 MB       | 1.2 MB            | 27, 0.5 MB    |
| PyTorch (Python shards)     | 206,882  | about 5,700   | 56.5 MB      | 13.2 MB           | 24, 0.6 MB    |

Prose does not grow with the repo (the agenda is capped, see the open item at the end);
the catalog grows linearly with symbols, so for PyTorch the catalog decision is the whole
shipping decision.

## Evidence

Four sources, all recorded in the survey wiki (`codebase-cartography-wiki`):
`wiki/notes/catalog-usage-at-query-time.md`, `wiki/notes/catalog-value-test.md`,
`plans/catalog-value-test/results.md`, and the review of a shipped torch_tpu silo.

### 1. Corpus measurement, 2026-09-12: agents never open catalog pages

Every agent transcript on one machine was scanned: 1,023 transcripts, 198 sessions that
read a wiki, of which 149 were query sessions over wikis holding 7,600 to 10,900 catalog
pages (the PyTorch silo among them) with the pinned source on disk.

- Catalog pages opened: 0 in 149 sessions. Source files read or grepped: about 1,700.
- Catalog links rendered in answers: 228. Followed: 0.
- Pages that were opened: overview, index, log and concept pages, 411 times.

The instructions were not the cause; they pointed at the catalog explicitly. The mechanism
is structural: a citation already names the source path, so agents treat the anchor path as
the source path; `grep -n ... -A 25` on the source returns the body, which the catalog
page never has; the only content grep cannot produce, callers, was incomplete (3 of 6 call
sites in a hand check) and the instructions said so; and both harnesses are trained on
grep-and-read.

### 2. Controlled test, 2026-09-12: the catalog is a weak source substitute

Four arms on copies of the torch_tpu silo (27 concept, 15 doc-concept, 560 catalog pages):
full catalog or collapsed to anchor maps, crossed with pinned source present or absent.
Sixty questions with git-verified keys (20 symbol-level, 20 mechanism, 20 caller and impact;
half C++ and half Python targets), one fresh sandboxed `claude -p` session (claude-opus-5)
per question and arm, 240 sessions, every tool call logged, answers graded by an LLM judge
against must-have facts with ten hand checks that all agreed. The instructions were a
downstream knowledge base's own routing block, which says to retrieve from the wiki
before reading source.

```
arm  catalog    source   must-fact %  correct/60  abstain  sessions opening a catalog page  usd/session
A    full       present  85.5         37          0        41 of 60                         0.52
B    full       absent   57.3         11          6        46 of 60                         0.56
C    collapsed  present  84.7         39          1        38 of 60                         0.50
D    collapsed  absent   52.8          8          11       41 of 60                         0.51
```

- A vs C, no difference. With source present the full catalog changes neither accuracy nor
  cost; per question, 8 wins each way and 44 ties. On C++ targets alone: 85.7 vs 83.3.
- B vs D, small gain. Without source the full catalog adds 4.5 points and halves abstentions.
  The gain is on symbol questions (+15) and Python targets (+7.4), whose entries carry a
  signature; on C++ targets, whose entries carry only a name, a line and one doc line, it is
  1.6 points.
- A vs B, source matters most. Removing source costs 28 points with the full catalog present;
  caller questions fall from 94.9 to 39.2 because free functions have no `used by` list.
- Access is not the problem. Catalog pages were opened in 70 percent of sessions in every
  arm (the routing block was followed). In B the symbol sessions reached the right module's
  page 19 times out of 20 and still scored 70 percent: the page lacked the fact. The planned
  symbol-tool and module-map arms were therefore not run; over the same data they would
  print the same missing content.

### 3. Scale, from the PyTorch silo

- 206,882 symbols, about 5,700 pages, 56.5 MB of catalog text, 7.1 MB gzipped, 13.2 MB of
  front matter alone.
- Name ambiguity: 59 percent of symbols share their name with another symbol; 2,105
  `__init__`, 493 `forward`, 211 `run`. A source grep for `def forward` returns 840 hits.
- Latency is not the issue: grep over the 56 MB of catalog text takes 0.02 s, grep over the
  21,000-file source tree 0.06 s.
- Caller edges: about 1.7 per documentable symbol on torch_tpu, heavy-tailed (one hub has
  1,712 callers).

The larger the repo, the more a lookup needs a key (module path plus qualified name) and
the less a bare name helps, and the more a file that must be grepped but never read needs
to be bounded.

### 4. Review of a shipped silo, 2026-09-14

A reviewer of the torch_tpu silo found, all confirmed against the raw SCIP indexes:

- a page named `catalog/.md` with an empty module: a file-level `--target-only` shard
  produced a SCIP document with an empty relative path, so the module's functions and
  classes were filed under module `""` while its two constants stayed on the real page;
- the leading underscore stripped from `_INTERNAL_PREFIX` in the front matter:
  `symbol_base` is `os.path.commonprefix` over monikers, character-wise, so when every
  symbol on a page starts with `_` the underscore joins the base;
- catalog pages for `.ipynb_checkpoints/` files: the ingest ran on a working copy with
  untracked Jupyter checkpoints, and code indexing does not skip them (docs discovery does);
- `go/keep-sorted start` as the doc line of 643 enumerators: scip-clang attaches the one
  comment before the first enumerator to every undocumented enumerator; wikify prints it;
- `jax_op(name: str, fn: None = None,, *, ...)`: the first `@overload` was rendered instead
  of the implementation, and scip-python renders the positional-only `/` as an empty line
  that flattens to `,,`;
- "does this anchor exist?": catalog anchors live in front matter, so a browser rendering
  shows no target; only an agent reading the file resolves them.

## What changes

### The catalog becomes an index; pages become a rendering

Files under `wiki/<slug>/catalog/`:

- `symbols/<top-level-dir>.tsv`: one row per documentable symbol, sorted by path. Columns:
  `anchor` (the citation target without `catalog/` and `.md`, e.g.
  `torch_tpu/eager/op_dispatcher.h#DispatchOp`), `path`, `line`, `kind`, `rank` (the
  existing importance score), `hash` (the body hash from state), `callers` (count), `pages`
  (concept pages citing it, semicolon-separated), and in the full profile `sig` and `doc`.
- `edges/<top-level-dir>.tsv`: one caller edge per line, `callee<TAB>caller`, complete,
  free functions included, no cap and no test filter (filtering is a rendering choice).
- `index.md`: the map. Every module with its symbol count, entry points ranked by the
  importance score, and the concept pages that cite into it. Deterministic now; one purpose
  line per module is a later, cheap synthesis pass.
- `<module>.md` pages only when `catalog: full`.

Every shard starts with a header that teaches the format, so the first grep an agent runs
shows the columns and the recipes:

```
# wikify symbol index: torch_tpu @ 4c0c7c57, shard torch_tpu/eager, 312 symbols
# columns: anchor  path  line  kind  rank  hash  callers  pages  sig  doc
# one symbol:  grep -P '^torch_tpu/eager/op_dispatcher.h#DispatchOp\t' catalog/symbols/*.tsv
# its callers: grep -P '^torch_tpu/eager/op_dispatcher.h#DispatchOp\t' catalog/edges/*.tsv
# by name:     grep -P '#DispatchOp\t' catalog/symbols/*.tsv | sort -t$'\t' -k5 -nr | head
```

Why TSV and not markdown: the cell delimiter. Signatures contain `|` (`Sequence[int] | None`,
C++ `||`), and escaping it as `\|` corrupts the grep output an agent pastes into its next
command. Tabs never occur inside a signature. Why sharded: at 60 MB a single file greps in
milliseconds but a default `Read` pulls 2,000 lines into context; a shard bounds that, and
a scoped grep becomes the normal query. Why two profiles: with source installed the
signature and doc line are redundant (the agent reads the source), and the navigation
profile is a third of the size; the full profile is for source-absent repos.

Config: `catalog: index | anchors | full`. `index` is the default for new wikis. `anchors`
is today's collapsed page (front-matter map plus source link) for consumers that need the
`.md` targets to exist. `full` renders per-module pages from the same data. The existing
`coverage_collapse` and `coverage_exclude` globs keep working inside `full`.

### Citations resolve through the graph

Lint rule 1 currently opens the catalog page's front matter. It will resolve the anchor
against the graph index that `coverage.py` already builds (module from the link path,
qualified name match), which works in every tier and removes a file read per citation.
The citation grammar `../catalog/<module>.md#<QualifiedName>` is unchanged, so no page is
rewritten. Each citation gains the source location as its link title
(`"torch_tpu/eager/op_dispatcher.h:L262"`), which is the hop agents already make by hand.

### The retrieval block describes what agents do

The block `wikify setup` injects currently says "retrieve from its wiki instead of reading
source" and routes symbol questions to `catalog/`. Instructions did not move the open rate;
content and keys did. The block will say:

- concept pages and the overview for mechanism and orientation;
- the citation path is the source path: read the source at the pin for bodies;
- the symbol index by anchor for "does X exist", "where is X", "who calls X"; grep it,
  sort by rank, head; never read it;
- the map for choosing an area before grepping;
- catalog pages only in silos built with `catalog: full`, for repos whose source is absent.

### Content fixes, ordered by measured effect

1. C++ signatures. The SCIP index has them; Python entries carry them and scored ten points
   higher in the source-absent arm.
2. Complete caller lists, free functions included. Caller questions lost 56 points without
   them.
3. Prefer the implementation signature over the first `@overload`; render the positional-only
   marker; drop enumerator doc lines that are identical across every member of an enum.

### Hygiene fixes, from the review

1. Index only git-tracked files, which removes checkpoint directories and any other
   untracked junk at once.
2. Fix the file-level shard that yields a SCIP document with an empty relative path (or
   expand `index_shards` globs to directories only).
3. Cut the shared moniker prefix at a descriptor boundary (`/`, `#`), not at a character.

### Invariants amended (SCHEMA.md), when this lands

- Invariant 1 becomes: plain text is the only shipped product, markdown pages plus TSV index
  files; no SQLite, no JSONL, no graph database. The front-matter `symbols:` map was already
  data inside markdown; the index makes the data explicit and greppable.
- Invariant 7 keeps the anchor grammar and the single source of the anchor format
  (`coverage.catalog_ref` / `qualified_name`), but the resolution table is the graph at build
  time and the index when shipped, not page front matter.

## What does not change

Acquire, indexing, the subsystem planner, packets, synthesis, adversarial verify,
`changes/`, relocation on moves, the coverage set-difference (computed from the same table),
the finalize order (emit, then lint), and the standalone-silo distribution model. The
consumer still needs nothing installed.

## What is deliberately not done

- No database and no server. The queries are one or two hops (lookup, callers, module
  listing); a versioned artifact must be a diffable, reviewable file; every graph engine
  worth using needs a server. If a derived SQLite is ever wanted for joins or transitive
  impact, it is built from the TSV in a second and never shipped.
- No `wikify symbol` command and no query-time skill. Everything such a command would
  compute (rank, cap, lookup by key) is precomputed into the rows and the header; the
  consumer keeps needing only grep. The test's tool arm was skipped for the same reason:
  content, not access, decided the answers.
- No per-symbol prose. Synthesis budget goes to concept pages and, later, the map's purpose
  lines, which are the pages that get opened.
- No further rewrites of routing text as the lever for catalog use.

## Sizes and costs

```
                              torch_tpu          PyTorch (Python shards)
today: full pages             3.3 MB             56.5 MB
anchors only                  1.2 MB             13.2 MB
index, navigation profile     ~1 MB              ~20 MB (about 3 MB gzipped)
index, full profile           ~2 MB              ~60 MB (about 8 MB gzipped)
edges                         ~0.3 MB            ~40 MB (about 5 MB gzipped)
```

Emitting the index is linear in symbols: seconds for torch_tpu, well under a minute for
PyTorch, against a minute or two for 5,700 pages today. Prose cost is unchanged.

## Validation before `index` becomes the default for shipped wikis

The sandboxed harness and the 60-question set from the controlled test are reusable:

1. Index with the two content fixes, source present, against arm A; source absent, against
   arm B. If the source-absent gain does not move with signatures and complete callers, the
   `full` tier can be dropped rather than kept as an option.
2. A migration-shaped question set on the PyTorch silo ("is this op supported", "who calls
   this", "where is this defined"), where the ambiguity numbers say keys should matter most.

## Open item, not part of this change: prose volume does not scale with the repo

| silo                    | documentable symbols | concept pages | symbols cited by a concept page |
|-------------------------|----------------------|---------------|---------------------------------|
| torch_tpu               | 13,335               | 27            | 765 (5.7 percent)               |
| PyTorch, Python shards  | 192,586              | 24            | 459 (0.2 percent)               |
| PyTorch, demo wiki      | 87,136               | 24            | 480 (0.6 percent)               |

The subsystem planner caps the agenda at `DEFAULT_MAX_SUBSYSTEMS = 24` per repo
(`agenda_max` overrides it), at parity with the earlier discovery cap. That is the cost
control: a concept page costs about 55k output tokens and two to three minutes of agent
time plus verification, so 24 pages is the same bill for any repo. On PyTorch it means 24
pages across the eleven configured shards, about two per subtree. A budget per index shard
(or per top-level directory) with the same default would scale the bill with the coverage
the maintainer chose: PyTorch's configuration would yield on the order of a hundred
pages and five to six hours of agent time per clean ingest, incremental afterwards. Resolved the same day
in `prose-budget.md`: a per-unit rule (5 modules or 20 external referrers) with an area-page
tier, a floor of 8, `agenda_max` as an opt-in ceiling, and the bill printed before synthesis.
Recorded here because the size comparison above would otherwise mislead.
