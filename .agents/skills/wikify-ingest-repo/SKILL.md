---
name: wikify-ingest-repo
description: >
  Ingest a code repo into a grounded, lint-clean markdown wiki an agent can
  answer internals questions from. Idempotent reconcile — first build, version
  bump (--ref), or an added concept are all the same operation. Trigger when the
  user asks to wikify/ingest a repo, build an internals wiki, or update one.
  Accepts a repo URL or local path as the argument and bootstraps `config/<slug>.md`
  itself — the user never hand-writes config.
---

# wikify-ingest-repo

Drive the deterministic `wikify` CLI around one LLM-in-the-loop step: **concept
synthesis**. The CLI does everything else (acquire, SCIP index, graph, diff,
packets, lint, assemble). You write one mechanism page per packet. Never put
synthesis in Python; never push linting into your prose.

## Preconditions
- `wikify` is on PATH, plus the SCIP indexer for the repo's language(s): `scip-python`
  and the vendored `scip-clang` come from `scripts/setup-vendor.sh` (see the repo's README);
  TS/JS, Go, and Rust indexers are installed **on demand** — `prepare` detects the language
  and auto-installs a missing indexer, announcing the command (pass `--no-install-indexers`
  to skip instead). If prepare reports a language was skipped anyway (install failed — e.g.
  scip-go needs a Go toolchain), surface that to the user rather than ignoring it: the
  skipped language's symbols are absent from the wiki until it's installed and prepare re-runs.

## Input — invoke with the repo to ingest
You are called with a repo **URL or local path** (e.g. `wikify-ingest-repo
https://github.com/owner/myrepo`), or with an existing `<slug>` to update.

**Step 0 — pick the layout, then bootstrap the config yourself (never ask the user to write
it).** Two layouts (implementation.md §10.15):
- **In-repo** — you are *inside* a code repository (no `config/` directory of host configs, no
  host `SCHEMA.md`), or the user says "wikify this repo" / "initialize the wiki here": run
  `wikify init` (idempotent). It writes `wikify.md`, gitignores `.wikify/`, and injects the
  wikify block into `CLAUDE.md` and `AGENTS.md` (add `--instructions CLAUDE.md,AGENTS.md,GEMINI.md`
  when the repo carries one). From then on every command below runs from the repo root with
  **no `<slug>`**; the wiki lands flat at `wiki/`. Skip steps 8–9 (register/connect): the
  injected block *is* the registration, and there are no sibling silos.
- **Host wiki** — a project with `config/` and a curated `wiki/` spanning repos: derive `<slug>`
  from the repo (basename, minus `.git`). If `config/<slug>.md` does not already exist, create it:
```
---
slug: <slug>
repo: <the URL or local path>
---
```
No `## Concepts` list is needed — the **subsystem planner** derives the agenda (a table of
contents of directory-shaped units with their entry points) for a fresh silo; add seeds later
only to add or rename a unit. If a config for that slug already exists, reuse it (re-ingest is
idempotent; an existing silo keeps module-level discovery until its config says
`agenda: subsystems`). Then run the Procedure below with `<slug>`.

**Focus (lens) — settle it before synthesizing; ask only if it isn't already known.** If the
config already has `synthesis_focus`, or the host wiki has an established lens (skim a couple of
sibling `config/*.md`, or the host `SCHEMA.md`), use that — **do not re-ask a settled question**.
Otherwise ASK the user for the domain angle in one line (e.g. *"TPU performance — kernels,
sharding, autotune, precision"*) and write it into `config/<slug>.md` as `synthesis_focus:`. In a
non-interactive/batch run with no signal, proceed neutrally (no lens). The lens shapes emphasis,
never grounding.

> **Docs mode (prose sources).** If the repo is documentation, not code — or you set
> `source_type: docs` in the config — the pipeline is the same shape but prose-grounded:
> `wikify prepare <slug>` emits one **doc packet** per document (no SCIP); you synthesize
> `topics/` + `sources/` pages following **`prompts/synthesis-docs.md`**, citing sections with
> `src:` tokens; `wikify finalize <slug>` gates those citations and runs the coverage floor. The
> grounding anchor is a *source document + section* instead of a symbol; everything below about
> catalogs/concepts is the code path. See design.md "Docs mode".

## Procedure

1. **Prepare (deterministic, no model).** Run:
   ```
   wikify prepare <slug> [--ref <commit>]
   ```
   This acquires + pins the repo, runs scip-python, builds the symbol graph,
   prints the reconcile **plan** (will build / rebuild / leave), and writes one
   packet per to-build concept at `.cache/packets/<slug>/<concept>.md`. If the
   plan is a no-op, STOP — the wiki is already converged.

   **Confirm the agenda before synthesizing (interactive; skip in batch).** In subsystem
   mode `prepare` prints the **proposed agenda** — a ranked table of units (slug, directory,
   modules, symbols, external fan-in, entry points) — and writes it to
   `.cache/plan/<slug>.agenda.md` (`wikify agenda <slug>` re-emits it alone). Show it to the
   user and ask what to drop, merge or add: drop with `agenda_exclude:` globs in
   `config/<slug>.md` (`dir` drops a unit, `dir/*` its children — e.g. fold per-op kernel
   directories back into their parent), cap with `agenda_max`, add or rename with
   `- **<slug>** — seeds: (subsystem: <dir prefix>)`. Then re-run `prepare` (cheap: the index
   is cached). With no user present, proceed with the proposal as printed.

   **Name the pages now, not later.** The agenda file ends with a paste-ready `## Concepts`
   block (one `(subsystem: ...)` line per unit). Slugs are permanent — page filenames, state
   keys, and what `connect` matches on — so rename the bold, directory-shaped slugs to
   **topic names** here (`compilation-cache-tiers`, not `common-cache_key`), preferring a key
   from the host `wiki/concepts/` vocabulary when the unit is an instance of one. Paste the
   renamed lines into `config/<slug>.md`; an entry replaces the planned unit(s) under its
   prefix, so renaming never builds a page twice. A community unit is addressed by its full
   `dir::stem` prefix.

   **Check the plan against the authors' own map (same step, before confirming).** The
   planner sees structure, not vocabulary. Read the repo's README and the headings of the
   docs `prepare` listed in `.cache/docs/<slug>.txt` (headings only — the docs themselves are
   ingested later, step 4), and compare with the proposed table:
   - **Names.** Where the docs have a name for a unit ("compilation cache tiers" for
     `common-cache_key`), use it as the slug, and keep the authors' other terms as
     `aliases:` on the page (the synthesis prompt asks for them). Retrieval is index + grep in
     the words a question uses — the authors' words, not directory names.
   - **Missing units.** A subsystem or workflow the README leads with that has no row: add
     it — `(subsystem: <prefix>)` for a directory the planner cut or split away, or a
     cross-unit **flow** concept with explicit symbol seeds (`- **eager-op-lifecycle** — seeds:
     \`DispatchOp\`, \`MaterializeAndReturn\``) for something that spans units. Only what
     resolves to packet symbols can be added; a documented feature with no code behind it
     is not a concept page.
   - **Documented, not found.** Note anything the docs describe that you cannot locate in the
     graph in the log entry for this ingest (stale docs or an unshipped feature); do not
     invent a page for it.
   Then confirm with the user and re-run `prepare`. In batch, apply the renames and additions
   you are confident of and proceed.

2. **Synthesize (this is your job — heavy processing, not annotation).** For EACH
   packet the plan built, read the packet and follow
   `prompts/synthesis.md` exactly to write ONE file: the
   mechanism page `wiki/code/<slug>/concepts/<concept>.md`. The packet is your grounding
   index; **READ THE ACTUAL SOURCE** at the `file:line` it gives you (the snippets
   are truncated) so the page explains *how it really works and why*, not a cited
   trace. Lead with Overview + a Mermaid **Diagram** + Design rationale; weave
   citations (a few per paragraph, no `[extracted →]` tags). You do **not** create
   symbol stubs — paste each symbol's `cite:` link from the Subgraph verbatim (it
   resolves to the catalog anchor). Cite ONLY Subgraph symbols; ungrounded → a
   `> [!inferred]` block. When the packet has a **`## Scope`** block, the page is about
   that subsystem *as a whole* — its responsibilities, the mechanism tying its modules
   together, how the rest of the repo enters it (the listed entry points) — never about
   the first hub symbol; hub utilities inside it are sections, not the subject.

   **Then offer to go deeper (interactive; skip in batch).** List the concept pages you just
   wrote and — from `wikify agenda <slug> --max 60` (planned units beyond the built set) and
   the Stage-6b coverage (modules that got only a catalog, not a deep page) — the subsystems
   *not yet* deep-dived. Ask the user which, if any, to add. For each chosen one, add
   `- **<slug>** — seeds: (subsystem: <dir prefix>)` to the `## Concepts` list in
   `config/<slug>.md` and **re-run from step 1** (`prepare` builds only the new packet). This is
   the derived, ranked agenda — offer real candidates, never free-form (a concept with no packet
   symbols cannot be grounded). With no user present, proceed with the planned set.

3. **Overview (after all concepts exist).** Follow
   `prompts/overview.md` to write `wiki/code/<slug>/overview.md` —
   the highest-level page: the main concepts, core system-level Mermaid diagrams,
   and a map of which concept answers which question. It is synthesis over the
   concept pages (no new grounding).

4. **Doc concepts (LAST synthesis step).** `prepare` wrote a doc worklist at
   `.cache/docs/<slug>.txt` (the project's own README / `docs/`, globbed from
   `config.docs`). For each doc, follow `prompts/ingest-docs.md`:
   read the doc, extract its concepts, and write **one grounded page per concept**
   into `wiki/code/<slug>/doc-concepts/<concept>.md` — each linking the symbols the doc
   names to their **catalog** entries and cross-linking sibling doc-concepts + code
   concepts. The doc stays in place (never moved). Skip if the worklist is empty.

5. **Finalize (deterministic gate).** Run:
   ```
   wikify finalize <slug>
   ```
   The citation linter is a hard gate over `concepts/`: every catalog citation must
   resolve to a real SCIP symbol, every Entry-points/Mechanism item must be cited,
   and no symbol outside the packet subgraph may appear. `doc-concepts/` get a
   lighter gate (citations must resolve — rule 1 — no subgraph/uncited gates). On
   success it also runs **Stage 6b coverage**: it emits a `catalog/<module>.md` page
   for every module (deterministic, no model) so the *whole repo* is represented,
   prints a coverage report, assembles `wiki/code/<slug>/index.md` (concepts + areas +
   **doc-derived concepts**), and updates reconcile state. It warns (exit 0) if
   `overview.md` is missing — the front door the host index and `connect` rely on; go back
   to step 3 and re-run finalize.

6. **Repair loop.** If `finalize` exits non-zero, it lists each failing
   `page:line [rule N]`. Fix those pages (add the missing citation or move the
   claim into an `[!inferred]` block) and run `wikify finalize <slug>` again.
   Repeat until it exits 0.

7. **Adversarial verify (after the gate is green; skip only if the user declines).** The
   linter proves every claim *cites* a real symbol — not that the claim is *true*. Run:
   ```
   wikify verify <slug>            # per-page count of load-bearing claims (no model)
   wikify verify <slug> --page <concept>   # the claim worklist for one page
   ```
   For each concept page, follow `prompts/verify.md`: re-read the real source behind each
   load-bearing claim and try to REFUTE it, then **record the verdicts**
   (`wikify verify <slug> --page <concept> --record <json>`). Fix refuted claims in place
   (correct the prose, or demote to `> [!inferred]` if the source can't support it), re-run
   `wikify finalize <slug>` so the gate re-checks the edited pages, then `wikify verify` again:
   the worklist is now **incremental** — only the claims you edited, claims whose cited code
   changed, and a small deterministic re-sample come back; recorded holds carry forward across
   `--ref` bumps too. `--all` forces the full list when you want a clean sweep.

8. **Register in the host wiki (REQUIRED; host layout only).** In-repo: re-run `wikify init`
   so the instruction block is current, and stop here. The ingest is not done until the repo is
   reachable from the host's read-first index and recorded in its log, **following the host
   wiki's own conventions** (read its `SCHEMA.md` / `index.md` for the exact format):
   - **Index** — add/refresh the repo's entry in the host's top `index.md`, linking the
     **`overview.md`** front door (`wiki/<wiki_subdir>/<slug>/overview.md`), *not* the per-repo
     `index.md` (the overview routes on to it). One entry per repo. If the host already lists the
     repo (e.g. a curated page), add the overview as an "internals" link on that same row. Use
     the overview's `description:` frontmatter as the row's one-line summary.
   - **Log** — append one line to the host's `log.md`, prefixed per its convention
     (e.g. `## [YYYY-MM-DD] ingest-code | <slug>`).
   (wikify's CLI never edits the curated `index.md` / `log.md`; that's deliberate — this
   step does, per the host's format.)

9. **Connect to the other repos (from the 2nd silo on).** If the host wiki has other
   ingested silos **and** a concept vocabulary (`wiki/concepts/*.md`), hand off to the
   **`wikify-connect-repo`** skill and let it drive: it proposes candidates (`wikify connect`),
   **asks the human which concepts to connect** (selective — not everything), applies them, and
   refreshes the already-connected concepts so they pick up this new repo's implementations.
   Do not run `wikify connect` yourself here — the connect skill owns that procedure, including
   `--refresh`. Skip only when this is the first/only repo, or the wiki has no `wiki/concepts/`
   vocabulary.

## Notes
- **Where pages go**: `wiki/<wiki_subdir>/<slug>/` — `wiki_subdir` defaults to `code`
  (so `wiki/code/<slug>/`, leaving `wiki/` for a curated index + prose). Set
  `wiki_subdir: ""` in `config/<slug>.md` for the classic flat `wiki/<slug>/` layout.
- **Adding a concept later**: add it to `config/<slug>.md` and re-run from step 1;
  `prepare` builds only the new packet (same commit, nothing else marked stale).
- **Version bump**: `wikify prepare <slug> --ref <newcommit>` — only changed
  symbols' pages rebuild. Symbols that merely **moved** (same body, new file or new
  module) are *relinked*: `prepare` rewrites their citations in place and reports
  `will relink`; nothing to synthesize for those pages. Rebuilt pages' packets carry a
  `## Since last ingest` block (the commits that touched their cited files): write the
  page's `## Recent changes` from it (prompts/synthesis.md). `finalize` then writes
  `changes/<newref>.md` (pages affected + commits by page, hashes linked to the forge
  when `source_url` is set) and appends the silo `log.md`. Optionally add a 3–5 line
  narrative at the top of the change page, above the `changes:auto` marker — it survives
  regeneration; the block below it is rewritten every finalize. Refresh the overview's
  task table row for "what changed".
- `wikify plan <slug>` previews the delta without emitting anything (requires a cached
  index from a prior `prepare`). `wikify agenda <slug> [--max N]` prints the planner's
  proposed table of contents from the same cached index, no packets.
- **Planner modes**: `agenda: subsystems` (directory-shaped units with entry points; the
  default for a fresh silo) or `agenda: modules` (single modules by centrality; what an
  existing silo keeps until its config opts in). `prepare --agenda <mode>` overrides for one run.
- **Interrupted or failed run**: the reconcile is idempotent — re-run `wikify prepare <slug>`
  and it rebuilds only what's missing/stale; already-written pages are never double-built.
  If `scip-python` OOMs on a huge repo (exit 137/144), add `index_shards:` globs to
  `config/<slug>.md` (see implementation.md §10) and re-run `prepare`.
