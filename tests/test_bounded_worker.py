import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker.codex import Codex, DENIED, DISABLED, check_config, _stderr_code
from ops.bounded_worker.task import Task, execute


class FakeWorker:
    def __init__(self, change=None, status='completed'):
        self.change = change
        self.status = status
        self.calls = 0

    def run(self, task, workspace, containment):
        containment.launch()
        self.calls += 1
        if self.change:
            (workspace / self.change).write_text('changed\n')
        return {'status': self.status, 'errors': [{'source': 'command', 'exit_code': 127,
                                                   'message': 'command failed'}],
                'usage': {'inputTokens': 12}}


class FakeContainment:
    """Contract fixture only; not a containment implementation."""
    def __init__(self, terminated=True, empty=True):
        self.terminated = terminated
        self.empty = empty
        self.events = []

    def launch(self, *args, **kwargs):
        self.events.append('launch')

    def terminate(self):
        self.events.append('terminate')
        return self.terminated

    def verify_empty(self):
        self.events.append('verify_empty')
        return self.empty


class BoundedWorkerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        os.chmod(root, 0o700)
        self.work = root / 'workspace'
        self.work.mkdir()
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.work, check=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE).stdout
        git('init', '-q')
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'user.name', 'Test')
        (self.work / 'allowed.txt').write_text('original\n')
        (self.work / 'other.txt').write_text('original\n')
        git('add', 'allowed.txt', 'other.txt')
        git('commit', '-qm', 'base')
        base = git('rev-parse', 'HEAD').decode().strip()
        self.task = Task('test-1', 'stevengalvis/modelfc', base, 'Make one edit', ('allowed.txt',))
        self.evidence = root / 'evidence'

    def tearDown(self):
        self.temp.cleanup()

    def test_success_and_reservation(self):
        worker = FakeWorker('allowed.txt')
        result = execute(self.task, self.work, self.evidence, worker, FakeContainment())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(worker.calls, 1)
        self.assertIn(b'+changed', (self.evidence / 'diff.patch').read_bytes())
        self.assertEqual(json.loads((self.evidence / 'result.json').read_text()), result)
        self.assertEqual(result['errors'][0]['exit_code'], 127)
        with self.assertRaises((FileExistsError, ValueError)):
            execute(self.task, self.work, self.evidence, worker, FakeContainment())
        self.assertEqual(worker.calls, 1)

    def test_spawn_failure_classification_excludes_raw_diagnostics(self):
        raw = b'failed to spawn secret-helper: no such file or directory TOKEN=private'
        self.assertEqual(_stderr_code(raw), 'spawn_failed')
        self.assertEqual(_stderr_code(b'unrelated private diagnostic'), 'stderr_observed')
        self.assertNotIn('secret', _stderr_code(raw))

    def test_directory_changes_are_out_of_scope(self):
        class DirectoryWorker(FakeWorker):
            def run(self, task, workspace, containment):
                containment.launch()
                for name in ('.aws', '.codex', '.agents'):
                    (workspace / name).mkdir()
                return {'status': 'completed'}
        result = execute(self.task, self.work, self.evidence, DirectoryWorker(), FakeContainment())
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('out_of_scope_change', result['errors'])
        self.assertTrue(result['cleanup_verified'])
        self.assertFalse((self.evidence / 'diff.patch').exists())

    def test_credential_paths_cannot_be_allowed(self):
        for path in ('.aws/config', '.codex/auth.json', 'nested/.aws/config'):
            task = Task('test', self.task.repository, self.task.base_revision, 'edit', (path,))
            with self.assertRaisesRegex(ValueError, 'invalid_scope'):
                task.validate()

    def test_diagnostics_bounded_and_write_failure_cleans_up(self):
        class DiagnosticWorker:
            def run(self, task, workspace, containment):
                containment.launch()
                for _ in range(130):
                    containment.record('spawn_failed')
                return {'status': 'failed'}
        backend = FakeContainment()
        result = execute(self.task, self.work, self.evidence, DiagnosticWorker(), backend)
        data = json.loads((self.evidence / 'lifecycle.json').read_text())
        self.assertEqual(data, {'events': ['spawn_failed'] * 128, 'truncated': True,
                                'terminal': None, 'observed': ['spawn_failed'], 'tools': []})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])

        backend = FakeContainment()
        from ops.bounded_worker import task as task_module
        write = task_module._durable_write
        def interrupted(path, data):
            if path.name == 'lifecycle.json':
                raise OSError('secret must not leak')
            return write(path, data)
        with patch.object(task_module, '_durable_write', side_effect=interrupted):
            result = execute(self.task, self.work, self.evidence.parent / 'interrupted', DiagnosticWorker(), backend)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])
        self.assertNotIn('secret', str(result))
        self.assertFalse((self.evidence.parent / 'interrupted' / 'diff.patch').exists())

    def test_fake_app_server_stream_retains_terminal_and_tool_summary(self):
        # A subprocess speaks only the offline app-server fixture protocol.
        script = r'''
import json, sys
mode = sys.argv[1]
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if 'id' not in req:
        continue
    method = req['method']
    results = {
        'initialize': {}, 'config/read': {'config': {}},
        'permissionProfile/list': {'data': [{'id': 'pilot', 'allowed': True}]},
        'thread/start': {'thread': {'id': 'thread'}, 'model': 'gpt-6.1-terra',
            'modelProvider': 'openai', 'approvalPolicy': 'never', 'serviceTier': 'default',
            'activePermissionProfile': {'id': 'pilot'}, 'cwd': req.get('params', {}).get('cwd')},
        'turn/start': {'turn': {'id': 'turn'}},
    }
    emit({'id': req['id'], 'result': results[method]})
    if method == 'turn/start':
        emit({'method': 'turn/started', 'params': {'turn': {'id': 'turn'}}})
        for _ in range(130):
            emit({'method': 'item/started', 'params': {'item': {'type': 'commandExecution',
                 'command': 'PRIVATE_ARGUMENT'}}})
        for kind, status in [('fileChange', 'failed'), ('fileChange', 'declined'),
                             ('mcpToolCall', {'unexpected': 'PRIVATE_STATUS'}),
                             ('dynamicToolCall', 'completed'), ('commandExecution', 'mystery')]:
            emit({'method': 'item/completed', 'params': {'item': {'type': kind, 'status': status,
                 'aggregatedOutput': 'PRIVATE_OUTPUT', 'path': 'PRIVATE_PATH'}}})
        sys.stderr.write('failed to spawn PRIVATE_HELPER: no such file or directory\n')
        sys.stderr.flush()
        emit({'method': 'turn/completed', 'params': {'turn': {
             'id': 'wrong' if mode == 'mismatch' else 'turn',
             'status': ({'private': 'PRIVATE_STATUS'} if mode == 'unknown' else
                        'failed' if mode == 'failed' else 'completed'),
             'error': {'message': 'PRIVATE_MODEL_TEXT'}}}})
'''
        adapter = Codex.__new__(Codex)
        adapter.account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir='/home/fake')
        adapter.executable = Path('/home/fake/codex-bin')
        adapter.sha256 = '0' * 64

        class ProcessFixture(FakeContainment):
            def __init__(self, mode):
                super().__init__()
                self.mode = mode
            def launch(self, _cmd, **kwargs):
                self.events.append('launch')
                for key in ('user', 'group', 'extra_groups'):
                    kwargs.pop(key)
                self.child = subprocess.Popen([sys.executable, '-u', '-c', script, self.mode], **kwargs)
                return self.child
            def terminate(self):
                self.events.append('terminate')
                self.child.kill()
                self.child.wait(timeout=5)
                return True
            def verify_empty(self):
                self.events.append('verify_empty')
                return self.child.poll() is not None

        from ops.bounded_worker import task as task_module
        write = task_module._durable_write
        for mode in ('completed', 'failed', 'unknown', 'mismatch', 'write_failure'):
            with self.subTest(mode=mode):
                backend = ProcessFixture(mode)
                evidence = self.evidence.parent / mode
                def fail_terminal(path, data):
                    if mode == 'write_failure' and path.name == 'lifecycle.json' and b'"terminal": "turn_completed"' in data:
                        raise OSError('PRIVATE_WRITE_FAILURE')
                    return write(path, data)
                class AdapterOS:
                    getuid = staticmethod(lambda: 0)
                    geteuid = staticmethod(lambda: 0)
                    def __getattr__(self, name):
                        return getattr(os, name)
                adapter_os = AdapterOS()
                with patch('ops.bounded_worker.codex.os', adapter_os), \
                     patch('ops.bounded_worker.codex.check_config', return_value=True), \
                     patch.object(adapter, '_binary', return_value=('pinned',)), \
                     patch.object(task_module, '_durable_write', side_effect=fail_terminal):
                    task = Task(self.task.task_id, self.task.repository, self.task.base_revision,
                                'PRIVATE_PROMPT', self.task.allowed_paths)
                    result = execute(task, self.work, evidence, adapter, backend)
                self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])
                self.assertTrue(result['cleanup_verified'])
                raw = (evidence / 'lifecycle.json').read_bytes()
                data = json.loads(raw)
                self.assertLess(len(raw), 16384)
                self.assertEqual(len(data['events']), 128)
                self.assertTrue(data['truncated'])
                self.assertIn('tool_completed', data['observed'])
                self.assertIn(['file_change', 'failed'], data['tools'])
                self.assertIn(['file_change', 'denied'], data['tools'])
                self.assertIn(['mcp', 'unknown'], data['tools'])
                self.assertIn(['command', 'unknown'], data['tools'])
                self.assertIn(['dynamic', 'completed'], data['tools'])
                self.assertNotIn(b'PRIVATE_', raw)
                self.assertNotIn('PRIVATE_', json.dumps(result))
                for path in evidence.iterdir():
                    self.assertNotIn(b'PRIVATE_', path.read_bytes())
                self.assertEqual(data['terminal'], {'completed': 'turn_completed', 'unknown': 'turn_unknown',
                                                    'failed': 'turn_failed', 'mismatch': None,
                                                    'write_failure': None}[mode])
                self.assertEqual(result['status'], {'completed': 'completed', 'unknown': 'failed',
                                                    'failed': 'failed', 'mismatch': 'failed',
                                                    'write_failure': 'uncertain'}[mode])
                if mode != 'completed':
                    self.assertFalse((evidence / 'diff.patch').exists())

    def test_out_of_scope(self):
        result = execute(self.task, self.work, self.evidence, FakeWorker('other.txt'), FakeContainment())
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('out_of_scope_change', result['errors'])
        self.assertFalse((self.evidence / 'diff.patch').exists())

    def test_failure_and_timeout_retain_evidence(self):
        for status in ('failed', 'timeout'):
            with self.subTest(status=status):
                result = execute(self.task, self.work, self.evidence.parent / status, FakeWorker(status=status), FakeContainment())
                self.assertEqual(result['status'], status)
                self.assertEqual(result['errors'][0]['message'], 'command failed')
                self.assertEqual(json.loads((self.evidence.parent / status / 'result.json').read_text()), result)

    def test_invalid_task_never_launches(self):
        worker = FakeWorker()
        bad = Task('bad', 'stevengalvis/modelfc', self.task.base_revision, 'edit', ('../escape',))
        with self.assertRaises(ValueError):
            execute(bad, self.work, self.evidence, worker, FakeContainment())
        self.assertEqual(worker.calls, 0)

    def test_codex_deadline_stops_fake_process(self):
        adapter = Codex.__new__(Codex)
        adapter.account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir='/home/fake')
        adapter.executable = Path('/home/fake/codex-bin')
        adapter.sha256 = '0' * 64
        class ProcessFixture:
            def record(self, event):
                pass
            def diagnostic(self, stage, reason):
                pass
            def launch(self, _cmd, **kwargs):
                for key in ('user', 'group', 'extra_groups'):
                    kwargs.pop(key)
                self.child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
                return self.child
            def terminate(self):
                self.child.kill()
                self.child.wait(timeout=5)
                return True
            def verify_empty(self):
                return self.child.poll() is not None
        backend = ProcessFixture()
        short = Task('deadline', self.task.repository, self.task.base_revision,
                     'edit', self.task.allowed_paths, timeout_seconds=1)
        with patch('ops.bounded_worker.codex.os.getuid', return_value=0), \
             patch('ops.bounded_worker.codex.os.geteuid', return_value=0), \
             patch.object(adapter, '_binary', return_value=('pinned',)):
            result = adapter.run(short, self.work, backend)
            backend.terminate()
        self.assertEqual(result['status'], 'timeout')
        self.assertIn('deadline exceeded', str(result['errors']))

    def test_uncertain_launch_is_not_retried(self):
        class BrokenWorker:
            calls = 0
            def run(self, task, workspace, containment):
                containment.launch()
                self.calls += 1
                raise RuntimeError('lost outcome')
        worker = BrokenWorker()
        result = execute(self.task, self.work, self.evidence, worker, FakeContainment())
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(json.loads((self.evidence / 'reservation.json').read_text())['status'], 'uncertain')
        with self.assertRaises(FileExistsError):
            execute(self.task, self.work, self.evidence, worker, FakeContainment())
        self.assertEqual(worker.calls, 1)

    def test_attempt_binding_failure_blocks_before_worker(self):
        class WrongAttempt(FakeContainment):
            def bind_attempt(self, evidence):
                raise ValueError('mismatch')
        worker = FakeWorker('allowed.txt')
        result = execute(self.task, self.work, self.evidence, worker, WrongAttempt())
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['errors'], ['containment_attempt_binding_failed'])
        self.assertEqual(worker.calls, 0)
        self.assertTrue((self.evidence / 'reservation.json').exists())
        self.assertFalse((self.evidence / 'diff.patch').exists())

    def test_unavailable_backend_never_calls_worker(self):
        worker = FakeWorker('allowed.txt')
        result = execute(self.task, self.work, self.evidence, worker)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['errors'], ['containment_unavailable'])
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(worker.calls, 0)
        self.assertTrue((self.evidence / 'reservation.json').exists())

    def test_cleanup_must_be_verified_before_acceptance(self):
        for termination, empty in ((False, True), (True, False), (True, None)):
            with self.subTest(termination=termination, empty=empty):
                backend = FakeContainment(termination, empty)
                worker = FakeWorker('allowed.txt')
                evidence = self.evidence.parent / f'blocked-{termination}-{empty}'
                result = execute(self.task, self.work, evidence, worker, backend)
                self.assertEqual(result['status'], 'blocked')
                self.assertFalse(result['cleanup_verified'])
                self.assertEqual(result['errors'][0]['exit_code'], 127)
                self.assertIn('containment_cleanup_unverified', result['errors'])
                self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])
                self.assertFalse((evidence / 'diff.patch').exists())
                self.assertTrue(self.work.exists())
                (self.work / 'allowed.txt').write_text('original\n')

    def test_cleanup_exception_blocks(self):
        class BrokenContainment(FakeContainment):
            def verify_empty(self):
                self.events.append('verify_empty')
                raise OSError('unavailable')
        backend = BrokenContainment()
        result = execute(self.task, self.work, self.evidence, FakeWorker('allowed.txt'), backend)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('containment_cleanup_unverified', result['errors'])
        self.assertFalse((self.evidence / 'diff.patch').exists())
        self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])

    def test_accept_only_after_verified_empty(self):
        backend = FakeContainment()
        result = execute(self.task, self.work, self.evidence, FakeWorker('allowed.txt'), backend)
        self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['status'], 'completed')
        self.assertTrue((self.evidence / 'diff.patch').exists())

    def test_failed_worker_still_requires_cleanup(self):
        for state in ('failed', 'timeout'):
            with self.subTest(state=state):
                backend = FakeContainment(empty=None)
                result = execute(self.task, self.work, self.evidence.parent / state,
                                 FakeWorker(status=state), backend)
                self.assertEqual(result['status'], 'blocked')
                self.assertFalse(result['cleanup_verified'])
                self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])

    def test_executable_requires_protected_file_and_ancestors(self):
        install = self.evidence.parent / 'protected-install'
        install.mkdir(mode=0o755)
        executable = install / 'codex'
        executable.write_bytes(b'known executable bytes')
        executable.chmod(0o755)
        adapter = Codex.__new__(Codex)
        adapter.executable = executable
        adapter.sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()

        # Model a root-owned installation on CI hosts that run tests as non-root.
        # /tmp is modeled as protected so only the fixture directory's mode varies.
        actual_lstat, actual_fstat = os.lstat, os.fstat
        class ProtectedMetadata:
            def __init__(self, original, protect_tmp=False):
                self.original = original
                self.st_uid = 0
                self.st_mode = original.st_mode & ~0o022 if protect_tmp else original.st_mode
            def __getattr__(self, name):
                return getattr(self.original, name)
        def protected_lstat(path, *args, **kwargs):
            return ProtectedMetadata(actual_lstat(path, *args, **kwargs), str(path) == '/tmp')
        def protected_fstat(fd):
            return ProtectedMetadata(actual_fstat(fd))
        with patch('ops.bounded_worker.codex.os.lstat', side_effect=protected_lstat), \
             patch('ops.bounded_worker.codex.os.fstat', side_effect=protected_fstat):
            self.assertEqual(adapter._binary()[-1], adapter.sha256)
            # A writer with directory access could substitute a different inode
            # and restore it after launch; reject even while bytes still match.
            install.chmod(0o777)
            with self.assertRaisesRegex(ValueError, 'unprotected_worker_executable'):
                adapter._binary()
            install.chmod(0o755)
            # An open descriptor would not prevent a writable file being changed
            # in place, so reject that independently of its matching digest.
            executable.chmod(0o777)
            with self.assertRaisesRegex(ValueError, 'unprotected_worker_executable'):
                adapter._binary()
            executable.chmod(0o755)
            self.assertEqual(adapter._binary()[-1], adapter.sha256)
            # A worker-owned 0755 file is still writable in place by its owner.
            def worker_owned_lstat(path, *args, **kwargs):
                value = protected_lstat(path, *args, **kwargs)
                if str(path) == str(executable):
                    value.st_uid = 1001
                return value
            with patch('ops.bounded_worker.codex.os.lstat', side_effect=worker_owned_lstat):
                with self.assertRaisesRegex(ValueError, 'unprotected_worker_executable'):
                    adapter._binary()

    def test_executable_rejects_symlink_in_path(self):
        install = self.evidence.parent / 'install'
        install.mkdir()
        executable = install / 'codex'
        executable.write_bytes(b'known')
        executable.chmod(0o755)
        link = self.evidence.parent / 'link'
        link.symlink_to(install, target_is_directory=True)
        adapter = Codex.__new__(Codex)
        adapter.executable = link / 'codex'
        adapter.sha256 = hashlib.sha256(b'known').hexdigest()
        actual_lstat = os.lstat
        class ProtectedMetadata:
            def __init__(self, original, protect_tmp=False):
                self.original = original
                self.st_uid = 0
                self.st_mode = original.st_mode & ~0o022 if protect_tmp else original.st_mode
            def __getattr__(self, name):
                return getattr(self.original, name)
        def protected_lstat(path, *args, **kwargs):
            return ProtectedMetadata(actual_lstat(path, *args, **kwargs), str(path) == '/tmp')
        with patch('ops.bounded_worker.codex.os.lstat', side_effect=protected_lstat):
            with self.assertRaisesRegex(ValueError, 'unprotected_worker_executable'):
                adapter._binary()

    def test_configuration_rejects_extra_access(self):
        work, binary, home = Path('/tmp/work'), Path('/home/worker/codex-bin'), '/home/worker'
        fs = {':minimal': 'read', str(work): 'write', str(binary): 'read', home + '/.codex': 'deny',
              **dict.fromkeys(DENIED, 'deny'), 'glob_scan_max_depth': None}
        config = {'permissions': {'pilot': {'filesystem': fs, 'network': {'enabled': False}}},
                  'default_permissions': 'pilot', 'features': {**dict.fromkeys(DISABLED, False),
                  'code_mode_host': True}, 'web_search': 'disabled', 'mcp_servers': {},
                  'shell_environment_policy': {'inherit': 'none', 'set': {'PATH': '/usr/bin:/bin'}}}
        self.assertTrue(check_config(config, work, binary, home))
        fs['/extra'] = 'write'
        self.assertFalse(check_config(config, work, binary, home))


if __name__ == '__main__':
    unittest.main()
