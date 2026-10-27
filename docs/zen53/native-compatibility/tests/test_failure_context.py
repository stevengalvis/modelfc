"""Synthetic AST-selected checks. Never import or run sandbox entrypoints."""
import ast
import hashlib
import json
import stat
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parents[1]
REPORT = HERE / "candidate/report.py"
SUPERVISOR = HERE / "candidate/supervisor.py"
BASE = HERE / 'historical'


def functions(path, names, env):
    tree = ast.parse(path.read_text())
    chosen = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=chosen, type_ignores=[]), '<synthetic-functions>', 'exec'), env)
    return env


def reporter():
    names = ('need', 'diagnostic_context', 'deny_outer_aliases', 'failure_line',
             'failure_context_line')
    env = dict(time=time, stat=stat, json=json)
    functions(REPORT, names, env)
    original = ast.parse(REPORT.read_text())
    for node in original.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id in
                   ('FAILURE_CHECKS', 'FAILURE_CLASSES', 'FAILURE_OSERROR_TYPES',
                    'FAILURE_PROBES', 'FAILURE_REPORTER_CODES') for target in node.targets):
                if any(isinstance(target, ast.Name) and target.id == 'FAILURE_REPORTER_CODES'
                       for target in node.targets):
                    # The complete owned-code list is irrelevant to numeric context.
                    continue
                exec(compile(ast.Module(body=[node], type_ignores=[]), '<constants>', 'exec'), env)
    env['FAILURE_REPORTER_CODES'] = ()
    return env


def context(env):
    status = {'NSpid': '30906 31202', 'Uid': '1 1 1 1', 'Gid': '1 1 1 1'}
    ns = {name: name + ':[' + str(4026532490 + i) + ']'
          for i, name in enumerate(('pid', 'mnt', 'net', 'user'))}
    rows = ((1, 63153, 1), (1, 63153, 1))
    outer = {'pid': 30906, 'start': 5892140}
    mounts = [{'id': 451, 'destination': '/proc', 'type': 'proc', 'device': '0:50'}]
    return env['diagnostic_context'](status, ns, rows, outer, mounts, {451})


class ReporterContext(unittest.TestCase):
    def test_exact_numeric_context_and_all_probe_failures(self):
        env = reporter()
        ctx = context(env)
        self.assertEqual(ctx[0], (30906, 31202))
        self.assertEqual(ctx[5], (30906, 5892140))
        self.assertEqual(ctx[7], (451, 0, 50))
        for index in range(7):
            for errno in (2, 5, 13):
                calls, closed = [], []
                def open_alias(path, flags):
                    calls.append(path)
                    if len(calls) - 1 == index:
                        raise OSError(errno, 'secret /path nonce')
                    raise PermissionError(13, 'secret')
                env['os'] = NS(O_PATH=1, O_CLOEXEC=2, open=open_alias, close=closed.append)
                env['time'] = NS(monotonic_ns=iter((100, 101) * 7).__next__)
                if errno == 13:
                    env['deny_outer_aliases'](30906, False, ctx)
                    self.assertEqual(len(calls), 7)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN') as caught:
                        env['deny_outer_aliases'](30906, False, ctx)
                    first = env['failure_context_line'](caught.exception)
                    second = env['failure_line'](caught.exception)
                    self.assertTrue(first.startswith('ZEN53_REPORT_CONTEXT version=1 '))
                    self.assertIn('nspid=30906,31202', first)
                    self.assertIn('outer=30906,5892140', first)
                    self.assertIn('proc=451,0,50', first)
                    self.assertIn('clock=100,101', first)
                    self.assertLessEqual(len(first), 512)
                    self.assertIn('cause_errno=' + str(errno), second)
                    self.assertNotIn('secret', first + second)
                    self.assertEqual(closed, [])

    def test_malformed_context_never_creates_pass(self):
        env = reporter()
        ctx = context(env)
        for bad in (None, (), ('secret',), ctx[:-1] + (('/secret',),)):
            error = RuntimeError('BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN')
            error._zen53_probe_context = (bad, 1, 2)
            self.assertEqual(env['failure_context_line'](error),
                             'ZEN53_REPORT_CONTEXT version=1 state=UNKNOWN\n')
        status = {'NSpid': 'secret', 'Uid': '1 1 1 1', 'Gid': '1 1 1 1'}
        self.assertIsNone(env['diagnostic_context'](status, {}, (), {}, [], set()))
        self.assertEqual(env['failure_context_line'](RuntimeError('BOOKKEEPING_OUTER_PROC_ACCESS')), '')

    def test_successful_alias_access_still_fails_and_closes(self):
        env = reporter(); closed = []
        env['os'] = NS(O_PATH=1, O_CLOEXEC=2, open=lambda *a: 71, close=closed.append)
        env['time'] = NS(monotonic_ns=lambda: 100)
        with self.assertRaisesRegex(RuntimeError, 'BOOKKEEPING_OUTER_PROC_ACCESS') as caught:
            env['deny_outer_aliases'](30906, False, context(env))
        self.assertEqual(closed, [71])
        self.assertEqual(env['failure_context_line'](caught.exception), '')

    def test_predicate_and_output_guard_unchanged(self):
        old = ast.parse((BASE / 'report.py').read_text())
        new = ast.parse(REPORT.read_text())
        def predicate(tree):
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'deny_outer_aliases')
            return next(ast.dump(n, include_attributes=False) for n in ast.walk(fn)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and
                        n.func.id == 'need' and len(n.args) == 2 and
                        isinstance(n.args[1], ast.Constant) and
                        n.args[1].value == 'BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN')
        self.assertEqual(predicate(old), predicate(new))
        launch = (HERE / 'candidate/launch.py').read_bytes()
        self.assertEqual(hashlib.sha256(launch).hexdigest(),
                         hashlib.sha256((BASE / 'launch.py').read_bytes()).hexdigest())
        env = reporter(); error = RuntimeError('BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN')
        error._zen53_probe_context = (context(env), 100, 101)
        block = (env['failure_context_line'](error) + env['failure_line'](error)).encode()
        self.assertLess(len(block), 4096)
        self.assertIn(b'ZEN53_REPORT_REQUIRED_OBSERVATION_FAILED', block)
        self.assertNotIn(b'secret', block)
        # The first context line is harmless; the next line remains rejected.
        from test_output_capture import capture
        for split in range(len(block) + 1):
            failure, records = capture([block[:split], block[split:]])
            self.assertEqual(str(failure), 'CODEX_STARTUP_ACCESS_OR_WRITE_FAILURE')
            self.assertIn(block, records[0])
        failure, records = capture([block[:1], block[1:] + b'x' * (4097 - len(block))])
        self.assertEqual(str(failure), 'CODEX_STARTUP_ACCESS_OR_WRITE_FAILURE')
        self.assertNotIn(block, records[0])
        failure, records = capture([b'x' * 4096] * 4 + [block])
        self.assertEqual(str(failure), 'native_OUTPUT_BOUND')
        self.assertEqual(records, [])

    def test_historical_metadata_preserved_and_route_explicitly_provisional(self):
        current = HERE / 'candidate'
        for name in ('PROPOSED_ARTIFACT_SHA256.json', 'pins.json', 'DEPENDENCIES.json',
                     'diagnostic.service', 'launch.py', 'EMPTY_BINDINGS.json', 'requirements.toml'):
            self.assertEqual((current / name).read_bytes(), (BASE / name).read_bytes())
        route = json.loads((HERE / 'NEW_REPORTER_SOURCE_ROUTE_PROPOSAL.json').read_text())
        self.assertEqual(route['status'], 'PROPOSAL_ONLY_NOT_STAGE_READY')
        self.assertEqual(route['target'], '/diagnostic/report.py')
        self.assertEqual(route['package_member'], 'candidate/report.py')
        self.assertEqual(route['sha256'], hashlib.sha256((current / 'report.py').read_bytes()).hexdigest())
        self.assertEqual(route['size'], (current / 'report.py').stat().st_size)
        self.assertEqual(route['historical_dependency_sha256'],
                         hashlib.sha256((BASE / 'report.py').read_bytes()).hexdigest())
        self.assertNotEqual(route['sha256'], route['historical_dependency_sha256'])
        self.assertTrue(route['acquisition_identity_required_at_preparation'])


