# Changelog

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
