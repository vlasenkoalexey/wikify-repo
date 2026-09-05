# wikify-repo — Implementation Plan (v1, standalone Python)

This is the **build spec**. The design doc (`docs/design.md`) is the
*what/why*; this is the *how*. Build **Phase 1 first** and make it pass its
acceptance test before anything else. v1 is a standalone Python repo — no
dependency on, and no file sharing with, the autoresearch repo.

> **Currency note.** §1–§9 were the original plan; where reality diverged, the
> affected sections have been **edited in place** (marked *realized* /
> *descoped*), and §10 records the mechanisms that went beyond the plan.
> There is no "later section silently overrides an earlier one" — if you find a
> contradiction, that's a bug in this doc; fix it here.

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
| ~~dispatch extractor~~ *(descoped — devirtualization in `build_graph` crosses the dynamic-dispatch seam instead; §10.2)* | **Python** | 3 |
| evidence collection (tests-as-spec; docs/dynamics-source not realized) | **Python** | 4 |
| agenda planning: subsystem tree/community split, entry points, seeds, scope | **Python** | 5 |
| **concept synthesis → mechanism pages** | **LLM agent** | 5 |
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
    cli.py            # typer app: prepare / finalize / lint / coverage / verify / connect / plan
    acquire.py        # Stage 0: local symlink / clone / submodule + pin
    scip_index.py     # Stage 1: run indexers (incl. sharded), parse .scip → SymbolGraph
    ast_fallback.py   # Stage 1: AST recovery for files pyright crashes on (§10.1)
    bazel_cc.py       # Stage 1: bazel build+aquery → compile_commands.json for scip-clang (§10.1)
    languages.py      # Stage 1: language registry/detection; on-demand TS/Go/Rust indexers (§10.8)
    graph.py          # SymbolGraph model: symbols, edges, callers/callees, devirtualize (§10.2)
    diff.py           # Stage 2: reconcile state vs new index
    evidence.py       # Stage 4: tests-as-spec
    packet.py         # build synthesis packets (Python → LLM interface)
    lint.py           # Stage 6: citation linter (catalog-anchor resolution)
    fix.py            # Stage 6: finalize --fix deterministic lint auto-repair
    verify.py         # adversarial-verify worklist + verdict aggregation (deterministic half)
    assemble.py       # Stage 6: write index.md (per-repo + top catalog)
    coverage.py       # Stage 6b: set-difference coverage + per-module catalog pages (+ docstrings, connections)
    discover.py       # Stage 5 agenda, module tier: centrality-rank + auto-seed (legacy `agenda: modules`)
    subsystems.py     # Stage 5 agenda, subsystem tier: tree/community split, entry points, scope (§10.11)
    docs.py           # Docs mode (source_type: docs): doc packets, src: lint, doc coverage (§10.7)
    source.py         # read def-body snippets + body hashes (shared by packet/diff)
    monikers.py       # parse SCIP symbol strings → descriptors (shared)
    connect.py        # Stage 7: inline cross-repo concept links (§10.10)
    slug.py           # (superseded by catalog anchors — invariant 7; symbols live in catalogs)
    state.py          # .cache/state/<slug>.json
    config.py         # parse config/<slug>.md (frontmatter + concept list)
  .agents/skills/     # tool-neutral canonical home (.claude/skills/ symlinks here)
    wikify-ingest-repo/
      SKILL.md
      prompts/{synthesis,overview,ingest-docs,synthesis-docs,verify}.md
    wikify-connect-repo/SKILL.md
  config/
    <slug>.md         # per-repo markdown config (authored)
    defaults.md       # shared default + type-aware concept sets
  tests/
```

Outputs (`wiki/`, `.cache/`, `raw/`) are created at runtime per the design doc's
three-bucket schema, NOT committed here.

---

## 4. CLI surface

```
wikify prepare  <slug> [--ref <commit>] [--repo <url|path>] [--no-reindex]
      # Stages 0–4. Idempotent. Emits .cache/packets/<slug>/<concept>.md and
      # prints the plan (will build / rebuild / leave) over the DERIVED agenda.
wikify finalize <slug> [--fix]
      # Stage 6 + 6b. Emits catalogs, lints the agent-written pages, assembles
      # index.md, updates state. Non-zero exit + a report on any unresolved citation.
wikify lint     <slug> [--fix]    # Stage 6 lint only (re-runnable)
wikify coverage <slug> [--emit]   # Stage 6b: report whole-repo coverage; --emit (re)writes catalogs
wikify verify   <slug> [--page]   # adversarial-verify worklist (claims per page; no model)
wikify plan     <slug> [--ref]    # dry-run: same derived agenda as prepare; needs a cached index
wikify agenda   <slug> [--max N]  # propose the subsystem table of contents from the cached index (§10.11)
      # prepare --agenda subsystems|modules overrides the config/default planner for one run
wikify connect  [--apply k1,k2] [--refresh] [--exclude repo/path] [--vocab concepts]
      # Stage 7: propose (no args) / wire cross-repo concept links inline (§10.10)
