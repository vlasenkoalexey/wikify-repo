# wikify-repo — Implementation Plan (v1, standalone Python)

This is the **build spec**. The design doc (`ingestion-pipeline-design.md`) is the
*what/why*; this is the *how*. Build **Phase 1 first** and make it pass its
acceptance test before anything else. v1 is a standalone Python repo — no
dependency on, and no file sharing with, the autoresearch repo.

---

## 1. Stack & prerequisites

- **Language**: Python 3.11+. The whole tool is Python; the LLM steps are driven
  by SKILL.md files, not Python.
- **Python deps**: `protobuf` (parse `.scip`), `pyyaml` (frontmatter +
  `native_functions.yaml`), `typer` (CLI), `gitpython` or stdlib `subprocess`
  (submodules), `pytest` (tests). No database. No web framework.
- **External binaries (invoked as subprocesses, not vendored)**:
  - `scip-python` — npm package `@sourcegraph/scip-python`. **Node is a build
    prereq** even though the tool is Python.
  - `scip-clang` — prebuilt binary (Phase 2 only).
  - `scip` CLI — for `scip lint`/`print`/`stats` on indexes (optional, debugging).
- **No network at runtime** beyond fetching the target repo (git).

---

## 2. The Python ↔ LLM division (read this first)

The tool is **mostly deterministic Python with exactly two LLM-in-the-loop
steps**. Keeping this boundary clean is the most important implementation rule.

| Work | Who | Stage |
|---|---|---|
| acquire/pin, run indexers, parse `.scip`, build symbol graph | **Python** | 0,1 |
| symbol diff (reconcile) | **Python** | 2 |
| dispatch extractor | **Python** | 3 |
| evidence collection (tests/docs/dynamics-source) | **Python** | 4 |
| **concern synthesis → mechanism pages** | **LLM agent** | 5 |
| citation linter, assemble, index | **Python** | 6 |
| coverage set-difference → module catalogs | **Python** | 6b |
| connect: dependency links | **Python** | 7a |
| connect: concept-correspondence judgment | **LLM agent** | 7b |

**Handoff is via files on disk.** The deterministic half never calls a model; the
agent half never parses protobuf. The flow per ingest:

```
wikify prepare <repo>   (Python)  → emits synthesis packets + a build plan
   ↓
[agent writes one page per packet]  (LLM, driven by SKILL.md)
   ↓
wikify finalize <repo>  (Python)  → lint, assemble index, update state
   ↓  (lint fails?) → agent fixes flagged pages → finalize again
```

