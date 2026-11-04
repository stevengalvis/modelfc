"""Offline tests for an unwired no-model Codex sandbox candidate."""
import errno
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import codex_sandbox_fixture_candidate as candidate
from ops.bounded_worker.codex import DENIED, DISABLED


class CandidateTests(unittest.TestCase):
    @staticmethod
    def supervisor(mode):
        return SimpleNamespace(unit='unit', uid=1003, mode=mode, observe=lambda _: (1234, 42, 10, 11))

    def test_command_is_fixed_and_uses_adapter_policy(self):
        args = candidate.command('/opt/codex', '/fixture/work', '/fixture/home', 'normal')
        self.assertNotIn('--include-managed-config', args)
        self.assertIn('permissions.pilot.network.enabled=false', args)
        self.assertIn('shell_environment_policy.inherit="none"', args)
        for flag in DISABLED:
            self.assertIn(flag, args)
        profile = next(v for v in args if v.startswith('permissions.pilot.filesystem'))
        for path in (*DENIED, '/fixture/home/.codex', '/fixture/work', '/opt/codex'):
            self.assertIn('"' + path + '"', profile)
        self.assertEqual(args[-2:], [candidate.PROBE_ARG, 'normal'])
        self.assertTrue(all('%' not in value and not any(ord(c) < 32 for c in value) for value in args))
        self.assertNotIn('app-server', args)
        self.assertNotIn('turn/start', args)
        compile(candidate.PROBE, '<fixture>', 'exec')

    def test_bad_paths_or_modes_block(self):
        for executable, work, home, mode in [('/opt/codex', '/fixture/work', '/fixture/home', 'bad'),
                ('relative', '/fixture/work', '/fixture/home', 'normal'),
                ('/opt/codex', '/root', '/fixture/home', 'normal'),
                ('/opt/codex', '/root/secret', '/fixture/home', 'normal'),
                ('/opt/codex', '/fixture/work/../other', '/fixture/home', 'normal')]:
            with self.subTest(work=work), self.assertRaises(ValueError):
                candidate.command(executable, work, home, mode)

    def test_readiness_requires_fixed_report_and_live_interrupted_child(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        child = Mock(stdout=Mock(), stdin=Mock(), stderr=Mock())
        child.poll.return_value = None
        boundary = Mock()
        boundary.backend.unit = 'unit'
        boundary.launch.return_value = child
        worker = candidate.CandidateWorker(pinned, account, 'interrupted', self.supervisor('interrupted'))
        with patch.object(candidate, 'read_line', return_value={'checks': 'writable_workspace_and_readonly_metadata', 'local_pid': 1}):
            result = worker.run(None, Path('/fixture/work'), boundary)
        self.assertEqual(result['status'], 'timeout')
        child.stdin.close.assert_not_called()
        self.assertEqual(boundary.launch.call_args.kwargs['stdin'], subprocess.PIPE)
        child.poll.return_value = 0
        with patch.object(candidate, 'read_line', return_value={'checks': 'writable_workspace_and_readonly_metadata', 'local_pid': 1}), \
                self.assertRaisesRegex(ValueError, 'probe_not_running'):
            worker.run(None, Path('/fixture/work'), boundary)

    def test_changed_binary_and_bad_reports_block(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(side_effect=[(1,), (2,)]))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'))
        boundary = Mock()
        boundary.backend.unit = 'unit'
        with self.assertRaisesRegex(ValueError, 'probe_binary_changed'):
            worker.run(None, Path('/fixture/work'), boundary)
        boundary.launch.assert_not_called()
        pinned._binary.side_effect = None
        pinned._binary.return_value = (1,)
        for report in ({'checks': 'writable_workspace_and_readonly_metadata', 'pid': True}, {'checks': 'ok'}, {}):
            with patch.object(candidate, 'read_line', return_value=report), self.assertRaises(ValueError):
                worker.run(None, Path('/fixture/work'), boundary)

    def test_supervisor_must_match_actual_backend_unit_and_worker(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'))
        boundary = Mock()
        boundary.backend.unit = 'other-unit'
        with self.assertRaisesRegex(ValueError, 'binding_mismatch'):
            worker.run(None, Path('/fixture/work'), boundary)
        pinned._binary.assert_not_called()
        boundary.launch.assert_not_called()

    def test_normal_completion_requires_zero_exit_and_unchanged_binary(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        child = Mock()
        child.wait.return_value = 0
        boundary = Mock()
        boundary.backend.unit = 'unit'
        boundary.launch.return_value = child
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'))
        report = {'checks': 'writable_workspace_and_readonly_metadata', 'local_pid': 1}
        with patch.object(candidate, 'read_line', return_value=report):
            self.assertEqual(worker.run(None, Path('/fixture/work'), boundary)['status'], 'completed')
        child.wait.return_value = 1
        with patch.object(candidate, 'read_line', return_value=report), \
                self.assertRaisesRegex(ValueError, 'probe_exit_failed'):
            worker.run(None, Path('/fixture/work'), boundary)
        child.wait.return_value = 0
        pinned._binary.side_effect = [(1,), (1,), (1,), (2,)]
        with patch.object(candidate, 'read_line', return_value=report), \
                self.assertRaisesRegex(ValueError, 'probe_binary_changed'):
            worker.run(None, Path('/fixture/work'), boundary)

    def test_probe_requires_writable_positive_control_and_all_readonly_masks(self):
        def open_file(path, *args):
            if path == 'fixture.txt':
                return 42
            raise OSError(errno.EROFS, 'read only')
        with patch('os.open', side_effect=open_file), patch('os.close'), patch('os.read', return_value=b'x'), \
                patch('sys.argv', ['probe', 'normal']), patch('sys.stdout', new_callable=io.StringIO) as output:
            exec(candidate.PROBE, {})
        self.assertIn('writable_workspace_and_readonly_metadata', output.getvalue())
        with patch('os.open', side_effect=OSError(errno.EROFS, 'read only')), \
                patch('sys.argv', ['probe', 'normal']), self.assertRaises(OSError):
            exec(candidate.PROBE, {})


if __name__ == '__main__':
    unittest.main()