```

`finalize` runs Stage 6b automatically (lint concepts → emit module catalogs →
write the coverage report into `index.md`). `wikify coverage` is the standalone
inspector — it answers "is the whole repo represented?" without re-synthesizing.
`finalize` also **warns** (never fails) when the silo has no `overview.md`: the overview is
the front door — the host index links it (skill register step) and `connect` discovers silos
by its presence — but it is written last, so a partial run must still finalize.

`prepare`/`finalize` are the two halves the SKILL.md orchestrates around agent
synthesis. `ingest` is the conceptual reconcile = `prepare` + agent + `finalize`.

---

## 5. Data contracts (pin these — the linter and catalogs depend on them)

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
  tree-sitter. Document this approximation where edges render ("calls/refs", not "calls").
- **Importance rank** (drives discovery seeding + catalog `uses`/`used by` ranking):
  `outbound*5 + ref_count*2` (context-sherpa formula). No clustering.

### 5.2 moniker ↔ catalog anchor (invariant 7 — no per-symbol files)
Symbols live in their **module catalog**, not per-symbol stubs (`slug.py`'s
filename scheme is superseded; the module is dead weight kept only in history).
- A symbol's home = `catalog/<module>.md` (one page per source file,
  `coverage.catalog_rel_path(def_path)`), at anchor
  `#<QualifiedName>` (`coverage.qualified_name(moniker)` — e.g. `#Buffer.shape`).
- The **authoritative** identifier is still the full moniker: each catalog page's
  frontmatter carries a `symbols:` map (anchor → moniker suffix under a factored
  `symbol_base:` prefix — §10.4). The linter resolves a citation by looking the
  anchor up in that map and checking the reconstructed moniker exists in the
  silo's SCIP graph — never by parsing filenames.
- `coverage.catalog_ref(module_path, moniker)` is the **single source of the
  citation target format**, shared by the packet (what to cite) and the catalog
  (what resolves).

### 5.3 Citation grammar (what `lint.py` parses)
- **Symbol citation** = a markdown link whose target is a **catalog anchor**:
  `../catalog/<module>.md#<QualifiedName>` (path contains `catalog/`, ends `.md`,
  carries an anchor). No provenance tags in the link text — provenance lives in
  page frontmatter and `[!inferred]` blocks.
- **Inferred block** = content inside a `> [!inferred]` blockquote; no citation
  required there.
- **Lint rules (hard gate, deterministic)**:
  1. Every catalog citation must resolve — the target catalog page exists and its
     frontmatter `symbols` map contains the anchor, reconstructing a moniker
     present in the silo's SCIP graph. Dead/unresolvable citation = FAIL.
  2. In the `## Entry points` and `## Mechanism (step-by-step)` sections, **every
     list item must contain ≥1 symbol citation or an L2 evidence link**.
     Uncited assertion there = FAIL (move it into an `[!inferred]` block to pass).
  3. No symbol cited that is absent from this concept's packet subgraph (catches
     invented symbols). = FAIL.
- The linter is **checkable without NLP** because rules 2–3 are scoped to named
  sections and list items, not arbitrary prose.
- `doc-concepts/` pages get rule 1 only (`lint.lint_doc_concepts`) — they come
  from a project doc, not a packet, so there is no subgraph/uncited gate.

### 5.4 Synthesis packet (`packet.py`, Python → LLM)
One markdown file per concept at `.cache/packets/<slug>/<concept>.md`
(+ `<concept>.subgraph.txt`, the moniker set rule 3 gates against):
```markdown
# Packet · <concept>  (repo <slug> @ <ref>)
## Synthesis focus (lens)        ← only when config sets `synthesis_focus` (§10.9)
## Seeds
<seed symbols, or "(discover: top-centrality in module X)">
## Scope                         ← subsystem planner (§10.11): the unit's modules + entry points
## Subgraph
<each symbol: moniker, signature, def file:line, docstring summary, callers[],
 callees[], and its `cite:` catalog-anchor link — copy VERBATIM when citing>
## Source
<def-body snippets for the subgraph symbols (truncated — read the real file)>
## Evidence
<matching tests (assert → symbols), DOCSTRINGS (author intent, citable as L2 —
 prefer quoting these over guessing; decision 8)>
## Template + rules
<the page template; the citation rules; "cite only symbols above; mark
 uncited claims [!inferred]; keep design-intent dynamics separate">
```
The agent reads the packet (and the real source it points to), writes
`wiki/code/<slug>/concepts/<concept>.md`, and creates **no stubs** — every
citation is a catalog anchor pasted from the Subgraph's `cite:` links.

### 5.5 Reconcile state (`.cache/state/<slug>.json`)
```json
{ "ref": "<sha>",
  "symbols": { "<moniker>": "<body-sha>" },
  "pages": { "<concept>": { "cited": ["<moniker>", ...], "built_ref": "<sha>" } } }
```
Reconcile: new symbol body-hashes vs `state.symbols` → changed monikers → any page
whose `cited` ∩ changed ≠ ∅ is `stale`. Concepts in config with no page = `build`.

### 5.6 Coverage / catalog (`coverage.py`, Stage 6b)
A **set-difference over the SCIP symbol table — NOT a graph walk** (see design
decision 7 for the why). Contracts:
- `documentable_symbols(graph)` = in-repo symbols with a def whose terminal
  descriptor suffix ∈ {Type (class), Method (fn/method), Term (module value)}.
  Externals (no def) and locals/params are excluded.
- `covered` = monikers cited by a concept page (`covered_monikers` resolves the
  catalog-anchor citations against the graph — module from the link path +
  qualified-name match — so it works while catalogs are being regenerated).
- A symbol is `covered` (deep, concept page), `catalog-only` (in a generated
  module catalog), or `unrepresented` (a coverage hole — should be empty after
  6b). `emit_catalogs` writes one `catalog/<def-file>.md` per module listing all
  its documentable symbols, so the catalogued set == the documentable set.
- The catalog page nests methods/terms under their owning class (via the
  symbol's Type descriptors), shows signatures + def `file:line` + intra-module
  calls/refs, and links covered symbols to their concept page. Generated straight
  from SCIP ⇒ `extracted` provenance, correct by construction, not linted.
- **Class connections** are rendered as `uses` / `used by` links, computed by
  rolling each class's member edges (methods AND fields — so `self.x = Foo()`
  counts) up to the class. This absorbs SCIP's member-granular reference scoping
  and yields true class→class edges, linked to the target's catalog page.
