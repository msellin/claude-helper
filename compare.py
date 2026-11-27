#!/usr/bin/env python3
"""Before/after per change: aggregates raw daily counters in a window before and after each change date.
Usage: compare.py <report.json> <changes.json> [--days 7]
changes.json: [{"name": "what you changed", "date": "YYYY-MM-DD", "metrics": ["opus_share", ...]}]
(see changes.example.json; metric names are the keys printed below)"""
import sys, json, datetime

rep = json.load(open(sys.argv[1])); rules = json.load(open(sys.argv[2]))
N = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 7
raw = rep['raw']

def agg(days):
    t = {}
    for d in days:
        for k, v in raw.get(d, {}).items():
            t[k] = t.get(k, 0) + v
    return t

def metrics(b):
    c = b.get('calls', 0); w = b.get('weighted', 0)
    tot = b.get('input', 0) + b.get('cache_read', 0) + b.get('cache_write', 0)
    if not c: return None
    return dict(calls=int(c),
        cache_hit=b['cache_read'] / tot if tot else None,
        avg_ctx_k=b['ctx_sum'] / c / 1000,
        pct_calls_gt250k=100 * b.get('calls_ctx_gt250k', 0) / c,
        opus_share=b.get('w_opus', 0) / w if w else None,
        subagent_share=b.get('sidechain_weighted', 0) / w if w else None,
        output_share=5 * b.get('output', 0) / w if w else None,
        rereads_per_1k=1000 * b.get('reread', 0) / c,
        big_toolres_per_1k=1000 * b.get('toolres_big', 0) / c,
        weighted_per_call_k=w / c / 1000,
        commits=b.get('commits', 0),
        weighted_M_per_commit=w / 1e6 / b['commits'] if b.get('commits') else None)

def span(d0, a, b):
    base = datetime.date.fromisoformat(d0)
    return [(base + datetime.timedelta(days=i)).isoformat() for i in range(a, b)]

for r in rules:
    d = r['date']
    before = metrics(agg(span(d, -N, 0))); after = metrics(agg(span(d, 0, N)))
    print(f"\n## {r['name']}  ({d}, +-{N} days)")
    if not before or not after or before['calls'] < 200 or after['calls'] < 200:
        print(f"  too little data: before={before and before['calls']} calls, after={after and after['calls']} calls"); continue
    print(f"  calls before/after: {before['calls']} / {after['calls']}")
    for m in r['metrics'] + ['weighted_per_call_k', 'weighted_M_per_commit', 'cache_hit']:
        x, y = before[m], after[m]
        if y is None: print(f'  {m}: n/a'); continue
        print(f"  {m:22s} {x:9.3f} -> {y:9.3f}  ({(y-x)/x*100:+.0f}%)" if x else f"  {m}: n/a")
