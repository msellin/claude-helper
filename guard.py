#!/usr/bin/env python3
"""claude-helper guard: declarative rules enforced through Claude Code hooks (PreToolUse + PostToolUse).

Rules: $CLAUDE_HELPER_RULES, else ~/.claude-helper/guard_rules.json (your copy, made by install.py),
else the bundled guard_rules.default.json. Each rule: metric, threshold (gte), event, action
(warn = inject context after a tool call; block = deny the tool call with a reason), mode (shadow = log only,
enforce = act), optional tools, repeat_step. Every firing is appended to $CLAUDE_HELPER_HOME/events.jsonl
(default ~/.claude-helper) so rules can later be judged on data (compare.py). A guard never breaks a session:
any error exits silently.
Metrics: context_k (last usage record), session_weighted_M (incremental, approx input-token equivalents),
agent_no_model (1 when an Agent call sets no model and its type is not pinned), same_bash_repeats (how many times the
command being run now has run since the last file edit; read-only commands such as ls / git status never count).
"""
import fcntl, glob, hashlib, json, os, re, sys, time

HOME = os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper'))
HERE = os.path.dirname(os.path.abspath(__file__))


EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
READ_ONLY = ('ls', 'cat', 'head', 'tail', 'grep', 'rg', 'find', 'wc', 'pwd', 'echo', 'which', 'stat', 'du', 'df', 'date',
             'tree', 'file', 'true', 'mkdir -p', 'git status', 'git log', 'git diff', 'git show', 'git branch', 'git remote',
             'git rev-parse', 'git fetch', 'sleep')


def read_only(cmd):
    """True when every part of a command chain only looks (ls, git status, mkdir -p ...): repeating those is normal."""
    parts = [p.strip() for p in re.split(r'&&|\|\||;|\||\n', cmd or '') if p.strip()]
    parts = [p for p in parts if not p.startswith('cd ')]
    return bool(parts) and all(any(p == w or p.startswith(w + ' ') for w in READ_ONLY) for p in parts)


def rules_path():
    """$CLAUDE_HELPER_RULES, else the user's copy in ~/.claude-helper, else the bundled defaults."""
    for p in (os.getenv('CLAUDE_HELPER_RULES'), os.path.join(HOME, 'guard_rules.json')):
        if p and os.path.exists(os.path.expanduser(p)):
            return os.path.expanduser(p)
    return os.path.join(HERE, 'guard_rules.default.json')


def pinned_agents():
    """Agent types whose definition (~/.claude/agents or ./.claude/agents) sets its own `model:` in the frontmatter."""
    out = set()
    for f in glob.glob(os.path.expanduser('~/.claude/agents/*.md')) + glob.glob('.claude/agents/*.md'):
        try:
            head = open(f, errors='replace').read(4000).split('---')
        except OSError:
            continue
        if len(head) < 3:
            continue
        fm = dict(l.split(':', 1) for l in head[1].splitlines() if ':' in l)
        if fm.get('model', '').strip() not in ('', 'inherit'):
            out.add(fm.get('name', os.path.basename(f)[:-3]).strip())
    return out


def weighted(u):
    cw = u.get('cache_creation_input_tokens') or 0
    cc = u.get('cache_creation') or {}
    c5, c1 = cc.get('ephemeral_5m_input_tokens', cw), cc.get('ephemeral_1h_input_tokens', 0)
    return ((u.get('input_tokens') or 0) + 0.1 * (u.get('cache_read_input_tokens') or 0)
            + 1.25 * c5 + 2.0 * c1 + 5.0 * (u.get('output_tokens') or 0))


def ctx(u):
    return sum(int(u.get(k) or 0) for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))


def handoff(path, sid, value):
    """Mechanical handover note from the transcript tail (no model call): task, files touched, commits, last text."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - 4_000_000))
            lines = f.read().decode('utf-8', 'ignore').splitlines()[1:]
    except OSError:
        return None
    prompts, files, commits, last_text, cwd, branch = [], {}, [], '', '', ''
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        cwd, branch = d.get('cwd') or cwd, d.get('gitBranch') or branch
        c = (d.get('message') or {}).get('content')
        if d.get('type') == 'user' and not d.get('isSidechain'):
            t = c if isinstance(c, str) else ' '.join(b.get('text', '') for b in c or [] if isinstance(b, dict) and b.get('type') == 'text')
            if t.strip() and not t.lstrip().startswith(('<system-reminder', '<command', '<local-command')):
                prompts.append(t.strip()[:300])
        elif d.get('type') == 'assistant' and not d.get('isSidechain'):
            for b in c if isinstance(c, list) else []:
                if b.get('type') == 'text' and b.get('text', '').strip():
                    last_text = b['text'].strip()
                elif b.get('type') == 'tool_use':
                    i = b.get('input') or {}
                    if b['name'] in ('Edit', 'Write', 'NotebookEdit') and i.get('file_path'):
                        files[i['file_path']] = files.get(i['file_path'], 0) + 1
                    elif b['name'] == 'Bash' and 'git commit' in (i.get('command') or ''):
                        commits.append((i['command'].splitlines() or [''])[0][:160])
    out = os.path.join(HOME, 'handoffs')
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, sid[:8] + '.md')
    md = ['# Handover (auto, claude-helper)', '', f'Session {sid}, context ~{round(value)}k tokens.', f'Directory: {cwd}  Branch: {branch}', '',
          '## What the user asked (first, last)'] + [f'- {x}' for x in (prompts[:1] + prompts[-2:])] + [
          '', '## Files edited (count)'] + [f'- {f} ({n})' for f, n in sorted(files.items(), key=lambda kv: -kv[1])[:25]] + [
          '', '## Recent commits'] + [f'- {c}' for c in commits[-5:]] + ['', '## Last assistant message', '', last_text[:1500], '',
          '## Added by the session before handing over', '', '(decisions, what is left, how to verify)', '',
          'Next session: read this file, check `git status`, and continue from the last message above.']
    open(p, 'w').write('\n'.join(md) + '\n')
    return p


def load_state(sid):
    p = os.path.join(HOME, 'state', hashlib.md5(sid.encode()).hexdigest() + '.json')
    try:
        return p, json.load(open(p))
    except (OSError, ValueError):
        return p, dict(offset=0, done=0.0, cur_id=None, cur_w=0.0, ctx=0, bash={}, fired={})


def scan(path, st):
    """Advance the transcript scan from the saved offset; messages repeat per content block, so keep the last per id."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < st['offset']:
        st.update(offset=0, done=0.0, cur_id=None, cur_w=0.0)
    with open(path, 'rb') as f:
        f.seek(st['offset'])
        data = f.read()
    end = data.rfind(b'\n') + 1
    for line in data[:end].decode('utf-8', 'ignore').splitlines():
        if '"type":"assistant"' not in line:
            continue
        try:
            m = json.loads(line).get('message') or {}
        except ValueError:
            continue
        u = m.get('usage')
        if not u:
            continue
        if m.get('id') != st['cur_id']:
            st['done'] += st['cur_w']
            st['cur_id'] = m.get('id')
        st['cur_w'] = weighted(u)
        st['ctx'] = ctx(u)
    st['offset'] += end


