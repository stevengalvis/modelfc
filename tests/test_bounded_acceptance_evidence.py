"""Offline acceptance-evidence tests; never invoke systemd."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.test_bounded_systemd_acceptance import run_acceptance


class FakeBackend:
    stop = True
    empty = True
    fail = False

    def __init__(self, attempt, uid, gid):
        self.unit = 'fixture.service'
        self.observations = [{'kind': 'systemd_show', 'value': {'ActiveState': 'inactive'}},
                             {'kind': 'cgroup_events', 'value': 'populated 0\n'}]

    def bind_attempt(self, attempt):
        pass

    def launch(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError('fixture')
        return type('Child', (), {'stdin': io.BytesIO(), 'stdout': io.BytesIO(b'123\n'),
                                  'stderr': io.BytesIO()})()

    def terminate(self):
        return self.stop

    def verify_empty(self):
        return self.empty


class EvidenceTest(unittest.TestCase):
    def check_case(self, *, stop=True, empty=True, fail=False, post=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            FakeBackend.stop, FakeBackend.empty, FakeBackend.fail = stop, empty, fail
            identities = [{'state': 'S', 'starttime_ticks': 42}, post or {'state': 'absent'}]
            with patch('tests.test_bounded_systemd_acceptance.subprocess.run',
                       return_value=subprocess.CompletedProcess([], 0, 'a' * 40)), \
                 patch('tests.test_bounded_systemd_acceptance.select.select', return_value=([1], [], [])), \
                 patch('tests.test_bounded_systemd_acceptance.proc_identity', side_effect=identities):
                if fail or not stop or not empty or post:
                    with self.assertRaises((AssertionError, RuntimeError)):
                        run_acceptance(root, 1003, 1003, FakeBackend)
                else:
                    run_acceptance(root, 1003, 1003, FakeBackend)
            path = root / 'attempt' / 'acceptance.json'
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            data = json.loads(path.read_text())
            self.assertEqual(data['revision'], 'a' * 40)
            self.assertEqual(data['unit'], 'fixture.service')
            self.assertTrue(data['observations'])
            return data

    def test_success_retains_pid_identity_and_readback(self):
        data = self.check_case()
        self.assertEqual(data['result'], 'passed')
        self.assertEqual(data['readiness_pid'], 123)
        self.assertEqual(data['readiness_identity']['starttime_ticks'], 42)
        self.assertEqual(data['post_stop_identity']['state'], 'absent')

    def test_failure_retains_evidence(self):
        self.assertEqual(self.check_case(fail=True)['result'], 'failed')
        self.assertEqual(self.check_case(stop=False)['stop'], False)

    def test_unknown_readback_blocks_and_preserves_workspace(self):
        self.assertEqual(self.check_case(empty=False)['result'], 'failed')
        self.assertEqual(self.check_case(post={'state': 'unknown'})['result'], 'failed')
        self.assertEqual(self.check_case(post={'state': 'Z', 'starttime_ticks': 43})['result'], 'failed')
