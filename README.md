# 🧠 wikify repo

**Compile any codebase into a knowledge base wiki your AI agent can actually trust.**

**wikify-repo** turns a repo into a grounded, lint-clean [**Andrej Karpathy style LLM markdown wiki**](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) where every claim is traced to
a real, compiler-resolved symbol — behind a citation linter that fails the build if one doesn't
check out. No graph database, no dashboard, no hosted service: the output is plain markdown your agent
answers from with `grep`, and that you own in your own git repo. Deterministic tool does the
grounding (SCIP symbol graph, packets, citation lint); one LLM-in-the-loop step does the synthesis.

Idea is simple, generate and record all classes and methods and relationship between them using SCIP. Then annotate top ~20% of most imporant nodes using LLM that should cover ~80% of the repo meaning.

## How wikify-repo compares

| | **wikify-repo** | graphify | understand-anything | Google Code Wiki |
|---|---|---|---|---|
| **Specialization** | Grounded markdown wiki you own — for trusted agent retrieval | Multi-modal knowledge graph (code + docs + media) | Visual codebase onboarding — explore it as a graph | Zero-setup hosted docs for public repos |
| **Output** | ✅ Markdown wiki — pages in your git repo | ➖ Knowledge graph (HTML + JSON) | ➖ React-Flow graph dashboard | ❌ Hosted web docs only |
| **Code structure from** | ✅ **SCIP** — compiler-grade symbol resolution (scip-python / scip-clang). **Full semantic mapping** | ➖ tree-sitter AST, **name-based** (20 languages). Syntactic mapping. | ➖ tree-sitter AST, **name-based**. Syntactic mapping. | ❔ Gemini (closed) |
| **Faithfulness** | ✅ **Citation linter is a hard build gate**; uncited → `[!inferred]` | ➖ `EXTRACTED / INFERRED / AMBIGUOUS` labels — honest, not gated | ❌ LLM per-node summaries, unverified | ❌ *"AI-generated map, not a source of truth"* |
| **Coverage** | ✅ **Deterministic set-difference** — every module gets a page | ➖ Leiden community clustering | ➖ analyzes discovered files — no stated completeness | ❔ not specified |
| **Inputs** | ➖ code + prose (docs / articles) | ✅ **widest** — code, SQL, shell, docs, papers, images, audio/video | ➖ code + docs / LLM-wikis | ➖ code repos only |
| **Retrieval** | ✅ `grep` + `index.md` — **no embeddings, no DB, not additional tools** | ➖ graph queries + clusters (no embeddings) | ➖ name + semantic search in the dashboard | ➖ hosted UI + Gemini chat — no MCP / API |
| **Updates** | ✅ **idempotent reconcile** — `--ref` rebuilds only changed *symbols* | ✅ `--update` re-extracts only changed *files* (caches semantic passes) | ✅ incremental — re-analyzes only changed *files* | ✅ auto-maintained (hosted) |
| **Ownership** | ✅ plain markdown in your repo — offline, git-diffable | ➖ local graph files | ➖ local dashboard | ❌ **Google-hosted** (private repos waitlisted) |

<sub>✅ strong · ➖ partial / trade-off · ❌ weak or absent · ❔ unknown / closed</sub>

The other three optimize for navigation and reach — a graph to traverse ([graphify](https://github.com/safishamsi/graphify)),
a visual dashboard to explore ([understand-anything](https://github.com/labolado/understand-anything)),
a zero-setup hosted site ([Google Code Wiki](https://developers.googleblog.com/introducing-code-wiki-accelerating-your-code-understanding/)).
wikify-repo optimizes for **trust and ownership**: every claim cites a resolved symbol behind a hard
gate, a deterministic coverage pass guarantees no module is silently dropped, and the result is plain
markdown an agent reads with **nothing but `grep`** — no runtime, no database, no SaaS. For retrieval, **you don't even need this repo**, just a few changes to your CLAUDE.md/AGENTS.md to instruct agent to navigage code wiki.

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

wikify writes into a **Karpathy-style wiki repo** — a project that carries the `wikify-ingest-repo`
skill and the agent conventions (`SCHEMA.md` + `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`), with the
`wiki/` output committed alongside. Two ways to get one — both assume the [Install](#install) above
(the `wikify` CLI + `scip-python`) is done:

**A — Start from the template.** Clone the demo's empty
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

Then, in **Claude Code, Codex, or Antigravity** opened on that project, just say:

> ingest https://github.com/owner/myrepo      (a local path works too)

The agent runs the `wikify-ingest-repo` procedure — bootstrap config → index → symbol graph → write the
concept pages → citation lint → assemble — and writes the wiki to `wiki/code/<slug>/`. Re-running is
idempotent: only changed concepts rebuild.

## Use it in your own project

wikify splits cleanly into a **producer** side (build/maintain the wiki — needs the install) and a
**consumer** side (answer from it — needs nothing).

**Producing** is the [Quick start](#quick-start) above. The point worth adding: because the skill lives
in `.agents/skills/` and the output is plain markdown, wikify slots into **any existing LLM-wiki
project** as the *code* source type — sitting alongside prose pages in a
[Karpathy-style](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) knowledge base and
sharing one `index.md` / `log.md`. (That's exactly what
[wikify-repo-demo](https://github.com/vlasenkoalexey/wikify-repo-demo) does — code wikis under
`wiki/code/` next to hand-written `topics/` and `sources/`.) So you don't need a dedicated repo: drop it
into the wiki you already keep.

**Consuming needs no install at all.** To let an agent answer from a wiki someone else built, commit the
`wiki/code/<slug>/` folder and point your agent config at it — no `wikify` CLI, no skill, no
`scip-python`. The markdown *is* the interface. Add this to your `SCHEMA.md` (or `CLAUDE.md` /
`AGENTS.md` / `GEMINI.md`):
> Source of truth: the wiki at `wiki/code/<slug>/`. **Retrieve** from it — read `overview.md` as the
> index, grep to locate the relevant concept/catalog page, read only that section and cite it; do not
> bulk-read whole pages.

## C++ ingestion (mixed repos only)

scip-clang indexes against a `compile_commands.json`. For a repo like pytorch, first produce
one (and any generated headers) with the project's own build — e.g. CMake with
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` plus the codegen targets — and point wikify at it via the
`compile_commands:` config key. wikify consumes that file; it does not run your build.

## Architecture (the Python ↔ LLM split is hard)

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

## Tests

```bash
python -m pytest          # all module tests (callers/callees needs scip-python on PATH)
```
