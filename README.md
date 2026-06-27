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
pip install -e .            # 1. the `wikify` CLI (deps: protobuf, pyyaml, typer, gitpython)
scripts/setup-vendor.sh     # 2. install scip-python + generate wikify/scip_pb2.py (one-time)
scripts/install-skill.sh    # 3. install the Claude Code skill (required for writing pages)
wikify --help               # verify the CLI is on PATH
```

That's the whole install. **All three steps matter:** the CLI does the deterministic stages, but
the page-writing (synthesis) stage is **LLM-in-the-loop**, so an agent must run the
`wikify-ingest-repo` skill — `install-skill.sh` drops it into `~/.claude/skills/` (global; pass a
`<project>/.claude/skills` path to scope it to one project). Use any Python ≥3.11 env (conda,
venv, or pipx-managed). Both scripts are idempotent, so re-running is harmless.


## Quick start

In Claude Code, run the `wikify-ingest-repo` skill with the repo — just say:

> ingest https://github.com/owner/myrepo      (a local path works too)

That's it. The skill bootstraps the config itself, then drives the whole pipeline — index →
symbol graph → write the concept pages → citation lint → assemble — and writes the wiki to
`wiki/<slug>/`. Re-running is idempotent: only changed concepts rebuild.

## Use it in your own project

**To build / maintain a wiki** — install the skill (step 3 above) so an agent can drive
`prepare → write pages → finalize`. Global install is fine, or scope it to one project:
```bash
scripts/install-skill.sh /path/to/your-project/.claude/skills
```
Then in that project, just ask Claude Code to “ingest `<repo>`” / run the `wikify-ingest-repo`
skill. (The script bundles `SKILL.md` + its `prompts/` into one skill dir, as the skill requires.)

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
