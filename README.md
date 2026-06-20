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
npm i -g @sourcegraph/scip-python         # Node-based indexer (build prereq)
pip install -e .                          # the `wikify` CLI
python -m grpc_tools.protoc -I vendor --python_out=wikify vendor/scip.proto  # gen scip_pb2.py
```

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