Do **not** put synthesis logic in Python (you'll get rigid templated junk) and do
**not** push linting into the prompt (you'll get nondeterministic validation).

---

## 3. Repo skeleton

```
wikify-repo/
  pyproject.toml
  README.md
  wikify/
    __init__.py
    cli.py            # typer app: prepare / finalize / connect / lint / plan
    acquire.py        # Stage 0: submodule + pin
    scip_index.py     # Stage 1: run indexer, parse .scip → SymbolGraph
    graph.py          # SymbolGraph model: symbols, edges, callers/callees
    diff.py           # Stage 2: reconcile state vs new index
    dispatch.py       # Stage 3: native_functions.yaml / registration → map (Phase 2)
    evidence.py       # Stage 4: tests + dynamics-source + docs
    packet.py         # build synthesis packets (Python → LLM interface)
    lint.py           # Stage 6: citation linter
    assemble.py       # Stage 6: write index.md, candidate concerns
    coverage.py       # Stage 6b: set-difference coverage + per-module catalog pages
    source.py         # read def-body snippets + body hashes (shared by packet/diff)
    monikers.py       # parse SCIP symbol strings → descriptors (shared)
    connect.py        # Stage 7 (Phase 3)
    slug.py           # moniker ↔ filename
    state.py          # .cache/state/<slug>.json
    config.py         # parse config/<slug>.md (frontmatter + concern list)
  skills/
    wikify-ingest-repo/SKILL.md
    wikify-connect-repo/SKILL.md
    prompts/synthesis.md      # the Stage-5 instruction (section 7)
  config/
    <slug>.md         # per-repo markdown config (authored)
    defaults.md       # shared default + type-aware concern sets
  tests/
```

Outputs (`wiki/`, `.cache/`, `raw/`) are created at runtime per the design doc's
three-bucket schema, NOT committed here.

---

## 4. CLI surface

```
wikify prepare  <slug> [--ref <commit>] [--repo <url|path>]
      # Stages 0–4. Idempotent. Emits .cache/packets/<slug>/<concern>.md and
      # prints the plan (will build / rebuild / leave / candidate concerns).
wikify finalize <slug>
      # Stage 6. Lints the agent-written pages, assembles index.md, updates state.
      # Non-zero exit + a report if any citation is unresolved.
wikify lint     <slug>            # Stage 6 lint only (re-runnable)
wikify coverage <slug> [--emit]   # Stage 6b: report whole-repo coverage; --emit (re)writes catalogs
wikify plan     <slug> [--ref]    # dry-run: print the delta, emit nothing
wikify connect  [--repos a,b,...] # Phase 3
```

`finalize` runs Stage 6b automatically (lint concerns → emit module catalogs →
write the coverage report into `index.md`). `wikify coverage` is the standalone
inspector — it answers "is the whole repo represented?" without re-synthesizing.

`prepare`/`finalize` are the two halves the SKILL.md orchestrates around agent
synthesis. `ingest` is the conceptual reconcile = `prepare` + agent + `finalize`.

---

## 5. Data contracts (pin these — the linter and stubs depend on them)

### 5.1 SymbolGraph from SCIP
Parse `.scip` (protobuf `Index`) → `documents[] → {occurrences[], symbols[]}`.
- A **symbol node** = each `SymbolInformation` (global symbols only; drop
  `local *`, anonymous, and stdlib/external per the context-sherpa pruning rule).
- **Definition location** = the occurrence of that symbol whose `symbol_roles`
  has the `Definition` bit.
- **Inheritance/implements edges** = `SymbolInformation.relationships`
  (`is_implementation`, `is_type_definition`) — these are explicit in SCIP.
- **Callers/callees**: SCIP has **no "call" role**. Approximate it: a *reference*
  occurrence (Definition bit unset) of a callable symbol `S` whose range falls
  inside the enclosing range of function `F`'s definition ⇒ edge `F → S`. Scope
  by `SymbolInformation.enclosing_range` if present, else by the span between
  consecutive definition occurrences in the document. **This is reference-based,
  not true call resolution** — but it is *symbol-accurate* (the name is bound to
  the right symbol by the compiler frontend), which is the whole gain over
  tree-sitter. Document this approximation in the stub ("calls/refs", not "calls").
- **Importance rank** (for which symbols get stubs / discovery): `outbound*5 +
  ref_count*2` (context-sherpa formula). No clustering.

### 5.2 moniker ↔ filename (`slug.py`)
- Filename = readable slug: `<lang>-<package>-<descriptor-path>`, descriptor
  suffixes (`#./()`) and unsafe chars → `-`, collapse repeats, lowercase-preserve.
- On collision, append `-<first8 of sha256(moniker)>`.
- The **authoritative** identifier is the full moniker in the stub's frontmatter,
  NOT the filename. The linter resolves a citation by reading the *target stub's
  frontmatter moniker* and checking it exists in the SCIP index — never by parsing
  the filename. So the slug only needs to be deterministic + collision-free, not
  invertible.

### 5.3 Citation grammar (what `lint.py` parses)
- **Symbol citation** = a markdown link whose target path ends in
  `symbols/<slug>.md`. Optionally prefixed inline by a provenance tag:
  `[extracted → `Sym`](...)` or `[inferred → ...]`.
- **Inferred block** = content inside a `> [!inferred]` blockquote; no citation
  required there.
- **Lint rules (hard gate, deterministic)**:
  1. Every link to a `symbols/*.md` must point to an existing stub whose
     frontmatter `moniker` resolves in the silo's SCIP index. Dead link = FAIL.
  2. In the `## Entry points` and `## Mechanism (step-by-step)` sections, **every
     list item must contain ≥1 symbol citation or an L2 evidence link**.
     Uncited assertion there = FAIL (move it into an `[!inferred]` block to pass).
  3. No symbol cited that is absent from this concern's packet subgraph (catches
     invented symbols). = FAIL.
- The linter is **checkable without NLP** because rules 2–3 are scoped to named
  sections and list items, not arbitrary prose.

### 5.4 Synthesis packet (`packet.py`, Python → LLM)
One markdown file per concern at `.cache/packets/<slug>/<concern>.md`:
```markdown
# Packet: <concern>  (repo <slug> @ <ref>)
## Seeds
<seed symbols, or "(discover: top-centrality in module X)">
## Subgraph
<each symbol: moniker, signature, def file:line, callers[], callees[]>
## Source
<def-body snippets for the subgraph symbols>
## Evidence
<matching tests (assert → symbols), dynamics-source snippets, doc excerpts>
## Template + rules
<the page template; the citation rules; "cite only symbols above; mark
 uncited claims [!inferred]; keep design-intent dynamics separate">
```
The agent reads only the packet, writes `wiki/<slug>/concerns/<concern>.md`, and
creates any missing `symbols/<slug>.md` stubs for symbols it cites.

### 5.5 Reconcile state (`.cache/state/<slug>.json`)
```json
{ "ref": "<sha>",
  "symbols": { "<moniker>": "<body-sha>" },
  "pages": { "<concern>": { "cited": ["<moniker>", ...], "built_ref": "<sha>" } } }
```
Reconcile: new symbol body-hashes vs `state.symbols` → changed monikers → any page
whose `cited` ∩ changed ≠ ∅ is `stale`. Concerns in config with no page = `build`.

### 5.6 Coverage / catalog (`coverage.py`, Stage 6b)
A **set-difference over the SCIP symbol table — NOT a graph walk** (see design
decision 7 for the why). Contracts:
- `documentable_symbols(graph)` = in-repo symbols with a def whose terminal
  descriptor suffix ∈ {Type (class), Method (fn/method), Term (module value)}.
  Externals (no def) and locals/params are excluded.
- `covered` = monikers cited by a concern page (resolved via each page's stub
  frontmatter, same resolver the linter uses).
- A symbol is `covered` (deep, concern page), `catalog-only` (in a generated
  module catalog), or `unrepresented` (a coverage hole — should be empty after
  6b). `emit_catalogs` writes one `catalog/<def-file>.md` per module listing all
  its documentable symbols, so the catalogued set == the documentable set.
- The catalog page nests methods/terms under their owning class (via the
  symbol's Type descriptors), shows signatures + def `file:line` + intra-module
  calls/refs, and links covered symbols to their concern page. Generated straight
  from SCIP ⇒ `extracted` provenance, correct by construction, not linted.
- **Coverage is representation, not connection** — it never creates a missing
  dynamic-dispatch edge (e.g. `model_parts[0](inputs)` → a model's `forward`).
  That seam, and cross-model concept unification, are separate optional ops.

---

## 6. Phased plan (build in order; each phase ends at its acceptance test)

### Phase 1 — MVP: one Python repo, end to end  ← build this first
Scope: Stages 0,1,2,5,6,**6b** for a **pure-Python** repo. **Skip** dispatch
(Stage 3), C++, connect, discovery, and L4. Evidence (Stage 4) = **tests only**.
- `acquire` (git submodule + pin), `scip_index` (run scip-python, parse),
  `graph`, `config` (parse `config/<slug>.md`), `packet`, `lint`, `assemble`,
  `coverage`, `state`, `cli` (`prepare`/`finalize`/`coverage`/`plan`).
- `skills/wikify-ingest-repo/SKILL.md` + `prompts/synthesis.md`.
- **Target repo**: start tiny to debug the loop, then `torchtitan` (yours, pure
  Python). Concerns from a hand-written `config/torchtitan.md` with seeds.

**Phase 1 acceptance (definition of done):**
1. `wikify prepare torchtitan` runs scip-python, emits one packet per concern,
   prints a plan. No model calls.
2. Agent synthesis produces one page per concern under `wiki/torchtitan/concerns/`.
3. `wikify finalize torchtitan` → linter exits 0: **every citation resolves**,
   every config concern has a page, no invented symbols.
4. Idempotency: re-running `prepare` with no source/config change ⇒ plan = no-op.
5. Adding a concern to the config ⇒ `prepare` builds **only** that packet.
6. A golden set of ~5 questions about torchtitan is answerable from the wiki alone
   (store them in `tests/golden/torchtitan.md`; manual check is fine for v1).
7. **Whole-repo coverage (Stage 6b).** `finalize` emits a `catalog/` page per
   module and a coverage report; **every class SCIP found is represented** in
   either a concern page or a module catalog — in particular every model
   (`Transformer`, `Attention`, `TransformerBlock`, `FeedForward`, per model).
   Verify by enumerating SCIP class symbols and checking each appears in the
   wiki. (Coverage represents and internally connects modules; it does not bridge
   the dynamic trainer→model dispatch seam — that is explicitly out of Phase 1.)

### Phase 2 — C++ + dispatch
- `scip-clang` path: out-of-tree build → `.cache/build/<slug>/compile_commands.json`
  → index. Mixed-language repos union the Python + C++ indexes.
- `dispatch.py`: prefer parsing **`native_functions.yaml`** (structured) over
  macro-parsing; fall back to `TORCH_LIBRARY_IMPL` sites only where the YAML is
  absent. Output `wiki/<slug>/maps/dispatch.md`.
- Add dynamics-source + in-repo-docs evidence (rest of Stage 4).
- Target: `pytorch/xla`, then `torch_tpu`.
- Acceptance: torch_tpu ingests; dispatch map rows all cite a registration site;
  C++ citations resolve.

### Phase 3 — connect (multi-repo)
- `connect.py` 7a (dependency links): re-resolve external citations against other
  silos' `.cache/scip/*.scip`; upgrade dangling refs to cross-repo links;
  `compat.md` version-coherence gate. Deterministic, no model.
- 7b (concept links): LLM judgment keyed on shared concerns; `_connect/decisions.md`
  cache; link-insertion only + re-lint.
- Acceptance: torch_tpu → xla dependency links resolve; re-running connect is
  idempotent; a concept link survives an update without churn.

### Phase 4 — discovery, lanes, L4
- Candidate-concern discovery → "Candidate concerns" in `index.md`.
- Lane router (code-py / code-cpp / pallas-kernel / config / doc) + Pallas
  extractor + tpu-recipes config path.
- Optional L4 runtime enrichment (`## Observed dynamics`), wired to XProf.

---

## 7. Stage-5 synthesis instruction (`skills/.../prompts/synthesis.md`)

This is the heart — its quality sets the wiki's quality. Drop it in verbatim and
tune.

```markdown
# Synthesis instruction — one mechanism page from one packet

You are given ONE packet describing ONE concern of a codebase. Produce ONE
markdown mechanism page. You are documenting how the code WORKS, grounded only in
the packet. You are not summarizing files.

## Hard rules
- Use ONLY symbols present in the packet's Subgraph. Never name a symbol that is
  not there. If you need one that's missing, say so in Open questions — do not
  invent it.
- In "Entry points" and "Mechanism", every bullet MUST cite a symbol with a
  markdown link to its stub: [`Sym`](../symbols/<slug>.md). Create the stub if it
  doesn't exist (frontmatter: moniker, def file:line, callers, callees, "Cited by").
- Any claim you cannot ground in a cited symbol or an Evidence item goes in a
  `> [!inferred]` block, flagged as your inference — never stated as fact.
- "Dynamics" describes DESIGN INTENT from source + tests only. Never claim runtime
  behavior you cannot see statically. Do not write an "Observed dynamics" section.

## Page template
---
title: <concern title>
type: concern
provenance: mixed
concern: <concern-slug>
updated: <date>
status: fresh
---
# <concern title>
<one-line scope>
## Entry points
- [`Sym`](...) — what it is, when it's hit.
## Mechanism (step-by-step)
1. <step> [extracted → `Sym`](...)
## Key data structures
## Dynamics (design intent)
<grounded in tests/scheduler source; link the test-spec page>
## Edge cases
## Open questions
## See also
```

## Method
1. Start from Seeds; walk the Subgraph (callers/callees already provided) to find
   the spine of the mechanism.
2. Read the provided Source snippets; do not ask for files outside the packet.
3. Write the steps in execution order. Cite each.
4. Pull dynamics from Evidence (tests + scheduler/stream/collective source).
5. List honest Open questions where the packet was insufficient.
```

---

## 8. What success looks like

v1 is "working" when Phase 1 acceptance passes on torchtitan: a standalone
`wikify` Python tool + two SKILL.md skills that take a pure-Python repo to a
grounded, lint-clean markdown wiki an agent can answer internals questions from,
idempotently. Everything after is additive (C++, connect, discovery), and the
autoresearch integration is a later phase whose only requirement is that this
tool keeps emitting a clean markdown tree.

---

## 9. Distribution & install

Three artifacts, three channels. The key property: **wiki consumers install
nothing** — heavy install sits with the publisher, mirroring the cost-placement
of the whole design.

| Artifact | Audience | Channel | Install |
|---|---|---|---|
| `wikify` engine (Python) | builds wikis | PyPI | `pipx install wikify-repo` |
| skills (SKILL.md) | builds wikis (in Claude Code) | Claude Code plugin | `/plugin install wikify-builder@wikify-repo` |
| generated wikis (markdown) | reads/queries wikis | git repo / submodule | `git submodule add ...` (no tool needed) |

### Channel 1 — the engine (pip/pipx)

CLI tool, installed in isolation. `pyproject.toml`:
```toml
[project]
name = "wikify-repo"
requires-python = ">=3.11"
dependencies = ["protobuf", "pyyaml", "typer", "gitpython"]

[project.scripts]
wikify = "wikify.cli:app"
```
- Install: `pipx install wikify-repo` or `uv tool install wikify-repo` → `wikify`
  on PATH.
- **External prereqs are not pip-installable**: `scip-python` (npm → Node),
  `scip-clang` (binary, Phase 2). Ship:
  - `wikify doctor` — checks for node / scip-python / scip-clang, reports what's
    missing.
  - `wikify setup` — bootstraps them (`npm i -g @sourcegraph/scip-python`,
    download scip-clang binary).
- **Docker** (Phase 2+): an image bundling Python + Node + scip tools (+ Bazel for
  the C++ build). For the C++ path this is close to required and makes CI
  cache-regeneration clean.

### Channel 2 — the skills (Claude Code plugin)

Plugins are the distribution format; skills are the content. **Split into two
plugins** so readers don't carry the builder weight:

```
wikify-repo/                      # ONE repo = pip source + plugin marketplace
  pyproject.toml                  # → Channel 1
  wikify/ ...                     # the engine
  <marketplace manifest>          # confirm exact path/name vs current CC docs
  plugins/
    wikify-builder/               # needs the engine on PATH
      plugin.json
      skills/
        wikify-ingest-repo/SKILL.md
        wikify-connect-repo/SKILL.md
        prompts/synthesis.md
    wikify-reader/                # lightweight; no engine needed
      plugin.json
      skills/
        wikify-query/SKILL.md     # graphify-style PreToolUse hook + query guidance
```

Install:
```
# to BUILD wikis:
pipx install wikify-repo                          # engine (Channel 1)
/plugin marketplace add <you>/wikify-repo
/plugin install wikify-builder@wikify-repo

# to READ wikis (optional, improves retrieval):
/plugin install wikify-reader@wikify-repo
```
Manual alternative: drop a skill folder into `~/.claude/skills/`, start a new
session.

Mechanics:
- The builder SKILL.md **shells out to the `wikify` CLI** (assumes Channel 1 is
  installed) and references its prompt via `${CLAUDE_SKILL_DIR}/prompts/synthesis.md`
  so the path resolves at any install level.
- The reader skill is just the hook + "query the wiki, don't grep raw files"
  guidance — no engine, no Python.
- Team/org rollout: list the marketplace + plugins in `.claude/settings.json`.
- **Confirm the exact plugin/marketplace manifest schema against the current
  Claude Code plugin docs (code.claude.com/docs) before publishing** — manifest
  file names/fields are a moving product detail; the structure above is the
  stable shape, not a guaranteed schema.

### Channel 3 — the wikis (git, zero-install for consumers)

- Distribute as a **git repo** the consumer clones or `git submodule`s into their
  project. **Tag releases by the source commits the wiki covers** so the
  version-skew check and `_connect/compat.md` mean something.
- The unit is either a single **standalone silo** or the whole **connected
  multi-repo wiki** (silos + `_connect/`) as one repo — e.g. "the TPU-ecosystem
  wiki."
- Consumers need **nothing**: no `wikify`, no Node, no Python — just the markdown
  and any agent. The optional `wikify-reader` plugin only sweetens retrieval in
  Claude Code.

### v1 packaging

One standalone GitHub repo is simultaneously: the **PyPI source** (engine), the
**plugin marketplace** (builder + reader), and the **build home**. When wikify is
later folded into the autoresearch repo, Channels 2–3 move inside it; the
mechanics are unchanged.
