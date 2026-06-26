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

## Install

**Prerequisites:** Python ≥ 3.11, Node.js + npm (for the `scip-python` indexer), and `git`.
The C++ indexer (`scip-clang`) is downloaded automatically, and only if you ingest C++.

```bash
git clone https://github.com/vlasenkoalexey/wikify-repo
cd wikify-repo
pip install -e .            # the `wikify` CLI (deps: protobuf, pyyaml, typer, gitpython)
scripts/setup-vendor.sh     # one-time: install scip-python + generate wikify/scip_pb2.py
wikify --help               # verify it's on PATH
```

That's the whole install — a clone and two commands. Use any Python ≥3.11 env (conda, venv,
or pipx-managed). `setup-vendor.sh` is idempotent, so re-running is harmless.

> **Why clone, not `pip install git+https://…`?** Two files can't ship in the wheel:
> `scip_pb2.py` is generated from `vendor/scip.proto` **against your local protobuf**
> (committing it would cause gencode/runtime version mismatches), and the `scip-clang`
> binary is ~130 MB (over GitHub's file limit). `setup-vendor.sh` produces both. So a
> clone is the reliable path; a bare `pip install git+…` would import-fail on the proto.

## Quick start — ingest a repo

1. **Say what to ingest** in `config/<slug>.md`:
   ```markdown
   ---
   slug: myrepo
   repo: https://github.com/owner/myrepo      # or a local path
   ---
   ## Concepts          # optional — discovery auto-seeds from code centrality if omitted
   ```
2. **Build it:**
   ```bash
   wikify prepare  myrepo     # index → symbol graph → packets + reconcile plan (pure Python, no model)
   #   → an LLM agent writes one concept page per packet (install the skill below)
   wikify finalize myrepo     # citation lint (hard gate) + catalogs/coverage + assemble wiki/myrepo/index.md
   ```
   Output is `wiki/myrepo/`. Reconcile is **idempotent**: re-running `prepare` with no change
   is a no-op; adding a concept builds only that packet.

   More: `wikify plan <slug>` (dry-run delta) · `wikify lint <slug>` (re-run the citation gate) ·
   `wikify coverage <slug>` (per-module coverage report).

## Use it in your own project

**To build / maintain a wiki** — the synthesis step is LLM-in-the-loop, so install the Claude
Code skill once and let an agent drive `prepare → write pages → finalize`:
```bash
DST=~/.claude/skills/wikify-ingest-repo            # global; or <project>/.claude/skills/...
mkdir -p "$DST/prompts"
cp skills/wikify-ingest-repo/SKILL.md "$DST/"
cp skills/prompts/*.md "$DST/prompts/"             # prompts must live inside the skill dir
```
Then in that project, just ask Claude Code to “ingest `<repo>`” / run the `wikify-ingest-repo`
skill.

**To only answer questions from an existing wiki** (no install needed) — drop `wiki/<slug>/`
into the project and add this to its `CLAUDE.md` so agents retrieve from it cheaply:
> Source of truth: the wiki at `wiki/<slug>/`. **Retrieve** from it — use `overview.md` as the
> index, grep to locate the relevant concept/catalog page, read only that section and cite it;
> do not bulk-read whole pages.

## C++ ingestion (mixed repos only)

scip-clang indexes against a `compile_commands.json`. For a repo like pytorch, first produce
one (and any generated headers) with the project's own build — e.g. CMake with
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` plus the codegen targets — and point wikify at it via the
`compile_commands:` config key. wikify consumes that file; it does not run your build.

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
