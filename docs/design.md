---
title: "wikify-repo — Grounded Framework-Internals Wiki — Design"
status: draft
applies_to: [torchtitan, pytorch/xla, torch_tpu, pytorch]
---

# wikify-repo

## Goal

Ingest a framework codebase (pure Python, pure C++, or mixed) into a wiki such
that an LLM agent can answer **mechanism questions** about its internals —
"how is compilation implemented", "how does compute/comm overlap work" — with
**minimal query-time effort** and **minimal hallucination**, and such that the
wiki is cheap to **upgrade** when the library version bumps and cheap to
**distribute** to many developers. When several repos share one wiki, connect
them so cross-framework questions resolve, without turning the wiki into a
hairball.

## Product surface — two skills

The product is a Karpathy-style skill family. Each skill is one slash command.

| Skill | Does | Maps to |
|---|---|---|
| `/wikify-ingest-repo <repo> [--ref <commit>]` | **Idempotent reconcile** — first build, version bump (`--ref`), or added concept, all the same operation | Stages 0–6 |
| `/wikify-connect-repo` | Link a repo's silo into the rest of the multi-repo wiki | Stage 7 |

**ingest is a declarative reconcile** (like `make`/`terraform apply`): its desired
state is `{pinned commit's symbols} × {requested concept set}`, and re-running
converges the wiki to it. Same inputs → no-op; new concept in the config → build
only that page; moved `--ref` → diff symbols, rebuild stale pages; wiped cache →
regenerate SCIP. So there is **no separate `update` skill** — "update" is just
`ingest --ref <newcommit>`. The Stage 2 diff is the *mechanism* reconcile uses
when the commit moved, not a separate command.

Before doing expensive work, reconcile **previews its delta** (`will build / will
rebuild (stale) / will leave / candidate concepts available`) — the
`plan`-then-`apply` split, so an overloaded ingest never surprises you.

Optional future skill: `/wikify-compare` — synthesize a *cross-repo* concept
page (e.g. "sharding across MaxText / axlearn / mine"). Distinct from connect:
connect *links*, compare *synthesizes*. Out of scope for v1.

Each repo's wiki is a **self-contained silo**, independently ingestable and
distributable. Connection is a re-runnable transform layered on top (Stage 7),
never a precondition for a single repo.

## Scope (v1)

v1 is a **standalone Python repo**, deliberately *not* merged into the model-
optimization (autoresearch) repo — no shared `program.md` / `SCHEMA.md`. It gets
its own self-contained schema and synthesis instruction; concepts may be borrowed
from the autoresearch repo's schema but files are not shared. Integration into
the autoresearch repo (feeding its codebase pages) is a later phase; v1's only
obligation is to keep the output a clean consumable markdown tree so that seam
stays easy.

## Load-bearing design decisions

1. **SCIP is the common structural substrate.** One downstream pipeline for all
   languages. `scip-python` (pyright-based, type-resolved) and `scip-clang`
   (real compilation; resolves macros/templates/dispatch registration) emit the
   same index format with stable symbol monikers. Those monikers are reused as
   (a) wiki anchors, (b) citation targets, (c) diff keys.

2. **Three layers, not one.** AST/SCIP is the *grounding* layer, never the
   comprehension engine. Mechanism understanding comes from LLM synthesis;
   dynamics (overlap, scheduling, async) are grounded *statically* at ingest.
   - **L1 Grounding** — SCIP symbol graph. Deterministic, exhaustive, no LLM.
   - **L2 Static evidence** — tests + dynamics-bearing source surfaces +
     in-repo design docs/comments. All available without running anything.
   - **L3 Comprehension** — concept-driven mechanism pages. LLM, every claim
     cited into L1/L2.
   - **L4 (optional, downstream)** — runtime enrichment from IR/HLO + traces,
     run only when hardware + a workload exist. Not a precondition for ingest.

3. **Ingestion is concept-driven (top-down), not file-driven (bottom-up).**
   Bottom-up "summarize the repo" is the root cause of shallow answers. Drive
   synthesis from an explicit list of architectural concepts. **But selectivity
   needs a coverage floor** (decision 7): concept-driven synthesis alone silently
   drops whole subsystems the concept list forgot, so a deterministic catalog
   pass represents every remaining module.

4. **Pure markdown product; no database, no binary shipped.** The wiki stays
   grep-able, human-readable, git-diffable, distributable. The SCIP index is a
   **derived, ingestion-side artifact only** (under `.cache/scip/`, never `raw/`)
   — the pipeline reads it while synthesizing and consumers never see it. Transitive graph traversal is
   an *ingestion-time* need (synthesis walks the graph once and writes the
   conclusion down); at query time the agent reads finished pages, so there is
   nothing for a query-time index to accelerate. The rare short-hop navigation a
   consuming agent wants is served by markdown itself: each symbol's module
   catalog lists its calls / `uses` / `used by` as relative links (an adjacency
   list, grep-able). **Consumers need only grep + a text editor + their agent.**

5. **Provenance and version pinning are mandatory** because the wiki is
   distributed. Every page is tagged `extracted` vs `inferred` and pinned to the
   ingested commit SHA. Consumers' installed version is checked against the pin.

6. **Tree-sitter is deferred (out of scope for v1).** It was considered as a
   complementary *intra-symbol* layer (function bodies, Pallas kernel
   block-size/`BlockSpec` extraction) — things SCIP's symbol-level output drops.
   That value is real but secondary; v1 is SCIP-only. If added later it is a
   *complement* (intra-symbol detail), never a competing cross-symbol graph, and
   any heuristic edge it produces must carry `ast-heuristic` provenance so it
   never masquerades as a resolved fact.

