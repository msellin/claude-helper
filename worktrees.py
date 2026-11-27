#!/usr/bin/env python3
"""Git worktree audit and cleanup. Read-only by default.

Usage:
  worktrees.py                 scan: table of what is removable and what needs a human
  worktrees.py --md            same as a Markdown section (digest.py uses this)
  worktrees.py --json          machine-readable result
  worktrees.py --apply         remove only worktrees classed `removable` (asks nothing, logs everything)
  worktrees.py --apply --dry-run   show the git commands without running them

A worktree is `removable` only when ALL hold: the folder exists, it is not locked, no process has it as
its working directory, it has no modified or untracked files, no nested worktrees, under 5 MB of git-ignored non-regenerable
files (.env.local, .data/ ...; these are copied to ~/.claude-helper/worktree-backups/ before removal), its HEAD is already contained in the repo's
default remote branch (origin/main or origin/master), and it is older than --min-age-hours (default 24).
Everything else is `review` (with the reason) or `keep`. Branches are never deleted, only the folder.
Missing folders are `prune` (git worktree prune). Every removal is logged to
~/.claude-helper/worktree-removals.jsonl with the HEAD sha so it can be re-created.

Config (optional) ~/.claude-helper/worktrees.json: {"roots": ["~/code"], "min_age_hours": 24, "stale_days": 14, "protect": ["substring of a path to never touch"]}
Worktrees whose path appears in a launchd plist or the crontab are never touched (they are live service checkouts).
Roots are scanned one level deep for git repos; their worktrees are found through `git worktree list`,
wherever on disk those live."""
import argparse, concurrent.futures as F, datetime as D, hashlib, json, os, shutil, subprocess, sys, time

HOME = os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper'))
CFG = os.path.join(HOME, 'worktrees.json')
DEFAULT_ROOTS = ['~/www', '~/code', '~/src', '~/dev', '~/projects', '~/repos', '~/git', '~/Developer', '~/work']


def sh(args, cwd=None, timeout=120):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 1, ''


def git(repo, *args):
    return sh(['git', '-C', repo, *args])


