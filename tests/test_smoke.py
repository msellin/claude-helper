"""Offline smoke tests: a fake transcript plus a throwaway CLAUDE_HELPER_HOME, settings.json and LaunchAgents folder.
Never touches ~/.claude or your real launchd jobs. Run: python3 -m unittest discover tests"""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def msg(i, ctx_tokens, tool=None, inp=None, model='claude-opus-x'):
    content = [{'type': 'tool_use', 'id': f't{i}', 'name': tool, 'input': inp or {}}] if tool else [{'type': 'text', 'text': 'hi'}]
    return {'type': 'assistant', 'timestamp': f'2026-01-0{1 + i % 5}T10:00:00Z', 'cwd': '/tmp/x', 'gitBranch': 'main',
            'message': {'id': f'm{i}', 'model': model, 'content': content,
                        'usage': {'input_tokens': 10, 'cache_read_input_tokens': ctx_tokens, 'cache_creation_input_tokens': 0, 'output_tokens': 100}}}


class Smoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(os.environ, CLAUDE_HELPER_HOME=os.path.join(self.tmp, 'home'),
                        CLAUDE_SETTINGS=os.path.join(self.tmp, 'settings.json'),
                        CLAUDE_HELPER_LAUNCHAGENTS=os.path.join(self.tmp, 'LaunchAgents'))
        self.env.pop('CLAUDE_HELPER_OFF', None)
        self.env.pop('CLAUDE_HELPER_RULES', None)
        proj = os.path.join(self.tmp, 'projects', '-Users-someone-code-demo')
        os.makedirs(proj)
        self.transcript = os.path.join(proj, 's1.jsonl')
        with open(self.transcript, 'w') as f:
            f.write(json.dumps({'type': 'user', 'timestamp': '2026-01-01T09:00:00Z', 'message': {'content': 'build the thing'}}, separators=(',', ':')) + '\n')
            for i in range(5):
                f.write(json.dumps(msg(i, 300_000, 'Bash', {'command': 'npm test'}), separators=(',', ':')) + '\n')

    def py(self, script, *args, stdin=None):
        return subprocess.run([sys.executable, '-I', os.path.join(ROOT, script), *args], input=stdin,
                              capture_output=True, text=True, env=self.env)

    def guard(self, event, tool, inp, rules):
        rp = os.path.join(self.tmp, 'rules.json')
        json.dump({'rules': rules}, open(rp, 'w'))
        self.env['CLAUDE_HELPER_RULES'] = rp
        ev = dict(hook_event_name=event, session_id='abc', transcript_path=self.transcript, tool_name=tool, tool_input=inp)
        return self.py('guard.py', stdin=json.dumps(ev))

    def test_report(self):
        out = os.path.join(self.tmp, 'r.json')
        r = self.py('tokwaste.py', '--root', os.path.join(self.tmp, 'projects'), '--out', out, '--quiet')
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.load(open(out))
        self.assertEqual(sum(w['calls'] for w in rep['weeks'].values()), 5)
        self.assertEqual(rep['top_sessions'][0]['project'], 'demo')

    def test_guard_enforce_blocks_and_shadow_logs(self):
        rule = dict(id='ctx-block', metric='context_k', gte=250, event='PreToolUse', action='block', mode='enforce', message='ctx {value}k')
        r = self.guard('PreToolUse', 'Agent', {'model': 'haiku'}, [rule])
        self.assertIn('"deny"', r.stdout)
        rule.update(id='ctx-shadow', mode='shadow')
        r = self.guard('PreToolUse', 'Agent', {'model': 'haiku'}, [rule])
        self.assertEqual(r.stdout.strip(), '')
        ev = [json.loads(l) for l in open(os.path.join(self.env['CLAUDE_HELPER_HOME'], 'events.jsonl'))]
        self.assertEqual([e['mode'] for e in ev], ['enforce', 'shadow'])

    def test_guard_warn_writes_handoff(self):
        rule = dict(id='ctx-warn', handoff=True, metric='context_k', gte=250, event='PostToolUse', action='warn', mode='enforce',
                    message='see {handoff}')
        r = self.guard('PostToolUse', 'Bash', {'command': 'ls'}, [rule])
        self.assertIn('additionalContext', r.stdout)
        self.assertTrue(os.listdir(os.path.join(self.env['CLAUDE_HELPER_HOME'], 'handoffs')))

    def test_guard_kill_switch_and_bad_input_fail_open(self):
        self.env['CLAUDE_HELPER_OFF'] = '1'
        r = self.guard('PreToolUse', 'Agent', {}, [dict(id='x', metric='context_k', gte=0, event='PreToolUse', action='block', message='no')])
        self.assertEqual((r.returncode, r.stdout), (0, ''))
        del self.env['CLAUDE_HELPER_OFF']
        r = self.py('guard.py', stdin='not json')
        self.assertEqual((r.returncode, r.stdout), (0, ''))

    def test_default_rules_are_all_shadow(self):
        rules = json.load(open(os.path.join(ROOT, 'guard_rules.default.json')))['rules']
        self.assertTrue(rules and all(r['mode'] == 'shadow' for r in rules))

    def test_install_is_idempotent_and_uninstall_cleans_up(self):
        mine = {'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'echo mine'}]}]}, 'model': 'x'}
        json.dump(mine, open(self.env['CLAUDE_SETTINGS'], 'w'))
        for _ in range(2):
            r = self.py('install.py')
            self.assertEqual(r.returncode, 0, r.stderr)
        s = json.load(open(self.env['CLAUDE_SETTINGS']))
        cmds = [h['command'] for ev in s['hooks'].values() for g in ev for h in g['hooks']]
        self.assertEqual(sum('guard.py' in c for c in cmds), 2)   # one PreToolUse, one PostToolUse
        self.assertIn('echo mine', cmds)
        self.assertTrue(os.path.exists(os.path.join(self.env['CLAUDE_HELPER_HOME'], 'guard_rules.json')))
        self.assertEqual(self.py('install.py', '--uninstall').returncode, 0)
        self.assertEqual(json.load(open(self.env['CLAUDE_SETTINGS'])), mine)


if __name__ == '__main__':
    unittest.main()
