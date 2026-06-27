# 🧠 wikify repo

**Compile any codebase into a knowledge base wiki your AI agent can actually trust.**

**wikify-repo** turns a repo into a grounded, lint-clean [**Andrej Karpathy style LLM markdown wiki**](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) where every claim is traced to
a real, compiler-resolved symbol — behind a citation linter that fails the build if one doesn't
check out. No graph database, no dashboard, no hosted service: the output is plain markdown your agent
answers from with `grep`, and that you own in your own git repo. Deterministic tool does the
grounding (SCIP symbol graph, packets, citation lint); one LLM-in-the-loop step does the synthesis.

The idea is simple: record every class, method, and their relationships with SCIP, then spend the LLM annotating only the most central ~20% of nodes — enough to explain ~80% of the repo, while the rest still get a deterministic catalog page so nothing is dropped.

## How wikify-repo compares

| | **wikify-repo** | graphify | understand-anything | Google Code Wiki |
|---|---|---|---|---|
| **Specialization** | Grounded markdown wiki you own — for trusted agent retrieval | Multi-modal knowledge graph (code + docs + media) | Visual codebase onboarding — explore it as a graph | Zero-setup hosted docs for public repos |
| **Output** | ✅ Markdown wiki — pages in your git repo | ➖ Knowledge graph (HTML + JSON) | ➖ React-Flow graph dashboard | ❌ Hosted web docs only |
| **Code structure from** | ✅ **SCIP** — compiler-grade symbol resolution (scip-python / scip-clang). **Full semantic mapping** | ➖ tree-sitter AST, **name-based** (20 languages). Syntactic mapping. | ➖ tree-sitter AST, **name-based**. Syntactic mapping. | ❔ Gemini (closed) |
| **Faithfulness** | ✅ **Citation linter is a hard build gate**; uncited → `[!inferred]` | ➖ `EXTRACTED / INFERRED / AMBIGUOUS` labels — honest, not gated | ❌ LLM per-node summaries, unverified | ❌ *"AI-generated map, not a source of truth"* |
| **Coverage** | ✅ **Deterministic set-difference** — every module gets a page | ➖ Leiden community clustering | ➖ analyzes discovered files — no stated completeness | ❔ not specified |
| **Inputs** | ➖ code + prose (docs / articles) | ✅ **widest** — code, SQL, shell, docs, papers, images, audio/video | ➖ code + docs / LLM-wikis | ➖ code repos only |
| **Retrieval** | ✅ `grep` + `index.md` — **no embeddings, no DB, no additional tools** | ➖ graph queries + clusters (no embeddings) | ➖ name + semantic search in the dashboard | ➖ hosted UI + Gemini chat — no MCP / API |
| **Updates** | ✅ **idempotent reconcile** — `--ref` rebuilds only changed *symbols* | ✅ `--update` re-extracts only changed *files* (caches semantic passes) | ✅ incremental — re-analyzes only changed *files* | ✅ auto-maintained (hosted) |
| **Ownership** | ✅ plain markdown in your repo — offline, git-diffable | ➖ local graph files | ➖ local dashboard | ❌ **Google-hosted** (private repos waitlisted) |

<sub>✅ strong · ➖ partial / trade-off · ❌ weak or absent · ❔ unknown / closed</sub>