def find_repos(roots):
    out = []
    for root in roots:
        root = os.path.expanduser(root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for n in names:
            p = os.path.join(root, n)
            if os.path.isdir(os.path.join(p, '.git')):  # a directory, so a main checkout, not a worktree
                out.append(p)
    return out


def default_ref(repo):
    rc, o = git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    if rc == 0 and o.strip():
        return o.strip()
    for r in ('origin/main', 'origin/master'):
        if git(repo, 'rev-parse', '--verify', '-q', r)[0] == 0:
            return r
    return None


def list_worktrees(repo):
    rc, o = git(repo, 'worktree', 'list', '--porcelain')
    wts, cur = [], {}
    for line in o.splitlines() + ['']:
        if not line:
            if cur:
                wts.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(' ')
        cur[k] = v or True
    return wts[1:] if wts else []  # first entry is the main checkout


def cwd_set():
    rc, o = sh(['lsof', '-a', '-d', 'cwd', '-Fn'], timeout=60)
    return {l[1:] for l in o.splitlines() if l.startswith('n')}


def du_mb(path):
    rc, o = sh(['du', '-sk', path], timeout=300)
    try:
        return int(o.split()[0]) / 1024
    except (ValueError, IndexError):
        return 0.0


# Ignored paths that are regenerable and never worth keeping. Anything else git ignores (.env.local, .data/, ...)
# is not in git, so `worktree remove` would destroy it: it is backed up first, or the worktree goes to review.
REGEN = {'node_modules', '.next', 'dist', 'build', '.turbo', '.cache', '__pycache__', '.venv', 'venv', 'coverage', '.pytest_cache',
         'target', '.gradle', '.nuxt', 'out', '.vite', '.parcel-cache', 'playwright-report', 'test-results', '.vercel', '.wrangler',
         '.open-next', '.DS_Store', '.idea', 'tsconfig.tsbuildinfo', '.eslintcache', 'next-env.d.ts', '.test-dist'}
BACKUP_CAP_MB = 5
PROTECT = []  # substrings from worktrees.json {"protect": [...]}; set in main()


def md5(p):
    try:
        return hashlib.md5(open(p, 'rb').read()).hexdigest()
    except OSError:
        return None


def ignored_extras(repo, path):
    """Non-regenerable ignored paths in a worktree, minus files byte-identical to the main checkout's copy."""
    _, o = git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory')
    out = []
    for p in o.splitlines():
        q = p.rstrip('/')
        if not q or q.split('/')[-1] in REGEN or q.endswith(('.pyc', '.log', '.tsbuildinfo')):
            continue
        fp = os.path.join(path, q)
        if os.path.isfile(fp) and md5(fp) == md5(os.path.join(repo, q)):
            continue
        out.append(q)
    return out


_REFS = None


def external_refs():
    """Text of every launchd plist and the crontab: a worktree whose path appears there is a live service checkout."""
    global _REFS
    if _REFS is None:
        _REFS = []
        for d in ('~/Library/LaunchAgents', '/Library/LaunchAgents', '/Library/LaunchDaemons'):
            d = os.path.expanduser(d)
            try:
                for n in os.listdir(d):
                    if n.endswith('.plist') and not n.startswith('com.claude-helper.'):
                        try:
                            _REFS.append((n, open(os.path.join(d, n), errors='replace').read()))
                        except OSError:
                            pass
            except OSError:
                pass
        _REFS.append(('crontab', sh(['crontab', '-l'])[1]))
    return _REFS


def newest_mtime(path, names):
    t = 0.0
    for n in names:
        try:
            t = max(t, os.stat(os.path.join(path, n)).st_mtime)
        except OSError:
            pass
    return t


def classify(repo, ref, wt, cwds, min_age, stale_days):
    path = wt['worktree']
    row = dict(repo=os.path.basename(repo), repo_path=repo, path=path, branch=(wt.get('branch') or 'detached').replace('refs/heads/', ''),
               head=wt.get('HEAD', '')[:9], head_full=wt.get('HEAD', ''), status='review', reason='', mb=0.0)
    if not os.path.isdir(path) or 'prunable' in wt:
        row.update(status='prune', reason='folder missing')
        return row
    row['mb'] = du_mb(path)
    if 'locked' in wt:
        row.update(reason='locked' + (f": {wt['locked']}" if wt['locked'] is not True else ''))
        return row
    if any(c == path or c.startswith(path + '/') for c in cwds):
        row.update(reason='process running in it')
        return row
    for name, text in external_refs():
        if path in text:
            row.update(reason=f'in use by {name}')
            return row
    for pat in PROTECT:
        if pat in path:
            row.update(reason=f'protected by config ({pat})')
            return row
    nested = os.path.join(path, '.claude', 'worktrees')
    if os.path.isdir(nested) and os.listdir(nested):
        row.update(reason='contains nested worktrees')
        return row
    if not ref:
        row.update(reason='no origin/main or origin/master to compare against')
        return row
    _, st = git(path, 'status', '--porcelain')
    lines = st.splitlines()
    mod = sum(1 for l in lines if not l.startswith('??'))
    unt = len(lines) - mod
    merged = git(path, 'merge-base', '--is-ancestor', 'HEAD', ref)[0] == 0
    _, ch = git(path, 'cherry', ref, 'HEAD')
    unique = sum(1 for l in ch.splitlines() if l.startswith('+'))  # commits whose patch is not in ref (squash merges count as in)
    pushed = bool(git(path, 'branch', '-r', '--contains', 'HEAD')[1].strip())
    preserved = merged or unique == 0 or pushed  # every commit is already safe elsewhere
    _, ct = git(path, 'log', '-1', '--format=%ct')
    try:
        newest = max(int(ct.strip()), os.stat(path).st_mtime, newest_mtime(path, [l[3:].strip('"').split(' -> ')[-1] for l in lines]))
    except (ValueError, OSError):
        newest = 0
    age_h = (time.time() - newest) / 3600
    extras = ignored_extras(repo, path)
    size = sum(du_mb(os.path.join(path, e)) for e in extras)
    if size > BACKUP_CAP_MB:
        row.update(reason=f'{size:.0f} MB of git-ignored files ({", ".join(extras[:3])})')
        return row
    row['backup'] = extras
    bits = []
    if mod or unt:
        bits.append(f'{mod} modified, {unt} untracked')
    if not preserved:
        bits.append(f'{unique} commits only in this worktree')
    if not bits:
        if age_h < min_age:
            row.update(reason=f'touched {age_h:.0f}h ago (min {min_age}h)')
            return row
        why = 'merged into ' + ref if merged else ('already in ' + ref + ' (squash)' if unique == 0 else 'pushed to a remote branch')
        row.update(status='removable', reason=f'clean, {why}' + (f'; backs up {len(extras)} ignored path(s)' if extras else ''))
        return row
    if age_h < stale_days * 24:
        row.update(reason=', '.join(bits) + f'; touched {age_h / 24:.0f}d ago (archive after {stale_days}d)')
        return row
    row.update(status='archive', reason=', '.join(bits) + f'; idle {age_h / 24:.0f}d',
               keep_branch=(not preserved and row['branch'] == 'detached'), dirty=bool(mod or unt))
    return row


def scan(roots, min_age, stale_days):
    repos, seen, jobs = find_repos(roots), set(), []
    cwds = cwd_set()
    with F.ThreadPoolExecutor(8) as ex:
        for repo in repos:
            common = os.path.realpath(os.path.join(repo, '.git'))
            if common in seen:
                continue
            seen.add(common)
            ref = default_ref(repo)
            for wt in list_worktrees(repo):
                jobs.append(ex.submit(classify, repo, ref, wt, cwds, min_age, stale_days))
        rows = [j.result() for j in jobs]
    return sorted(rows, key=lambda r: (r['status'], -r['mb']))


def copy_to(dest, root, rel):
    os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
    src = os.path.join(root, rel)
    shutil.copytree(src, os.path.join(dest, rel), symlinks=True) if os.path.isdir(src) and not os.path.islink(src) else shutil.copy2(src, os.path.join(dest, rel), follow_symlinks=False)


def backup(r, dest):
    """Everything `git worktree remove` would destroy and a commit does not hold. Raises OSError/RuntimeError on any problem."""
    os.makedirs(dest, exist_ok=True)
    for e in r.get('backup') or []:
        copy_to(dest, r['path'], e)
    if r.get('dirty'):
        with open(os.path.join(dest, 'dirty.patch'), 'wb') as fh:  # bytes straight to disk: no decoding surprises
            if subprocess.run(['git', '-C', r['path'], 'diff', 'HEAD', '--binary'], stdout=fh, timeout=300).returncode != 0:
                raise RuntimeError('git diff failed')
        _, un = git(r['path'], 'ls-files', '--others', '--exclude-standard')
        total = 0
        for rel in un.splitlines():
            total += os.path.getsize(os.path.join(r['path'], rel)) if os.path.isfile(os.path.join(r['path'], rel)) else 0
            if total > 50 * 1024 * 1024:
                raise RuntimeError('over 50 MB of untracked files')
            copy_to(os.path.join(dest, 'untracked'), r['path'], rel)
    json.dump(dict(path=r['path'], branch=r['branch'], head=r['head_full'], reason=r['reason']), open(os.path.join(dest, 'meta.json'), 'w'))
    if r.get('keep_branch'):
        name = f"claude-helper/archive/{os.path.basename(r['path'])}-{r['head']}"
        if git(r['repo_path'], 'branch', name, r['head_full'])[0] != 0 and git(r['repo_path'], 'rev-parse', '--verify', '-q', name)[0] != 0:
            raise RuntimeError('could not preserve commits as a branch')
        r['kept_as'] = name


def apply(rows, dry, archive):
    os.makedirs(HOME, exist_ok=True)
    log = open(os.path.join(HOME, 'worktree-removals.jsonl'), 'a')
    done, failed = 0, 0
    pruned = set()
    for r in rows:
        if r['status'] == 'prune' and r['repo_path'] not in pruned:
            pruned.add(r['repo_path'])
            print(f"git -C {r['repo_path']} worktree prune")
            if not dry:
                git(r['repo_path'], 'worktree', 'prune')
        if r['status'] != 'removable' and not (archive and r['status'] == 'archive'):
            continue
        cmd = ['git', '-C', r['repo_path'], 'worktree', 'remove', r['path']]
        if r['status'] == 'archive':
            cmd.insert(-1, '--force')  # only after backup() succeeded; clean ones never use --force
        print(('[archive] ' if r['status'] == 'archive' else '') + ' '.join(cmd))
        if dry:
            continue
        dest = os.path.join(HOME, 'worktree-backups', f"{D.date.today()}-{os.path.basename(r['repo_path'])}-{os.path.basename(r['path'])}")
        try:
            if r.get('backup') or r['status'] == 'archive':
                backup(r, dest)
        except (OSError, RuntimeError) as err:
            failed += 1
            print(f'  backup failed ({err}), left in place')
            continue
        rc, _ = sh(cmd, timeout=600)
        if rc == 0:
            done += 1
            log.write(json.dumps(dict(ts=D.datetime.now().isoformat(timespec='seconds'), repo=r['repo_path'], path=r['path'], tier=r['status'],
                                      branch=r['branch'], head=r['head'], mb=round(r['mb']), backup=dest if os.path.isdir(dest) else None,
                                      kept_as=r.get('kept_as'))) + '\n')
        else:
            failed += 1
            print('  failed, left in place')
    log.close()
    print(f'removed {done}, failed {failed}' + (' (dry run)' if dry else ''))


def gb(mb):
    return f'{mb / 1024:.1f} GB'


def render(rows, md):
    by = {s: [r for r in rows if r['status'] == s] for s in ('removable', 'archive', 'review', 'prune')}
    tot = sum(r['mb'] for r in rows)
    h = '## Worktrees' if md else 'Worktrees'
    out = [h, '', f"{len(rows)} extra worktrees, {gb(tot)}. Removable now: {len(by['removable'])} ({gb(sum(r['mb'] for r in by['removable']))}). Archivable (backup, then remove): {len(by['archive'])} ({gb(sum(r['mb'] for r in by['archive']))}). "
           f"Need review: {len(by['review'])}. Stale entries to prune: {len(by['prune'])}.", '']
    for s, title in (('review', 'Needs a human'), ('archive', 'Archivable (--apply --archive)'), ('removable', 'Removable (--apply)')):
        if by[s]:
            out += [f'### {title}' if md else f'{title}:', '| Repo | Worktree | Branch | Size | Why |' if md else '']
            if md:
                out.append('|---|---|---|---|---|')
            for r in by[s][:25]:
                p = r['path'].replace(os.path.expanduser('~'), '~')
                out.append(f"| {r['repo']} | {p} | {r['branch']} | {r['mb']:.0f} MB | {r['reason']} |" if md else
                           f"  {r['mb']:7.0f} MB  {p}  [{r['branch']}]  {r['reason']}")
            if len(by[s]) > 25:
                out.append(f'... and {len(by[s]) - 25} more' if not md else f'| | ... and {len(by[s]) - 25} more | | | |')
            out.append('')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true'); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--md', action='store_true'); ap.add_argument('--json', action='store_true')
    ap.add_argument('--archive', action='store_true', help='with --apply: also back up and remove idle worktrees that have edits or unique commits'); ap.add_argument('--stale-days', type=float)
    ap.add_argument('--only', help='with --apply: only worktrees whose path contains this text'); ap.add_argument('--root', action='append'); ap.add_argument('--min-age-hours', type=float)
    a = ap.parse_args()
    cfg = {}
    try:
        cfg = json.load(open(CFG))
    except (OSError, ValueError):
        pass
    PROTECT[:] = cfg.get('protect', [])
    roots = a.root or cfg.get('roots') or [r for r in DEFAULT_ROOTS if os.path.isdir(os.path.expanduser(r))]
    min_age = a.min_age_hours if a.min_age_hours is not None else cfg.get('min_age_hours', 24)
    stale = a.stale_days if a.stale_days is not None else cfg.get('stale_days', 14)
    rows = scan(roots, min_age, stale)
    if a.json:
        print(json.dumps(rows, indent=1))
    elif a.apply:
        apply([r for r in rows if not a.only or a.only in r['path']], a.dry_run, a.archive)
    else:
        print(render(rows, a.md))


if __name__ == '__main__':
    main()
