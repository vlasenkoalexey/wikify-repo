# Changelog

## 0.4.0 - 2026-09-25

### Changed
- **Citations link the source line, titled with the index key** (docs/citations.md). When a
  silo has a `source_url`, a citation is ``[`Sym`](<source_url>/<path>#L<line> "<module>#<Sym>")``
  instead of `../catalog/<module>.md#<Sym>`: a reader with repository access lands on the code
  at the pin, and a reader without it (a private repo) greps the title in `catalog/symbols.tsv`
  for path, line, signature and doc. The `index` tier no longer needs catalog pages for links
  to work; the 0.3.5 `anchors` tier stays available but nothing points at it.
- `finalize` rewrites every citation in the silo from the index it just wrote, so hrefs track
  the pin; packets emit the new form in their `cite:` lines.
- Lint, fix, verify, OKF, relink, coverage and the index's area grouping resolve both forms
  through one parser (`cite.key_of`). Dead-citation messages name the key.
- Verify cache keys reduce each citation to its key, so a link rewrite does not re-open
  verified claims; caches from 0.3 still match.
- Retrieval block: citations described in the new form; never WebFetch source links; for a
  body read a local checkout, else one `gh api` call at the pin, else answer from the row.

### Added
- `wikify source-links [slug]`: re-point a shipped silo's citations at source from its
  shipped `symbols.tsv`, without a SCIP index or checkout. The migration for silos built
  before 0.4; idempotent.

## 0.3.5 - 2026-09-25

### Fixed
- **Citations land in a browser.** A citation `catalog/<module>.md#<QualifiedName>` resolved
  for the linter and for agents through the front-matter symbol map, but the rendered page had
  no element with that id, so every fragment was dead (in the `index` tier the page does not
  exist at all). The `anchors` and `full` tiers now put an `<a id="<QualifiedName>">` on every
  symbol, taken from the same anchor table as the front matter, so the two cannot drift. The
  collapsed `anchors` page, previously a single note, is now a jump table: one row per symbol
  with its anchor and a link to its source line. A citation followed on GitHub lands on the
  symbol's row, and one more click opens the exact line. GitHub folds ids to lowercase and its
  fragment resolver lowercases too, so mixed-case anchors resolve. Anchors that differ only by
  case (a nested class `Config` and an attribute `config`; 24 of 10,462 in torchtitan) share one
  id there and land on whichever comes first; case-sensitive renderers land exactly.
- **Full catalogs no longer drop nested classes that share a short name.** Classes were keyed
  by their bare name, so `SelectiveAC.Config` and `FullAC.Config` in one module overwrote each
  other: all but one vanished from the page and their members were merged under the survivor.
  Classes are now keyed, and headed, by their qualified name. Members of a class that is not
  itself rendered (for example one defined inside a function) get an "Other members" section
  instead of appearing nowhere. `full` is the tier an existing silo keeps when its config sets
  no `catalog:`, so such silos render the missing classes on their next `finalize`.

## 0.3.4 - 2026-09-17

### Changed
- **Recipes respect the search tool's line cap.** Hubs have hundreds of callers (107 of
  7,144 callees on torch_tpu exceed 50) and an agent's search tool shows about 50 lines,
  truncating silently. The edge-file header, the map and the retrieval blocks now say: count
  first (`grep -c`), narrow a hub's callers by directory (`cut -f2 | cut -d/ -f1-2 | sort |
  uniq -c`), then list with `| head -20`; a "by area" lookup (`'^<dir>/.*#<Name>\t'`) is
  added. Header examples are picked from symbols with 10 to 300 callers so they demonstrate a
  bounded result.
- **Private source links.** When `source_url` is a web URL, the map says the links are
  permalinks for readers with repository access and must not be fetched; the row carries the
  path, line, signature and doc line. The retrieval blocks carry the same sentence.

## 0.3.3 - 2026-09-17

### Fixed
- With `index_shard_depth: 0` the module map collapsed into one section; sections are now
  always by directory, each linking the joined files with the section's own row and edge
  counts.

## 0.3.2 - 2026-09-17

### Changed
- **Index recipes are complete and correct.** The bare-name recipe matched only free functions
  (`#forward`); it is now `[#.]<Name>` so `Class.forward` matches too. Symbol headers add "what
  calls" (the callee column of the edge list), "one module" (rows by path) and "to prose"
  (column 8, the concept pages citing the symbol); edge headers give both directions with a
  real anchor; every header and the map say "grep it by anchor; never read it whole". The map
  carries the five recipes as a code block.
- **Retrieval blocks are a third shorter** (`wikify setup` / `wikify init`): five bullets,
  the same recipes, no mention of catalog pages (a `catalog: full` silo still has them; the
  index is the documented path). The README copy is regenerated.

## 0.3.1 - 2026-09-17

### Added
- **`index_shard_depth`** (config): how many leading path components make one index file.
  `2` (default) keeps `catalog/symbols/<dir>.tsv`; `0` writes the two-file layout
  `catalog/symbols.tsv` + `catalog/edges.tsv`. Switching layouts removes the other one's
  files. Headers and recipes follow the layout.
