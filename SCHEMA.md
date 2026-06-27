# wikify-repo — project memory

## What this is
A standalone Python CLI + a tool-neutral agent skill that ingest a code repo into
a grounded, lint-clean **markdown** wiki an agent can answer internals questions
from. v1 is standalone (NOT merged into the autoresearch repo) and Python-only.

> **Ingesting a repo:** follow the procedure in `skills/wikify-ingest-repo/SKILL.md` — a
> tool-neutral markdown procedure (Claude Code runs it as a skill; Codex and Antigravity read
> it via this file). Just ask your agent to: ingest <repo-url-or-local-path>.

## Source of truth — read both before writing code
- `docs/design.md` — what & why (architecture, schema, tradeoffs). Don't relitigate.
- `docs/implementation.md` — how (stack, repo layout, data contracts, phased plan,
  the synthesis prompt, acceptance tests). **Build from this.**

If anything here conflicts with the docs, the docs win. If the docs are silent or
ambiguous, ask — do not guess and invent.

## Non-negotiable invariants
1. **Markdown is the only shipped product.** No SQLite / JSONL / graph DB in
   output. SCIP indexes and build artifacts live in gitignored `.cache/`, never
   in `raw/` (raw = immutable inputs only).
2. **The Python/LLM split is hard.** Deterministic stages (SCIP parse, diff,
   dispatch, lint, dependency-links, **coverage/catalogs**) are pure Python — zero
   model calls. Only concern *synthesis* and concept-link *judgment* are LLM,
   driven by SKILL.md, handed off via files (`prepare` → agent writes pages →
   `finalize`/lint). Never put synthesis in Python or linting in a prompt.
3. **Grounding before prose.** Synthesis uses ONLY symbols in the packet subgraph;
   every Entry-point / Mechanism claim cites a SCIP-resolved stub; uncited claims
   go in `> [!inferred]` blocks. The citation linter is a hard build gate.
4. **Idempotent reconcile.** `ingest` converges to {pinned commit × concern set}:
   re-run = no-op, or builds only the delta. `update` is just `ingest --ref`.
5. **Two-tier coverage — concerns for depth, catalogs for the whole repo.**
   Concern synthesis is top-down and *selective by design* (it avoids shallow
   per-file summaries), so on its own it silently drops whole subsystems. The
   floor against that is a deterministic **coverage** pass: a *set-difference over
   the SCIP symbol table* (enumeration, NOT a graph walk) emits one catalog page
   per module so every documentable symbol is represented. See "Why coverage is a
   set-difference" below. Coverage ≠ connection — it represents every module but
   does not invent the missing dynamic-dispatch edges.
6. **Comprehension is derived and graded — not authored and binary** (design
   decision 8). The agenda of what gets understood is computed from topology
   (module tree + centrality), not a hand-written concern list; LLM effort on a
   unit is *monotonic in its centrality*, a gradient of four bands: deep mechanism
   page → **docstring annotation** (author's words, `extracted`, free) → purpose
   blurb (LLM fallback where undocumented) → structural catalog. **Prefer
   docstrings over synthesis**: they are ground-truth comprehension at zero model
   cost, so spend the LLM only where the author was silent or the truth is
   cross-symbol. Discovery/ranking/tiering are deterministic; only synthesis and
   blurbs are LLM. *Implemented: coverage + catalogs + docstrings + `discover.py`
   (derived, centrality-ranked, auto-seeded agenda) + scaled synthesis. The
   mid-tier "purpose blurb" for undocumented modules is the one remaining band.*
   **Concern synthesis is HEAVY processing** (`skills/wikify-ingest-repo/prompts/synthesis.md`): the
   agent reads the real source (packets truncate) and writes Overview + a grounded
   **Mermaid diagram** + Design rationale + insight Mechanism with citations woven
   in — never a citation-per-clause trace. A per-repo **overview page**
   (`skills/wikify-ingest-repo/prompts/overview.md`) is synthesized last: main concepts + core
   system diagrams + a map of the wiki.
7. **Symbols live in their module catalog, not in per-symbol stubs.** A citation
   is a catalog anchor `../catalog/<module>.md#<QualifiedName>`; the catalog's
   frontmatter `symbols:` map (anchor→moniker) is the linter's resolution table.
   There is no `wiki/<slug>/symbols/` directory — it was folded into `catalog/`
   (one home per symbol, source-tree organized). `coverage.catalog_ref` /
   `qualified_name` are the single source of the anchor format, shared by the
   packet (what to cite) and the catalog (what resolves).

## Why coverage is a set-difference (read before touching ingestion)
The first torchtitan ingest covered the 3 hand-authored Trainer concerns and
**missed every model** (`Transformer`, `Attention`, …) — the essence of the repo.
Root cause analysis settled the approach:
- **Concern-driven ingest is selective on purpose.** Bottom-up "summarize every
  file" is the design's named anti-pattern (shallow answers). So selectivity is
  correct for *depth* — but it needs a coverage floor.
- **You cannot reach the missing code by traversal.** Models are invoked as
  `model_parts[0](inputs)` through `nn.Module.__call__` — a *dynamic* dispatch
  with **no static call edge**. Walking the call graph from entry points dies at
  that seam. A per-file "is it connected?" check is worse: it false-flags the
  model files as dead code (the same blind spot that makes name-based call graphs,
  e.g. CodeGraphContext's dead-code detector, wrong).
- **Enumeration sidesteps dispatch.** SCIP already indexed every symbol, so we
  never rely on reachability to *find* code. Coverage = `documentable_symbols −
  concern-cited` → emit a catalog page per module. Deterministic, no LLM, can't
  miss a file.
- **Coverage ≠ connection.** Catalogs represent and *internally* connect each
  module (intra-module edges are real static calls). They do NOT create the
  trainer→model edge, nor unify the N separate `Attention` classes into one
  concept — those are separate, optional ops (devirtualization / concept-
  correspondence), not prerequisites for whole-repo coverage.

Implemented in `wikify/coverage.py`; emitted by `finalize` (Stage 6b) and
inspectable via `wikify coverage <slug>`.

## Status (was "Phase 1 only" — now well beyond)
Phase 1 (Python end-to-end) ✅ and Phase 2 (C++) ✅ are done, validated on
**pytorch, jax, torch_tpu** (mixed C++/Python) plus torchtitan. Realized beyond
the original plan — **read `design.md` "Decisions log" + `implementation.md` §10
(authoritative) before touching these**:
- Stage 1: sharded scip-python (`index_shards`) + AST fallback + orphan-synthesis;
  C++ via scip-clang with an auto-generated bazel compile DB (`bazel_targets`).
- build_graph: devirtualization (CHA over `is_implementation`).
- packets: relevance-bounded subgraph.
- Stage 6: catalog format (symbol_base compression, per-member detail + docstrings,
  RELATIVE source links, test-filtered/importance-ranked uses-by); `finalize --fix`;
  adversarial `verify`; synthesized `overview.md` linked from the index.
- `symbols/` stubs are gone — folded into `catalog/` (see invariant 7).

The risky foundation remains the **SCIP-occurrence → callers/callees derivation**
(`implementation.md` §5.1) — reference-scoping, since SCIP has no "call" role;
keep its focused pytest green. New mechanisms each ship with a pinning test (now
93 tests).
