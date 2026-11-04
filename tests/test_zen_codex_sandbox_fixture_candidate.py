"""Offline tests for an unwired no-model Codex sandbox candidate."""
import errno
import io
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

from ops.bounded_worker import codex_sandbox_fixture_candidate as candidate
from ops.bounded_worker.codex import DENIED, DISABLED
from ops.bounded_worker.task import Task, execute
from tests.test_bounded_worker import FakeContainment


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
        child.stdin.write.return_value = 1
        child.poll.return_value = None
        boundary = Mock()
        boundary.backend.unit = 'unit'
        boundary.backend.verify_gate.return_value = True
        boundary.launch.return_value = child
        worker = candidate.CandidateWorker(pinned, account, 'interrupted', self.supervisor('interrupted'), ('/root',), lambda: ('/root',))
        report = {'checks': 'writable_workspace_and_readonly_metadata', 'local_pid': 1}
        with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]):
            result = worker.run(None, Path('/fixture/work'), boundary)
        self.assertEqual(result['status'], 'timeout')
        child.stdin.close.assert_not_called()
        self.assertEqual(boundary.launch.call_args.kwargs['stdin'], subprocess.PIPE)
        child.poll.side_effect = [None, 0]
        with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]), \
                self.assertRaisesRegex(ValueError, 'probe_not_running'):
            worker.run(None, Path('/fixture/work'), boundary)

    def test_changed_binary_and_bad_reports_block(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(side_effect=[(1,), (2,)]))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'), ('/root',), lambda: ('/root',))
        boundary = Mock()
        boundary.backend.unit = 'unit'
        boundary.backend.verify_gate.return_value = True
        boundary.launch.return_value.poll.return_value = None
        boundary.launch.return_value.stdin.write.return_value = 1
        with self.assertRaisesRegex(ValueError, 'probe_binary_changed'):
            worker.run(None, Path('/fixture/work'), boundary)
        boundary.launch.assert_not_called()
        pinned._binary.side_effect = None
        pinned._binary.return_value = (1,)
        for report in ({'checks': 'writable_workspace_and_readonly_metadata', 'pid': True}, {'checks': 'ok'}, {}):
            with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]), self.assertRaises(ValueError):
                worker.run(None, Path('/fixture/work'), boundary)

    def test_supervisor_must_match_actual_backend_unit_and_worker(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'), ('/root',), lambda: ('/root',))
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
        child.poll.return_value = None
        child.stdin.write.return_value = 1
        boundary = Mock()
        boundary.backend.unit = 'unit'
        boundary.backend.verify_gate.return_value = True
        boundary.launch.return_value = child
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'), ('/root',), lambda: ('/root',))
        report = {'checks': 'writable_workspace_and_readonly_metadata', 'local_pid': 1}
        with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]):
            self.assertEqual(worker.run(None, Path('/fixture/work'), boundary)['status'], 'completed')
        child.wait.return_value = 1
        with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]), \
                self.assertRaisesRegex(ValueError, 'probe_exit_failed'):
            worker.run(None, Path('/fixture/work'), boundary)
        child.wait.return_value = 0
        pinned._binary.side_effect = [(1,), (1,), (1,), (2,)]
        with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]), \
                self.assertRaisesRegex(ValueError, 'probe_binary_changed'):
            worker.run(None, Path('/fixture/work'), boundary)

    def test_gate_never_executes_without_valid_release(self):
        argv = ['/opt/codex', 'sandbox']
        payload = {'argv': argv, 'uid': 983, 'gid': 983, 'stores': ['/root'], 'control': '/work'}
        status = ('Uid:\t983\t983\t983\t983\nGid:\t983\t983\t983\t983\nGroups:\nNoNewPrivs:\t1\n'
                  + ''.join(name + ':\t00000000\n' for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')))
        def call(ready, byte, *, denied=True, error=errno.EACCES, allowed_mode=None, extra_fds=False,
                 groups=None, open_allowed=False, open_error=errno.EACCES, control_denied=False, fd_error=None):
            libc = SimpleNamespace(syscall=lambda number, base, path, mode, flags:
                                   0 if (path.value == b'/work' and not control_denied) or not denied
                                   or mode == allowed_mode else -1)
            def fstat(number):
                if number > 2 and fd_error is not None:
                    raise OSError(fd_error, 'fixture')
                return SimpleNamespace(st_mode=stat.S_IFIFO)
            with patch('sys.argv', ['gate', json.dumps(payload)]), patch('sys.stdout', new_callable=io.StringIO), \
                 patch('builtins.open', mock_open(read_data=status)), patch('os.getuid', return_value=983), \
                 patch('os.geteuid', return_value=983), patch('os.getgid', return_value=983), \
                 patch('os.getegid', return_value=983), patch('os.getgroups', return_value=groups or []), \
                 patch('os.fstat', side_effect=fstat), \
                 patch('os.listdir', return_value=['0', '1', '2', '4'] if extra_fds else ['0', '1', '2']), \
                 patch('os.open', side_effect=(lambda *a: 42) if open_allowed else OSError(open_error, 'denied')), \
                 patch('os.close'), \
                 patch('platform.machine', return_value='x86_64'), patch('ctypes.CDLL', return_value=libc), \
                 patch('ctypes.get_errno', return_value=error), patch('select.select', return_value=(ready, [], [])), \
                 patch('os.read', return_value=byte), patch('os.execve') as execute:
                try:
                    exec(candidate.GATE, {})
                except SystemExit:
                    pass
                return execute.call_args
        for ready, byte in (([], b'g'), ([0], b''), ([0], b'x')):
            self.assertIsNone(call(ready, byte))
        self.assertIsNone(call([0], b'g', denied=False))
        self.assertIsNone(call([0], b'g', error=errno.ENOENT))
        self.assertIsNone(call([0], b'g', allowed_mode=os.R_OK))
        self.assertIsNone(call([0], b'g', allowed_mode=os.W_OK))
        self.assertIsNone(call([0], b'g', allowed_mode=os.X_OK))
        self.assertIsNone(call([0], b'g', extra_fds=True))
        self.assertIsNone(call([0], b'g', extra_fds=True, fd_error=errno.EACCES))
        self.assertIsNotNone(call([0], b'g', extra_fds=True, fd_error=errno.EBADF))
        self.assertIsNone(call([0], b'g', groups=[1003]))
        self.assertIsNone(call([0], b'g', open_allowed=True))
        self.assertIsNone(call([0], b'g', open_error=errno.ENOENT))
        self.assertIsNone(call([0], b'g', control_denied=True))
        self.assertEqual(call([0], b'g').args[:2], ('/opt/codex', argv))

    def test_gate_report_or_release_failure_blocks_before_probe(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'), ('/root',), lambda: ('/root',))
        boundary = Mock()
        boundary.backend.unit = 'unit'
        boundary.backend.verify_gate.return_value = True
        child = boundary.launch.return_value
        child.poll.return_value = None
        with patch.object(candidate, 'read_line', return_value={'gate': 'wrong'}), \
                self.assertRaisesRegex(ValueError, 'gate_unverified'):
            worker.run(None, Path('/fixture/work'), boundary)
        child.stdin.write.assert_not_called()
        boundary.backend.verify_gate.return_value = False
        with patch.object(candidate, 'read_line', return_value={'gate': 'waiting', 'credentials': 'denied'}), \
                self.assertRaisesRegex(ValueError, 'gate_unverified'):
            worker.run(None, Path('/fixture/work'), boundary)
        child.stdin.write.assert_not_called()
        boundary.backend.verify_gate.return_value = True
        worker.revalidate_scope = lambda: ('/changed',)
        with patch.object(candidate, 'read_line', return_value={'gate': 'waiting', 'credentials': 'denied'}), \
                self.assertRaisesRegex(ValueError, 'credential_scope_changed'):
            worker.run(None, Path('/fixture/work'), boundary)
        child.stdin.write.assert_not_called()
        worker.revalidate_scope = lambda: ('/root',)
        child.stdin.write.return_value = 0
        with patch.object(candidate, 'read_line', return_value={'gate': 'waiting', 'credentials': 'denied'}), \
                self.assertRaisesRegex(ValueError, 'gate_release_failed'):
            worker.run(None, Path('/fixture/work'), boundary)

    def test_gate_verification_precedes_release(self):
        pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_dir='/fixture/home')
        worker = candidate.CandidateWorker(pinned, account, 'normal', self.supervisor('normal'), ('/root',), lambda: ('/root',))
        boundary = Mock()
        boundary.backend.unit = 'unit'
        child = boundary.launch.return_value
        child.poll.return_value = None
        child.wait.return_value = 0
        order = []
        boundary.backend.verify_gate.side_effect = lambda args: order.append('verified') or True
        child.stdin.write.side_effect = lambda data: order.append(data) or 1
        report = {'checks': 'writable_workspace_and_readonly_metadata', 'local_pid': 1}
        with patch.object(candidate, 'read_line', side_effect=[{'gate': 'waiting', 'credentials': 'denied'}, report]):
            worker.run(None, Path('/fixture/work'), boundary)
        self.assertEqual(order, ['verified', b'g', b'x'])

    def test_gate_verification_failure_still_cleans_attempt_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            work.mkdir()
            def git(*args):
                return subprocess.run(['git', *args], cwd=work, check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (work / 'fixture.txt').write_text('fixture\n')
            git('add', 'fixture.txt')
            git('commit', '-qm', 'fixture')
            base = git('rev-parse', 'HEAD').decode().strip()
            task = Task('fixture', 'stevengalvis/modelfc', base, 'fixture', ('fixture.txt',))
            child = Mock()
            child.poll.return_value = None
            class Backend(FakeContainment):
                unit = 'unit'
                def launch(self, *args, **kwargs):
                    self.events.append('launch')
                    return child
                def verify_gate(self, args):
                    raise ValueError('unverified')
            backend = Backend()
            uid, gid = os.getuid(), os.getgid()
            account = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_dir='/fixture/home')
            pinned = SimpleNamespace(executable=Path('/opt/codex'), _binary=Mock(return_value=(1,)))
            supervisor = SimpleNamespace(unit='unit', uid=uid, mode='normal', observe=lambda _: (1, 2, 3, 4))
            worker = candidate.CandidateWorker(pinned, account, 'normal', supervisor, ('/root',), lambda: ('/root',))
            with patch.object(candidate, 'read_line', return_value={'gate': 'waiting', 'credentials': 'denied'}):
                result = execute(task, work, root / 'evidence', worker, backend)
            self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])
            self.assertTrue(result['cleanup_verified'])
            self.assertEqual(result['status'], 'uncertain')
            child.stdin.write.assert_not_called()

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
