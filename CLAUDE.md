# wikify-repo — project memory

## What this is
A standalone Python CLI + Claude Code skills that ingest a code repo into a
grounded, lint-clean **markdown** wiki an agent can answer internals questions
from. v1 is standalone (NOT merged into the autoresearch repo) and Python-only.

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

## Current objective: PHASE 1 ONLY
Build Phase 1 per `docs/implementation.md` §6 — one pure-Python repo end to end
(scip-python → symbol graph → packets → [agent synthesis] → lint → **coverage
catalogs** → markdown).
NOTHING else: no C++, no dispatch extractor, no connect, no discovery, no L4.
**Done = the §6 acceptance test passes on `torchtitan`, including coverage:
every class is represented in a concern page or a module catalog.** Do not start
later phases until Phase 1 passes and I confirm.

## Build order within Phase 1
Start with `scip_index.py` + `graph.py`, and specifically the **SCIP-occurrence →
callers/callees derivation** (`implementation.md` §5.1). It is the risky
foundation everything else rests on (SCIP has no "call" role; it's a
reference-scoping heuristic). Write a focused pytest validating callers/callees
against a small repo with a known call structure, and show it passing, BEFORE
building anything downstream.
