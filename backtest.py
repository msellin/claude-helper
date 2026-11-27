#!/usr/bin/env python3
"""Replay guard rules over historical transcripts: how often would each rule have fired, and how much cost came after?
Usage: backtest.py [--rules <file>] [--root ~/.claude/projects] [--since 2026-10-01]
Main session files only (not subagent transcripts). Cost = weighted input-token equivalents (see guard.weighted)."""
import sys, os, json, glob, argparse, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guard import weighted, ctx, rules_path, pinned_agents
PINNED = pinned_agents()

ap = argparse.ArgumentParser()
ap.add_argument('--rules', default=rules_path())
ap.add_argument('--root', default=os.path.expanduser('~/.claude/projects'))
ap.add_argument('--since', default='', help='YYYY-MM-DD')
a = ap.parse_args()


def timeline(path):
    """List of per-API-call dicts (w, ctx, bash = max identical-Bash count so far), last usage per message id."""
    ents, by_id, bash = [], {}, collections.Counter()
    for line in open(path, errors='replace'):
        if '"type":"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get('message') or {}
        u = m.get('usage')
        if not u or (a.since and (d.get('timestamp') or '') < a.since):
            continue
        bmax = nomodel = 0
        for b in m.get('content') or []:
            if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Agent':
                i = b.get('input') or {}
                nomodel = nomodel or int(not i.get('model') and i.get('subagent_type') not in PINNED)
            if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Bash':
                bash[(b.get('input') or {}).get('command')] += 1
                bmax = max(bmax, bash[(b.get('input') or {}).get('command')])
        e = by_id.get(m.get('id'))
        if e is None:
            e = by_id[m.get('id')] = {}
            ents.append(e)
        e.update(w=weighted(u), ctx=ctx(u), bash=max(e.get('bash', 0), bmax), nomodel=max(e.get('nomodel', 0), nomodel))
    return ents


def value(metric, ents, i, cum):
    e = ents[i]
    return {'context_k': e['ctx'] / 1000, 'session_weighted_M': cum / 1e6, 'same_bash_repeats': e['bash'], 'agent_no_model': e['nomodel']}.get(metric, 0)


def fires(rule, ents):
    """Indices where the rule would fire (first crossing, then every repeat_step of growth)."""
    out, last, cum = [], None, 0.0
    for i, e in enumerate(ents):
        cum += e['w']
        v = value(rule['metric'], ents, i, cum)
        if v < rule['gte']:
            continue
        step = rule.get('repeat_step')
        if last is not None and (not step and rule['action'] == 'warn' or step and v < last + step):
            continue
        out.append(i); last = v
    return out


files = glob.glob(a.root + '/*/*.jsonl')
sess = [t for t in (timeline(f) for f in files) if t]
tot_calls = sum(len(t) for t in sess); tot_w = sum(e['w'] for t in sess for e in t)
print(f'{len(sess)} sessions, {tot_calls} calls, {tot_w/1e6:.0f}M weighted' + (f' (since {a.since})' if a.since else ''))


def run(rule):
    n_sess = n_fire = after_calls = 0; after_w = 0.0
    for t in sess:
        f = fires(rule, t)
        if not f:
            continue
        n_sess += 1; n_fire += len(f)
        after_calls += len(t) - 1 - f[0]
        after_w += sum(e['w'] for e in t[f[0] + 1:])
    return n_sess, n_fire, after_calls, after_w


def line(label, r):
    n_sess, n_fire, ac, aw = r
    return (f'{label:34s} sessions {n_sess:4d} ({100*n_sess/len(sess):4.1f}%)  fires {n_fire:5d}  '
            f'calls after 1st fire {100*ac/tot_calls:4.1f}%  cost after 1st fire {100*aw/tot_w:4.1f}%')


print('\n== your rules ==')
for r in json.load(open(a.rules))['rules']:
    print(line(f"{r['id']} ({r['metric']}>={r['gte']})", run(r)))

print('\n== threshold sweeps (first crossing per session) ==')
for k in (150, 200, 250, 300, 350, 400, 500):
    print(line(f'context_k >= {k}', run(dict(metric='context_k', gte=k, action='block'))))
for m in (5, 10, 15, 25, 40):
    print(line(f'session_weighted_M >= {m}', run(dict(metric='session_weighted_M', gte=m, action='block'))))
for n in (2, 3, 4, 5):
    print(line(f'same_bash_repeats >= {n}', run(dict(metric='same_bash_repeats', gte=n, action='block'))))
