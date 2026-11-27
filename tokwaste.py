#!/usr/bin/env python3
"""Read-only Claude Code transcript analyzer: weekly token-waste metrics.
Usage: tokwaste.py [--root ~/.claude/projects] [--out ~/.claude-helper/report.json] [--quiet]
Weighted units are input-token equivalents (cache read 0.1x, cache write 1.25x/2x, output 5x):
a relative measure, NOT dollars.
"""
import sys, os, re, json, glob, collections, datetime, argparse

ap = argparse.ArgumentParser()
ap.add_argument('--root', default=os.path.expanduser('~/.claude/projects'))
ap.add_argument('--out', default=os.path.join(os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper')), 'report.json'))
ap.add_argument('--quiet', action='store_true', help='do not print the report')
a = ap.parse_args()


COMMIT_CMD = re.compile(r'(?:^|[;&|]\s*|\n\s*)git\s+(?:-C\s+\S+\s+)?commit\b')
COMMIT_BAD = ('nothing to commit', 'nothing added to commit', 'fatal:', 'error:', 'hook failed', 'Aborting commit', 'no changes added')


def week(ts):
    d = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return d.astimezone().strftime('%Y-%m-%d')


def proj_name(d):
    """Encoded dir name -> short project label; scratch workspaces collapse into one bucket."""
    if 'scratch-workspaces' in d or 'scratch-20' in d:
        return 'scratch'
    d = re.sub(r'^-(Users|home)-[^-]+-', '', d)                      # drop the home folder
    d = re.sub(r'^(www|code|src|dev|projects|repos|git|work|Documents|Developer)-', '', d)
    d = d.split('--claude-worktrees')[0]     # worktrees count towards their parent project
    return d or '?'


def new_bucket():
    return collections.defaultdict(float)


weeks = collections.defaultdict(new_bucket)
proj_day = collections.defaultdict(lambda: collections.defaultdict(float))   # (day, project) -> counters
sessions = {}
tool_bytes = collections.Counter()
tool_calls = collections.Counter()

files = glob.glob(a.root + '/*/*.jsonl') + glob.glob(a.root + '/*/*/subagents/*.jsonl')
for n, path in enumerate(files):
    proj = proj_name(os.path.relpath(path, a.root).split(os.sep)[0])
    msgs = {}            # message id -> (ts, usage, model, sidechain)
    tool_name = {}       # tool_use_id -> name
    commit_ids = set()   # Bash tool_use ids that ran git commit
    reads = collections.Counter()  # file -> reads since last edit
    cmds = collections.Counter()
    wk_of_session = None
    sid = os.path.basename(path)
    ev = collections.defaultdict(float)  # per-week event counters, keyed (week, metric)
    try:
        fh = open(path, errors='replace')
    except OSError:
        continue
    with fh:
        for line in fh:
            if '"type":"assistant"' not in line and '"type":"user"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = d.get('timestamp')
            if not ts:
                continue
            wk = week(ts)
            t = d.get('type')
            m = d.get('message') or {}
            c = m.get('content')
            if t == 'assistant':
                mid = m.get('id') or d.get('uuid')
                u = m.get('usage')
                if u:
                    msgs[mid] = (wk, u, m.get('model') or '?', bool(d.get('isSidechain')))
                for b in c if isinstance(c, list) else []:
                    if b.get('type') != 'tool_use':
                        continue
                    nm = b['name']
                    tool_name[b['id']] = nm
                    inp = b.get('input') or {}
                    if nm == 'Read':
                        k = inp.get('file_path')
                        reads[k] += 1
                        if reads[k] > 1:
                            ev[(wk, 'reread')] += 1
                    elif nm in ('Edit', 'Write', 'NotebookEdit'):
                        reads.pop(inp.get('file_path'), None)
                    elif nm == 'Bash':
                        k = inp.get('command')
                        if COMMIT_CMD.search(k or ''):
                            commit_ids.add(b['id'])
                        cmds[k] += 1
                        if cmds[k] == 3:
                            ev[(wk, 'bash_repeat3')] += 1
            elif t == 'user' and isinstance(c, list):
                for b in c:
                    if b.get('type') != 'tool_result':
                        continue
                    cc = b.get('content')
                    size = len(cc) if isinstance(cc, str) else sum(
                        len(x.get('text', '')) for x in cc if isinstance(x, dict))
                    nm = tool_name.get(b.get('tool_use_id'), '?')
                    tool_bytes[nm] += size
                    tool_calls[nm] += 1
                    ev[(wk, 'toolres_chars')] += size
                    if b.get('tool_use_id') in commit_ids:
                        txt = cc if isinstance(cc, str) else ' '.join(x.get('text', '') for x in cc if isinstance(x, dict))
                        if not any(x in txt[:20000] for x in COMMIT_BAD):
                            ev[(wk, 'commits')] += 1
                        proj_day[(wk, proj)]['commits'] += 1
                    if size > 20000:
                        ev[(wk, 'toolres_big')] += 1
    sess = collections.defaultdict(float)
    for mid, (wk, u, model, side) in msgs.items():
        cr = u.get('cache_read_input_tokens') or 0
        cw = u.get('cache_creation_input_tokens') or 0
        i = u.get('input_tokens') or 0
        o = u.get('output_tokens') or 0
        cc5 = (u.get('cache_creation') or {}).get('ephemeral_5m_input_tokens', cw)
        cc1 = (u.get('cache_creation') or {}).get('ephemeral_1h_input_tokens', 0)
        w = i + 0.1 * cr + 1.25 * cc5 + 2.0 * cc1 + 5.0 * o
        ctx = i + cr + cw
        b = weeks[wk]
        pd = proj_day[(wk, proj)]
        pd['calls'] += 1; pd['weighted'] += w
        if ctx > 250000:
            pd['gt250k'] += 1
        if 'opus' in model:
            pd['opus_w'] += w
        b['calls'] += 1
        b['input'] += i; b['cache_read'] += cr; b['cache_write'] += cw; b['output'] += o
        b['weighted'] += w
        b['ctx_sum'] += ctx
        if ctx > 250000:
            b['calls_ctx_gt250k'] += 1
        if side:
            b['sidechain_weighted'] += w
        fam = 'opus' if 'opus' in model else 'sonnet' if 'sonnet' in model else 'haiku' if 'haiku' in model else 'other'
        b['w_' + fam] += w
        # cache waste: tokens written to cache that were never reread are visible only in aggregate
        sess[wk] += w
    for (wk, k), v in ev.items():
        weeks[wk][k] += v
    if msgs:
        sessions[sid] = (proj, sum(sess.values()), len(msgs))
    if n % 500 == 0:
        print(f'{n}/{len(files)}', file=sys.stderr)

rows = {}
raw = {wk: dict(b) for wk, b in weeks.items()}
for wk in sorted(weeks):
    b = weeks[wk]
    tot_in = b['input'] + b['cache_read'] + b['cache_write']
    rows[wk] = dict(
        calls=int(b['calls']),
        weighted_M=round(b['weighted'] / 1e6, 1),
        cache_hit=round(b['cache_read'] / tot_in, 3) if tot_in else None,
        avg_ctx_k=round(b['ctx_sum'] / b['calls'] / 1000, 1) if b['calls'] else None,
        pct_calls_gt250k=round(100 * b['calls_ctx_gt250k'] / b['calls'], 1) if b['calls'] else None,
        opus_share=round(b['w_opus'] / b['weighted'], 2) if b['weighted'] else None,
        subagent_share=round(b['sidechain_weighted'] / b['weighted'], 2) if b['weighted'] else None,
        output_share=round(5 * b['output'] / b['weighted'], 2) if b['weighted'] else None,
        rereads_per_1k_calls=round(1000 * b['reread'] / b['calls'], 1) if b['calls'] else None,
        bash_repeat3_per_1k=round(1000 * b['bash_repeat3'] / b['calls'], 1) if b['calls'] else None,
        commits=int(b['commits']),
        weighted_M_per_commit=round(b['weighted'] / 1e6 / b['commits'], 2) if b['commits'] else None,
        big_toolres_per_1k=round(1000 * b['toolres_big'] / b['calls'], 1) if b['calls'] else None,
    )
top = sorted(sessions.items(), key=lambda kv: -kv[1][1])[:10]
out = dict(
    weeks=rows,
    raw=raw,
    projects={f'{d}|{p}': {k: round(v, 1) for k, v in c.items()} for (d, p), c in sorted(proj_day.items())},
    top_sessions=[dict(session=k, project=v[0][-60:], weighted_M=round(v[1] / 1e6, 1), calls=v[2]) for k, v in top],
    tool_result_chars_M={k: round(v / 1e6, 1) for k, v in tool_bytes.most_common(10)},
    tool_result_calls=dict(tool_calls.most_common(10)),
)
os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
json.dump(out, open(a.out, 'w'), indent=1)
if not a.quiet:
    print(json.dumps(out, indent=1))
print(f'report written to {a.out}', file=sys.stderr)