- **The module map links the index files**: an `## Index files` table (shard, symbols file
  with row count, edges file with edge count) and an `Index:` line under every section.

### Fixed
- `finalize` reported the number of symbols as the number of index rows; rows are one per
  anchor (overloads share one), so the message now says "N symbols in M rows (K overloads
  folded), E caller edges", and shard headers say "rows".
- The silo `log.md` linked `changes/<ref>.md` on a same-ref rebuild, where no change page is
  written; the link is now added only when the pin actually moved.

### Note
- Rebuilding a silo at the same pin with 0.3.x flags pages that cite overloaded functions
  as stale once: merging the duplicate symbol records changed those symbols' stored
  signature, which is part of the body hash. Keep the verified content and re-finalize.

## 0.3.0 - 2026-09-15

### Changed
- **The catalog is a symbol index; pages are a rendering** (`docs/catalog-index.md`).
  `finalize` now writes `catalog/symbols/<dir>.tsv` (one row per documentable symbol:
  anchor, path, line, kind, rank, body hash, caller count, citing concept pages; `sig` and
  `doc` in the `full` profile), `catalog/edges/<dir>.tsv` (one caller edge per line,
  complete, unfiltered) and `catalog/index.md` (the module map), sharded by two directory
  levels, each shard headed by the columns and the three grep recipes. A new config key
  `catalog: index | anchors | full` decides whether per-module pages are rendered on top;
  a fresh silo defaults to `index`, a silo that already has pages in state keeps `full`
  until its config says otherwise. `index_profile: nav | full` picks the column set.
  Measured over 149 query sessions, agents opened catalog pages zero times when the source
  was on disk; a 240-session controlled test found no difference between full and
  collapsed pages with source present.
- **Lint resolves citations through the graph** (`coverage.symbol_index`), not through
  catalog front matter, so every tier lints the same; front matter remains a fallback for
  callers without a graph. Citations may carry the source location as their link title
  (`[Sym](../catalog/m.md#Sym "path:L12")`); packets now emit it, lint strips it.
- **Prose budget: a per-unit rule and an area tier instead of the 24-page cap**
  (`docs/prose-budget.md`). A planned unit gets a deep mechanism page when it has at
  least 5 modules or at least 20 outside referrers (`agenda_deep_modules`,
  `agenda_deep_fanin`; floor 8); every other unit becomes a section of a new
  `areas/<area>.md` page, one per top-level area, with placeholders for two short prose
  sections and a regenerated block (units, entry points, small units). `agenda_max` is now
  an opt-in ceiling on deep pages, never a truncation of the plan. The agenda render shows
  each unit's tier and the clause that decided it, plus the bill at the measured per-page
  rate. On torch_tpu the rule reproduces the 27 pages of the capped plan; on PyTorch's
  shards it plans 124 instead of 24. New prompt `prompts/area.md`; skill steps renumbered.
- **Retrieval block** (`wikify setup` / `wikify init`) rewritten to describe the measured
  routing: concept and area pages for mechanism, the citation path for source, the symbol
  index by anchor for existence and callers, the map for orientation; grep, never read.
- The silo `index.md` lists area pages first and points the coverage section at the index;
  `wikify agenda --max` is the deep-page ceiling.

### Fixed
- **Empty-path shard documents** (`scip_index._repair_doc_path`): a file-level
  `--target-only` shard emits its own file with an empty `relative_path`; the repair
  returned early because `project_dir / ""` exists, filing the module's functions under
  module `""` (a `catalog/.md` page). Repaired at merge time and, for indexes built before
  the fix, at graph build (`build_graph(repair_root=...)`).
- **Only git-tracked files are indexed** (`build_graph(only_paths=...)`): untracked
  `.ipynb_checkpoints/` and scratch files on a working copy no longer become modules.
- **`symbol_base` cut at a descriptor boundary**: when every symbol on a page started with
  `_` the common prefix swallowed the underscore (`INTERNAL_PREFIX` for `_INTERNAL_PREFIX`).
- **Signatures**: the implementation's signature is preferred over the first `@overload`
  stub (scip-python emits one SymbolInformation per `def`; they are now merged, docstring
  included); the positional-only `/` marker is restored (it flattened to `fn=None,, *`);
  C++ declarations are read from the source when the indexer emits none
  (`source.read_signature`, display-only so recorded hashes stay valid).
- **Inherited enumerator comments**: scip-clang attaches the comment before an enum's
  first enumerator to every undocumented enumerator (`go/keep-sorted start` on 643 rows);
  documentation identical across three or more members of one type is dropped.
- **Catalog cross-links** (`wikify/coverage.py`): `uses` / `used by` lists no longer link
  namespace or macro monikers. They are not documentable symbols, so they have no catalog
  anchor, and their "home" file (often a header with no other symbols) may have no catalog
  page; on a C++ repo this left the same dead link on hundreds of pages.

