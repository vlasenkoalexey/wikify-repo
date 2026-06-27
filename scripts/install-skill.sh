#!/usr/bin/env bash
# Install the `wikify-ingest-repo` skill into a project so it works across coding
# agents — Claude Code, Codex, and Antigravity.
#
# The skill is a self-contained, tool-neutral markdown procedure (SKILL.md + prompts/).
# How each agent finds it:
#   - Claude Code : native skills dir — we SOFT-LINK it into <proj>/.claude/skills/.
#   - Codex       : reads AGENTS.md -> SCHEMA.md  (no skills dir).
#   - Antigravity : reads GEMINI.md -> SCHEMA.md  (no skills dir).
# So we install ONE copy under <proj>/skills/, soft-link it where Claude looks, and add
# a reference to <proj>/SCHEMA.md that Codex + Antigravity read. One install, all three.
#
#   scripts/install-skill.sh                 # into the current project (.)
#   scripts/install-skill.sh /path/to/proj   # into another project
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HERE/skills/wikify-ingest-repo"
PROJ="$(cd "${1:-.}" && pwd)"

# 1. tool-neutral home for the self-contained skill (committed with the project)
NEUTRAL="$PROJ/skills/wikify-ingest-repo"
if [ "$NEUTRAL" != "$SRC" ]; then
  mkdir -p "$PROJ/skills"
  rm -rf "$NEUTRAL"
  cp -r "$SRC" "$NEUTRAL"
fi
echo "skill    : $NEUTRAL  (SKILL.md + prompts/)"

# 2. Claude Code: soft-link the neutral copy into its skills dir (stays in sync)
mkdir -p "$PROJ/.claude/skills"
ln -sfn "../../skills/wikify-ingest-repo" "$PROJ/.claude/skills/wikify-ingest-repo"
echo "Claude   : .claude/skills/wikify-ingest-repo -> ../../skills/wikify-ingest-repo (symlink)"

# 3. Codex + Antigravity: ensure SCHEMA.md references the procedure (they read it via
#    AGENTS.md / GEMINI.md). CLAUDE.md/AGENTS.md/GEMINI.md should already point to SCHEMA.md.
REF='> **Ingesting a repo:** follow the procedure in `skills/wikify-ingest-repo/SKILL.md` — a
> tool-neutral markdown procedure (Claude Code runs it as a skill; Codex and Antigravity read
> it via this file). Just ask your agent to: ingest <repo-url-or-local-path>.'
SCHEMA="$PROJ/SCHEMA.md"
if [ -f "$SCHEMA" ]; then
  if grep -q "skills/wikify-ingest-repo/SKILL.md" "$SCHEMA"; then
    echo "Codex/AG : $SCHEMA already references the skill"
  else
    printf '\n%s\n' "$REF" >> "$SCHEMA"
    echo "Codex/AG : added skill reference to SCHEMA.md"
  fi
else
  echo "Codex/AG : no SCHEMA.md found — add this so Codex/Antigravity discover the skill,"
  echo "           and make AGENTS.md / GEMINI.md point to SCHEMA.md:"
  printf '   %s\n' "$REF"
fi

echo "Done. In Claude Code / Codex / Antigravity, ask: ingest <repo>"