7. **Two-tier coverage: concepts for depth, catalogs for the whole repo.**
   Concept synthesis (decision 3) is deliberately *selective*, which means a
   forgotten concept = a missing subsystem. The first torchtitan ingest proved
   this: three Trainer concepts, and **every model** (`Transformer`, `Attention`,
   …) was absent — the essence of the repo. Two failed "obvious" fixes show why
   the right fix is a set-difference, not more traversal:
   - *Reachability traversal fails by construction.* The trainer invokes a model
     as `model_parts[0](inputs)` via `nn.Module.__call__` — a **dynamic dispatch
     with no static call edge**. Walking out from entry points cannot cross that
     seam; the models look unreachable.
   - *A per-file "is it connected?" check is worse.* Model files have ~zero static
     inbound edges (their one inbound edge is the dynamic one), so a connectivity
     test mislabels them as dead code — the exact failure mode of name-based call
     graphs (e.g. CodeGraphContext's dead-code detector).
   - *Enumeration sidesteps dispatch.* SCIP already enumerated **every** symbol,
     so coverage never asks "what is reachable?" It asks "what is *represented*?"
     `coverage = documentable_symbols − concept-cited`, then emit one **catalog
     page per module** (symbols, signatures, def links, intra-module calls/refs).
     Deterministic, no LLM, cannot miss a file.

   **Coverage is representation, not connection.** A catalog represents each
   module and captures its *internal* edges (those are real static calls), so a
   model's `Transformer → TransformerBlock → Attention → FeedForward` spine is
   fully grounded. It does **not** synthesize the missing trainer→model edge, nor
   unify the N independent `Attention` classes into one concept — those are
   separate, optional operations (static devirtualization via SCIP
   `is_implementation`; intra-repo concept-correspondence à la Stage 7b), never a
   precondition for whole-repo coverage. *(The first of those has since been
   realized: `build_graph` devirtualizes via CHA — the Decisions log entry
   "Devirtualization IS the connection op", implementation.md §10.2.)* Catalog
   entries are `extracted` provenance; any future heuristic bridge carries
   `heuristic`/`inferred`.

8. **Comprehension is derived and graded — not authored and binary.** Coverage
   (decision 7) guarantees every symbol is *represented*; this decision governs
   how much each is *understood*. The defect it fixes: the first ingest took its
   comprehension agenda from a hand-written concept list — **manual** (so it had
   gaps), **fixed-size** (3, regardless of repo), and **binary** (deep LLM page or
   nothing). The fix makes the agenda a *function of the code's own topology*:
   - **Derived, not authored.** What earns a page is computed from intrinsic,
     language-agnostic signals every repo has — the module/package tree and graph
     centrality (fan-in). Human curation is an optional override, never the
     source. The wiki covers what the code says is important, not what someone
     remembered to list.
   - **Graded, not binary.** LLM effort on a unit is *monotonic in its
     centrality*, a gradient with four bands: (1) high-centrality cluster → deep
     **mechanism page**; (2) any symbol with a **docstring** → the author's own
     words, `extracted`, free; (3) mid-centrality, undocumented → a short
     synthesized **purpose blurb** (LLM, only as a fallback for where the author
     was silent); (4) low/trivial → **structural catalog** only. No important unit
     is un-annotated; no trivial unit burns a deep page.
   - **The unit is the derived cluster** (module-tree node / graph community), not
     the file (→ shallow "summarize everything") and not the hand-named concept
     (→ gaps). Boundaries are computed; size adapts to the repo. *(Realized
     2026-09-04 as the **subsystem planner**, implementation.md §10.11: directory
     subtrees split to a module budget, flat directories split by reference
     community, ranked by external fan-in, seeded from entry points + hubs. The
     first realization — one module per unit, ranked by fan-in — survives as
     `agenda: modules`; see the Decisions log entry "The page unit is the
     subsystem, not the hub module".)*
   - **Docstrings are L2 authored evidence — prefer them over synthesis.** A
     docstring is comprehension that is *also ground truth*: the author's stated
     intent, more authoritative than any LLM guess and free to ingest. So they
     reorder the economy — *spend the model only where the author was silent, or
     where the truth is cross-symbol* (mechanism, execution order) and no single
     docstring can carry it. This also sharpens the Python/LLM split (decision in
     §implementation): the docstring covers "what this symbol is for"; the LLM
     covers "how these symbols work together" — the one thing docstrings can't.
   - **Discovery is deterministic; only synthesis is LLM.** Clustering, centrality
     ranking, tier assignment, and auto-seeding are pure graph math. The model
     only writes prose for units the deterministic layer already selected.

   In layer terms: **L3's agenda is a deterministic function of L1's topology, and
   L3's effort density is monotonic in symbol centrality.**

### Decisions log (settled across the pytorch / jax / torch_tpu ingests)

These were forced or refined by real ingests; recorded so they aren't relitigated.
The *how* lives in `implementation.md` §10.

- **Scale by sharding, not heap.** scip-python is single-process and OOMs on
  pytorch at any heap size; the fix is `--target-only` shards unioned by global
  moniker — never "give pyright more memory."
- **A symbol-recovery floor.** A type checker can't index everything: it drops
  symbols on `RangeError` (→ orphan-synthesis from the definition occurrence) and
  fails some files entirely (→ deterministic AST fallback whose monikers match
  scip-python's scheme). Ingestion is robust to partial indexer failure.
- **Devirtualization IS the connection op.** CHA over SCIP `is_implementation`
  builds the base→override edges reference-scoping misses (decision 7's deferred
  "connection"). Coverage still ≠ connection; this is the bridge.
- **C++ comes from bazel.** For repos with no checked-in compile DB, generate one
  from `bazel build`+`aquery` (`bazel_targets:`); the sources are kept in-project by
  setting `directory` = the real repo root, which also drops external dep headers.
- **A correctness floor above the grounding floor.** The linter proves every claim
  cites a real symbol; *adversarial verify* (skeptic agents refuting against
  source) proves the claim is *true*. Both are gates, at different altitudes.
- **The catalog is a navigation surface, not a symbol dump.** Per-member detail
  with extracted docstrings + relative source links; uniform and **uncapped on a
  module's own members** (so an agent can deterministically find any symbol);
  `uses`/`used by` are the only capped lists (unbounded cross-refs), test-filtered
  and importance-ranked. The `symbols/` per-symbol stubs are gone — folded into the
  module catalog (one home per symbol).
- **Source links are relative and local, never absolute, never github-by-default.**
  An absolute `/…` path is a broken link in markdown (reads as repo-root); a github
  URL isn't local. Default: a path relative to the catalog page into the indexed
  repo. `source_url` opts into a URL base.
- **`project_version` stays `0.0.0`.** A SCIP moniker field we leave at the
  placeholder — nothing depends on its value (monikers need only internal
  consistency), and pinning it to the commit would churn every moniker per ingest
  and hurt reconcile diffs.
- **`third_party`/`vendor` are dependencies, not noise.** Excluded from *concept
  discovery* (don't write a deep page about vendored fmt) but kept in `uses`/`used
  by` (a vendored caller is a real relationship); only test/example paths are
  filtered there.
- **A synthesis lens is a first-class, optional input.** `synthesis_focus` — a
  one-line domain framing (e.g. "TPU performance — kernels, sharding, autotune,
  precision") — is injected into the packet and the overview/concept prompts so
  synthesis *foregrounds* it: the overview leads with a focus-relevant surfaces
  section, becoming the domain entry point (it can replace a hand-written perf page).
  The lens moves emphasis, never grounding — every claim still cites a real symbol.
  Host-wiki-owned; when absent from context, the ingest skill *asks* rather than guesses.
- **The page unit is the subsystem, not the hub module.** The first discovery
  ranked single modules by fan-in. Centrality rewards what everything depends on,
  so on torch_tpu the 27 deep pages landed on `to_string`, `status_builder`,
  `error_utils`, `macro_utils`, while the compilation-cache tiers, the distributed
  backend, the compile backend, PjRt and RNG — the subsystems users ask about — had
  no mechanism page at all (catalog + doc-concepts only); on tpu-inference, a flat
  Python package with few hubs, discovery found 5 units against 208 catalog pages.
  An openwiki run on the same repo planned 32 subsystem pages that matched the
  questions. The fix keeps decision 8's derivation but changes the unit: the
  planner splits the module tree to a budget (flat directories by reference
  community), ranks units by *external* fan-in + internal interactions, seeds each
  from its entry points (the API surface the rest of the repo enters through) and
  hubs, and hands synthesis a `## Scope` block so the page is about the unit, not
  the first hub. Hubs become sections. The proposal is shown before synthesis;
  curation is config (`agenda_exclude`, `seeds: (subsystem: <prefix>)`). The gate,
  packets, coverage and verify are unchanged. (§10.11)
- **The ingest skill self-connects into the host wiki (register step).** The CLI
  never edits curated files (invariant 2), but a fresh silo that nothing links to is
  invisible. So the *skill's* final step registers the new `overview.md` into the host
  `index.md` and appends `log.md`, per the host's conventions. Ingest is "build the
  silo" (CLI, deterministic) **plus** "wire it into the wiki" (skill, curated
  placement) — the two stay on the correct sides of the Python/LLM split.

### Docs mode — prose as a first-class source type (`source_type: docs`)

wikify's identity is *Karpathy synthesis wrapped in a deterministic shell*: a **grounding
gate** + a **coverage floor**, with the LLM doing only the synthesis. The default (code) mode
anchors that shell to **SCIP symbols**. A repo that is documentation, not code, has no symbols —
so the LLM synthesis has nothing to cite and coverage has nothing to enumerate (see "How this
minimizes hallucination"). Rather than degrade, docs mode **swaps the anchor**, keeping the shell
identical:

| | code mode | docs mode |
|---|---|---|
| grounding anchor | SCIP symbol (moniker) | **source document + `#section`** |
| coverage domain | set-difference over **modules** | set-difference over **doc files** |
| the gate | citation resolves to a real symbol | citation resolves to a real doc + section |
| Python↔LLM split | unchanged | unchanged |

The synthesis step is Karpathy's Ingest verbatim (read a doc → topic + source pages → cite →
cross-link → *reconcile into existing topics*); docs mode adds the two things a manual
knowledge-base lacks: a citation that **fails the build** if it points at a section that doesn't
exist, and a coverage floor so **no doc is silently dropped**.

Only the *anchor resolver* is format-sensitive, so it is a per-format **adapter**
(`anchors(text)`): markdown/rst → heading slugs, HTML → `id`/heading slugs, notebooks → cell
index, plain text/PDF/images → whole-file (coarse). Enumeration, coverage, and the gate are
format-agnostic; the framework is **one polymorphic grounding target + one coverage domain**,
instantiated for code (symbol/module) or docs (section/file) — not a forked pipeline. A prose
citation is a `[label](src:<repo-rel-doc>#<anchor>)` link the doc packet hands over verbatim
(mirroring how a code packet hands over `cite:` catalog anchors). Realized in `wikify/docs.py`;
`prompts/synthesis-docs.md` drives synthesis. **Honest limit:** grounding *strength* degrades
with format fidelity — markdown/HTML get fine-grained section anchors; PDF/images fall back to
page-/file-level.

---

## Architecture

```
            ┌──────────────────────────────────────────────────────┐
  acquire → │ L1 GROUNDING (deterministic)                         │
   & pin    │   scip-python / scip-clang  → SCIP index             │
            │   dispatch/registration extractor → op→kernel map    │
            └──────────────────────────────────────────────────────┘
                          │ symbols, edges, monikers
                          ▼
            ┌──────────────────────────────────────────────────────┐
            │ L2 STATIC EVIDENCE (no execution)                     │
            │   tests (assert → exercised symbols)                  │
            │   dynamics-bearing source (scheduler/stream/collective)│
            │   in-repo design docs + comments                      │
            └──────────────────────────────────────────────────────┘
                          │ evidence pages, cited to L1
                          ▼
            ┌──────────────────────────────────────────────────────┐
            │ L3 COMPREHENSION (LLM, concept-driven)               │
            │   per concept: traverse L1 graph, read source+L2,    │
            │   emit ONE mechanism page, every claim cites L1/L2   │
            └──────────────────────────────────────────────────────┘
                          │
                          ▼
            ┌──────────────────────────────────────────────────────┐
            │ ASSEMBLE: markdown wiki (the whole product)          │
            │   provenance tags · commit pin · citation linter     │
            └──────────────────────────────────────────────────────┘

   L4 (optional, downstream): IR/HLO + traces enrich pages when hardware exists.
```

---

## Pipeline stages

### Stage 0 — Acquire & pin
- Add repo as submodule under `raw/code/<slug>`, record `commit: <sha>`.
- Record build config needed for L1 (see Stage 1 C++ path).
- Output: pinned source tree + `config/<slug>.md` (per-repo markdown config).

### Stage 1 — Structural extraction → SCIP (L1, deterministic, no LLM)

| Language | Tool | Notes |
|---|---|---|
| Python | `scip-python` | pyright-backed; type-resolved refs. Works build-free. Bundled. |
| C++ | `scip-clang` | needs `compile_commands.json`. Resolves macros/templates. Bundled. |
| TS/JS | `scip-typescript` | uses/infers `tsconfig`. **On-demand indexer.** |
| Go | `scip-go` | module root, needs `go.mod`. **On-demand indexer.** |
| Rust | `rust-analyzer scip` | Cargo project. **On-demand indexer.** |
| Mixed / polyglot | all, merged | run per-language, union the SCIP indexes into one graph. |

**Because grounding is SCIP (language-neutral), a language is a pluggable indexer** — everything
downstream reads the symbol table, not a per-language AST (`wikify/languages.py`). Python + C++ ship
with `setup-vendor.sh`; TS/JS, Go, Rust are **installed on demand**: `prepare` detects the language
(root marker or ≥3 source files) and, if the indexer is missing, **installs it automatically** —
always announcing the exact command it runs, never silent. `--no-install-indexers` opts out
(guidance printed, that language skipped, the rest still index). Rationale: ingest usually runs
through an agent's non-interactive shell, where the earlier ask-first design could never actually
ask — languages were silently dropped in practice (`implementation.md` §10.8).

- **C++ compile database**: emit `compile_commands.json` into
  `.cache/build/<slug>/`. Build **out-of-tree** so `raw/code/<slug>/` (the pinned
  submodule) stays immutable — build outputs and generated headers live in
  `.cache/build/`, never in `raw/`.
  - CMake: `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`.
  - Bazel (xla / torch_tpu): `bazel-compile-commands-extractor`.
- For each symbol the SCIP index yields: stable moniker, kind, def location
  (file:line), signature, leading doc/comment, callers, callees, references,
  relationships (overrides/implements). **This is the citation namespace.**
- Persist the SCIP index under `.cache/scip/<slug>.scip` — a **derived,
  regenerable** artifact (ingestion-side, not shipped); never under `raw/`.

### Stage 2 — Structural diff & scoping (incremental layer)
- Hash each symbol's `(signature + body-span)` from SCIP.
- First run: all symbols are `added`.
- Upgrade: diff new vs old SCIP index → `{added, removed, changed}` symbols.
- **Citation-aware invalidation**: any L3 mechanism page citing a `changed`
  symbol is flagged `stale`. The citation graph gives this for free.
- **Behavioral changes are not always AST-visible** (a lowering tweak with an
  identical signature). At ingest you can't catch these — flag it: a page whose
  cited symbols are unchanged but whose *concept* touches lowering/scheduling
  carries `behavioral_recheck: true`. The optional L4 enrichment (if/when run)
  diffs IR/HLO to confirm or update. AST-diff alone drives core re-ingestion.

### Stage 3 — Dispatch / registration extractor *(descoped — never built)*
The plan: parse `TORCH_LIBRARY[_IMPL]` / `native_functions.yaml` into an
**op → kernel → backend/dispatch-key** map (`wiki/maps/dispatch.md`), since
generic SCIP misses registration tables. It was never implemented — no
`dispatch.py`, no `maps/` in any ingested silo — because **devirtualization**
(CHA over SCIP `is_implementation`, implementation.md §10.2) crossed the
dynamic-dispatch seam generically, which covered the questions the map was for.
Revisit only if a real backend question needs the registration table itself;
if built, every row cites its registration-site moniker, regenerated never
hand-edited.

### Stage 4 — Static dynamics evidence (L2, no execution)
"How does overlap / scheduling / async work" is invisible to bare structure but
recoverable *statically* from three sources — no hardware, no runnable build:
- **Tests as spec**: from SCIP refs in test files, map each test → asserted
  behavior → exercised symbols. Emit `wiki/tests/<area>.md`. For undocumented
  code (torch_tpu) this is the highest-signal behavioral truth available.
- **Dynamics-bearing source surfaces**: the machinery is in the source even
  without a trace — async dispatch sites, stream/queue/event APIs, future/`wait`
  usage, collective-op calls, the scheduler class, the dependency tracker. These
  are statically locatable via SCIP; the synthesis reads them directly.
- **In-repo design docs / comments / RFCs**: framework source usually explains
  the strategy in prose somewhere; ingest it as `wiki/sources/<slug>.md`.
- **Docstrings (per-symbol authored intent)**: SCIP captures each symbol's
  docstring in `SymbolInformation.documentation`. This is the *highest-grounding,
  zero-cost* comprehension layer (decision 8): the author's own "what this does",
  `extracted` provenance. It is rendered inline on the symbol's catalog entry
  (module/class/member level, summary form) and surfaced to synthesis as citable
  L2 evidence, so the model can quote intent instead of guessing it.

### Stage 4b — Runtime enrichment (L4, OPTIONAL, downstream)
Run only when hardware + a workload exist — never a precondition for ingest.
- Capture IR/HLO (`XLA_FLAGS=--xla_dump_to=...`) + an XProf trace for a workload.
- Append an `## Observed dynamics` block to the relevant mechanism page, clearly
  separated from the static `## Dynamics (design intent)` section.
- Naturally lives in the autoresearch loop, which already captures HLO per run.

### Stage 5 — Derived, graded synthesis (L3, LLM)

The comprehension agenda is **derived from topology, not authored**, and effort is
**graded by centrality** (decision 8). Discovery is the primary driver; the manual
list is an optional override, not the source.

**Where the agenda comes from (derivation order — deterministic until synthesis):**
1. **Planning (primary, automated) — the subsystem planner.** Split the module
   tree (library modules only; tests/examples/vendored excluded) top-down until
   every node holds at most a budget of modules; a flat directory over budget is
   split by reference community instead. Each unit gets its symbol set, internal
   edges, and **external fan-in** (distinct outside symbols that reference it);
   units are ranked by external fan-in + internal interactions and capped. Each is
   seeded from its **entry points** (inside symbols with the most outside callers —
   the API surface) and then its hubs, and its packet carries a `## Scope` block
   naming the modules and entry points. The proposal (a table of contents) is
   printed and written to `.cache/plan/<slug>.agenda.md` for the user to confirm
   before synthesis. Module-level centrality (one module per unit, ranked by
   fan-in) remains as `agenda: modules` — an existing silo keeps it until told
   otherwise, so a `--ref` bump never surprises with new pages. (§10.11)
2. **Shared + type-aware defaults.** A stable domain set (`compilation-pipeline,
   dispatch-path, compute-comm-overlap, …`) and a per-repo-kind set (trainer →
   `sharding / checkpointing / data-pipeline`; Pallas → `block-sizing / autotune /
   numerics`) seed the agenda before discovery refines it.
3. **Curation (optional override).** The user edits `config/<slug>.md`: drop
   planned units with `agenda_exclude:` globs, cap them with `agenda_max`, add or
   rename a unit with `- **<slug>** — seeds: (subsystem: <dir prefix>)` (seeded
   from that directory's entry points + hubs, re-derived every run), or supply
   seed symbols directly — worth it only for priority repos; never required.

**Synthesis is HEAVY processing, not annotation.** A concept page that merely
traces the code with a citation per clause is a failure even if it lints. The
agent uses the packet as a grounding/citation index but **reads the actual source**
(packets truncate) and writes for a senior reader: an **Overview** (the mental
model), a grounded **Mermaid diagram** of the mechanism, a **Design rationale**
(the *why*), then an insight-rich Mechanism with citations woven in (a few per
paragraph, no `[extracted →]` tag spam). The bar: a reader learns something they
could not get by skimming the code.

**A top-level overview page per repo** (`wiki/code/<slug>/overview.md`) is synthesized
last, over the concept pages: the main concepts, core *system-level* diagrams, and
a map of which concept answers which question — the god-node entry point.

**Graded tiers (LLM effort monotonic in centrality):**
- **Deep mechanism page** — high-centrality clusters. Full heavy synthesis
  (Overview + diagram + rationale + insight Mechanism, citations woven).
- **Docstring annotation** — any symbol with a docstring: the author's words,
  `extracted`, free (Stage 4 / 6b). Preferred over synthesis where present.
- **Purpose blurb** — mid-centrality, undocumented: a short synthesized "what this
  is for", LLM, as a fallback for where the author was silent.
- **Structural catalog** — the trivial/experiment tail (Stage 6b, no LLM).

**Per concept, the agent:**
  1. seeds from entry symbols (config or discovered), **traverses the SCIP graph**
     (real edges, not grep) to gather the implementing subgraph,
  2. reads that source + relevant L2 evidence + tests,
  3. writes **one mechanism page**, structured: Overview · Entry points ·
     Mechanism (step-by-step) · Key data structures · Dynamics (design intent,
     cite L2) · Edge cases · Open questions. (An `## Observed dynamics` section
     is added later only if L4 enrichment runs.)
- **Citation rule**: every non-trivial claim ends with a SCIP moniker or L2
  artifact ref. Uncited claims are marked `> [!inferred]`.

**Adding a concept later (same-commit reconcile).** Add it to `config/<slug>.md`
(or accept a candidate) and re-run ingest. Reconcile builds **only** the new
page from the existing SCIP index (no re-extraction, no commit bump, nothing
marked stale — newly-cited symbols already have their catalog home), wires
see-also/back-links (link-insertion only), re-lints, and — if the repo is
connected — re-runs connect
for it + neighbors (a new concept can create cross-repo correspondences).

### Stage 6 — Assemble, lint, publish
- **Citation linter** (hard gate): every citation must resolve to a symbol in
  the SCIP index. Dead citation = build failure. Pages with uncited assertions
  outside `[!inferred]` blocks = build failure. *This is the hallucination floor.*
- Write the per-repo `wiki/code/<slug>/index.md` catalog + the top-level
  `wiki/index.md` (all repos + connection status) + per-page provenance frontmatter.
- Product is the `wiki/` markdown tree. Nothing else is shipped.

### Stage 6b — Structural coverage / module catalogs (deterministic, no LLM)
The whole-repo floor under concept selectivity (load-bearing decision 7). After
the concept pages are linted, classify every documentable symbol by a
**set-difference over the SCIP symbol table** — never a graph walk:
- `documentable` = every in-repo class / function / method / module value SCIP
  found (locals, params, externals already pruned).
- `covered` = the symbols cited by a concept page (read back from the pages).
- For each module (`= def file`), emit `wiki/code/<slug>/catalog/<module-path>.md`: a
  generated structural index of that module's symbols — signatures, def
  `file:line`, intra-module calls/refs, class→class `uses`/`used-by` edges, and a
  link to the concept page for any covered symbol. **Every** documentable symbol
  lands on its module's catalog, so the repo is fully represented even where no
  concept touched it.
- **Docstrings inline (decision 8).** Each class / function / module-value entry
  carries its docstring summary (the author's intent, `extracted`), so the
  catalog conveys *meaning*, not just structure — the cheapest, highest-grounding
  comprehension layer, with no model call. Undocumented symbols fall back to
  structure only.
- Emit a **coverage report** into `wiki/code/<slug>/index.md`: documentable total,
  deep (concept) %, catalog-only count, classes represented. This makes
  "whole repo ingested" a measured property, not a hope.

Catalogs are `extracted` (generated straight from SCIP, correct by construction)
and are not run through the citation linter. They represent and *internally*
connect modules; they do not bridge dynamic-dispatch seams (see decision 7).

---

## Wiki output schema

Three buckets, strictly separated: **`raw/`** = immutable inputs only,
**`.cache/`** = derived regenerable intermediates (never shipped, gitignored),
**`wiki/`** = the derived product. SCIP and profiles are *derived*, so they live
in `.cache/`, **not** under `raw/`.

```
wiki/                          the product (shipped)
  index.md                     top-level catalog: all repos + connection status
  <wiki_subdir>/<slug>/        one self-contained silo per repo (default subdir
                               "code" → wiki/code/<slug>; "" = flat wiki/<slug>)
    index.md                   per-repo catalog
    concepts/<concept>.md      L3 mechanism pages (the answer surface)
    catalog/<module-path>.md   Stage-6b generated structural index per module
                               (the whole-repo coverage floor; one per def-file).
                               ALSO the home of every symbol: its frontmatter
                               `symbols:` map (anchor→moniker) is the citation
                               target + the linter's resolution table. Citations
                               are `../catalog/<module>.md#<QualifiedName>`.
    doc-concepts/<concept>.md  grounded pages extracted from the repo's own docs
    (maps/dispatch.md — planned op→kernel table, descoped with Stage 3;
     tests/<area>.md, sources/<name>.md — planned L2 pages, not yet emitted)

    (No `symbols/` directory: per-symbol stubs were folded into `catalog/` —
     one source-tree-organized home per symbol. A cross-repo connect link
     therefore targets a catalog anchor, not a stub file.)
  concepts/                    the host's curated concept vocabulary + connect hubs
    <key>.md                   links DOWN to each repo's implementation (connect:auto block)
  (No `_connect/` directory: cross-repo connection is inline links on the concept
   pages above + an up-link on each silo page, not a side-table.)

.cache/                        derived, regenerable, gitignored, NOT shipped
  scip/<slug>.scip             SCIP index per repo (read at ingest AND connect)
  build/<slug>/                C++ only: out-of-tree build + generated headers
    compile_commands.json      input to scip-clang; machine-local, abs paths
  profiles/<slug>/             IR dumps, traces — only if L4 ever runs

raw/                           immutable inputs ONLY
  code/<slug>/                 pinned source submodule
```

> **`.cache/` is gitignored, never committed** — it's cheap to regenerate from
> `raw/`. So the skills treat it as disposable: `/wikify-ingest`, `-update`, and
> `-connect` each **regenerate any missing or commit-stale SCIP index before
> proceeding**, making a fresh clone "just work." Cost is minutes for Python
> silos (scip-python is build-free). **Caveat for C++ silos**: regeneration needs
> the build toolchain present to produce `compile_commands.json` (Bazel/CMake),
> so a C++ silo is *not* regenerable from a bare clone without its build
> environment — plan CI/build access wherever connect or update touches a C++
> repo.

**Where the commit SHA lives — per-repo, not per-page.** Every page in a silo is
generated from one ingested commit, so repeating it on every page is redundant.
Record it **once** in the per-repo `wiki/code/<slug>/index.md`. The currency a page
needs is *"am I valid for the silo's current commit?"*, which is answered by the
per-page `status: fresh | stale` flag (maintained by Stage 2 diff) against the
repo's single commit — not by a per-page SHA. After an incremental update,
unchanged pages stay `fresh` (their symbols didn't move, so they're still valid
at the new commit) without needing a SHA bump. Connect (and the future dependency-link
compat check) and the consumer version check both read the *per-repo* commit. (Optional: a page may
carry `synthesized_at: <sha>` purely as an audit trail of when its prose was last
written — separate from version-pinning, and never the currency source of truth.)

Per-repo frontmatter (`wiki/code/<slug>/index.md`):
```yaml
slug: <slug>
commit: <sha>            # the one ingested source commit for this silo
scip_tool: scip-clang@<v> | scip-python@<v>
updated: YYYY-MM-DD
```

Per-page frontmatter (every other page):
```yaml
provenance: extracted | inferred | mixed
concept: <concept-slug>  # concept + evidence pages
updated: YYYY-MM-DD
status: fresh | stale     # currency vs the silo's commit; set by Stage 2 diff
# synthesized_at: <sha>   # optional audit trail only, not a version pin
```

---

## How this minimizes hallucination

1. Synthesis traverses a **deterministic SCIP graph**, not grep guesses — the
   agent navigates real call/ref edges to find the right code.
2. **Citation linter** rejects any claim citing a non-existent symbol — a
   hallucinated API name cannot survive the build.
3. **extracted vs inferred** provenance separates fact from model guess at the
   page and claim level.
4. **L2 static evidence** grounds dynamics claims in tests and the actual
   scheduler/stream/collective source — and design-intent is labeled as such, so
   it is never confused with measured runtime behavior.
5. Contradictions are marked, never silently overwritten; the human adjudicates.

## How this minimizes effort

- **Query time**: agent reads `index.md` → the concept page answers directly →
  citations let it verify or drill down deterministically. No re-derivation.
- **New repo**: drop a `config/<slug>.md`, run `/wikify-ingest-repo <slug>`.
  Concepts inherit a shared default + type-aware set, overridden per repo.
- **Upgrade**: `/wikify-ingest-repo <slug> --ref <new>` — reconcile re-runs only
  `changed` symbols and `stale` pages.
- **Scale (PyTorch)**: the SCIP index holds *all* symbols (ingestion-side);
  markdown materializes concept pages plus one deterministic catalog page per
  module. The structural layer is exhaustive and cheap; the prose layer is
  concept-scoped and curated. The shipped tree stays proportionate.
- **Multi-repo**: silos are ingested/updated independently; connect re-runs as a
  cheap deterministic post-pass (dependency links are free), so adding the Nth
  repo costs one ingest + one connect, not a re-build of the whole wiki.

---

## Per-repo config (markdown, not TOML)

Config is **markdown with YAML frontmatter** — same shape as every wiki page, so
the agent edits it exactly as it edits pages and there is no second syntax.
Frontmatter carries typed scalars; the body carries the concept list (the wiki's
table of contents), which benefits from being an annotatable markdown list.

It is an **authored input** — neither derived nor product — so it lives at the
project **top level**, alongside the schema (like `CLAUDE.md` in the Karpathy
pattern), NOT under `wiki/`, `raw/`, or `.cache/`. One file per repo:
`config/<slug>.md` (or a single `wikify.md` with per-repo sections).

```markdown
---
slug: torch_tpu
languages: [cpp, python]
build: bazel                     # cmake | bazel | path/to/compile_commands.json
ref: a1b9f0c                     # pinned commit / tag
tests: ["test/**/*.py", "test/cpp/**/*.cpp"]
docs:  ["**/README*.md", "docs/**/*.md", "**/*RFC*.md"]
---

# torch_tpu — ingest config

Concepts inherit a shared default + a type-aware set (Stage 5); the list below
overrides/extends. Seeds are optional entry-point symbols; `(auto)` = discover.

## Concepts
- **compilation-pipeline** — seeds: `LazyGraphExecutor::Compile`, `Compiler::LowerToHlo`
- **dispatch-path** — seeds: (auto)
- **compute-comm-overlap** — seeds: `CollectiveScheduler::Schedule`
- **memory-management** — seeds: `BufferAllocator::Allocate`  <!-- added 2026-06-19 -->

<!-- Enrichment workloads (OPTIONAL, downstream L4 only):
     llama3_8b_fwd: python bench/llama3.py --steps 5 --dump-hlo -->
```

The schema linter validates this file's structure (known frontmatter keys, a
`## Concepts` list) the same way it validates pages — recovering the strict
parse TOML would give, with tooling already in the build.

---

## Distribution

- Ship the `wiki/` markdown tree + the `commit` pins. Nothing else.
- **Two shippable forms**: a single **standalone silo** (pre-connect, link-free)
  or the **connected wiki** (silos + inline cross-links on the concept pages). Both
  are pure markdown; the connected form just has links resolved.
- On consume, verify each repo's installed version == `commit` pin; mismatch →
  warn and mark affected concept pages `version-skew`. (If dependency-links — Stage 7
  "(a)", not yet automated — are ever added, a compatible-commit-pair check gates
  them; its record form is TBD but it is *not* a `_connect/` directory.)
- `extracted` pages are safe to trust as facts. `inferred` pages are
  interpretations frozen at ingest — surfaced as such so no consumer mistakes a
  guess for ground truth.

---

## Build vs reuse

- **External tools (invoked, not vendored)**: `scip-python`, `scip-clang`, the
  SCIP format + `scip` CLI, your XProf MCP (only for the optional L4 pass). No
  database, no new runtime dep.
- **Build (small, targeted)**: the dispatch/registration extractor (Stage 3),
  the concept-driven synthesis prompt + traversal (Stage 5), the citation linter
  (Stage 6), the SCIP diff (Stage 2), and the connect pass (Stage 7:
  cross-repo citation re-resolution + the concept-correspondence judgment cache +
  the compat/version-coherence check). None of these are large; they're glue
  around deterministic tools.
- **Do not** rebuild a generic AST/graph extractor — SCIP is strictly better
  here, especially for C++ macros/templates, and it's already markdown-renderable.

---

## Prior art — borrowed concepts (no code reuse)

We build the glue ourselves; we only **borrow ideas** that make sense. Nothing
below is vendored or taken as a dependency. (The SCIP indexers are *invoked as
external tools*, like a compiler — that is not code reuse.)

- **context-sherpa** → symbol pruning + importance ranking. Drop locals,
  anonymous closures, and stdlib symbols from the graph; rank importance by a
  simple reference-count formula (no community detection). Reimplemented over our
  own SCIP read; we do not take its SQLite storage.
- **graphify** → (1) the `extracted / inferred / ambiguous` confidence
  vocabulary (maps onto our provenance model); (2) a **PreToolUse hook** that
  nudges the agent to query the wiki instead of grepping raw files — our
  distribution mechanism inside Claude Code; (3) its markdown-wiki emission as a
  rendering reference. Concept only; its tree-sitter core is out of scope.
- **AutoDocs/Sita** → **topological dependency ordering**: generate in dependency
  order so cross-repo links resolve (ingest xla before torch_tpu), and order
  concept synthesis leaf-up within a repo.
- **Karpathy LLM-wiki / the autoresearch `SCHEMA.md`** → the ingest/lint
  operations model, `raw/` vs `wiki/` separation, `index.md` + `log.md`
  discoverability. v1 writes its **own** self-contained schema (standalone repo);
  it borrows these *patterns* conceptually but does not share files. Aligning the
  two schemas so the autoresearch wiki can consume these silos is a later phase.
- **CodeGraphContext** → explicitly **not** adopted. Borrow only the
  graceful-fallback idea and the `.cgcignore` convention. Rejected as a
  dependency because it couples to a graph database and its SCIP mode skips
  Python (scip-python); we call the indexers directly instead.

---

## Stage 7 — Multi-repo connection (`/wikify-connect-repo`)

A single ingest produces a **silo**. With several silos in one wiki, connect
them. Connecting *everything* is noise; the rule below keeps it selective.

### Connect is two operations, not one

**(a) Dependency links — deterministic, exhaustive, almost free.** Some repos
literally use each other (torch_tpu → xla, maxtext → jax, torchax → jax). In the
silo, a symbol from another repo is just an *unresolved external citation*. Once
that repo is in the wiki, connect **re-resolves the external citation against the
other repo's SCIP index** and upgrades the dangling reference into a real
cross-repo link. This touches citation/catalog links only — **no mechanism prose
is rewritten** — is deterministic, and has **zero churn**. Connect all of these.

**(b) Concept correspondences — semantic, selective, judgment-cached.** Repos
that independently implement the same idea (sharding, attention, remat) with no
import relationship. Selected by the **shared concept vocabulary**, NOT by
intra-repo centrality (a concept central to one repo may have no analog
elsewhere; importance-*within* ≠ importance-*for-connection*). connect links
concept pages that share a concept key across repos, and the LLM decides whether
a given correspondence is worth keeping.

### Links are inline; connect is a re-runnable transform

Cross-links live **inline in the prose** (it's a wiki, not silos + a side-table —
there is **no `_connect/` directory**). The regeneration problem is solved not by
segregating links but by treating them as **derived, not authored**: connect is an
**idempotent post-pass re-run after every update**. Pipeline is `ingest|update → connect`.
Links sit in delimited `connect:auto` blocks that are recomputed wholesale.

- **Dependency links** re-derive with zero churn (deterministic citation
  re-resolution).
- **Concept links** need no decision side-file: **connection state is the wiki itself** — a
  concept page carrying a `connect:auto` block is connected. `wikify connect --refresh` regenerates
  those blocks after any ingest; the human's original *which-concepts* choice is preserved because it
  is exactly the set of pages that already carry a block. A stray name-match is dropped with
  `--exclude` (or fixed at the source by an explicit `concepts:` tag).

### The shared concept vocabulary is host-owned; candidates are generated, not invented

The concept keys that drive (b) — `splash-attention`, `remat`, `sharding`, … — are
**not** wikify's; they are the host wiki's vocabulary (its `wiki/concepts/` filenames;
`--vocab <dir>` overrides), read by connect directly from the wiki — no per-repo config. wikify
supplies the *mechanism*; the wiki supplies the *terms*, so a non-TPU wiki grows its own
spine. Correspondence is never free-form LLM matching: connect **generates candidates
deterministically** — a silo concept whose symbols share a name/moniker, a vendored-from
lineage, or a synthesis-emitted `concepts:` tag matching a vocabulary entry — then the LLM
only **confirms or rejects** each candidate (the analog of devirtualization's CHA
candidates + judgment). Grounded proposal, judgment on top; the LLM never conjures a
correspondence from nothing.

### Links are inline through the concept page — no side-table, no new primitive

Connection is **wiki-native**: the correspondence lives as ordinary links on pages that already
exist. The host's shared concept page (`wiki/concepts/<key>.md` — already curated, not a new
artifact) is the **hub**: it links *down* to every repo's implementation page, and each
implementation links *up* to the concept. This is `wiki ↔ silo`, routed through the concept page —
**not** `silo ↔ silo` (a full mesh is O(N²) and churns every page when a repo is added) and **not**
a generated `_connect/` index (that would be the side-table this section's opening warns against).
Adding a repo touches its own page + one line per hub: O(N).

The links sit in delimited `connect:auto` blocks — a `## In this wiki's repos` list on the concept
page, a one-line up-link on each silo page — so connect regenerates them without touching
hand-written prose (the same discipline as coverage catalogs). **Connection state is the wiki
itself**: a concept page carrying a block is connected, so there is no decision side-file to keep in
sync; `wikify connect --refresh` re-derives the blocks after any ingest.

### Selective by design — the human picks which concepts to connect

Connecting *every* matched concept to *every* repo drowns the pages, so connect is deliberately
selective, and the selection is the **interactive step**: `wikify connect` (deterministic, writes
nothing) proposes the candidate concepts and their repos; the human chooses which to wire; `wikify
connect --apply <keys>` inserts the links for exactly those. Candidates come from an explicit
`concepts:` frontmatter tag (authoritative — synthesis stamps it from the vocabulary handed into the
packet) or a name/token match; a stray name-collision is dropped with `--exclude`. The *proposal* and
the *link insertion* are pure Python; the only judgment is which concepts belong in the spine.

### Optionally, a concept page becomes a real hub

A connected concept page can grow beyond the auto link-list into a genuine cross-repo **hub** — a
lens-framed definition and a short *how the implementations differ* synthesis, authored in the page's
prose **above** the `connect:auto` block and grounded in the linked silo pages. This is optional LLM
work (the `wikify-connect-repo` skill), never required, and it never rewrites the linted silo pages —
only their one-line up-link block is machine-managed.

### Interactive & context-dependent

Ingest and connect **ask when the answer isn't already in context, and proceed otherwise**
(so batch ingests stay non-interactive):

1. **Focus.** If `synthesis_focus` is set, or a host-wiki lens is detectable, use it; else
   *ask* for the lens before synthesis.
2. **Agenda.** After the first synthesis pass, present the *derived, centrality-ranked* agenda
   from `discover.py` — "I deep-dove these; here are the next N I could" — and let the operator
   add concepts. A grounded menu, never a free-form ask (an ungroundable concept has no packet
   symbols to cite).
3. **Register.** finalize + the skill's register step wire the silo into `index.md` / `log.md`.
4. **Connect.** `wikify connect` proposes; the human picks *which concepts* to wire; `--apply`
   inserts the inline links; `--refresh` regenerates already-connected concepts. Non-interactive
   runs do only `--refresh` (never auto-connect new concepts).

This makes `wikify-connect-repo` the natural tail of `wikify-ingest-repo` — invoked from the
*second* repo onward (the first has nothing to connect to) — and separately re-runnable whenever a
later repo must wire into the existing spine.

### Guardrails

- **connect inserts links only; it never re-synthesizes prose** — the sole optional
  exception is *hub prose* a human adds to a **concept page** (`wiki/concepts/<key>.md`),
  above its `connect:auto` block (see "Optionally, a concept page becomes a real hub"). It
  writes the delimited link blocks and re-lints. Rewriting claims on already-linted *silo*
  pages would re-open the hallucination surface, so that is never done — only a silo page's
  one-line up-link block is machine-managed.
- **Version coherence.** A dependency edge is valid only for a *compatible pair
  of pinned commits* (torch_tpu@sha1 was built against xla@sha2). connect
  **refuses to link** silos whose commits weren't built compatibly, and marks
  such pairs `version-incompatible` rather than emitting links that lie.
- **Direction is asymmetric.** "A uses `B::X`" is a useful per-page link; the
  reverse (B → every consumer) is a large fan-in — aggregate it on B's catalog
  entry as a count/list, don't enumerate it inline on every page.
- **Staleness.** Re-ingesting repo A (`/wikify-ingest-repo A --ref ...`)
  re-triggers connect for A **and
  its cross-repo neighbors**: dependency edges are re-checked (does the SCIP
  symbol still resolve?), concept links re-validated against the decision cache.
- **Pure silo recoverable.** The standalone, link-free silo is just the
  pre-connect ingest output — ship that form when distributing one repo alone.

### Layout

See the canonical layout under **Wiki output schema** above: per-repo silos at
`wiki/code/<slug>/`, the shared concept vocabulary + hubs at `wiki/concepts/<key>.md` (where the
inline cross-repo links live — there is no `_connect/` directory), and SCIP indexes at
`.cache/scip/<slug>.scip` (derived — read by connect, never under `raw/`).

---

## Worked examples

> Symbol names below are **illustrative** for a hypothetical `torch_tpu` backend,
> to show the schema — not claims about real code. For *real* examples, read an
> ingested silo in this repo's `wiki/` (pytorch, jax, xla, torch_tpu, torchtitan)
> or the public demo: <https://github.com/vlasenkoalexey/wikify-repo-demo>.

### Layout (concrete, current)

```
wiki/
  index.md                     top-level curated catalog (all repos)
  concepts/                    host-owned cross-repo concept vocabulary (Stage 7)
    splash-attention.md
  code/                        wiki_subdir (default "code"; "" = flat)
    torch_tpu/                 one silo per ingested repo
      index.md                 per-repo catalog (generated by finalize)
      overview.md              synthesized front door (concepts map + system diagrams)
      concepts/
        compilation-pipeline.md
        compute-comm-overlap.md
      doc-concepts/            grounded pages extracted from the repo's own docs
        quantization.md
      catalog/                 Stage 6b: one page per module — every symbol's home
        torch_tpu/csrc/compiler.md
```

### Example — citing a symbol from a mechanism page (current format)

A concept page cites **catalog anchors** (invariant 7 — there are no per-symbol
stub files). In `concepts/compilation-pipeline.md`:

```markdown
## Mechanism (step-by-step)
1. [`Compiler.LowerToHlo`](../catalog/torch_tpu/csrc/compiler.md#Compiler.LowerToHlo)
   folds the traced graph into HLO before partitioning.

> [!inferred]
> The retry loop probably exists to absorb transient TPU runtime resets — no
> cited symbol states this.
```

The linter resolves the anchor via the catalog page's frontmatter (rule 1), and
rules 2–3 gate uncited Mechanism items and out-of-subgraph symbols. In
`catalog/torch_tpu/csrc/compiler.md`:

```markdown
---
type: catalog
module: torch_tpu/csrc/compiler
symbol_base: "scip-clang cxx torch_tpu . . `torch_tpu::"
symbols:
  Compiler.LowerToHlo: "Compiler#LowerToHlo(49f6a3c887f6a086)."
---
```

`symbol_base + symbols[anchor]` reconstructs the full SCIP moniker, which must
exist in the silo's graph — a citation cannot name a symbol the compiler
frontend didn't resolve.

### Example — cross-repo connection (`/wikify-connect-repo`)

Connection is **inline**, through the host's concept page — no `_connect/` directory. The human
picked `splash-attention` at the connect phase; `wikify connect --apply splash-attention` then wrote
two delimited blocks.

**On the concept page** `wiki/concepts/splash-attention.md` — a down-block linking every repo's
implementation (regenerated on `--refresh`; hand prose above it is untouched):

```markdown
<!-- connect:auto:begin -->
## In this wiki's repos
Grounded implementations of **splash-attention** across the ingested repos:
- [maxtext](../code/maxtext/concepts/maxtext-kernels-attention-splash_attention_kernel.md) — SplashAttention backward
- [tokamax](../code/tokamax/concepts/tokamax-...-splash_attention_kernel.md) — extended splash scheduler
- [jax](../code/jax/concepts/jax-...-splash_attention.md) — the reference TPU kernel
<!-- connect:auto:end -->
```

**On each silo page** — a one-line up-link back to the concept it's part of:

```markdown
<!-- connect:up:begin -->
> **Cross-repo concept:** part of [splash-attention](../../../concepts/splash-attention.md) across this wiki's repos.
<!-- connect:up:end -->
```

A concept the human *didn't* pick stays unconnected. Connection state is these blocks themselves —
no side-file. (The separate, not-yet-automated dependency-link op — Stage 7 "(a)" — would instead
re-resolve a torch_tpu → xla external citation against `.cache/scip/xla.scip`; that touches a
citation, not prose, and is gated on a compatible commit pair.)
