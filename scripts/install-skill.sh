#!/usr/bin/env bash
# Kept for one release as a wrapper: `wikify setup --project <dir>` installs the skill into a
# project's .agents/skills (Codex/Antigravity) with the .claude/skills symlink and .gitignore line.
set -euo pipefail
exec wikify setup --no-user --indexers none --project "${1:-.}"
