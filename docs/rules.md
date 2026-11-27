# Guard rules

The guard reads rules from the first file that exists:

1. `$CLAUDE_HELPER_RULES`
2. `~/.claude-helper/guard_rules.json` (your copy; `install` creates it)
3. `guard_rules.default.json` in the repo

## Rule fields

| Field | Meaning |
|---|---|
| `id` | name shown in events and digests |
| `metric` | one of the metrics below |
| `gte` | fires when the metric is ≥ this |
| `event` | `PreToolUse` (can block the call) or `PostToolUse` (can add a note after it) |
| `action` | `warn`: inject `message` into the model's context. `block`: deny the tool call with `message` as the reason (PreToolUse only) |
| `mode` | `shadow`: log to `events.jsonl` only. `enforce`: act |
| `tools` | optional list of tool names the rule applies to (e.g. `["Bash"]`) |
| `repeat_step` | optional: fire again each time the metric grows by this much. Without it a warn fires once per session |
| `handoff` | optional `true`: write a handover note to `~/.claude-helper/handoffs/<session>.md` when it fires |
| `message` | text for the model; `{value}`, `{gte}` and `{handoff}` are filled in |

## Metrics

| Metric | Meaning |
|---|---|
| `context_k` | tokens in the context at the last API call, in thousands |
| `session_weighted_M` | weighted units used so far in this session, in millions |
| `same_bash_repeats` | how many times the Bash command about to run has run in this session |
| `agent_no_model` | 1 when an Agent call sets no `model` and its agent type has no `model:` in `~/.claude/agents/*.md` or `.claude/agents/*.md` |

## Files in ~/.claude-helper

| Path | Content |
|---|---|
| `guard_rules.json` | your rules |
| `events.jsonl` | every firing (time, session, rule, value, mode, tool) |
| `state/` | per-session scan state (safe to delete) |
| `handoffs/` | auto handover notes |
| `report.json`, `digests/` | reports |
| `OFF` | if this file exists the guard does nothing |

Set `CLAUDE_HELPER_HOME` to use another folder.
