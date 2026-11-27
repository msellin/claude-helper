#!/usr/bin/env python3
"""Weekly digest: last 7 days vs the 7 before, per-project cost, guard activity, rules to retire.
Usage: digest.py [--days 7]   Writes ~/.claude-helper/digests/<date>.md and prints it."""
import argparse, collections as C, datetime as D, json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guard import rules_path
from outcomes import compute as outcomes, render as render_outcomes

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper'))
ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int, default=7); a = ap.parse_args()

tmp = os.path.join(tempfile.mkdtemp(), 'r.json')
subprocess.run(['nice', sys.executable, '-I', os.path.join(HERE, 'tokwaste.py'), '--out', tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
rep = json.load(open(tmp))
today = D.date.today()
cut1, cut0 = (today - D.timedelta(days=a.days)).isoformat(), (today - D.timedelta(days=2 * a.days)).isoformat()
cur = lambda d: d >= cut1
prev = lambda d: cut0 <= d < cut1


def agg(sel):
    t = C.Counter()
    for d, b in rep['raw'].items():
        if sel(d):
            for k, v in b.items():
                t[k] += v
    w = t['weighted'] or 1
    n = t['calls'] or 1
    u = t['usd'] or 1
    return dict(calls=int(t['calls']), weighted_M=t['weighted'] / 1e6, commits=int(t['commits']), usd=t['usd'],
                usd_per_commit=(t['usd'] / t['commits']) if t['commits'] else None, opus_usd_pct=100 * t['usd_opus'] / u,
                M_per_commit=(t['weighted'] / 1e6 / t['commits']) if t['commits'] else None,
                gt250k_pct=100 * t['calls_ctx_gt250k'] / n, avg_ctx_k=t['ctx_sum'] / n / 1000,
                opus_pct=100 * t['w_opus'] / w, subagent_pct=100 * t['sidechain_weighted'] / w,
                big_res_per_1k=1000 * t['toolres_big'] / n, rereads_per_1k=1000 * t['reread'] / n)


c, p = agg(cur), agg(prev)
f = lambda x, d=1: '-' if x is None else f'{x:.{d}f}'
rows = [('Est. API cost ($)', 'usd', 0, 0), ('$ per commit', 'usd_per_commit', 2, 0), ('Opus share of cost (%)', 'opus_usd_pct', 0, 0),
        ('Weighted units (M)', 'weighted_M', 1, 0), ('Commits', 'commits', 0, 1), ('M per commit', 'M_per_commit', 2, 0),
        ('Calls above 250k ctx (%)', 'gt250k_pct', 1, 0), ('Avg context (k)', 'avg_ctx_k', 0, 0), ('Opus share (%)', 'opus_pct', 0, 0),
        ('Subagent share (%)', 'subagent_pct', 0, 0), ('Big tool results /1k calls', 'big_res_per_1k', 1, 0), ('File re-reads /1k calls', 'rereads_per_1k', 1, 0)]
out = [f'# claude-helper digest {today} (last {a.days} days vs previous {a.days})', '', '| Metric | Now | Before | Change |', '|---|---|---|---|']
for label, k, dec, _ in rows:
    ch = '' if not p[k] or c[k] is None else f'{100 * (c[k] - p[k]) / p[k]:+.0f}%'
    out.append(f'| {label} | {f(c[k], dec)} | {f(p[k], dec)} | {ch} |')

flags = []
if c['opus_usd_pct'] - p['opus_usd_pct'] > 10: flags.append(f"Opus share of cost up {c['opus_usd_pct'] - p['opus_usd_pct']:.0f} points ({p['opus_usd_pct']:.0f}% to {c['opus_usd_pct']:.0f}%): is the model-per-task rule slipping?")
if c['gt250k_pct'] > 15 and c['gt250k_pct'] > p['gt250k_pct']: flags.append(f"{c['gt250k_pct']:.0f}% of calls run above 250k context (was {p['gt250k_pct']:.0f}%): handoffs are not happening.")
if p['M_per_commit'] and c['M_per_commit'] and c['M_per_commit'] > 1.3 * p['M_per_commit']: flags.append('Cost per commit rose over 30%.')
if p['usd'] and c['usd'] > 1.5 * p['usd']: flags.append('Estimated cost rose over 50%.')
if p['usd_per_commit'] and c['usd_per_commit'] and c['usd_per_commit'] > 1.3 * p['usd_per_commit']: flags.append('Estimated cost per commit rose over 30%.')
out += ['', '## Flags'] + ([f'- {x}' for x in flags] or ['- none'])

pr = C.defaultdict(C.Counter)
for k, v in rep.get('projects', {}).items():
    d, name = k.split('|', 1)
    if cur(d):
        for m, x in v.items():
            pr[name][m] += x
out += ['', '## Projects by cost', '| Project | Est. $ | Commits | $/commit | >250k % | Opus % of $ |', '|---|---|---|---|---|---|']
for name, v in sorted(pr.items(), key=lambda kv: -kv[1]['usd'])[:8]:
    out.append(f"| {name[:40]} | {v['usd']:.0f} | {int(v['commits'])} | {f(v['usd'] / v['commits'], 2) if v['commits'] else '-'} | {100 * v['gt250k'] / v['calls']:.0f} | {100 * v['opus_usd'] / v['usd'] if v['usd'] else 0:.0f} |")

ev = []
try:
    ev = [json.loads(l) for l in open(os.path.join(HOME, 'events.jsonl'))]
except OSError:
    pass
since = D.datetime.combine(today - D.timedelta(days=a.days), D.time()).timestamp()
mon = D.datetime.combine(today - D.timedelta(days=30), D.time()).timestamp()
rules = [r['id'] for r in json.load(open(rules_path()))['rules']]
out += [''] + render_outcomes(outcomes(a.days), md=True).splitlines()
loops = C.Counter(x.get('detail') for x in ev if x['rule'] == 'retry-loop' and x['ts'] >= since and x.get('detail'))
if loops:
    out += ['', 'Retry loops (command: times):'] + [f'- `{k[:90]}`: {v}' for k, v in loops.most_common(5)]
idle = [rid for rid in rules if not any(x['rule'] == rid and x['ts'] >= mon for x in ev)]
out += ['', '## Rules to retire or review', '- ' + (', '.join(idle) + ' fired 0 times in 30 days' if idle else 'every rule fired in the last 30 days')]
try:  # worktree section is best effort: a slow or failing scan must not break the digest
    wt = subprocess.run(['nice', sys.executable, '-I', os.path.join(HERE, 'worktrees.py'), '--md'], capture_output=True, text=True, timeout=600)
    if wt.returncode == 0 and wt.stdout.strip():
        out += [''] + wt.stdout.rstrip().splitlines()
except (subprocess.TimeoutExpired, OSError):
    pass
top = rep['top_sessions'][:3]
out += ['', '## Costliest sessions (all time)'] + [f"- {s['project']}: ${s.get('usd', 0):.2f}, {s['calls']} calls" for s in top]
md = '\n'.join(out) + '\n'
os.makedirs(os.path.join(HOME, 'digests'), exist_ok=True)
open(os.path.join(HOME, 'digests', f'{today}.md'), 'w').write(md)
print(md)
