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
   dispatch, lint, dependency-links) are pure Python — zero model calls. Only
   concern *synthesis* and concept-link *judgment* are LLM, driven by SKILL.md,
   handed off via files (`prepare` → agent writes pages → `finalize`/lint). Never
   put synthesis in Python or linting in a prompt.
3. **Grounding before prose.** Synthesis uses ONLY symbols in the packet subgraph;
   every Entry-point / Mechanism claim cites a SCIP-resolved stub; uncited claims
   go in `> [!inferred]` blocks. The citation linter is a hard build gate.
4. **Idempotent reconcile.** `ingest` converges to {pinned commit × concern set}:
   re-run = no-op, or builds only the delta. `update` is just `ingest --ref`.

## Current objective: PHASE 1 ONLY
Build Phase 1 per `docs/implementation.md` §6 — one pure-Python repo end to end
(scip-python → symbol graph → packets → [agent synthesis] → lint → markdown).
NOTHING else: no C++, no dispatch extractor, no connect, no discovery, no L4.
**Done = the §6 acceptance test passes on `torchtitan`.** Do not start later
phases until Phase 1 passes and I confirm.

## Build order within Phase 1
Start with `scip_index.py` + `graph.py`, and specifically the **SCIP-occurrence →
callers/callees derivation** (`implementation.md` §5.1). It is the risky
foundation everything else rests on (SCIP has no "call" role; it's a
reference-scoping heuristic). Write a focused pytest validating callers/callees
against a small repo with a known call structure, and show it passing, BEFORE
building anything downstream.