- **Docstrings (decision 8)** are rendered inline per class/function/module-value
  as a summary line. The docstring prose is the symbol's
  `SymbolInformation.documentation` with the leading signature code-fence stripped
  (`Symbol.docstring` / `Symbol.doc_summary`). `extracted` provenance. ~27% of
  torchtitan's documentable symbols carry one — a large free comprehension layer.
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
- `.agents/skills/wikify-ingest-repo/SKILL.md` + `prompts/synthesis.md`.
- **Target repo**: start tiny to debug the loop, then `torchtitan` (yours, pure
  Python). Concepts from a hand-written `config/torchtitan.md` with seeds.

**Phase 1 acceptance (definition of done):**
1. `wikify prepare torchtitan` runs scip-python, emits one packet per concept,
   prints a plan. No model calls.
2. Agent synthesis produces one page per concept under `wiki/code/torchtitan/concepts/`.
3. `wikify finalize torchtitan` → linter exits 0: **every citation resolves**,
   every config concept has a page, no invented symbols.
4. Idempotency: re-running `prepare` with no source/config change ⇒ plan = no-op.
5. Adding a concept to the config ⇒ `prepare` builds **only** that packet.
6. A golden set of ~5 questions about torchtitan is answerable from the wiki alone
   (store them in `tests/golden/torchtitan.md`; manual check is fine for v1).
7. **Whole-repo coverage (Stage 6b).** `finalize` emits a `catalog/` page per
   module and a coverage report; **every class SCIP found is represented** in
   either a concept page or a module catalog — in particular every model
   (`Transformer`, `Attention`, `TransformerBlock`, `FeedForward`, per model).
   Verify by enumerating SCIP class symbols and checking each appears in the
   wiki. (Coverage represents and internally connects modules; it does not bridge
   the dynamic trainer→model dispatch seam — that is explicitly out of Phase 1.)

### Phase 2 — C++  *(realized — dispatch extractor descoped)*
- `scip-clang` path ✅: compile DB auto-generated from bazel (`bazel_targets:` →
  `bazel_cc.py`, §10.1) or a pre-existing `compile_commands:` path. Mixed-language
  repos union the Python + C++ indexes.
- ~~`dispatch.py` (`native_functions.yaml` / `TORCH_LIBRARY_IMPL` → `maps/dispatch.md`)~~
  **descoped**: never built, and no `wiki/maps/` exists in any ingested silo.
  Devirtualization (CHA over `is_implementation`, §10.2) turned out to cross the
  dynamic-dispatch seam generically — a registration-table extractor would add a
  pytorch-specific map on top; revisit only if a real question needs it.
- Dynamics-source + in-repo-docs evidence (rest of Stage 4): **not realized**;
  evidence = tests-as-spec. Project docs are ingested separately as doc-concepts
  (§10.4), which covers most of the original "docs evidence" intent.
- Target: `pytorch/xla`, then `torch_tpu` — realized on pytorch, jax, xla, torch_tpu.
- Acceptance (as realized): torch_tpu ingests; C++ citations resolve.

### Phase 3 — connect (multi-repo), graded & interactive
Split by the Python/LLM line and by depth (design.md Stage 7). A new `wikify-connect-repo`
skill drives the LLM half; `connect.py` is the deterministic half.
- **Concept correspondences (`connect.py` + `wikify-connect-repo` skill).** ✅ *Realized* (§10.10).
  Inline, as a normal wiki — the host `wiki/concepts/<key>.md` links down to each repo's
  implementation, each silo page links up. `wikify connect` proposes candidates (explicit `concepts:`
  tags + name/token matches); the human picks **which** concepts to connect (selective — not
  everything); `--apply` writes the bidirectional links in regenerable `connect:auto` blocks;
  `--refresh` regenerates already-connected concepts after a new ingest. No side-table, no new page
  type. Deepening a concept into a full hub is optional LLM prose outside the auto block.
- **7a dependency links (`connect.py`, deterministic).** *Not yet automated.* Re-resolve external
  citations against other silos' `.cache/scip/*.scip`; upgrade dangling refs to cross-repo links;
  `compat.md` version-coherence gate.
- **Interactive gates (skills).** Ingest asks `synthesis_focus` if absent and offers `discover.py`'s
  ranked agenda for more concepts; connect asks **which concepts** to wire. Connect is invoked at the
  tail of ingest from the 2nd repo on, and separately re-runnable.
- Acceptance: re-running connect is idempotent (no churn); an applied concept links every matched
  silo page bidirectionally; a new ingest + `--refresh` adds the new repo to existing hubs; the
  human's concept selection is honored (unpicked concepts stay unconnected).

### Phase 4 — discovery, lanes, L4
- Candidate-concept discovery → realized twice: module centrality (`discover.py`) and the
  subsystem planner (`subsystems.py`, §10.11), which proposes the table of contents.
- Lane router (code-py / code-cpp / pallas-kernel / config / doc) + Pallas
  extractor + tpu-recipes config path.
- Optional L4 runtime enrichment (`## Observed dynamics`), wired to XProf.

---

## 7. Stage-5 synthesis instruction

This is the heart — its quality sets the wiki's quality. The **authoritative,
shipped prompt is `.agents/skills/wikify-ingest-repo/prompts/synthesis.md`** —
edit it there; this doc deliberately does not embed a copy (an embedded copy
drifted badly once: stub-style citations, no Mermaid diagram, three iterations
stale). What it enforces, in one breath:

- **Grounding floor (non-negotiable):** cite ONLY Subgraph symbols, by pasting the
  packet's `cite:` catalog-anchor links verbatim (no stubs are ever created);
  ungrounded claims go in `> [!inferred]` blocks; Entry-points/Mechanism items
  must each carry a citation (the linter's rule 2).
- **Heavy processing, not annotation:** read the REAL source at the packet's
  `file:line` (snippets are truncated); lead with Overview + a grounded **Mermaid
  diagram** + Design rationale; weave citations into insight prose — never a
  citation-per-clause trace.
- **Lens-aware:** if the packet carries a "Synthesis focus" block, emphasis (not
  grounding) follows it (§10.9).

Sibling prompts in the same directory: `overview.md` (per-repo overview page),
`ingest-docs.md` (doc-concept extraction), `synthesis-docs.md` (docs-mode track),
`verify.md` (adversarial refutation pass).

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
  `scip-clang` (binary, Phase 2). Today `scripts/setup-vendor.sh` bootstraps both
  (+ generates `scip_pb2.py`), and TS/Go/Rust indexers are installed on demand by
  `prepare` (§10.8). *Planned for PyPI packaging:* fold that into
  `wikify doctor` (report what's missing) / `wikify setup` (bootstrap) — these
  commands do **not** exist yet.
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
  version-skew check means something.
- The unit is either a single **standalone silo** or the whole **connected
  multi-repo wiki** (silos + inline cross-repo links on the concept pages) as one
  repo — e.g. "the TPU-ecosystem wiki."
- Consumers need **nothing**: no `wikify`, no Node, no Python — just the markdown
  and any agent. The optional `wikify-reader` plugin only sweetens retrieval in
  Claude Code.

### v1 packaging

One standalone GitHub repo is simultaneously: the **PyPI source** (engine), the
**plugin marketplace** (builder + reader), and the **build home**. When wikify is
later folded into the autoresearch repo, Channels 2–3 move inside it; the
mechanics are unchanged.

---

## 10. Realized mechanisms (post-v1 iteration — authoritative)

The §1–9 plan held, but four real ingests (torchtitan, **pytorch**, **jax**,
**torch_tpu**) forced mechanisms beyond it. §1–9 have been edited in place where
reality diverged (see the currency note at the top), so this section should no
longer *contradict* anything above — it *extends* the plan with the realized
mechanisms. Phase status: Phase 1 (Python) ✅; Phase 2 (C++) ✅ via bazel
(dispatch extractor descoped — see Phase 2); discovery + scaled synthesis ✅.

### 10.1 Stage 1 — indexing at scale & C++ from bazel
- **Sharded scip-python** (`run_indexer_sharded`, config `index_shards: ["pkg/*"]`).
  scip-python/pyright is single-process and OOMs (exit 144) on pytorch even at
  128 GB heap — *more heap does not help; shard instead*. Each shard is one
  `scip-python --target-only <path>` process with a bounded working set; monikers
  are global so shards union via `build_graph(*indexes)`. `merge_shards` repairs
  each doc's target-relative path back to repo-relative (falling back to the path
  derived from the symbol moniker for ambiguous `../` spillover).
- **AST fallback** (`ast_fallback.py`). Some files crash pyright with an unbounded
  `RangeError` and emit *nothing* (e.g. `torch/_tensor.py` = `torch.Tensor`). We
  parse them with Python's `ast` and synthesize symbols whose monikers match
  scip-python's scheme exactly, so the thousands of existing references join
  (Tensor recovered with 4990 callers). Run automatically for any target file the
  shards didn't emit.
- **C++ via scip-clang**, auto-generated compile DB from bazel (`bazel_targets:
  "//pkg/..."`, `wikify/bazel_cc.py`). `prepare` runs `bazel build` (materialize
  generated headers), `bazel aquery` (compile actions), converts to
  `compile_commands.json` — splitting combined `-isystem path` tokens, absolutizing
  includes against the execroot, `directory` = the real repo root (so sources are
  in-project and external torch/XLA/llvm headers drop), stripping output flags —
  then runs scip-clang with a raised `--ipc-size-hint-bytes` and long
  `--receive-timeout-seconds` (these TUs pull the whole torch header graph). A
  pre-existing `compile_commands:` path is the alternative. `build_graph` unions
  the Python + C++ indexes.

### 10.2 build_graph — recovery & connection
- **Orphan-synthesis (step 1.5)**: pyright drops a symbol's `SymbolInformation`
  when it fails to type it (RangeError), yet records the definition occurrence; we
  synthesize the node from that occurrence so the symbol stays citable/coverable
  (recovered `nn.Module` + ~2000 symbols).
- **Devirtualization (step 3, `graph.devirtualize`)**: Class Hierarchy Analysis
  over SCIP `is_implementation` → base→override / class→subclass edges, crossing
  the dynamic-dispatch seam reference-scoping can't see (pytorch: 8026 virtual
  edges; `nn.Module` → its 400 subclasses). Tracked in `graph.virtual_edges` and
  shown as `(virtual)` in packets. This is the "connection" op decision 7 deferred.

### 10.3 Packets — relevance-bounded subgraph
`packet.gather_subgraph` replaces the flat 50-symbol BFS cap with a frontier scored
by **importance ÷ (1 + distance from a seed)**, filling a budget (60) by relevance
so a hub (e.g. `nn.Module`, 1000+ callers) keeps its load-bearing collaborators
instead of an alphabetical slice. Seeds are always kept.

