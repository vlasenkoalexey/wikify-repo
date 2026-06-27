#!/usr/bin/env bash
# Install the `wikify-ingest-repo` Claude Code skill so an agent can drive the
# synthesis step (prepare -> write concept pages -> finalize).
#
# The SKILL.md references its prompts via ${CLAUDE_SKILL_DIR}/prompts/, so the
# prompts must live INSIDE the installed skill dir — this script bundles them.
# Idempotent: re-running overwrites in place.
#
#   scripts/install-skill.sh                     # -> ~/.claude/skills      (global)
#   scripts/install-skill.sh /path/to/proj/.claude/skills   # per-project
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_ROOT="${1:-$HOME/.claude/skills}"
DST="$DEST_ROOT/wikify-ingest-repo"

mkdir -p "$DST/prompts"
cp "$HERE/skills/wikify-ingest-repo/SKILL.md" "$DST/"
cp "$HERE"/skills/prompts/*.md "$DST/prompts/"

echo "Installed wikify-ingest-repo skill -> $DST"
echo "  SKILL.md + $(ls "$DST"/prompts/*.md | wc -l | tr -d ' ') prompts ($(ls "$DST"/prompts | tr '\n' ' '))"
echo "Now ask Claude Code in that project to 'ingest <repo>' / run the wikify-ingest-repo skill."
