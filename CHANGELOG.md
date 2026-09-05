# Changelog

## Unreleased

### Added
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
  the host-wiki retrieval block into `SCHEMA.md` or the agent instruction files, idempotently.
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
