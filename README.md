# wikify-repo

Ingest a code repo into a grounded, lint-clean **markdown** wiki an agent can
answer internals questions from. Deterministic Python does the grounding (SCIP
symbol graph, packets, citation lint); one LLM-in-the-loop step does concern
synthesis. v1 is standalone and Python-only — see `docs/design.md` (what/why) and
`docs/implementation.md` (how).

## Status — Phase 1 (MVP) complete

One pure-Python repo, end to end: `scip-python → symbol graph → packets →
[agent synthesis] → citation lint → markdown wiki`. Validated on `torchtitan`
(see `tests/golden/torchtitan.md`).

## Install (dev)

```bash
conda activate torchtitan_py312          # or any Python 3.11+ env
pip install -e .                          # the `wikify` CLI
scripts/setup-vendor.sh                    # fetch indexers (scip-python, scip-clang) + gen scip_pb2.py
```

`scripts/setup-vendor.sh` is idempotent and replaces the old manual steps: it
`npm i`s **scip-python** (Python indexer), downloads the pinned **scip-clang**
binary (C++ indexer — ~130 MB, *not* committed: it exceeds GitHub's 100 MB/file
limit), and generates `wikify/scip_pb2.py` from `vendor/scip.proto`. The C++ tool
is only needed for mixed C++/Python repos; Python-only ingestion works without it.

### C++ ingestion prerequisite (mixed repos only)

scip-clang indexes against a `compile_commands.json`. For a repo like pytorch you
must first produce one (and any generated headers) with the project's own build —
e.g. a CMake configure with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` plus the codegen
targets. wikify consumes that file (config key `compile_commands:`); it does not
run your build for you.

## Use

```bash
wikify prepare  torchtitan      # Stages 0-4: index, graph, packets, print plan (no model)
#   → agent writes one page per packet under wiki/<slug>/ per skills/prompts/synthesis.md
wikify finalize torchtitan      # Stage 6: citation lint (hard gate), assemble index, update state
wikify plan     torchtitan      # dry-run reconcile delta
wikify lint     torchtitan      # re-run the citation gate alone
```

The reconcile is idempotent: re-running `prepare` with no change is a no-op;
adding a concern to `config/<slug>.md` builds only that packet.

## Architecture (the Python ↔ LLM split is hard)

| Stage | Module | Who |
|---|---|---|
| 0 acquire & pin | `acquire.py` | Python |
| 1 SCIP index → graph | `scip_index.py`, `graph.py`, `monikers.py` | Python |
| 2 reconcile diff | `diff.py`, `state.py`, `source.py` | Python |
| 4 evidence (tests) | `evidence.py` | Python |
| — packet build | `packet.py`, `slug.py`, `config.py` | Python |
| 5 concern synthesis | `skills/…/SKILL.md` + `prompts/synthesis.md` | **LLM agent** |
| 6 citation lint + assemble | `lint.py`, `assemble.py` | Python |

The risky foundation is the **SCIP-occurrence → callers/callees** heuristic (SCIP
has no "call" role); it's reference-scoped, not true call resolution, and is
validated by `tests/test_callers_callees.py`.

## Tests

```bash
python -m pytest          # all module tests (callers/callees needs scip-python on PATH)
```
