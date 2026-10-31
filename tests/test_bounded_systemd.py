"""Deterministic fake-systemd tests. These do not establish real containment."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ops.bounded_worker.systemd_backend import SystemdContainment


class Process:
    def __init__(self):
        self.returncode = None
        self.wait_error = None

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        if self.wait_error:
            raise self.wait_error
        self.returncode = 0
        return 0


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.evidence = self.root / 'attempt'
        self.evidence.mkdir()
        (self.evidence / 'reservation.json').write_text('{}')
        self.cg = self.root / 'cgroup'
        self.cg.mkdir()
        (self.cg / 'cgroup.controllers').write_text('cpu memory pids\n')
        self.backend = SystemdContainment(self.evidence, 1234, 1234)
        self.backend.bind_attempt(self.evidence)
        self.process = Process()
        self.fields = {'LoadState': 'not-found'}
        self.stop_error = None
        self.commands = []
        for obj in (patch('ops.bounded_worker.systemd_backend._CGROUP', self.cg),
                    patch('ops.bounded_worker.systemd_backend.os.getuid', return_value=0),
                    patch('ops.bounded_worker.systemd_backend.os.geteuid', return_value=0),
                    patch.object(self.backend, '_show', side_effect=lambda: self.fields),
                    patch('ops.bounded_worker.systemd_backend.subprocess.Popen', side_effect=self.popen),
                    patch('ops.bounded_worker.systemd_backend.subprocess.run', side_effect=self.run_command)):
            obj.start()
            self.addCleanup(obj.stop)

    def popen(self, argv, **kwargs):
        self.commands.append(argv)
        self.fields = {'LoadState': 'loaded', 'Transient': 'yes', 'ActiveState': 'active',
                       'ControlGroup': '/system.slice/' + self.backend.unit}
        return self.process

    def run_command(self, argv, **kwargs):
        self.commands.append(argv)
        if self.stop_error:
            raise self.stop_error
        self.fields = {'LoadState': 'loaded', 'Transient': 'yes', 'ActiveState': 'inactive',
                       'MainPID': '0', 'ControlPID': '0', 'Job': '', 'ControlGroup': ''}
        return subprocess.CompletedProcess(argv, 0)

    def launch(self):
        return self.backend.launch(['/usr/bin/worker', 'app-server'], cwd='/work',
                                   env={'HOME': '/home/worker', 'PATH': '/usr/bin:/bin'},
                                   user=1234, group=1234, extra_groups=[], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0)

    def test_verified_cleanup_and_contained_arguments(self):
        self.assertIs(self.launch(), self.process)
        record = json.loads((self.evidence / 'containment.json').read_text())
        self.assertEqual(record['unit'], self.backend.unit)
        command = self.commands[0]
        for option in ('--pipe', '--wait', '--property=KillMode=control-group',
                       '--property=RuntimeMaxSec=180', '--property=Delegate=no',
                       '--clear-groups', '--no-new-privs'):
            self.assertIn(option, command)
        self.assertTrue(self.backend.terminate())
        self.assertTrue(self.backend.verify_empty())

    def test_launch_failure_is_uncertain(self):
        with patch('ops.bounded_worker.systemd_backend.subprocess.Popen', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                self.launch()
        self.assertTrue((self.evidence / 'containment.json').exists())
        self.assertFalse(self.backend.terminate())
        self.assertFalse(self.backend.verify_empty())

    def test_start_timeout_is_uncertain_even_if_stop_succeeds(self):
        with patch.object(self.backend, '_show', side_effect=[{'LoadState': 'not-found'},
                          {'LoadState': 'not-found'}]), \
             patch('ops.bounded_worker.systemd_backend.time.monotonic', side_effect=[0, 0, 11]), \
             patch('ops.bounded_worker.systemd_backend.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                self.launch()
        self.assertFalse(self.backend.terminate())
        self.assertFalse(self.backend.verify_empty())

    def test_termination_failure_and_timeout(self):
        for error in (subprocess.CalledProcessError(1, 'stop'), subprocess.TimeoutExpired('stop', 10)):
            with self.subTest(error=error):
                self.backend.stopped = False
                self.backend.attempted = self.backend.started = self.backend.may_have_started = True
                self.stop_error = error
                self.assertFalse(self.backend.terminate())
                self.assertFalse(self.backend.verify_empty())

    def test_nonempty_and_unknown_cgroup_fail_closed(self):
        self.launch()
        self.assertTrue(self.backend.terminate())
        group = self.cg / 'system.slice' / self.backend.unit
        group.mkdir(parents=True)
        events = group / 'cgroup.events'
        for data in ('populated 1\nfrozen 0\n', 'frozen 0\n', 'populated 0\npopulated 1\n', 'invalid'):
            events.write_text(data)
            self.assertFalse(self.backend.verify_empty())
        events.write_text('populated 0\nfrozen 0\n')
        self.assertTrue(self.backend.verify_empty())
        events.unlink()
        self.assertFalse(self.backend.verify_empty())
        self.fields['Job'] = '123'
        self.assertFalse(self.backend.verify_empty())

    def test_wrong_identity_or_unit_collision_never_launches(self):
        self.fields = {'LoadState': 'loaded'}
        with self.assertRaisesRegex(ValueError, 'unit_name_unavailable'):
            self.launch()
        self.assertFalse(self.backend.terminate())
        self.assertEqual(self.commands, [])

    def test_collected_unit_requires_absent_cgroup(self):
        self.launch()
        self.assertTrue(self.backend.terminate())
        self.fields = {'LoadState': 'not-found', 'ActiveState': 'inactive', 'Job': ''}
        self.assertTrue(self.backend.verify_empty())
        group = self.cg / 'system.slice' / self.backend.unit
        group.mkdir(parents=True)
        self.assertFalse(self.backend.verify_empty())

    def test_mismatched_reservation_and_environment_rejected(self):
        other = SystemdContainment(self.evidence, 1234, 1234)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            other.bind_attempt(self.root / 'other')
        with self.assertRaisesRegex(ValueError, 'environment'):
            self.backend.launch(['/usr/bin/worker'], cwd='/work', env={'LD_PRELOAD': '/tmp/evil'},
                                user=1234, group=1234, extra_groups=[], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, bufsize=0)
        self.assertEqual(self.commands, [])

    def test_launcher_wait_timeout_blocks_cleanup(self):
        self.launch()
        self.process.wait_error = subprocess.TimeoutExpired('launcher', 5)
        self.assertFalse(self.backend.terminate())
        self.assertFalse(self.backend.verify_empty())

    def test_systemd_readback_error_is_unknown(self):
        self.launch()
        self.assertTrue(self.backend.terminate())
        with patch.object(self.backend, '_show', side_effect=subprocess.TimeoutExpired('show', 5)):
            self.assertFalse(self.backend.verify_empty())


if __name__ == '__main__':
    unittest.main()
