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
   consuming agent wants is served by markdown itself: cited symbol stubs list
   callers/callees as relative links (an adjacency list, grep-able). **Consumers
   need only grep + a text editor + their agent.**

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
   precondition for whole-repo coverage. Catalog entries are `extracted`
   provenance; any future heuristic bridge carries `heuristic`/`inferred`.

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
     (→ gaps). Boundaries are computed; size adapts to the repo.
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
| Python | `scip-python` | pyright-backed; type-resolved refs. Works build-free. |
| C++ | `scip-clang` | needs `compile_commands.json`. Resolves macros/templates. |
| Mixed (PyTorch) | both, merged | run per-language, union the SCIP indexes. |

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

### Stage 3 — Dispatch / registration extractor (targeted, deterministic)
Generic SCIP misses registration tables, which are central to a backend.
- PyTorch / torch_tpu: parse `TORCH_LIBRARY`, `TORCH_LIBRARY_IMPL`, `m.impl(...)`,
  and `native_functions.yaml` → deterministic **op → kernel → backend/dispatch-key**
  map. For a backend (xla, torch_tpu) this *is* the spine of "what it does".
- JAX-family (if ever ingested): primitive registration + lowering-rule tables.
- Output: `wiki/maps/dispatch.md` — a generated table, every row cited to the
  registration site moniker. Regenerated, never hand-edited.

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
1. **Discovery (primary, automated).** Cluster the symbol graph into cohesive
   units (default: the module/package tree, refinable by graph community
   structure), rank by centrality (aggregate fan-in), and **assign a tier by
   relative rank** so thresholds adapt to any repo size. Auto-seed each unit from
   its highest-centrality symbols — no hand-seeding. This is the bulk of the
   agenda for every repo.
2. **Shared + type-aware defaults.** A stable domain set (`compilation-pipeline,
   dispatch-path, compute-comm-overlap, …`) and a per-repo-kind set (trainer →
   `sharding / checkpointing / data-pipeline`; Pallas → `block-sizing / autotune /
   numerics`) seed the agenda before discovery refines it.
