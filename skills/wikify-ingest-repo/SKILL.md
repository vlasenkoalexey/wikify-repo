---
name: wikify-ingest-repo
description: >
  Ingest a code repo into a grounded, lint-clean markdown wiki an agent can
  answer internals questions from. Idempotent reconcile — first build, version
  bump (--ref), or an added concern are all the same operation. Trigger when the
  user asks to wikify/ingest a repo, build an internals wiki, or update one.
---

# wikify-ingest-repo

Drive the deterministic `wikify` CLI around one LLM-in-the-loop step: **concern
synthesis**. The CLI does everything else (acquire, SCIP index, graph, diff,
packets, lint, assemble). You write one mechanism page per packet. Never put
synthesis in Python; never push linting into your prose.

## Preconditions
- `wikify` is on PATH (`pipx install wikify-repo`) and `scip-python` is installed
  (`npm i -g @sourcegraph/scip-python`; Node required). A `config/<slug>.md`
  exists (frontmatter + a `## Concerns` list with optional seed symbols).

## Procedure

1. **Prepare (deterministic, no model).** Run:
   ```
   wikify prepare <slug> [--ref <commit>]
   ```
   This acquires + pins the repo, runs scip-python, builds the symbol graph,
   prints the reconcile **plan** (will build / rebuild / leave), and writes one
   packet per to-build concern at `.cache/packets/<slug>/<concern>.md`. If the
   plan is a no-op, STOP — the wiki is already converged.

2. **Synthesize (this is your job).** For EACH packet the plan built, read the
   packet file and follow `${CLAUDE_SKILL_DIR}/prompts/synthesis.md` exactly to
   write:
   - the mechanism page `wiki/<slug>/concerns/<concern>.md`, and
   - a stub `wiki/<slug>/symbols/<stub-slug>.md` for every symbol you cite
     (copy the FULL moniker from the packet's Subgraph verbatim into the stub
     frontmatter — the linter resolves citations by that moniker).
   Cite ONLY symbols in the packet's Subgraph. Put any ungrounded claim in a
   `> [!inferred]` block. Read only the packet — do not open repo files outside it.

3. **Finalize (deterministic gate).** Run:
   ```
   wikify finalize <slug>
   ```
   The citation linter is a hard gate: every `symbols/*.md` citation must resolve
   to a real SCIP symbol, every Entry-points/Mechanism item must be cited, and no
   symbol outside the packet subgraph may appear. On success it also runs
   **Stage 6b coverage**: it emits a `catalog/<module>.md` page for every module
   (deterministic, no model) so the *whole repo* is represented — not just the
   concerns you wrote — and prints a coverage report (deep % vs catalog-only).
   It then assembles `wiki/<slug>/index.md` and updates reconcile state.

4. **Repair loop.** If `finalize` exits non-zero, it lists each failing
   `page:line [rule N]`. Fix those pages (add the missing citation, create the
   missing stub, or move the claim into an `[!inferred]` block) and run
   `wikify finalize <slug>` again. Repeat until it exits 0.

## Notes
- **Adding a concern later**: add it to `config/<slug>.md` and re-run from step 1;
  `prepare` builds only the new packet (same commit, nothing else marked stale).
- **Version bump**: `wikify prepare <slug> --ref <newcommit>` — only changed
  symbols' pages rebuild.
- `wikify plan <slug>` previews the delta without emitting anything.