### Added (from the unreleased line)
- **Diagram checks** (`wikify/diagrams.py`): a Mermaid structural floor (diagram type, balanced
  brackets, node band, non-empty) and a lint-checked `Legend:` under flowcharts mapping node ids
  to catalog citations; warnings from `finalize` and `lint`, never a gate. Prompts now ask for
  diagrams chosen by the question, nodes that are symbols, real edges, <= 20 nodes, and a legend.
- **`wikify setup` / `wikify doctor`**: the two install scripts are folded into the CLI. The
  skills ship inside the package (`wikify/skills/`, `.agents/skills/` symlinks to them) so
  `pipx install git+…` works without a checkout; `setup` installs the skill at user level
  (`~/.claude/skills`) and/or into a project's `.agents/skills`; indexers install into a user
  prefix (`~/.wikify/vendor`) on demand at `prepare` or via `--indexers`; `init --with-skill`.
  The generated `scip_pb2.py` is committed with `protobuf>=5.29,<7`. `setup --project` also injects
  the host-wiki retrieval block into `SCHEMA.md` or the agent instruction files, idempotently;
  `--no-skill` writes the block without copying the skill into the project.
- **In-repo layout** (`wikify init`): the wiki can live inside the repository it documents —
  `wikify.md` at the root, the repo itself as the source pinned at HEAD, the silo flat at
  `wiki/`, the cache at `.wikify/`, and a marker-delimited block injected into `CLAUDE.md` /
  `AGENTS.md` telling agents where the wiki is and how to update it. Every command then runs
  from the repo root without a slug. Host-wiki projects are unchanged.
- **Version-to-version changes** (`wikify/changes.py`): on a `--ref` bump, rebuilt pages'
  packets carry the commits that touched their cited files (`## Since last ingest`, so the
  page can say what changed and why in the authors' words); `finalize` writes
  `changes/<ref>.md` (pages affected, commits by page, forge links or `git show` hints) and
  one silo `log.md` line per ingest; the index gains a `## Changes` section.
- **Moves are relinked, not rebuilt** (`wikify/relink.py`, `diff.detect_moves`): a symbol with
  an unchanged body in a new file, or a one-to-one rename with the same qualified name and
  body, no longer invalidates the pages citing it. `prepare` rewrites their citations, packet
  subgraphs and verify-cache evidence and folds the move into state (a new `paths` map);
  the plan reports a `relink` bucket. `finalize` prunes catalog pages for modules that no
  longer exist, so a stale link can no longer pass lint silently.

## 0.2.0 - 2026-09-05

The page unit becomes the subsystem, packets become citable, verify becomes incremental.

### Added
- **Subsystem planner** (`wikify/subsystems.py`, `wikify agenda`): the agenda is a table of
  contents of directory-shaped units (tree split to a module budget, flat directories split by
  reference community), ranked by external fan-in, seeded from entry points and hubs. Config
  `agenda: subsystems | modules`, `agenda_max`, `agenda_exclude`; seed form
  `(subsystem: <prefix>)`, including `dir::stem` community units; `prepare --agenda`.
  A fresh silo plans by subsystem; an existing silo keeps module discovery until told otherwise.
- **Scope-aware packet budget**: every unit member is a candidate, 75% of the budget is reserved
  for members, outside symbols are capped per module; packets carry a `## Scope` block and mark
  outside symbols. On torch_tpu: 8/60 in-unit symbols -> 45/60.
- **Incremental verify**: verdicts memoized per claim on prose + cited-symbol body hashes
  (`.cache/verify/`), `wikify verify --record`, `--all`, deterministic 5% re-sample.
- **Front door**: `description:` front matter rendered in the silo index, concept table grouped
  by source area, task-shaped routing in the overview, agenda `## Concepts` block for renaming
  units at confirmation (a config entry replaces the planned units it covers).
- `finalize` warns when `overview.md` is missing or links to a page that does not exist.
- Skill: docs/README gap check at confirmation (names, `aliases:`, missed units, cross-unit
  flow concepts, "documented, not found"); README-led topics must route in the overview.
- **OKF v0.2-compatible output** (`wikify/okf.py`): `finalize` stamps `generated` and
  file-level `sources` on concept pages (generated on doc-concepts and the overview), drops
  `status: fresh`; `verify --record` stamps `verified` once every claim on a page holds; the
  silo `index.md` declares `okf_version: "0.2"` and the pinned snapshot; three shape warnings.
- `wikify --version`.

### Fixed
- `packet`: no `cite:` link for non-documentable kinds (namespaces, macros).
- `scip_index`: `.pyi` module paths resolve for compiled-extension stubs.

### Docs
- design.md decisions log: subsystem unit; packet budget; docs name the units; verify memoized.
- implementation.md 10.3 (scope budget), 10.4 (verify cache), 10.11 (planner, front door).

## 0.1.0

Initial release: SCIP-grounded ingest (Python, C++ via bazel, TS/Go/Rust on demand), packets,
citation lint gate, coverage catalogs, adversarial verify, connect, docs mode.
