#!/usr/bin/env bash
# Install the `wikify-ingest-repo` skill into a project for Claude Code, Codex, and Antigravity.
#
# All three discover project-scoped skills, and Codex + Antigravity use the SAME folder:
#   - Codex       : <repo>/.agents/skills/   (scanned cwd -> repo root)  https://developers.openai.com/codex/skills
#   - Antigravity : <repo>/.agents/skills/   (project scope)             https://antigravity.google/docs/skills
#   - Claude Code : <repo>/.claude/skills/
# So `.agents/skills/` is the canonical home (native for Codex + Antigravity); we soft-link it into
# `.claude/skills/` for Claude Code. One self-contained skill (SKILL.md + prompts/), all three agents.
#
#   scripts/install-skill.sh                 # into the current project (.)
#   scripts/install-skill.sh /path/to/proj   # into another project
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HERE/.agents/skills/wikify-ingest-repo"
PROJ="$(cd "${1:-.}" && pwd)"

# 1. Codex + Antigravity: install the self-contained skill into the canonical .agents/skills/
DEST="$PROJ/.agents/skills/wikify-ingest-repo"
if [ "$DEST" != "$SRC" ]; then
  mkdir -p "$PROJ/.agents/skills"
  rm -rf "$DEST"
  cp -r "$SRC" "$DEST"
fi
echo "Codex+AG : $DEST  (.agents/skills — native for Codex & Antigravity)"

# 2. Claude Code: soft-link the same skill into its skills dir (one source, stays in sync)
mkdir -p "$PROJ/.claude/skills"
ln -sfn "../../.agents/skills/wikify-ingest-repo" "$PROJ/.claude/skills/wikify-ingest-repo"
echo "Claude   : .claude/skills/wikify-ingest-repo -> ../../.agents/skills/wikify-ingest-repo (symlink)"

echo "Done. In Claude Code / Codex / Antigravity, ask: ingest <repo>"