The other three optimize for navigation and reach — a graph to traverse ([graphify](https://github.com/safishamsi/graphify)),
a visual dashboard to explore ([understand-anything](https://github.com/labolado/understand-anything)),
a zero-setup hosted site ([Google Code Wiki](https://developers.googleblog.com/introducing-code-wiki-accelerating-your-code-understanding/)).
wikify-repo optimizes for **trust and ownership**: every claim cites a resolved symbol behind a hard
gate, a deterministic coverage pass guarantees no module is silently dropped, and the result is plain
markdown an agent reads with **nothing but `grep`** — no runtime, no database, no SaaS. For retrieval, **you don't even need this repo**, just a few changes to your CLAUDE.md/AGENTS.md to instruct agent to navigate code wiki.

## SCIP vs AST parsing

Most code-knowledge tools (graphify, understand-anything) parse with [**tree-sitter**](https://tree-sitter.github.io/tree-sitter/) — a fast,
build-free [**AST**](https://en.wikipedia.org/wiki/Abstract_syntax_tree) (abstract syntax tree), one tree per file. Great for breadth (20+ languages, no toolchain), but it resolves
references syntactically **by name**: it sees a call to something *called* `forward`, not *which* `forward`.
Cross-file bindings, import aliases, inheritance/overrides, and overloads are guesses.

**wikify-repo** indexes with [**SCIP**](https://github.com/sourcegraph/scip) (Sourcegraph's Code Intelligence Protocol) via `scip-python`
(pyright) and `scip-clang` (clang) — the language's *real* name-and-type resolver. Every definition
and reference binds to a globally-unique **moniker**, so a citation points at *the* symbol, across
files — not a string that happens to match. That's what makes grounding *enforceable*: a claim's
`cite:` either resolves to a real symbol in the SCIP table, or the **linter fails the build**.

Honest tradeoffs: SCIP needs a real indexer (`scip-python` over npm; a `compile_commands.json` for
C++) — heavier than a zero-build parse, which is the price of precision. Tree-sitter trades that precision for breadth: the right
call for navigation, the wrong one for *citeable* grounding.

## Why use a wiki as the storage format

The consumer is an **AI agent**, and agents already read markdown and retrieve with `grep` / `ripgrep`
natively — no query language, no graph runtime, no vector index, no MCP server, even no skill. **The output is the
interface.** Drop `wiki/` into a repo and any agent (Claude Code, Codex, Antigravity) answers from it
with zero adapter.

Honest tradeoff: a graph DB wins at arbitrary transitive queries ("every transitive caller of `X`").
wikify's answer is to **materialize** the common ones into the pages — per-symbol uses-by lists,
per-module catalogs — so the frequent questions are already answered as text, and the rare deep query
drops to the pinned source. For *agent retrieval of internals knowledge*, materialized markdown beats
a live graph you have to query.

## Demo and template

**[wikify-repo-demo](https://github.com/vlasenkoalexey/wikify-repo-demo)**
is a live, populated wiki *produced by this tool* — two real codebases
([`mini_pytorch_xla`](https://github.com/vlasenkoalexey/wikify-repo-demo/blob/main/wiki/code/mini_pytorch_xla/overview.md)
and wikify-repo itself) plus prose pages, all grounded, cited, and cross-linked.

[![Force-directed graph of the wiki: two ingested codebases (mini_pytorch_xla and wikify-repo) plus the prose pages, colored by page type](assets/demo-graph.png)](https://vlasenkoalexey.github.io/wikify-repo-demo/tools/graph/)
(click image for interactive view)

It plays two roles:

- **Showcase** — browse a finished wiki end to end (`overview.md` → `concepts/` → `catalog/` → the pinned source) to see exactly what wikify-repo emits and how an agent answers from it.
- **Template** — click **"Use this template"** (or start from the empty [`clean`](https://github.com/vlasenkoalexey/wikify-repo-demo/tree/clean) branch) to get a new repo with the `wikify-ingest-repo` skill and the `SCHEMA.md` / `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` agent conventions already wired in — then just `ingest <your-repo>`.

But it is important to note that wikify-repo can be integrated into any LLM wiki project.

## Install

**Prerequisites:** Python ≥ 3.11, Node.js + npm (for the `scip-python` indexer), and `git`.
The C++ indexer (`scip-clang`) is downloaded automatically, and only if you ingest C++.

```bash
git clone https://github.com/vlasenkoalexey/wikify-repo
cd wikify-repo
pip install -e .                  # 1. the `wikify` CLI (deps: protobuf, pyyaml, typer, gitpython)
scripts/setup-vendor.sh           # 2. install scip-python + generate wikify/scip_pb2.py (one-time)
scripts/install-skill.sh /proj    # 3. install the ingest skill into your wiki project
wikify --help                     # verify the CLI is on PATH
```

That's the whole install. **All three steps matter:** the CLI does the deterministic stages, but
the page-writing (synthesis) stage is **LLM-in-the-loop**, so an agent must run the
`wikify-ingest-repo` skill. The skill is one self-contained, **tool-neutral** markdown procedure
(`SKILL.md` + `prompts/`); `install-skill.sh` installs it into your project's `.agents/skills/` —
the folder **Codex** and **Antigravity** read project skills from — and soft-links it into
`.claude/skills/` for **Claude Code**. One install, all three agents.
Use any Python ≥3.11 env (conda, venv, or pipx-managed). Every script is idempotent, so re-running
is harmless.

## Quick start

wikify has a **producer** side (build/maintain the wiki — needs the install) and a **consumer** side
(answer from it — needs nothing). Both work in **Claude Code, Codex, and Antigravity**.

### Build a wiki

wikify writes into a **Karpathy-style wiki repo** — a project carrying the `wikify-ingest-repo` skill,
the agent conventions, and the committed `wiki/`. Two ways to get one (after [Install](#install)):

**A — Start from the new clean wiki template.** Clone the demo's empty
[`clean`](https://github.com/vlasenkoalexey/wikify-repo-demo/tree/clean) branch; it ships the skill and
conventions pre-wired, with an empty `wiki/`:
```bash
git clone -b clean https://github.com/vlasenkoalexey/wikify-repo-demo my-wiki
scripts/install-skill.sh my-wiki   # wires the Claude Code symlink (Codex/Antigravity already see it)
```

**B — Add it to an existing repo.** Install the skill into a project you already have — it drops the
self-contained skill into `.agents/skills/` (read natively by Codex + Antigravity) and soft-links it
into `.claude/skills/` for Claude Code:
```bash
scripts/install-skill.sh /path/to/your-project
```

Either way, open the project in your agent and say:

> ingest https://github.com/owner/myrepo      (a local path works too)

The agent runs the `wikify-ingest-repo` procedure — bootstrap config → index → symbol graph → write the
concept pages → citation lint → assemble — and writes the wiki to `wiki/code/<slug>/`. Re-running is
idempotent: only changed concepts rebuild. Because the skill lives in `.agents/skills/` and the output
is plain markdown, this also slots into **any existing LLM-wiki project** as the *code* source type —
alongside prose, sharing one `index.md` / `log.md` (exactly what the
[demo](https://github.com/vlasenkoalexey/wikify-repo-demo) does, with `wiki/code/` next to hand-written
`topics/` and `sources/`).

### Answer from a wiki — no install needed

To let an agent answer from a wiki — one you built, or one someone else committed — you need **nothing
installed**: no `wikify` CLI, no skill, no `scip-python`. Commit the `wiki/code/<slug>/` folder and tell
the agent to retrieve from it. Add a block like this to **`CLAUDE.md`** (Claude Code), **`AGENTS.md`**
(Codex), and/or **`GEMINI.md`** (Antigravity) — or to a shared **`SCHEMA.md`** that all three point at:

```markdown
## Codebase wiki — source of truth
A grounded wiki for <repo> lives at `wiki/code/<slug>/`. To answer questions about its internals,
**retrieve from the wiki instead of reading source**:
- Read `wiki/code/<slug>/overview.md` first — it maps concepts to pages.
- `grep` the wiki to find the relevant `concepts/` (mechanism) or `catalog/` (per-symbol) page; read
  only that section.
- Cite the catalog anchor `catalog/<module>.md#<Symbol>`; follow its source link only when you need
  the exact line.
- Don't bulk-read whole pages, and don't guess — every claim should trace to a cited symbol.
```

The markdown *is* the interface — that's the whole integration.

## Architecture (the Python ↔ LLM split is hard)

The hard rule behind the table: **the deterministic stages are pure Python — zero model calls** (SCIP
parse, reconcile diff, packet build, dependency links, coverage, citation lint), and the LLM is invoked
at exactly **one** step — concern *synthesis* (plus concept-link judgment). Synthesis never leaks into
Python and linting never leaks into a prompt: the model proposes prose, Python decides what's true. That
boundary is what keeps the wiki both grounded *and* cheap — the expensive, hallucination-prone work is
fenced to a single file-handoff stage, while everything that has to be exact stays mechanical and
testable. It's also why coverage is a deterministic *set-difference* over the SCIP symbol table rather
than a model pass: enumeration can't miss a module, so the LLM is spent only where the truth is genuinely
cross-symbol.

| Stage | Module | Who |
|---|---|---|
| 0 acquire & pin | `acquire.py` | Python |
| 1 SCIP index → graph | `scip_index.py`, `graph.py`, `monikers.py` | Python |
| 2 reconcile diff | `diff.py`, `state.py`, `source.py` | Python |
| 4 evidence (tests) | `evidence.py` | Python |
| — packet build | `packet.py`, `slug.py`, `config.py` | Python |
| 5 concern synthesis | `.agents/skills/…/SKILL.md` + `prompts/synthesis.md` | **LLM agent** |
| 6 citation lint + assemble | `lint.py`, `assemble.py` | Python |

The risky foundation is the **SCIP-occurrence → callers/callees** heuristic (SCIP
has no "call" role); it's reference-scoped, not true call resolution, and is
validated by `tests/test_callers_callees.py`.

