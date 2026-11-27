#!/bin/bash
# Weekly worktree cleanup: remove clean/merged ones, back up then remove idle ones, write a report of what is left.
# DRY=1 shows what would happen without removing anything. Output: ~/.claude-helper/worktree-runs/<date>.{log,md}
set -u
H="${CLAUDE_HELPER_HOME:-$HOME/.claude-helper}"
HERE="$(cd "$(dirname "$0")" && pwd)"
D="$(date +%F)"
mkdir -p "$H/worktree-runs"
PY="${PYTHON:-}"
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do [ -z "$PY" ] && [ -x "$p" ] && PY="$p"; done
FLAGS="--apply --archive"; [ "${DRY:-}" = 1 ] && FLAGS="$FLAGS --dry-run"
nice "$PY" -I "$HERE/worktrees.py" $FLAGS > "$H/worktree-runs/$D.log" 2>&1
nice "$PY" -I "$HERE/worktrees.py" --md > "$H/worktree-runs/$D.md" 2>&1
SUMMARY="$(tail -1 "$H/worktree-runs/$D.log"); $(sed -n 3p "$H/worktree-runs/$D.md" | cut -c1-150)"
osascript -e "display notification \"$SUMMARY\" with title \"claude-helper worktrees\"" 2>/dev/null || true
echo "$SUMMARY"
