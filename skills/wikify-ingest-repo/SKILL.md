---
name: wikify-ingest-repo
description: >
  Ingest a code repo into a grounded, lint-clean markdown wiki an agent can
  answer internals questions from. Idempotent reconcile — first build, version
  bump (--ref), or an added concept are all the same operation. Trigger when the
  user asks to wikify/ingest a repo, build an internals wiki, or update one.
---

# wikify-ingest-repo

Drive the deterministic `wikify` CLI around one LLM-in-the-loop step: **concept
synthesis**. The CLI does everything else (acquire, SCIP index, graph, diff,
packets, lint, assemble). You write one mechanism page per packet. Never put
synthesis in Python; never push linting into your prose.

## Preconditions
- `wikify` is on PATH (`pipx install wikify-repo`) and `scip-python` is installed
  (`npm i -g @sourcegraph/scip-python`; Node required). A `config/<slug>.md`
  exists (frontmatter + a `## Concepts` list with optional seed symbols).

## Procedure

1. **Prepare (deterministic, no model).** Run:
   ```
   wikify prepare <slug> [--ref <commit>]
   ```
   This acquires + pins the repo, runs scip-python, builds the symbol graph,
   prints the reconcile **plan** (will build / rebuild / leave), and writes one
   packet per to-build concept at `.cache/packets/<slug>/<concept>.md`. If the
   plan is a no-op, STOP — the wiki is already converged.

2. **Synthesize (this is your job — heavy processing, not annotation).** For EACH
   packet the plan built, read the packet and follow
   `${CLAUDE_SKILL_DIR}/prompts/synthesis.md` exactly to write ONE file: the
   mechanism page `wiki/<slug>/concepts/<concept>.md`. The packet is your grounding
   index; **READ THE ACTUAL SOURCE** at the `file:line` it gives you (the snippets
   are truncated) so the page explains *how it really works and why*, not a cited
   trace. Lead with Overview + a Mermaid **Diagram** + Design rationale; weave
   citations (a few per paragraph, no `[extracted →]` tags). You do **not** create
   symbol stubs — paste each symbol's `cite:` link from the Subgraph verbatim (it
   resolves to the catalog anchor). Cite ONLY Subgraph symbols; ungrounded → a
   `> [!inferred]` block.

3. **Overview (after all concepts exist).** Follow
   `${CLAUDE_SKILL_DIR}/prompts/overview.md` to write `wiki/<slug>/overview.md` —
   the highest-level page: the main concepts, core system-level Mermaid diagrams,
   and a map of which concept answers which question. It is synthesis over the
   concept pages (no new grounding).

4. **Finalize (deterministic gate).** Run:
   ```
   wikify finalize <slug>
   ```
   The citation linter is a hard gate: every `symbols/*.md` citation must resolve
   to a real SCIP symbol, every Entry-points/Mechanism item must be cited, and no
   symbol outside the packet subgraph may appear. On success it also runs
   **Stage 6b coverage**: it emits a `catalog/<module>.md` page for every module
   (deterministic, no model) so the *whole repo* is represented — not just the
   concepts you wrote — and prints a coverage report (deep % vs catalog-only).
   It then assembles `wiki/<slug>/index.md` and updates reconcile state.

5. **Repair loop.** If `finalize` exits non-zero, it lists each failing
   `page:line [rule N]`. Fix those pages (add the missing citation or move the
   claim into an `[!inferred]` block) and run `wikify finalize <slug>` again.
   Repeat until it exits 0.

## Notes
- **Adding a concept later**: add it to `config/<slug>.md` and re-run from step 1;
  `prepare` builds only the new packet (same commit, nothing else marked stale).
- **Version bump**: `wikify prepare <slug> --ref <newcommit>` — only changed
  symbols' pages rebuild.
- `wikify plan <slug>` previews the delta without emitting anything.