class SupervisorSnapshot(unittest.TestCase):
    def make(self, alive=True, malformed=False):
        selected = ('pinned_outer_failure_snapshot',)
        identity = {'pid': 30906, 'start': 5892140, 'invocation': 'a' * 32,
                    'group': '/system.slice/zen53-mincompat.service'}
        io = NS(pidfd=11, procfd=12, identity=identity)
        rows = {'status': b'NSpid:\t30906 31202\nUid:\t1 1 1 1\nGid:\t1 1 1 1\n',
                'uid_map': b'1 63153 1\n', 'gid_map': b'1 63153 1\n'}
        if malformed:
            rows['status'] = b'NSpid:\tsecret\nUid:\t1 1 1 1\nGid:\t1 1 1 1\n'
        env = dict(time=NS(monotonic_ns=iter((100, 101)).__next__),
                   select=NS(select=lambda *a: ([11] if not alive else [], [], [])),
                   parse_proc_identity=lambda fd, inv, group: identity,
                   read_at=lambda fd, name, limit: rows[name],
                   os=NS(readlink=lambda path, dir_fd: path.split('/')[-1] + ':[4026532496]'))
        functions(SUPERVISOR, selected, env)
        return env, io

    def test_live_pinned_numeric_snapshot(self):
        env, io = self.make()
        record = env['pinned_outer_failure_snapshot'](io)
        self.assertEqual(record['state'], 'LIVE')
        self.assertTrue(record['identity_match'])
        self.assertEqual(record['nspid'], [30906, 31202])
        self.assertEqual(record['uid_map'], [1, 63153, 1])
        self.assertEqual(record['expected_start'], 5892140)

    def test_dead_missing_and_malformed_fail_closed(self):
        for alive, malformed, expected in ((False, False, 'DEAD'), (True, True, 'UNKNOWN')):
            env, io = self.make(alive, malformed)
            self.assertEqual(env['pinned_outer_failure_snapshot'](io)['state'], expected)
        env, _ = self.make()
        self.assertEqual(env['pinned_outer_failure_snapshot'](None)['state'], 'UNKNOWN')

    def test_death_or_identity_change_during_snapshot_is_not_live(self):
        env, io = self.make()
        calls = iter(([], [11]))
        env['select'] = NS(select=lambda *a: (next(calls), [], []))
        self.assertEqual(env['pinned_outer_failure_snapshot'](io)['state'], 'DEAD_AT_END')
        env, io = self.make()
        calls = 0
        def changed(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                return dict(io.identity, start=io.identity['start'] + 1)
            return io.identity
        env['parse_proc_identity'] = changed
        record = env['pinned_outer_failure_snapshot'](io)
        self.assertEqual(record['state'], 'MISMATCH')
        self.assertFalse(record['identity_match'])

    def test_existing_pin_and_no_new_pid_path(self):
        tree = ast.parse(SUPERVISOR.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and
                  n.name == 'pinned_outer_failure_snapshot')
        raw = ast.get_source_segment(SUPERVISOR.read_text(), fn)
        self.assertNotIn('/proc/', raw)
        self.assertIn('io.pidfd', raw)
        self.assertIn('io.procfd', raw)


if __name__ == '__main__':
    unittest.main()
