# claude-helper

**See where your Claude Code tokens go, and put guardrails on the waste.**

claude-helper reads your local Claude Code transcripts and shows where tokens went: context size, cache hits, Opus vs Sonnet/Haiku, subagents, retry loops and cost per commit. It also adds a small hook that can warn or block wasteful patterns as they happen, such as runaway context, the same failing command run over and over, or subagents that inherit Opus by accident.

- **Local only.** It reads `~/.claude/projects/**/*.jsonl` and writes to `~/.claude-helper/`. Nothing is uploaded.
- **No dependencies.** Python 3.8+ standard library. macOS and Linux.
- **Safe to try.** Every rule starts in *shadow* mode (log only). The hook fails open (an error never breaks a session), and there is a kill switch.

## Install (2 minutes)

```bash
git clone https://github.com/msellin/claude-helper.git
```

```bash
cd claude-helper && ./claude-helper install
```

`install` adds the guard to `~/.claude/settings.json` as a `PreToolUse` + `PostToolUse` hook (with a backup next to it) and copies the starter rules to `~/.claude-helper/guard_rules.json`. New Claude Code sessions pick it up. Optional:

```bash
./claude-helper install --schedule-digest
```

This schedules a weekly Markdown digest, Mondays 08:00 (macOS launchd; on Linux it prints a cron line for you to add). To remove everything: `./claude-helper uninstall` (your data in `~/.claude-helper` is kept).

Optional: put it on your PATH with `ln -s "$PWD/claude-helper" /usr/local/bin/claude-helper`.

## Use

| Command | What you get |
|---|---|
| `./claude-helper report` | Daily table for the last 8 days: calls, weighted tokens, cache hit, average context, % of calls above 250k context, Opus share, commits. Plus your costliest sessions. Full JSON in `~/.claude-helper/report.json`. A few GB of transcripts takes a minute or two. |
| `./claude-helper backtest --since 2026-01-01` | How often each rule *would* have fired on your history, and what share of cost came after the first firing. Use it to pick thresholds before you enforce anything. |
| `./claude-helper events` | What the guard actually did in the last 7 days, per rule, shadow vs enforce. |
| `./claude-helper digest` | Last 7 days vs the 7 before: flags, cost per project, guard activity, rules that never fire. |
| `./claude-helper compare my-changes.json` | Before/after metrics around the dates you changed something (a CLAUDE.md rule, a new hook). See `changes.example.json`. |
| `./claude-helper worktrees` | Finds finished git worktrees that agents left behind. Read-only unless you add `--apply`. |
| `./claude-helper off` / `on` | Kill switch for the guard (same as `CLAUDE_HELPER_OFF=1`). |

"Weighted" means input-token equivalents: cache read ×0.1, cache write ×1.25 (5 min) or ×2 (1 h), output ×5. It is a relative cost measure, **not dollars**.

## The guard

Rules live in `~/.claude-helper/guard_rules.json`; edit that file, not the one in the repo. Each rule has a metric, a threshold, an action and a mode:

| Rule | Fires when | Action |
|---|---|---|
| `ctx-warn` | context ≥ 250k tokens (again every +50k) | Tells the model to hand off, and writes a handover note to `~/.claude-helper/handoffs/` |
| `ctx-block` | context ≥ 400k | Blocks new Agent / WebSearch / WebFetch / SendMessage calls |
| `session-cost` | session ≥ 10M weighted units | Warns: finish the step, then hand off |
| `retry-loop` | the same Bash command runs a 3rd time | Blocks it: read the error, change approach |
| `agent-model` | an Agent call sets no `model` and its agent type doesn't pin one | Blocks it: pick haiku / sonnet / opus explicitly |

**Recommended rollout:** leave everything in `shadow` for a week, run `./claude-helper events` and `backtest`, then set `"mode": "enforce"` on the rules whose firings were real waste. Full rule format: [docs/rules.md](docs/rules.md).

## How it works

```
~/.claude/projects/*.jsonl ──► tokwaste.py ──► report.json ──► report / digest / compare
                         └──► backtest.py (replays rules over history)
Claude Code hook event ──► guard.py ──► warn / block / log ──► ~/.claude-helper/events.jsonl
```

| File | Role |
|---|---|
| `claude-helper` | CLI entry point |
| `install.py` | adds/removes the hook and optional scheduled jobs |
| `guard.py` | the hook: reads the session transcript incrementally, evaluates rules |
| `guard_rules.default.json` | starter rules (all shadow) |
| `tokwaste.py` | transcript analyser |
| `backtest.py`, `compare.py`, `digest.py` | rule replay, before/after, weekly digest |
| `worktrees.py`, `worktrees-weekly.sh` | git worktree audit and cleanup |

## Worktree cleanup (optional)

Agents that work in git worktrees leave folders behind. `./claude-helper worktrees` scans repos one level under `~/www`, `~/code`, `~/src`, `~/dev`, `~/projects`, `~/repos`, `~/git` (or the `roots` in `~/.claude-helper/worktrees.json`) and labels each worktree:

- **removable**: clean, merged or pushed, idle 24h
- **archive**: has local-only work, idle 14 days
- **review**: needs a human (locked, a process is running in it, too recent)

`--apply` removes only the *removable* ones, with plain `git worktree remove`: no `--force`, and branches are kept. `--apply --archive` first backs up the archive tier to `~/.claude-helper/worktree-backups/`. Any worktree whose path appears in a launchd plist, the crontab or `protect` in the config is never touched. To run this weekly: `./claude-helper install --schedule-worktrees`.

## Develop

```bash
python3 -m unittest discover tests
```

The tests are offline and use temp folders only. See [CLAUDE.md](CLAUDE.md) for conventions. Your own notes and experiments go in `private/`, which git ignores.

## License

MIT