def metric(name, st):
    if name == 'context_k':
        return st['ctx'] / 1000
    if name == 'session_weighted_M':
        return (st['done'] + st['cur_w']) / 1e6
    if name == 'agent_no_model':
        return st.get('cur_agent_nomodel', 0)
    if name == 'same_bash_repeats':
        return st.get('cur_bash', 0)
    return 0


def log(ev):
    os.makedirs(HOME, exist_ok=True)
    with open(os.path.join(HOME, 'events.jsonl'), 'a') as f:
        f.write(json.dumps(ev) + '\n')


def main():
    if os.getenv('CLAUDE_HELPER_OFF') or os.path.exists(os.path.join(HOME, 'OFF')):
        return                      # kill switch: env var, or `touch ~/.claude-helper/OFF`
    d = json.load(sys.stdin)
    event, sid = d.get('hook_event_name'), d.get('session_id') or 'x'
    if event not in ('PreToolUse', 'PostToolUse'):
        return
    rules = json.load(open(rules_path()))['rules']
    os.makedirs(os.path.join(HOME, 'state'), exist_ok=True)
    lock = open(os.path.join(HOME, 'state', 'lock-' + hashlib.md5(sid.encode()).hexdigest()), 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)          # parallel tool calls run hooks concurrently: serialise per session
    p, st = load_state(sid)
    if d.get('transcript_path'):
        scan(d['transcript_path'], st)
    tool, ti = d.get('tool_name', ''), d.get('tool_input') or {}
    st['cur_bash'] = 0
    if event == 'PreToolUse' and tool in EDIT_TOOLS:
        st['bash'] = {}                         # re-running a test or build after a fix is not a retry loop
    if event == 'PreToolUse' and tool == 'Bash' and not read_only(ti.get('command')):
        k = hashlib.md5((ti.get('command') or '').encode()).hexdigest()
        st['bash'][k] = st['bash'].get(k, 0) + 1
        st['cur_bash'] = st['bash'][k]          # repeats of THIS command since the last edit
    st['cur_agent_nomodel'] = 0
    if event == 'PreToolUse' and tool == 'Agent' and not ti.get('model'):
        # agent types with a model in their definition carry it; everything else inherits the parent model
        st['cur_agent_nomodel'] = 0 if ti.get('subagent_type') in pinned_agents() else 1
    notes, deny = [], None
    for r in rules:
        if r['event'] != event or (r.get('tools') and tool not in r['tools']):
            continue
        v = metric(r['metric'], st)
        if v < r['gte']:
            continue
        step = r.get('repeat_step')
        last = st['fired'].get(r['id'])
        if step and last is not None and v < last + step:
            continue
        if not step and r['action'] == 'warn' and last is not None:
            continue
        if not step and r.get('mode', 'enforce') == 'shadow' and last is not None:
            continue                            # shadow block rules log once per session, not on every call
        st['fired'][r['id']] = v
        hp = handoff(d['transcript_path'], sid, v) if r.get('handoff') and d.get('transcript_path') else None
        msg = r['message'].format(value=round(v), gte=r['gte'], handoff=hp or '(no transcript)')
        enforced = r.get('mode', 'enforce') == 'enforce'
        log(dict(ts=time.time(), session=sid, rule=r['id'], metric=r['metric'], value=round(v, 2), action=r['action'],
                 mode=r.get('mode', 'enforce'), tool=tool,
                 detail=(ti.get('command') or '')[:160] if tool == 'Bash' else None))
        if enforced:
            if r['action'] == 'block':
                deny = deny or msg
            else:
                notes.append(msg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(st, open(p + '.tmp', 'w'))
    os.replace(p + '.tmp', p)
    if deny:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                                 'permissionDecisionReason': 'claude-helper guard: ' + deny}}))
    elif notes:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': event,
                                                 'additionalContext': 'claude-helper guard: ' + ' | '.join(notes)}}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