3. **Curation (optional override).** The user edits `config/<slug>.md` to add or
   re-tier a concept, or supply seed symbols — worth it only for priority repos;
   never required.

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
marked stale), materializes newly-cited stubs, wires see-also/back-links
(link-insertion only), re-lints, and — if the repo is connected — re-runs connect
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
  <slug>/                      one self-contained silo per repo
    index.md                   per-repo catalog
    concepts/<concept>.md      L3 mechanism pages (the answer surface)
    catalog/<module-path>.md   Stage-6b generated structural index per module
                               (the whole-repo coverage floor; one per def-file).
                               ALSO the home of every symbol: its frontmatter
                               `symbols:` map (anchor→moniker) is the citation
                               target + the linter's resolution table. Citations
                               are `../catalog/<module>.md#<QualifiedName>`.
    maps/dispatch.md           generated op→kernel→backend table
    tests/<area>.md            L2 test-spec pages
    sources/<name>.md          L2 in-repo design docs / RFC notes

    (No `symbols/` directory: per-symbol stubs were folded into `catalog/` —
     one source-tree-organized home per symbol. A cross-repo connect link
     therefore targets a catalog anchor, not a stub file.)
  _connect/                    cross-repo layer (present only when >1 repo)
    decisions.md               concept-correspondence keep/drop cache
    compat.md                  linkable (repo@sha, repo@sha) pairs

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
at the new commit) without needing a SHA bump. Connect's `compat.md` and the
consumer version check both read the *per-repo* commit. (Optional: a page may
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
  markdown materializes only concept pages, the dispatch map, and cited-symbol
  stubs. The structural layer is exhaustive and cheap; the prose layer is
  concept-scoped and curated. The shipped tree stays small.
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
  or the **connected wiki** (silos + inline cross-links + `_connect/`). Both are
  pure markdown; the connected form just has links resolved.
- On consume, verify each repo's installed version == `commit` pin; mismatch →
  warn and mark affected concept pages `version-skew`. For the connected form,
  also honor `_connect/compat.md` — cross-links are valid only for the pinned
  pairs recorded there.
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
cross-repo link. This touches citations/stubs only — **no mechanism prose is
rewritten** — is deterministic, and has **zero churn**. Connect all of these.

**(b) Concept correspondences — semantic, selective, judgment-cached.** Repos
that independently implement the same idea (sharding, attention, remat) with no
import relationship. Selected by the **shared concept vocabulary**, NOT by
intra-repo centrality (a concept central to one repo may have no analog
elsewhere; importance-*within* ≠ importance-*for-connection*). connect links
concept pages that share a concept key across repos, and the LLM decides whether
a given correspondence is worth keeping.

### Links are inline; connect is a re-runnable transform

Cross-links live **inline in the prose** (it's a wiki, not silos + a side-table).
The regeneration problem is solved not by segregating links but by treating them
as **derived, not authored**: connect is an **idempotent post-pass re-run after
every update**. Pipeline is `ingest|update → connect`. Nothing is preserved
across regeneration because everything is recomputed.

- **Dependency links** re-derive with zero churn (deterministic citation
  re-resolution).
- **Concept links** would churn under naive re-derivation (LLM may decide
  differently each run), so persist the *decision* — a small keep/drop cache
  (`A:sharding ↔ B:sharding = keep`) under `wiki/_connect/decisions.md`. connect
  consults it instead of re-litigating every correspondence. The cache is
  **metadata**; the visible link still renders inline.

### Guardrails

- **connect inserts links only; it never re-synthesizes prose.** It upgrades a
  citation or appends a "see also / compare" reference, then **re-lints**.
  Rewriting claims would re-open the hallucination surface on already-linted pages.
- **Version coherence.** A dependency edge is valid only for a *compatible pair
  of pinned commits* (torch_tpu@sha1 was built against xla@sha2). connect
  **refuses to link** silos whose commits weren't built compatibly, and marks
  such pairs `version-incompatible` rather than emitting links that lie.
- **Direction is asymmetric.** "A uses `B::X`" is a useful per-page link; the
  reverse (B → every consumer) is a large fan-in — aggregate it on B's stub as a
  count/list, don't enumerate it inline on every page.
- **Staleness.** Re-ingesting repo A (`/wikify-ingest-repo A --ref ...`)
  re-triggers connect for A **and
  its cross-repo neighbors**: dependency edges are re-checked (does the SCIP
  symbol still resolve?), concept links re-validated against the decision cache.
- **Pure silo recoverable.** The standalone, link-free silo is just the
  pre-connect ingest output — ship that form when distributing one repo alone.

### Layout

See the canonical three-bucket layout under **Wiki output schema** above:
per-repo silos at `wiki/code/<slug>/`, the cross-repo layer at `wiki/_connect/`
(`decisions.md` + `compat.md`), and SCIP indexes at `.cache/scip/<slug>.scip`
(derived — read by connect, never under `raw/`).

---

## Worked examples

> Symbol names below are **illustrative** for a hypothetical `torch_tpu` backend,
> to show the schema — not claims about real code. The linter checks every
> citation link resolves and that its moniker exists in the SCIP index.
>
> ⚠️ **Superseded:** these examples predate two realized decisions (see the
> Decisions log and `implementation.md` §10). The per-symbol `symbols/<…>.md` stub
> files **no longer exist** — every symbol's home is its **module catalog**
> (`catalog/<module>.md`), and a citation targets a catalog anchor
> (`../catalog/<module>.md#Qualified.Name`) resolved via the catalog's
> `symbol_base` + `symbols` frontmatter map. Read the `symbols/...md (stub)` pages
> below as "the symbol's catalog entry." The catalog format also evolved
> (per-member detail + docstrings + relative source links).

### Layout (concrete)

```
wiki/
  index.md                   top-level catalog (all repos)
  torch_tpu/                 the silo
    index.md
    overview.md              synthesized top-level overview (front door)
    concepts/
      compilation-pipeline.md
      dispatch-path.md
      compute-comm-overlap.md
      memory-management.md
    catalog/                 one page per module — every symbol's home
      torch_tpu/...md        (replaces the old symbols/ stub dir)
    maps/
      dispatch.md
    tests/
      compute-comm-overlap.md
    sources/
      torch_tpu-design-notes.md
  _connect/                  present once a second repo is connected
.cache/
  scip/torch_tpu.scip        # derived, ingestion-side, not shipped
raw/
  code/torch_tpu/            # submodule @ pinned sha (immutable input)
```

### Example 1 — `wiki/torch_tpu/index.md` (per-repo catalog)

```markdown
---
title: torch_tpu — wiki index
commit: a1b9f0c
scip_tool: scip-clang@0.3.x, scip-python@0.6.x
updated: 2026-06-19
---

# torch_tpu internals wiki

Generated, grounded wiki. Start from a concept; drill into cited symbols.

## Concepts
| Concept | Page | Status |
|---|---|---|
| Compilation pipeline | [compilation-pipeline](concepts/compilation-pipeline.md) | fresh |
| Dispatch path | [dispatch-path](concepts/dispatch-path.md) | fresh |
| Compute/comm overlap | [compute-comm-overlap](concepts/compute-comm-overlap.md) | behavioral_recheck |
| Memory management | [memory-management](concepts/memory-management.md) | fresh |

## Maps
- [Dispatch map](maps/dispatch.md) — op → kernel → dispatch key (generated)

## Provenance
`extracted` = from SCIP / source. `inferred` = LLM judgment, treat as such.
Design-intent dynamics are labeled; none are runtime-measured (no L4 pass run).
```

### Example 2 — `wiki/torch_tpu/concepts/compilation-pipeline.md` (mechanism page)

```markdown
---
title: Compilation pipeline
type: concept
provenance: mixed
concept: compilation-pipeline
updated: 2026-06-19
status: fresh
---

# Compilation pipeline

How a traced graph becomes a cached TPU executable.

## Entry points
- [`LazyGraphExecutor::Compile`](../symbols/cxx-torch_tpu-LazyGraphExecutor-Compile.md)
  — called when a pending trace is flushed (mark-step or a value is read).
- [`Compiler::LowerToHlo`](../symbols/cxx-torch_tpu-Compiler-LowerToHlo.md)
  — converts the device IR to HLO.

## Mechanism (step-by-step)
1. Op execution is deferred; ops accumulate as device IR nodes until a barrier
   forces a flush. [extracted →
   `LazyGraphExecutor::Compile`](../symbols/cxx-torch_tpu-LazyGraphExecutor-Compile.md)
2. The pending graph is hashed; a hit in the executable cache short-circuits
   compilation. [extracted →
   `LazyGraphExecutor::Compile`](../symbols/cxx-torch_tpu-LazyGraphExecutor-Compile.md)
3. On a miss, the IR is lowered to HLO and handed to XLA for compilation.
   [extracted →
   `Compiler::LowerToHlo`](../symbols/cxx-torch_tpu-Compiler-LowerToHlo.md)

> [!inferred]
> The cache key appears to include the device mesh shape, so re-sharding likely
> forces recompilation. Not confirmed against a registration site — verify.

## Key data structures
- Executable cache (graph-hash → compiled executable). See the entry-point stub.

## Dynamics (design intent)
Compilation is synchronous on the calling thread; the cache is what keeps
steady-state steps from recompiling. Grounded in tests, not a trace:
[overlap test-spec](../tests/compute-comm-overlap.md).

## Open questions
- Is compilation ever moved off the calling thread? No async-compile symbol found.

## See also
- [Dispatch path](dispatch-path.md) · [Dispatch map](../maps/dispatch.md)
```

### Example 3 — `wiki/torch_tpu/symbols/cxx-torch_tpu-Compiler-LowerToHlo.md` (stub)

```markdown
---
title: "Compiler::LowerToHlo"
type: symbol
provenance: extracted
moniker: "scip-clang cxx torch_tpu a1b9f0c torch_tpu/compiler/`Compiler`#LowerToHlo()."
updated: 2026-06-19
---

# Compiler::LowerToHlo

**Defined:** `torch_tpu/compiler/compiler.cc:212`
**Signature:** `xla::XlaComputation Compiler::LowerToHlo(const IrGraph&)`

## Called by
- [`LazyGraphExecutor::Compile`](cxx-torch_tpu-LazyGraphExecutor-Compile.md)

## Calls
- `LoweringContext::Build` · `xla::XlaBuilder::Build`  *(uncited: not yet stubbed)*

## Cited by
- [Compilation pipeline](../concepts/compilation-pipeline.md)
```

### Example 4 — `wiki/torch_tpu/maps/dispatch.md` (generated excerpt)

```markdown
---
title: Dispatch map
type: map
provenance: extracted
updated: 2026-06-19
---

# Dispatch map (op → kernel → key)

Generated from `TORCH_LIBRARY_IMPL` sites. Do not hand-edit.

| ATen op | Kernel symbol | Dispatch key | Registration |
|---|---|---|---|
| `aten::mm` | `torch_tpu::mm` | `TPU` | `ops/matmul.cc:48` |
| `aten::add.Tensor` | `torch_tpu::add` | `TPU` | `ops/binary.cc:91` |
| `c10d::allreduce_` | `torch_tpu::allreduce_` | `TPU` | `distributed/coll.cc:33` |
```

### Example 5 — `wiki/torch_tpu/tests/compute-comm-overlap.md` (test-spec)

```markdown
---
title: "Tests: compute/comm overlap"
type: test-spec
provenance: extracted
concept: compute-comm-overlap
updated: 2026-06-19
---

# Tests exercising compute/comm overlap

| Test | Asserts | Exercises (SCIP refs) |
|---|---|---|
| `test_allreduce_overlaps_matmul` | collective issued before dependent matmul completes | [`CollectiveScheduler::Schedule`](../symbols/cxx-torch_tpu-CollectiveScheduler-Schedule.md) |
| `test_no_overlap_when_serialized` | `TORCH_TPU_DISABLE_OVERLAP=1` serializes the two | same |

These pin **intended** overlap behavior. Whether the TPU runtime achieves it is
a runtime question — answerable only by the optional L4 enrichment, not here.
```

### Example 6 — cross-repo connection (`/wikify-connect-repo`)

**Before connect** — torch_tpu's stub has a dangling external reference
(`cxx-torch_tpu-Compiler-LowerToHlo.md`):

```markdown
## Calls
- `LoweringContext::Build` · `xla::XlaBuilder::Build`  *(uncited: external)*
```

**After connect** (xla silo is present, commits compatible) — the external ref
is re-resolved against `.cache/scip/xla.scip` and upgraded to a real cross-repo
link. Prose is untouched; only the citation changed:

```markdown
## Calls
- [`LoweringContext::Build`](../../xla/symbols/cxx-xla-LoweringContext-Build.md)
- [`xla::XlaBuilder::Build`](../../xla/symbols/cxx-xla-XlaBuilder-Build.md)
```

**Concept-correspondence decision cache** — `wiki/_connect/decisions.md`:

```markdown
| Concept | repo A page | repo B page | decision |
|---|---|---|---|
| compute-comm-overlap | torch_tpu/concepts/compute-comm-overlap | maxtext/concepts/compute-comm-overlap | keep |
| memory-management | torch_tpu/concepts/memory-management | jax/concepts/memory-management | drop (different abstraction) |
```

**Compatibility record** — `wiki/_connect/compat.md`:

```markdown
| repo A @ sha | repo B @ sha | linkable |
|---|---|---|
| torch_tpu@a1b9f0c | xla@7e3d12a | yes |
| torch_tpu@a1b9f0c | jax@0.9.2 | yes |
```
