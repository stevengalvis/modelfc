"""Fake pidfds and pipe lines only; no native process or namespace operation."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OBSERVER_CODE = compile((ROOT / 'proc_bracket_runtime.inc').read_text(), '<bracket>', 'exec')


class Bracket(unittest.TestCase):
    def setup_observer(self):
        identity = dict(pid=10637, start=1125286, uid=63153, gid=63153,
                        invocation='a' * 32, group='/system.slice/zen53-mincompat.service')
        self.events = []
        self.alive = True
        self.now = 100
        self.identity = identity
        def require(value, code):
            if not value:
                raise RuntimeError(code)
        proc = dict(id=344, device='0:48', root='/', destination='/proc',
                    type='proc', options='rw,nosuid,nodev,noexec', super='rw,hidepid=invisible')
        env = dict(require=require, json=json, hashlib=hashlib,
            provision_worker=NS(procfd=12, identity=identity),
            parse_proc_identity=lambda *a: self.identity.copy(),
            read_at=lambda fd, name, limit: b'0 0 4294967295\n' if name.endswith('_map') else b'fixture',
            parse_mounts=lambda raw: [proc], mount_visibility=lambda mounts: ({344}, {}),
            os=NS(getpid=lambda: 10637, pidfd_open=lambda *a: 11,
                  set_inheritable=lambda *a: None, close=lambda *a: None,
                  readlink=lambda path, **kw: path[3:] + ':[4026531837]'),
            select=NS(select=lambda *a: ([] if self.alive else [11], [], [])),
            time=NS(monotonic_ns=lambda: self.now),
            emit=lambda phase, **data: self.events.append((phase, data)))
        exec(OBSERVER_CODE, env)
        return env['native_bracket'], env

    @staticmethod
    def line(pid=10637, clock='120,121'):
        return (f'ZEN53_REPORT_CONTEXT version=1 nspid=10942,2 uid=63153,63153,63153,63153 '
                f'gid=63153,63153,63153,63153 uidmap=63153,0,1 gidmap=63153,0,1 '
                f'outer={pid},1125286 ns=4026532552,4026532493,4026532553,4026532491 '
                f'proc=401,0,48 clock={clock}\n').encode()

    def test_bound_live_before_fork_after_context(self):
        observer, _ = self.setup_observer()
        observer.begin()
        self.now = 200
        observer.observe(self.line())
        value = self.events[0][1]
        self.assertEqual(value['state'], 'BOUND_OUTER_LIVE_BEFORE_FORK_AND_AFTER_CONTEXT')
        self.assertEqual(value['before']['at_ns'], 100)
        self.assertEqual(value['after']['at_ns'], 200)
        self.assertEqual(value['before']['proc']['super'], 'rw,hidepid=invisible')
        self.assertEqual(value['context_sha256'], hashlib.sha256(self.line()).hexdigest())
        self.assertEqual(value['meaning'], 'OBSERVATION_ONLY_NOT_ACCEPTANCE')
        observer.observe(self.line())
        self.assertEqual(len(self.events), 1)

    def test_dead_before_or_after_cannot_bind(self):
        for death_before in (True, False):
            observer, _ = self.setup_observer()
            self.alive = not death_before
            observer.begin()
            self.alive = False
            self.now = 200
            observer.observe(self.line())
            self.assertEqual(self.events[0][1]['state'], 'UNKNOWN')

    def test_wrong_pid_reuse_bad_interval_and_missing_context(self):
        for kind in ('pid', 'start', 'interval', 'malformed'):
            observer, _ = self.setup_observer()
            observer.begin()
            self.now = 200
            line = self.line()
            if kind == 'pid': line = self.line(pid=99999)
            if kind == 'start': self.identity = dict(self.identity, start=99999)
            if kind == 'interval': line = self.line(clock='121,120')
            if kind == 'malformed': line = b'ZEN53_REPORT_CONTEXT version=1 state=UNKNOWN\n'
            observer.observe(line)
            self.assertEqual(self.events[0][1]['state'], 'UNKNOWN', kind)
        observer, _ = self.setup_observer()
        observer.begin()
        observer.observe(b'unrelated output\n')
        self.assertEqual(self.events, [])

    def test_generated_hook_order_and_unchanged_classifier(self):
        import proc_bracket
        raw = (ROOT / 'historical/launch.py').read_bytes()
        patched = proc_bracket.instrument(raw).decode()
        self.assertLess(patched.index('native_bracket.begin()'), patched.index('pid = os.fork()'))
        self.assertIn('native_bracket.begin()\n        provision_worker.before_native()\n    pid = os.fork()', patched)
        self.assertLess(patched.index('native_bracket.observe(line)'), patched.index('lower = line.lower()'))
        old = ast.parse(raw)
        new = ast.parse(patched)
        def classifier(tree):
            return next(ast.dump(n, include_attributes=False) for n in ast.walk(tree)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == 'need' and len(n.args) == 2
                        and isinstance(n.args[1], ast.Constant)
                        and n.args[1].value == 'CODEX_STARTUP_ACCESS_OR_WRITE_FAILURE')
        self.assertEqual(classifier(old), classifier(new))
        with self.assertRaisesRegex(ValueError, 'BRACKET_INSTRUMENTATION_SITE'):
            proc_bracket.instrument(patched.encode())

    def test_context_is_observed_before_fatal_line_for_every_pipe_split(self):
        import proc_bracket
        generated = proc_bracket.instrument((ROOT / 'historical/launch.py').read_bytes())
        tree = ast.parse(generated)
        selected = [n for n in tree.body if
                    isinstance(n, ast.ClassDef) and n.name == 'NativeOutput' or
                    isinstance(n, ast.FunctionDef) and n.name == 'need' or
                    isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and
                        t.id == 'EXPECTED_ALIAS_WARNING' for t in n.targets)]
        classifier_code = compile(ast.Module(body=selected, type_ignores=[]), '<classifier>', 'exec')
        block = self.line() + b'ZEN53_REPORT_REQUIRED_OBSERVATION_FAILED\n'
        for split in range(len(block) + 1):
            observer, env = self.setup_observer()
            observer.begin()
            self.now = 200
            exec(classifier_code, env)
            output = env['NativeOutput']()
            with self.assertRaisesRegex(RuntimeError, 'CODEX_STARTUP_ACCESS_OR_WRITE_FAILURE'):
                output.feed(block[:split])
                output.feed(block[split:])
            self.assertEqual(len(self.events), 1)
            self.assertEqual(self.events[0][1]['state'],
                             'BOUND_OUTER_LIVE_BEFORE_FORK_AND_AFTER_CONTEXT')

    def test_death_during_after_sample_is_unknown(self):
        observer, env = self.setup_observer()
        observer.begin()
        observations = iter(([], [11]))
        env['select'] = NS(select=lambda *a: (next(observations), [], []))
        self.now = 200
        observer.observe(self.line())
        self.assertEqual(self.events[0][1]['state'], 'UNKNOWN')

    def test_pidfd_is_noninheritable_and_closed_once_on_sample_failure(self):
        observer, env = self.setup_observer()
        inherited, closed = [], []
        env['os'].set_inheritable = lambda fd, value: inherited.append((fd, value))
        env['os'].close = closed.append
        self.alive = False
        observer.begin()
        self.assertIsNone(observer.before)
        self.assertEqual(inherited, [(11, False)])
        observer.close()
        observer.close()
        self.assertEqual(closed, [11])


if __name__ == '__main__':
    unittest.main()
