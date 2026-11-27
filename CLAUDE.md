# claude-helper: notes for working on this repo

Local tools that analyse Claude Code transcripts and enforce token-waste rules through hooks. Public repo: keep it usable by anyone.

## Rules
- **Standard library only**, Python 3.8+. No pip installs.
- **The guard must never break a session.** `guard.py` exits silently on any error and must stay fast (it runs on every tool call). Keep it small.
- **No personal data or machine paths in tracked files.** No `/Users/<name>`, no project or client names, no real usage numbers. Personal notes, research and reports go in `private/` (git-ignored; `private/DESIGN.md` holds the product plan and dogfooding history, `private/NOTES.md` the old status log).
- **New rules ship in `shadow` mode** in `guard_rules.default.json`. The test suite checks this.
- **Never write to the real `~/.claude/settings.json`, `~/.claude-helper` or `~/Library/LaunchAgents` from tests.** Use the env overrides: `CLAUDE_HELPER_HOME`, `CLAUDE_HELPER_RULES`, `CLAUDE_SETTINGS`, `CLAUDE_HELPER_LAUNCHAGENTS`, `CLAUDE_PROJECTS`.
- **Keep file names stable** (`guard.py`, `digest.py`, `worktrees-weekly.sh`). Installed hooks and launchd jobs point at them.
- **Prices** live in `prices.py` (list API prices, $ per million tokens). When Anthropic changes prices or ships a model, update the table there and the test in `tests/test_smoke.py`.
- **The backtest must replay rules the same way the guard evaluates them** (`backtest.py` reuses `read_only` and `EDIT_TOOLS` from `guard.py`).
- Transcript lines are compact JSON; scanners pre-filter on `'"type":"assistant"'`, so test fixtures must use `separators=(',', ':')`.

## Check before committing
```bash
python3 -m unittest discover tests
```
```bash
git grep -nE '/Users/|/home/' -- ':!CLAUDE.md' ':!tests'
```

When you change a command, flag or rule, update the README and `docs/rules.md` in the same commit.
