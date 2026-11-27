#!/usr/bin/env python3
"""Did the guard's rules change behaviour? Joins guard events with the session transcripts.

Usage: outcomes.py [--days 7] [--md]
  ctx-warn    per warned session: API calls after the first enforced warning (fewer = the handover happened),
              and how many handover notes a later session actually opened
  retry-loop  blocks, and how many times the model sent the same blocked command again
Shadow-mode rules are listed with fire counts only (nothing was shown to the model)."""
import argparse, collections as C, datetime as D, glob, json, os, re, statistics as S

HOME = os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper'))
ROOT = os.path.expanduser(os.getenv('CLAUDE_PROJECTS', '~/.claude/projects'))
READ_HANDOFF = re.compile(r'"name":"Read","input":\{"file_path":"[^"]*claude-helper/handoffs/')


def ts_of(s):
    return D.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def calls_after(path, t0):
    ids = set()
    for line in open(path, errors='replace'):
        if '"type":"assistant"' not in line or '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get('timestamp') and ts_of(d['timestamp']) > t0:
            ids.add((d.get('message') or {}).get('id'))
    return len(ids)


def compute(days):
    since = (D.datetime.now() - D.timedelta(days=days)).timestamp()
    try:
        ev = [json.loads(l) for l in open(os.path.join(HOME, 'events.jsonl'))]
    except OSError:
        ev = []
    ev = [e for e in ev if e.get('ts', 0) >= since]
    paths = {os.path.basename(p)[:-6]: p for p in glob.glob(os.path.join(ROOT, '*', '*.jsonl'))}
    out = {'days': days, 'rules': {}}
    by = C.defaultdict(list)
    for e in ev:
        by[e['rule']].append(e)
    for rule, es in sorted(by.items()):
        enforced = [e for e in es if e.get('mode', 'enforce') == 'enforce']
        out['rules'][rule] = r = dict(fires=len(es), sessions=len({e['session'] for e in es}), enforced=len(enforced))
        if rule == 'ctx-warn' and enforced:
            first = {}
            for e in enforced:
                first.setdefault(e['session'], e['ts'])
            after = [calls_after(paths[s], t) for s, t in first.items() if s in paths]
            if after:
                r.update(warned_sessions=len(after), calls_after_median=S.median(after),
                         stopped_within_10_pct=round(100 * sum(a <= 10 for a in after) / len(after)),
                         kept_going_50_pct=round(100 * sum(a > 50 for a in after) / len(after)))
        if rule == 'retry-loop' and enforced:
            seen = C.Counter((e['session'], e.get('detail')) for e in enforced)
            r['reblocked_same_command'] = sum(n - 1 for n in seen.values())
    notes = 0
    for p in paths.values():
        try:
            if os.path.getmtime(p) >= since and READ_HANDOFF.search(open(p, errors='replace').read()):
                notes += 1
        except OSError:
            pass
    out['handoff_notes_opened'] = notes
    try:
        out['handoff_notes_written'] = sum(os.path.getmtime(f) >= since for f in glob.glob(os.path.join(HOME, 'handoffs', '*.md')))
    except OSError:
        out['handoff_notes_written'] = 0
    return out


def render(o, md=False):
    lines = ['## Rule outcomes' if md else f'rule outcomes, last {o["days"]} days', '']
    if md:
        lines += ['| Rule | Fires | Sessions | Enforced | Effect |', '|---|---|---|---|---|']
    for rule, r in o['rules'].items():
        eff = '-'
        if 'calls_after_median' in r:
            eff = (f"after the warning: median {r['calls_after_median']:.0f} more calls, {r['stopped_within_10_pct']}% stopped within 10, "
                   f"{r['kept_going_50_pct']}% kept going past 50")
        elif 'reblocked_same_command' in r:
            eff = f"{r['reblocked_same_command']} times the model re-sent a command that was just blocked"
        elif not r['enforced']:
            eff = 'shadow only (the model saw nothing)'
        lines.append(f"| {rule} | {r['fires']} | {r['sessions']} | {r['enforced']} | {eff} |" if md else
                     f"  {rule:14s} fires {r['fires']:4d}  sessions {r['sessions']:3d}  enforced {r['enforced']:4d}  {eff}")
    if not o['rules']:
        lines.append('no guard events in this window')
    lines += ['', f"Handover notes: {o['handoff_notes_written']} written, opened in {o['handoff_notes_opened']} later sessions."]
    return '\n'.join(lines)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--md', action='store_true')
    a = ap.parse_args()
    print(render(compute(a.days), a.md))
