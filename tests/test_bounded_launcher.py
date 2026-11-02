from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker import launcher
from ops.bounded_worker.task import Task


class Worker:
    calls = 0
    gate = None

    def __init__(self, *_):
        self.account = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())

    def run(self, task, workspace, containment):
        containment.launch()
        type(self).calls += 1
        if self.gate:
            self.gate.wait(5)
        (workspace / 'file.txt').write_text('changed\n')
        return {'status': 'completed', 'errors': [], 'usage': None}


class Backend:
    def __init__(self, attempt, uid, gid):
        self.attempt = attempt
        self.unit = 'modelfc-bounded-' + 'a' * 32 + '.service'

    def bind_attempt(self, evidence):
        assert evidence == self.attempt

    def launch(self):
        path = self.attempt / 'containment.json'
        path.write_text(json.dumps({'backend': 'systemd-v1', 'unit': self.unit}))
        path.chmod(0o600)

    def terminate(self):
        return True

    def verify_empty(self):
        return True


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.work = root / 'work'
        self.evidence = root / 'evidence'
        self.work.mkdir(mode=0o700)
        self.evidence.mkdir(mode=0o700)
        self.checkout = self.work / 'one'
        self.checkout.mkdir(mode=0o700)
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.checkout, stderr=subprocess.DEVNULL)
        git('init', '-q')
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'user.name', 'Test')
        (self.checkout / 'file.txt').write_text('before\n')
        git('add', 'file.txt')
        git('commit', '-qm', 'base')
        self.task = Task('one', 'stevengalvis/modelfc', git('rev-parse', 'HEAD').decode().strip(), 'edit', ('file.txt',))
        self.config = launcher.Configuration(str(self.work), str(self.evidence), 'unused', '/unused', '0' * 64)
        roots = patch.object(launcher, '_roots', return_value=(self.work, self.evidence))
        roots.start()
        self.addCleanup(roots.stop)
        # Isolate the execute/state tests from real root/account requirements.
        directory = patch.object(launcher, '_directory', side_effect=lambda path, *_args, **_kw: Path(path))
        directory.start()
        self.addCleanup(directory.stop)
        Worker.calls = 0
        Worker.gate = None

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self):
        return launcher.submit(self.task, self.config, _worker_factory=Worker, _backend_factory=Backend)

    def inspect(self):
        return launcher.inspect('one', self.config, _worker_factory=Worker)

    def test_complete_and_duplicate(self):
        self.assertEqual(self.submit()['status'], 'completed')
        self.assertEqual(self.inspect()['status'], 'completed')
        self.assertEqual(self.submit()['status'], 'blocked')
        self.assertEqual(Worker.calls, 1)

    def test_opt_in_prepares_before_worker_and_publishes(self):
        self.config = replace(self.config, prepared_baseline=True)
        self.assertEqual(self.submit()['status'], 'completed')
        for name in ('.agents', '.aws', '.codex'):
            self.assertTrue((self.checkout / name).is_dir())
        baseline = json.loads((self.evidence / 'one' / 'attempt' / 'baseline.json').read_text())
        self.assertEqual(baseline['status'], 'verified')
        self.assertEqual(set(baseline['directories']), {'.agents', '.aws', '.codex'})

    def test_concurrent_submission(self):
        Worker.gate = threading.Event()
        result = []
        thread = threading.Thread(target=lambda: result.append(self.submit()))
        thread.start()
        # Wait until the first reservation is visible; even before launch it is exclusive.
        for _ in range(500):
            if (self.evidence / 'one').exists():
                break
            threading.Event().wait(.01)
        self.assertEqual(self.submit()['status'], 'blocked')
        Worker.gate.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['status'], 'completed')
        self.assertEqual(Worker.calls, 1)

    def test_crash_after_reservation_or_launch_record_retains_attempt(self):
        original = launcher._durable_write
        for fail_call in (1, 2, 3):
            with self.subTest(fail_call=fail_call):
                # Distinct identities allow independent interrupted attempts.
                task_id = 'case' + str(fail_call)
                os.rename(self.checkout, self.work / task_id)
                self.checkout = self.work / task_id
                from dataclasses import replace
                self.task = replace(self.task, task_id=task_id)
                count = [0]
                def crash(path, data):
                    count[0] += 1
                    if count[0] == fail_call:
                        raise OSError('simulated crash')
                    return original(path, data)
                with patch.object(launcher, '_durable_write', side_effect=crash):
                    try:
                        self.submit()
                    except OSError:
                        pass
                self.assertEqual(launcher.inspect(task_id, self.config, _worker_factory=Worker)['status'], 'blocked')
                self.assertEqual(self.submit()['status'], 'blocked')
                self.assertTrue(self.checkout.exists())
                self.assertTrue((self.evidence / task_id).exists())
                # Restore checkout contents for the next independent case.
                subprocess.run(['git', 'checkout', '--', 'file.txt'], cwd=self.checkout, check=True)

    def test_missing_or_malformed_records_block(self):
        self.assertEqual(self.submit()['status'], 'completed')
        directory = self.evidence / 'one'
        state = directory / 'state.json'
        saved = state.read_bytes()
        for raw in (b'{', b'{}', b'{"phase":"finished","phase":"finished"}'):
            state.write_bytes(raw)
            self.assertEqual(self.inspect()['status'], 'blocked')
        state.write_bytes(saved)
        unit = directory / 'attempt' / 'containment.json'
        unit.write_text('{"backend":"systemd-v1","unit":"modelfc-bounded-' + 'b' * 32 + '.service"}')
        self.assertEqual(self.inspect()['status'], 'blocked')
        unit.unlink()
        self.assertEqual(self.inspect()['status'], 'blocked')
        self.assertEqual(self.submit()['status'], 'blocked')
        self.assertTrue(self.checkout.exists())

    def test_failure_before_reservation_never_launches(self):
        with patch.object(Path, 'mkdir', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.submit()
        self.assertEqual(Worker.calls, 0)
        self.assertEqual(self.inspect()['status'], 'blocked')

    def test_interrupted_output_and_mismatched_attempt_are_not_accepted(self):
        self.assertEqual(self.submit()['status'], 'completed')
        directory = self.evidence / 'one'
        state = directory / 'state.json'
        record = json.loads(state.read_text())
        record['phase'] = 'launching'
        state.write_text(json.dumps(record))
        self.assertEqual(self.inspect()['status'], 'blocked')
        record['phase'] = 'finished'
        state.write_text(json.dumps(record))
        reservation = directory / 'attempt' / 'reservation.json'
        data = json.loads(reservation.read_text())
        data['base_revision'] = 'f' * 40
        reservation.write_text(json.dumps(data))
        self.assertEqual(self.inspect()['status'], 'blocked')
        self.assertTrue((directory / 'attempt' / 'diff.patch').exists())
        self.assertTrue(self.checkout.exists())

    def test_result_missing_or_partial_blocks(self):
        self.assertEqual(self.submit()['status'], 'completed')
        result = self.evidence / 'one' / 'attempt' / 'result.json'
        result.write_text('{')
        self.assertEqual(self.inspect()['status'], 'blocked')
        result.unlink()
        self.assertEqual(self.inspect()['status'], 'blocked')
        self.assertEqual(self.submit()['status'], 'blocked')

    def test_ambiguous_containment_blocks_and_preserves_evidence(self):
        class UncertainBackend(Backend):
            def verify_empty(self):
                return False
        result = launcher.submit(self.task, self.config, _worker_factory=Worker,
                                 _backend_factory=UncertainBackend)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.inspect()['status'], 'blocked')
        self.assertEqual(self.submit()['status'], 'blocked')
        self.assertTrue((self.evidence / 'one' / 'attempt' / 'containment.json').exists())
        self.assertTrue(self.checkout.exists())
        self.assertFalse((self.evidence / 'one' / 'attempt' / 'diff.patch').exists())

    def test_trusted_limits_apply_before_reservation(self):
        from dataclasses import replace
        config = replace(self.config, timeout_seconds=1)
        with self.assertRaises(ValueError):
            launcher.submit(self.task, config, _worker_factory=Worker, _backend_factory=Backend)
        self.assertFalse((self.evidence / 'one').exists())
        self.assertEqual(Worker.calls, 0)
