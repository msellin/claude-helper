#!/usr/bin/env python3
"""Install or remove the claude-helper guard hook in Claude Code.

Usage:
  python3 install.py                     add the guard hook to ~/.claude/settings.json, copy starter rules
  python3 install.py --schedule-digest   also run the weekly digest every Monday 08:00 (macOS launchd)
  python3 install.py --schedule-worktrees  also run the weekly worktree cleanup, Sundays 10:00 (macOS launchd)
  python3 install.py --uninstall         remove the hook and any claude-helper launchd jobs (keeps your data)
  python3 install.py --dry-run           show what would change

settings.json is backed up to settings.json.bak-claude-helper-<time> before every change. Idempotent: running it
twice does not add the hook twice. Your rules live in ~/.claude-helper/guard_rules.json and are never overwritten.
"""
import argparse, json, os, platform, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper'))
SETTINGS = os.path.expanduser(os.getenv('CLAUDE_SETTINGS', '~/.claude/settings.json'))
GUARD = os.path.join(HERE, 'guard.py')
CMD = f'python3 -I {GUARD}' if ' ' not in GUARD else f'python3 -I "{GUARD}"'
EVENTS = ('PreToolUse', 'PostToolUse')
AGENTS = os.path.expanduser(os.getenv('CLAUDE_HELPER_LAUNCHAGENTS', '~/Library/LaunchAgents'))


def is_ours(h):
    c = h.get('command', '')
    return GUARD in c or ('claude-helper' in c and 'guard.py' in c)


def load_settings():
    try:
        return json.load(open(SETTINGS))
    except FileNotFoundError:
        return {}


def save_settings(s, dry):
    if dry:
        print(json.dumps(s.get('hooks', {}), indent=2))
        return
    if os.path.exists(SETTINGS):
        b = f'{SETTINGS}.bak-claude-helper-{time.strftime("%Y%m%d-%H%M%S")}'
        shutil.copy2(SETTINGS, b)
        print(f'backup: {b}')
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + '.tmp'
    json.dump(s, open(tmp, 'w'), indent=2)
    os.replace(tmp, SETTINGS)


def strip_ours(s):
    """Remove every claude-helper guard entry; return how many were removed."""
    n = 0
    hooks = s.get('hooks', {})
    for ev in list(hooks):
        keep = []
        for group in hooks[ev]:
            inner = [h for h in group.get('hooks', []) if not is_ours(h)]
            n += len(group.get('hooks', [])) - len(inner)
            if inner:
                keep.append({**group, 'hooks': inner})
        if keep:
            hooks[ev] = keep
        else:
            del hooks[ev]
    if not hooks:
        s.pop('hooks', None)
    return n


def plist(label, args, weekday, hour, extra=''):
    a = ''.join(f'<string>{x}</string>' for x in args)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{a}</array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>{weekday}</integer><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>{HOME}/{label}.out</string>
  <key>StandardErrorPath</key><string>{HOME}/{label}.err</string>
  <key>Nice</key><integer>19</integer>{extra}
</dict></plist>
'''


def schedule(label, args, weekday, hour, dry):
    if platform.system() != 'Darwin':
        print(f'{label}: scheduling is macOS-only; on Linux add a cron line instead: '
              f'0 {hour} * * {weekday} {" ".join(args)}')
        return
    p = os.path.join(AGENTS, label + '.plist')
    print(f'schedule {label}: {p}')
    if dry:
        return
    os.makedirs(AGENTS, exist_ok=True)
    os.makedirs(HOME, exist_ok=True)
    open(p, 'w').write(plist(label, args, weekday, hour))
    uid = os.getuid()
    subprocess.run(['launchctl', 'bootout', f'gui/{uid}/{label}'], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', f'gui/{uid}', p], check=False)


def unschedule(dry):
    if not os.path.isdir(AGENTS):
        return
    for n in os.listdir(AGENTS):
        if n.startswith('com.claude-helper.') and n.endswith('.plist'):
            print(f'remove launchd job {n}')
            if not dry:
                subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{n[:-6]}'], capture_output=True)
                os.remove(os.path.join(AGENTS, n))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--schedule-digest', action='store_true')
    ap.add_argument('--schedule-worktrees', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if sys.version_info < (3, 8):
        sys.exit('claude-helper needs Python 3.8 or newer')
    s = load_settings()
    if a.uninstall:
        n = strip_ours(s)
        save_settings(s, a.dry_run)
        unschedule(a.dry_run)
        print(f'removed {n} hook entr{"y" if n == 1 else "ies"}; your data in {HOME} is kept (delete it by hand if you want)')
        return
    strip_ours(s)  # re-adding keeps the command path current if the repo moved
    for ev in EVENTS:
        s.setdefault('hooks', {}).setdefault(ev, []).append({'hooks': [{'type': 'command', 'command': CMD, 'timeout': 10}]})
    save_settings(s, a.dry_run)
    print(f'hook: {CMD}  ({", ".join(EVENTS)}) in {SETTINGS}')
    mine = os.path.join(HOME, 'guard_rules.json')
    if not os.path.exists(mine):
        print(f'rules: {mine} (starter rules, all in shadow mode = log only)')
        if not a.dry_run:
            os.makedirs(HOME, exist_ok=True)
            shutil.copy(os.path.join(HERE, 'guard_rules.default.json'), mine)
    else:
        print(f'rules: keeping your existing {mine}')
    if a.schedule_digest:
        schedule('com.claude-helper.digest', [sys.executable, os.path.join(HERE, 'digest.py')], 1, 8, a.dry_run)
    if a.schedule_worktrees:
        schedule('com.claude-helper.worktrees', ['/bin/bash', os.path.join(HERE, 'worktrees-weekly.sh')], 0, 10, a.dry_run)
    print('done. New Claude Code sessions pick up the hook. Try: ./claude-helper report')


if __name__ == '__main__':
    main()