**Scope-aware budget (realized 2026-09-05; design.md Decisions log "The packet budget
belongs to the unit").** For a planned subsystem, `prepare` passes the unit's member
monikers as `scope_symbols` (`Agenda.scope_sets`, wired to `build_packet(...,
scope_symbols=)` → `gather_subgraph(..., scope=)`). With a scope: every member is a
candidate at distance `MAX_HOPS+1` even if no seed reaches it; the first
`SCOPE_RESERVE` (0.75) of the budget is filled with members by relevance; outside
symbols follow by relevance but no definition file may contribute more than
`SCOPE_OUTSIDE_PER_MODULE` (2); members backfill any remaining room, and only once
members are exhausted is the cap relaxed to fill the budget (a small unit gets more
context; nothing is crowded out). The packet's
`## Scope` block reports "N of M symbols inside this unit" and each outside symbol's
heading carries *(outside this unit)*. Measured on torch_tpu @ `ea8ca515` (60-symbol
packets): `ops` 8→45 inside / 31→9 helpers, `eager-device_buffer` 9→45 / 29→8,
`distributed` 8→45 / 30→9, `internal-compile` 8→45 / 29→9, `pjrt` 8→45 / 29→8;
`common-cache_key` 26→45 with helpers 20→25 because that unit *contains* the helpers
(correct). Rejected alternatives, measured: seed exclusion of ubiquitous helpers
(no change to the packet), unit re-ranking by fan-out (cosmetic). Without a scope the
function is byte-for-byte the legacy path. Tests: `tests/test_packet_scope.py`.

### 10.4 Stage 6 — catalog format, fix, verify, overview
- **Catalog format** (`coverage.render_catalog`): frontmatter factors the common
  moniker prefix into `symbol_base:` (anchors→terminal, ~70% shorter); no
  per-page boilerplate paragraph (implied by `type: catalog`). Each class lists
  **per-member detail** — `name(params) — Lline — docstring` for public/documented
  members (no caps: a module's own contents are the deterministic content an agent
  navigates to), with undocumented dunder/private folded but present+linked.
  Signatures are decorator-stripped. **Source links are RELATIVE** to the catalog
  page (never absolute — a leading `/` is repo-root, a broken link); `source_url`
  overrides with a base URL (github `…/blob/<commit>`), `""` disables.
- **`uses` / `used by`**: the only capped lists (unbounded cross-refs). Test/example
  callers are filtered (path segments test/tests/testing/example/benchmark — but
  NOT third_party/vendor, which are legit deps), the rest ranked by importance,
  hidden counts reported (`(+440 more; 671 test-only)`).
- **`finalize --fix`** (`fix.py`): deterministic auto-repair of the three lint
  rules against the packet (wrong anchor → packet's link; out-of-subgraph →
  de-link; uncited Mechanism step → link a symbol it names). Only removes or swaps
  in the packet's own link — never manufactures grounding; residuals reported.
- **Adversarial verify** (`verify.py`, `.agents/skills/wikify-ingest-repo/prompts/verify.md`, `wikify
  verify`): the correctness floor above the grounding floor. Extracts load-bearing
  claims; a skeptic agent tries to refute each against real source; verdicts fold
  to pass/fail. (On jax it caught 3 real errors in 323 claims.)
- **Overview** (`.agents/skills/wikify-ingest-repo/prompts/overview.md`, SKILL step 3): synthesized
  `wiki/code/<slug>/overview.md` AFTER concepts; `assemble` links it from `index.md`
  ("Start here → Overview").
- **Doc-concept ingest** (the LAST synthesis step — `.agents/skills/wikify-ingest-repo/prompts/ingest-docs.md`,
  SKILL step 4; adapts the autoresearch INGEST-SOURCE op to a silo). `prepare` globs
  `config.docs` → a worklist (`.cache/docs/<slug>.txt`); per doc, an agent extracts
  its concepts and writes **one grounded page per concept** into
  `wiki/code/<slug>/doc-concepts/` — each links the symbols the doc names to their
  **catalog** entries and cross-links siblings. The doc is never moved. `finalize`
  lints `doc-concepts/` on **rule 1 only** (`lint.lint_doc_concepts` — citations
  resolve; no subgraph/uncited gate, since these come from a doc not a packet), and
  `index.md` gets a **"Doc-derived concepts"** section.

### 10.5 Config keys (frontmatter)
`index_shards` (shard globs), `compile_commands` (pre-existing C++ DB),
`bazel_targets` (auto-generate the C++ DB from bazel), `source_url` (catalog
source-link base; default relative-local, `""` disables), `acquire`
(`submodule` default | `clone`; submodule mode converts an existing plain clone at
the same slug in place), `wiki_subdir` (default `code` → `wiki/code/<slug>`; `""` = flat),
`source_type` (`code`|`docs`), `doc_globs` (docs-mode file globs), `languages` (override
detection; e.g. `[python, typescript]`), `synthesis_focus` (a domain **lens** foregrounded in
overview/concept synthesis — e.g. "TPU performance — kernels, sharding, autotune, precision"),
`coverage_collapse` (globs → catalog page kept citeable but member body dropped — model zoos),
`coverage_exclude` (globs → no catalog page — uncited tests/vendored only),
`agenda` (`subsystems` | `modules` — the Stage 5 planner, §10.11; unset → subsystems for a
fresh silo, modules for one that already has state), `agenda_max` (cap on planned units),
`agenda_exclude` (globs over directory prefixes: `dir` drops the unit, `dir/*` its children).
In the `## Concepts` list, `seeds: (subsystem: <dir prefix>)` seeds a concept from a whole
directory (entry points + hubs, re-derived each run) in either mode.
*Connect (Stage 7) adds no per-repo config keys:* its vocabulary is the host wiki's
`wiki/concepts/` filenames (CLI `--vocab <dir>` to override) and *which* concepts to wire is a
skill-interactive choice (batch does only `--refresh`) — nothing to put in `config/<slug>.md`.

### 10.6 Vendored tools / setup
`scripts/setup-vendor.sh` fetches scip-python (npm) + scip-clang (pinned binary,
~130 MB — NOT committed, exceeds GitHub's limit) and generates `scip_pb2.py`.
`scripts/bazel_compile_commands.py` is a thin CLI over `wikify.bazel_cc`.
`project_version` stays `"0.0.0"` (a placeholder; nothing depends on its value —
monikers only need internal consistency).

### 10.7 Docs mode (`source_type: docs`) — `wikify/docs.py`
The prose track (design.md "Docs mode"). Same Karpathy-synthesis-in-a-deterministic-shell as
code, with the anchor swapped from SCIP symbol → source document + `#section`. `cli.py` branches
`prepare`/`finalize` on `cfg.source_type`:
- **`_prepare_docs`** — no SCIP. `docs.enumerate_docs` (vendor-skipped globs) → `build_doc_map`
  (per-format anchor adapters: `_markdown_anchors` ATX/setext, `_html_anchors` via `HTMLParser`
  headings+`id`; `.txt`/unknown → whole-file) → `write_doc_packets` (one packet/doc: source path,
  truncated text, the exact `src:` tokens, sibling links). The doc map is persisted to
  `.cache/docs/<slug>.docmap.json` for finalize.
- **synthesis** (LLM) — `prompts/synthesis-docs.md`: `topics/` (cross-source, reconciled) +
  `sources/` (per-doc), citing `[label](src:<doc>#<anchor>)`.
- **`_finalize_docs`** — `docs.lint_docs` (the gate: every `src:` citation resolves to a real
  doc + section, else exit 1) → `docs.docs_coverage` (set-difference over the doc file set;
  a doc is represented if it has a `sources/` page or an inbound citation) → `assemble_docs_index`.
Pinning tests: `tests/test_docs.py` (adapters, enumerate, gate pass/fail, coverage set-difference).
**Limit:** grounding resolution degrades for anchorless formats (PDF/images → whole-file).

### 10.8 Multi-language SCIP — `wikify/languages.py`
Grounding is SCIP, which is language-neutral, so the whole downstream (graph, monikers, catalogs,
coverage, lint) is unchanged per language — adding one is a registry entry + a thin
``scip_index.run_*``. `LANGS` maps `python`/`cpp` (bundled) + `typescript`/`go`/`rust` (on demand)
to `(exts, markers, bin, install, scip_suffix, run)`. Indexers: `scip-typescript` (TS/JS, uses/infers
tsconfig), `scip-go` (module root, needs go.mod), `rust-analyzer scip` (writes `index.scip`, relocated).
- **Detection** — `detect_languages` from root marker files (`go.mod`, `Cargo.toml`,
  `package.json`/`tsconfig.json`, `pyproject.toml`) or ≥3 source files, with a bounded
  vendor-skipping walk (`_WALK_CAP`). `cfg.languages` overrides; empty → detect (default python).
- **On demand, not by default** — the TS/Go/Rust indexers are NOT fetched by `setup-vendor.sh`.
  When a language is present but its `bin` is missing, `ensure_indexer` **auto-installs** it:
  echoes the registry's install command, runs it, extends PATH with the standard installer bin
  dirs (`~/.local/bin`, `~/go/bin`, `~/.cargo/bin`) so the fresh binary resolves immediately,
  and reports the outcome — announced, never silent. `prepare --no-install-indexers` opts out
  (guidance printed, that language dropped, the rest still index). The rust-analyzer installer
  works without rustup (falls back to the standalone release binary). *History:* the original
  design *asked* before installing, but the ask required a tty — through agent shells it never
  fired and languages were skipped every time, so ask-first was replaced by announce-and-install.
- **Merge** — each language writes `.cache/scip/<slug><suffix>.scip`; `cli._graph` globs and merges
  them all (`<slug>.scip` + `<slug>.*.scip`), so a polyglot repo becomes one graph.
Pinning tests: `tests/test_languages.py` (detection, registry, announced auto-install +
opt-out + failure-skip).

### 10.9 Synthesis lens + host-registration (realized)
Two thin, high-leverage additions realized post-v1 (design.md Decisions log):
- **`synthesis_focus` lens.** `packet.build_packet(..., focus=cfg.synthesis_focus)` emits a
  "Synthesis focus (lens)" block into the packet; `prompts/{synthesis,overview,ingest-docs}.md`
  each honor it — the overview leads with a focus-relevant *surfaces* section. Grounding is
  unchanged: the lens shifts emphasis, not citations. When it's absent from context the ingest
  skill asks (see §10.8's "ask, never silently" pattern) rather than guessing.
- **Register step (skill-side, not CLI).** The CLI never edits curated files (invariant 2). The
  `wikify-ingest-repo` SKILL's final step links the new `overview.md` into the host `index.md` and
  appends `log.md`, per host conventions. The deterministic silo (CLI) and its curated placement
  (skill) sit on opposite sides of the Python/LLM split — the CLI stays safe to re-run in batch,
  the skill owns the one judgment-bearing edit to curated files.

### 10.10 Connect — inline cross-repo concept links (realized)
`wikify/connect.py` + `wikify connect [--apply k1,k2] [--refresh] [--exclude repo/path] [--vocab
concepts]` (whole-wiki, no slug). Wires silos on the concept axis **inline, as a normal wiki** — no
side-table, no new page type. `load_vocabulary` (host `wiki/concepts/` stems) × `discover_silos`
(any dir with `overview.md` + `concepts/`, minus the vocab dir — layout-agnostic across
`code`/`codebases`) → `build_index` → `concept → [Match]` candidates. A silo page matches a key by an
explicit `concepts:` frontmatter tag (`"tag"`, authoritative — synthesis emits it when `prepare`
hands the vocabulary into the packet) or a name/token heuristic (`"name"`; prefix-share ≥4 so
`remat`↔`rematerialization`).
- **Propose** (`wikify connect`, no args): print candidate concepts (most-implemented first) + which
  are already connected. Writes nothing.
- **Apply** (`--apply <keys>`): for the human-chosen keys, `apply_connections` writes, inside
  regenerable `connect:auto` blocks (hand prose untouched), a `## In this wiki's repos` down-block on
  each `wiki/concepts/<key>.md` linking every implementation, and a one-line up-link block on each
  linked silo page. `--exclude` drops a stray `repo/rel-path` match.
- **State is the wiki itself** — `connected_keys` = concept pages that carry a down-block (no
  side-file); `--refresh` re-applies them after a new ingest. Idempotent (re-apply → no churn).
Selection (which concepts) is a human decision at the connection phase — connecting everything drowns
the pages. Deepening a concept into a real hub is optional LLM prose *outside* the auto block
(`wikify-connect-repo` skill). Dependency links (Stage 7 "(a)") are not yet automated. Pinning tests:
`tests/test_connect.py`.

### 10.11 Subsystem planner — `wikify/subsystems.py` (realized 2026-09-04)
Decision 8's unit is "the derived cluster"; the first realization (`discover.py`) used one
module per unit ranked by fan-in, which on real repos surfaces hub headers (design.md
Decisions log, "The page unit is the subsystem, not the hub module"). The planner replaces the
*unit*, not the derivation:

- **Tree split.** Documentable symbols → definition files (library only: `PLANNER_EXCLUDES` =
  `discover.DEFAULT_EXCLUDES` + per-file tests `_test.`/`_tests.`/`conftest.py`). Strip the
  umbrella package (longest shared directory prefix). Split a directory whose subtree holds
  more than `max_modules` (20) modules into its children; children under `min_modules` (2)
  fold into the parent's own group; files directly in a split directory form that
  directory's own unit. (`_split`)
- **Flat split.** A flat directory over budget (torch_tpu's `common/`, 65 modules) cannot
  split by tree: run `discover.label_propagation` over the directory's own symbols, give each
  module its dominant community, and make communities spanning >= 3 modules units named
  `<dir>::<stem>` after the cluster's **largest** module (naming by importance picks the
  status helper everyone calls; the substantive module is the big one). The remainder stays
  as the directory's group. Falls back to one flat unit when nothing separates. (`_split_flat`)
- **Stats + seeds.** Per unit: symbol set, internal edges, **external fan-in** (distinct
  library symbols outside the unit referencing inside; test callers never count),
  external fan-out, class count. **Entry points** = inside symbols ranked by distinct
  external callers (callables/types first, `operator*` last); **hubs** = inside symbols by
  importance. Seeds = entry points then hubs, capped at 8, handed to `packet.gather_subgraph`
  unchanged (relevance-bounded, budget 60). (`_fill`)
- **Rank + cap.** `score = fanin_external * 2 + internal_edges`; drop units under
  `min_symbols` (8) but always keep the top one; de-dup slugs; cap at `agenda_max` (24).
- **Slug.** Directory path minus umbrella, `/` → `-`, leading `_` stripped, `::` → `-`; root
  → `core`. So `torch_tpu/_internal/compile` → `internal-compile`, `torch_tpu/common::cache_key`
  → `common-cache_key`.
- **Packet scope.** `render_scope` → the packet's `## Scope` block: unit, module list, entry
  points, and the instruction to write about the unit as a whole (hubs are sections).
  `packet.build_packet(..., scope=)`.
- **Agenda file.** `render_agenda` → `.cache/plan/<slug>.agenda.md`: a ranked table (slug,
  subsystem, modules, symbols, ext fan-in, internal, entry points) plus per-unit module
  lists, with the curation instructions inline. `prepare` prints it; `wikify agenda` emits it
  alone from the cached index (no packets) — the skill's "confirm before synthesizing" step.
- **Mode resolution** (`cli._agenda_mode`): `--agenda` > config `agenda:` > default rule —
  a silo with no recorded pages plans by subsystems, a silo with state keeps `modules` (and
  prints the hint to switch). Config `(subsystem: <prefix>)` concepts are seeded via
  `subsystem_for_prefix` in either mode.

Evidence at first run (torch_tpu @ `ea8ca515`, cached index): 24 units over 323 library
modules — `common-cache_key`, `common`, `ops-op_names`, `eager-device_buffer`, `ops`,
`ops-op_builder_utils`, `ops-macros`, `ops-view_decomposition`, `ops-scaled_dot_product_attention`,
`eager`, `distributed`, `pjrt`, `internal-compile`, `internal-utils`, `internal-profiler`, ...
— against the module tier's `to_string.h` / `status_builder.h` / `macro_utils.h`. torchtitan:
15 units (`components`, `models-flux`, `distributed`, `config`, `tools`, `protocols`, one per
model family, `models-moe`, `hf_datasets`). Pinning tests: `tests/test_subsystems.py`,
`tests/test_config.py` (`(subsystem: …)`, `agenda*` keys), `tests/test_cli.py` (planner mode,
`agenda` command, fresh/existing default, config subsystem seeds).

**Front door (realized 2026-09-05, borrowed from openwiki's index/quickstart).**
- **`description:` frontmatter** on every concept, doc-concept and overview page (one sentence,
  required by `prompts/synthesis.md` / `overview.md`). `assemble.write_repo_index` renders it in
  the concept table, the doc-concept list and the "Start here" line; the skill's register step
  reuses the overview's for the host index row. Pages written before the field existed render
  with an empty cell — never linted.
- **Area grouping.** Each concept row carries the *area* its catalog citations mostly point
  into (`assemble.page_area`: the directory of the most-cited `../catalog/<module>.md` link,
  `(cross-cutting)` when a page cites nothing). Derived from the page itself, so it needs no
  side file and works for legacy pages. Past `GROUP_MIN_CONCEPTS` (6) pages with >= 2 areas
  the table becomes per-area sections, biggest first; below that, one flat table with an Area
  column.
- **Task routing.** `prompts/overview.md` adds a "Where to go for a given task" table
  (verb-shaped rows, each cell a page that exists) beside the question-shaped map. The overview
  is not under the citation gate, so `finalize` checks its relative links for existence and
  **warns** per dead link (`cli._overview_link_warnings`; fenced code and http links skipped).
- **Topic titles at confirmation.** `render_agenda` ends with a paste-ready `## Concepts`
  block (`- **<slug>** — seeds: (subsystem: <prefix>)` per unit); the confirm step renames the
  directory-shaped slug to a topic name, preferring host `wiki/concepts/` keys. A config
  subsystem entry **replaces** the planned unit(s) at or under its prefix (`cli._covers`:
  exact, descendant, or the `dir` of a `dir::stem` community unit; `.`/`""` covers all), so a
  rename never builds a page twice. `subsystem_for_prefix` accepts a full `dir::stem` prefix.
  Tests: `tests/test_assemble.py`, `tests/test_cli.py` (dead-link warning, suppression, agenda
  block), `tests/test_subsystems.py` (community prefix, concepts block).

**Docs as the naming signal (realized 2026-09-05, process only).** No code: the ingest
skill's confirm step reads the README and the headings of the `prepare` docs worklist
(`.cache/docs/<slug>.txt`) against the proposed agenda — renames slugs to the authors'
terms, records other terms as `aliases:` (synthesis prompt frontmatter), adds a missed
unit as `(subsystem: <prefix>)` or a cross-unit flow concept with explicit symbol seeds
(grounding rule unchanged: no packet symbols, no page), and logs "documented, not found".
`prompts/overview.md` requires every README-led topic to route to a page or be declared
uncovered. Design.md decisions log: "The graph finds the units; the docs name them".

**Verify cache (realized 2026-09-05; design.md decisions log "Verify verdicts are memoized").**
`wikify verify` is now incremental. `Claim.key` = sha256 of the normalized prose (16 hex);
`verify.claim_evidence` = `{moniker: body_sha}` for the symbols a claim cites (via
`lint._resolve_citation` + `diff.current_hashes`; a removed symbol maps to `''`).
`verify.plan_worklist` splits a page's claims into to-verify (new / cited code changed /
previously refuted / re-sample / forced) and cached holds; `record_verdicts` stores the
reviewer's STRICT JSON (matched by `claim_line`) under `.cache/verify/<slug>/<page>.json`
with evidence, ref and date. CLI: `wikify verify <slug> [--page P] [--all] [--record FILE]`
prints per-page "N to verify, K cached hold(s), J cited code changed, R re-sampled, F still
refuted"; with `--page` only the to-verify claims are listed, each tagged with its reason.
`RESAMPLE_PCT` (5) holds are re-checked per run, chosen by `sha256(key:ref)` so the set
rotates with the pin. A claim whose citations do not resolve (no catalog yet) is never
cached. Without a cached SCIP index the cache is bypassed with a note. `prompts/verify.md`
ends with the record step; the skill's step 7 describes the incremental loop.
Tests: `tests/test_verify.py` (key stability, evidence invalidation, refuted/resample
handling, record round-trip), `tests/test_cli.py` (worklist → record → cached).

### 10.12 OKF v0.2 compatibility — `wikify/okf.py` (realized 2026-09-05)
Design.md decisions log "OKF: compatible by naming, minimal by design". Front matter is edited
**textually and key-scoped** (`okf.set_keys`): only the owned keys (`generated`, `verified`,
`sources`, an invalid `status`) are removed/re-appended; every other line is byte-identical and
a second run is a no-op.
- **`finalize`**: `generated: {by: wikify/<version>, at}` on concept pages (refreshed only when
  the page *body* sha, now recorded in `state.pages[<slug>].body_sha`, changes), doc-concepts and
  `overview.md` (set if absent); `sources:` on concept pages = the definition files the page's
  citations resolve to (`okf.cited_files`, occurrence-counted, most-cited first, `MAX_SOURCES`
  10) as `<base>/<path>` where base is `source_url` or the page-relative path into the pinned
  checkout (same rule as catalog links; `source_url: ""` → no sources); `status: fresh` dropped.
  The silo `index.md` gets `okf_version: "0.2"` and one snapshot `sources` entry
  (`source_url`'s `/blob/<sha>` → `/tree/<sha>`, else the relative checkout path).
- **`verify --record`**: after recording, if every claim holds at current evidence
  (worklist empty or re-sample only), `verified` gains/replaces the `wikify-verify/<version>`
  entry; if any claim is refuted or invalidated the tool entry is removed. Entries by other
  producers (`human:<id>`) always survive; the list is re-rendered in flow style.
- **`okf.warnings`** (printed by finalize, never a gate): actor pattern
  (`producer/version` | `human:<id>` | `process:<id>`), offset-qualified ISO datetimes,
  `status` ∈ {draft, stable, deprecated}, `stale_after` datetime.
- Prompts no longer write `status: fresh`; synthesis.md tells the agent the tool owns
  `generated`/`verified`/`sources`/`status`. Tests: `tests/test_okf.py`, `tests/test_cli.py`.
