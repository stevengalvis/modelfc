import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker.codex import Codex, DENIED, DISABLED, check_config
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
