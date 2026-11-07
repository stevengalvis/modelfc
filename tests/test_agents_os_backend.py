"""No namespaces, services, model, credentials or network are launched here."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_os_backend as sandbox
from ops.bounded_worker.systemd_backend import SystemdContainment


def specimen():
    return sandbox.AcceptanceSpec('/srv/modelfc-agents-fixture-' + 'a' * 32,
                                  'b' * 64, 'c' * 64, 'd' * 64, 1234, 1234)


def host_namespaces():
    return {name: name + ':[123]' for name in sandbox._NAMESPACES}


def probe(spec):
    return {'schema': 'agents-no-network-probe-v1', 'attempt_id': spec.attempt_id,
            'namespaces': {name: name + ':[456]' for name in sandbox._NAMESPACES},
            'workspace_identity': [12, 34], 'checks': {name: True for name in sandbox._CHECKS}}


class CommandTests(unittest.TestCase):
    def test_fixed_command_has_no_host_workspace_credentials_or_shell(self):
        command = sandbox.build_command(specimen(), host_namespaces())
        self.assertEqual(command[0], '/usr/bin/bwrap')
        for required in ('--unshare-all', '--disable-userns', '--die-with-parent',
                         '--new-session', '--clearenv', '--cap-drop'):
            self.assertIn(required, command)
        self.assertEqual(command.count('--ro-bind'), 1)
        self.assertNotIn('--bind', command)
        self.assertNotIn('--share-net', command)
        self.assertEqual(command[command.index('--') + 1], '/usr/bin/python3')
        self.assertEqual(command[command.index('--ro-bind') + 1], specimen().fixture_root + '/image')
        self.assertIn('/workspace', command)
        self.assertNotIn('CODEX_API_KEY', repr(command))
        self.assertNotIn('/bin/sh', command)
        self.assertFalse(any('%' in arg or any(ord(c) < 32 or ord(c) == 127 for c in arg)
                             for arg in command))
        compile(sandbox._PROBE, '<acceptance-probe>', 'exec')  # compile only

    def test_nonfixture_paths_unpinned_inputs_and_root_identity_rejected(self):
        invalid = {'fixture_root': ('/srv/production', '/srv/modelfc-agents-fixture-' + 'a' * 32 + '/..'),
                   'image_sha256': ('latest', 'b' * 63), 'bubblewrap_sha256': ('', None),
                   'attempt_id': ('../../other', 'd' * 63), 'uid': (0, True, -1), 'gid': (0, 1.0)}
        for name, values in invalid.items():
            for value in values:
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    sandbox.build_command(replace(specimen(), **{name: value}), host_namespaces())

    def test_namespace_binding_rejects_missing_or_injected_values(self):
        for host in ({}, {**host_namespaces(), 'net': 'net:[1]\n'},
                     {**host_namespaces(), 'extra': 'extra:[1]'}):
            with self.assertRaises(ValueError):
                sandbox.build_command(specimen(), host)

    def test_production_backend_accepts_exact_command_without_modifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'reservation.json').write_text('{}')
            cgroup = root / 'cgroup'
            cgroup.mkdir()
            (cgroup / 'cgroup.controllers').write_text('cpu memory pids')
            backend = SystemdContainment(root, 1234, 1234)
            backend.bind_attempt(root)
            command = sandbox.build_command(specimen(), host_namespaces())
            process = Mock()
            process.poll.return_value = None
            with patch('ops.bounded_worker.systemd_backend.os.getuid', return_value=0), \
                 patch('ops.bounded_worker.systemd_backend.os.geteuid', return_value=0), \
                 patch('ops.bounded_worker.systemd_backend._CGROUP', cgroup), \
                 patch.object(backend, '_show', side_effect=[{'LoadState': 'not-found'},
                     {'LoadState': 'loaded', 'Transient': 'yes', 'ActiveState': 'active',
                      'ControlGroup': '/system.slice/' + backend.unit}]), \
                 patch('ops.bounded_worker.systemd_backend.subprocess.Popen', return_value=process) as popen:
                self.assertIs(backend.launch(command, cwd='/', env={'PATH': '/usr/bin:/bin'},
                    user=1234, group=1234, extra_groups=[], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True, bufsize=0), process)
            actual = popen.call_args.args[0]
            self.assertEqual(actual[-len(command):], command)
            for flag in ('--property=MemoryMax=1G', '--property=TasksMax=64',
                         '--property=NoNewPrivileges=yes', '--property=KillMode=control-group'):
                self.assertIn(flag, actual)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / 'image'
        self.image.mkdir()
        for name in ('usr/bin', 'proc', 'dev', 'tmp', 'workspace'):
            (self.image / name).mkdir(parents=True)
        (self.image / 'usr/bin/python3').write_bytes(b'fake non-executed interpreter')
        for directory, dirs, files in os.walk(self.image):
            os.chmod(directory, 0o555)
            for name in files:
                os.chmod(Path(directory) / name, 0o555)
        self.addCleanup(self.make_writable)
        # Model root ownership only. File bytes, modes, no-follow opens, hardlink
        # counts and identity comparisons still use real disposable local files.
        self.real_lstat, self.real_fstat = Path.lstat, os.fstat
        def metadata(info):
            return SimpleNamespace(**{name: (0 if name == 'st_uid' else getattr(info, name))
                                      for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
                                                   'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')})
        for mocked in (patch.object(sandbox, 'protected_dir', side_effect=lambda path, *a: Path(path)),
                       patch.object(Path, 'lstat', lambda path: metadata(self.real_lstat(path))),
                       patch.object(os, 'fstat', lambda fd: metadata(self.real_fstat(fd))),
                       patch.object(sandbox, 'parse_mountinfo', return_value=[])):
            mocked.start()
            self.addCleanup(mocked.stop)

    def make_writable(self):
        for directory, dirs, files in os.walk(self.image):
            os.chmod(directory, 0o755)

    def test_content_pin_and_identity_change_when_file_changes(self):
        first = sandbox.inspect_image(self.image)
        path = self.image / 'usr/bin/python3'
        path.chmod(0o755)
        path.write_bytes(b'changed fake interpreter')
        path.chmod(0o555)
        self.assertNotEqual(sandbox.inspect_image(self.image), first)

    def test_empty_data_file_is_supported(self):
        parent = self.image / 'usr'
        parent.chmod(0o755)
        file = parent / 'empty'
        file.touch(mode=0o444)
        parent.chmod(0o555)
        sandbox.inspect_image(self.image)

    def test_writable_image_entry_rejected(self):
        (self.image / 'usr/bin/python3').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'image_entry_rejected'):
            sandbox.inspect_image(self.image)

    def test_interpreter_path_cannot_be_directory(self):
        parent = self.image / 'usr/bin'
        parent.chmod(0o755)
        interpreter = parent / 'python3'
        interpreter.unlink()
        interpreter.mkdir(mode=0o555)
        parent.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'image_python_not_executable'):
            sandbox.inspect_image(self.image)

    def test_symlink_entry_rejected(self):
        parent = self.image / 'usr'
        parent.chmod(0o755)
        (parent / 'linked').symlink_to('/etc/passwd')
        parent.chmod(0o555)
        with self.assertRaises(ValueError):
            sandbox.inspect_image(self.image)

    def test_hardlink_entry_rejected(self):
        parent = self.image / 'usr/bin'
        parent.chmod(0o755)
        os.link(parent / 'python3', parent / 'other')
        parent.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'unprotected_acceptance_file'):
            sandbox.inspect_image(self.image)

    def test_mountpoint_must_be_empty(self):
        work = self.image / 'workspace'
        work.chmod(0o755)
        (work / 'unexpected').write_bytes(b'x')
        work.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'image_mountpoint_not_empty'):
            sandbox.inspect_image(self.image)

    def test_nested_mount_is_rejected(self):
        with patch.object(sandbox, 'parse_mountinfo', return_value=[{'mount': str(self.image / 'usr')}]), \
             self.assertRaisesRegex(ValueError, 'image_nested_mount'):
            sandbox.inspect_image(self.image)

    def test_unapproved_top_level_path_rejected(self):
        self.image.chmod(0o755)
        (self.image / 'root').mkdir(mode=0o555)
        self.image.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'image_layout_rejected'):
            sandbox.inspect_image(self.image)


class ProbeTests(unittest.TestCase):
    def test_exact_report_required(self):
        spec, host = specimen(), host_namespaces()
        self.assertTrue(sandbox._valid_probe(probe(spec), spec, host))
        for field, value in [('attempt_id', 'other'), ('checks', {}),
                             ('workspace_identity', [True, 2]), ('namespaces', host),
                             ('checks', {name: 1 for name in sandbox._CHECKS})]:
            with self.subTest(field=field):
                self.assertFalse(sandbox._valid_probe({**probe(spec), field: value}, spec, host))

    def read_chunks(self, chunks):
        process = SimpleNamespace(stdout=Mock(), stderr=Mock())
        process.stdout.fileno.return_value = 4
        with patch.object(sandbox.select, 'select', return_value=([process.stdout], [], [])), \
             patch.object(sandbox.os, 'read', side_effect=chunks):
            return sandbox._read_probe(process)

    def test_split_frame_is_assembled(self):
        raw = json.dumps(probe(specimen())).encode() + b'\n'
        self.assertEqual(self.read_chunks([raw[:30], raw[30:]]), probe(specimen()))

    def test_duplicate_keys_extra_frames_and_oversize_fail(self):
        for chunks in ([b'{"a":1,"a":2}\n'], [b'{}\n{}\n'], [b'x' * 4097]):
            with self.subTest(size=len(chunks[0])), self.assertRaises(ValueError):
                self.read_chunks(chunks)

    def test_timeout_is_bounded(self):
        with patch.object(sandbox.select, 'select', return_value=([], [], [])), \
             self.assertRaisesRegex(ValueError, 'probe_timeout'):
            sandbox._read_probe(SimpleNamespace(stdout=Mock(), stderr=Mock()))

    def test_effective_limits_are_read_not_assumed(self):
        data = {'MemoryMax': '1073741824', 'TasksMax': '64', 'CPUQuotaPerSecUSec': '1s',
                'RuntimeMaxUSec': '3min', 'TimeoutStopUSec': '5s', 'KillMode': 'control-group',
                'SendSIGKILL': 'yes', 'NoNewPrivileges': 'yes', 'Delegate': 'no',
                'ProtectControlGroups': 'yes'}
        body = '\n'.join(key + '=' + value for key, value in data.items()).encode()
        with patch.object(sandbox.subprocess, 'run', return_value=SimpleNamespace(stdout=body)) as run:
            self.assertEqual(sandbox._effective_limits('modelfc-bounded-' + 'a' * 32 + '.service'), data)
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
            run.return_value.stdout = body.replace(b'MemoryMax=1073741824', b'MemoryMax=infinity')
            with self.assertRaises(ValueError):
                sandbox._effective_limits('modelfc-bounded-' + 'a' * 32 + '.service')


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'evidence').mkdir(mode=0o700)
        self.spec = specimen()
        self.process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
        self.backend = Mock(unit='modelfc-bounded-' + 'e' * 32 + '.service', process=self.process)
        self.backend.launch.return_value = self.process
        self.backend.terminate.return_value = True
        self.backend.verify_empty.return_value = True
        self.inventory = ('b' * 64, (('image', (1, 2)),))
        patches = {
            'protected_dir': Mock(side_effect=lambda path, *args:
                                  self.root if str(path) == self.spec.fixture_root else Path(path)),
            'inspect_image': Mock(return_value=self.inventory),
            '_file': Mock(return_value=('c' * 64, (3, 4))),
            '_namespaces': Mock(return_value=host_namespaces()),
            'SystemdContainment': Mock(return_value=self.backend),
            '_effective_limits': Mock(return_value={'synthetic': 'only'}),
            '_read_probe': Mock(return_value=probe(self.spec)),
        }
        self.mocks = patches
        for name, replacement in patches.items():
            mocked = patch.object(sandbox, name, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        for name in ('getuid', 'geteuid'):
            mocked = patch.object(os, name, return_value=0)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_success_requires_probe_limits_cleanup_and_persists_exact_binding(self):
        def launch(*args, **kwargs):
            reservation = self.root / 'evidence' / self.spec.attempt_id / 'reservation.json'
            self.assertEqual(json.loads(reservation.read_text())['attempt_id'], self.spec.attempt_id)
            return self.process
        self.backend.launch.side_effect = launch
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'passed')
        self.assertFalse(result['ready_for_live'])
        path = self.root / 'evidence' / self.spec.attempt_id / 'acceptance.json'
        self.assertEqual(json.loads(path.read_text()), result)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.backend.terminate.assert_called_once()
        self.backend.verify_empty.assert_called_once()
        self.assertTrue(self.process.stdout.closed)

    def test_launch_exception_still_attempts_cleanup_and_never_replays(self):
        self.backend.launch.side_effect = OSError('do not expose raw error')
        self.backend.terminate.return_value = False
        self.backend.verify_empty.return_value = False
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'blocked')
        self.assertEqual(result['failure_stage'], 'launch')
        self.assertNotIn('do not expose', repr(result))
        self.backend.terminate.assert_called_once()
        self.backend.verify_empty.assert_called_once()
        with self.assertRaises(FileExistsError):
            sandbox.run_acceptance(self.spec)
        self.backend.launch.assert_called_once()

    def test_unknown_cleanup_is_never_passed(self):
        self.backend.verify_empty.return_value = False
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'blocked')
        self.assertTrue(result['probe_verified'])
        self.assertFalse(result['cleanup_verified'])

    def test_probe_failure_is_never_passed(self):
        self.mocks['_read_probe'].return_value = {}
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'blocked')
        self.assertEqual(result['failure_stage'], 'probe')
        self.assertTrue(result['cleanup_verified'])

    def test_effective_limit_failure_blocks_before_probe(self):
        self.mocks['_effective_limits'].side_effect = ValueError('unlimited')
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'blocked')
        self.assertEqual(result['failure_stage'], 'effective_limits')
        self.mocks['_read_probe'].assert_not_called()

    def test_content_pin_mismatch_prevents_any_launch(self):
        with self.assertRaisesRegex(ValueError, 'acceptance_content_pin_mismatch'):
            sandbox.run_acceptance(replace(self.spec, image_sha256='f' * 64))
        self.backend.launch.assert_not_called()

    def test_prelaunch_identity_change_blocks(self):
        self.mocks['inspect_image'].side_effect = [self.inventory, ('wrong', ()), self.inventory]
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'blocked')
        self.backend.launch.assert_not_called()

    def test_poststop_identity_change_blocks(self):
        self.mocks['inspect_image'].side_effect = [self.inventory, self.inventory, ('wrong', ())]
        result = sandbox.run_acceptance(self.spec)
        self.assertEqual(result['result'], 'blocked')
        self.assertFalse(result['inputs_unchanged'])

    def test_nonroot_rejected_before_inspection_or_launch(self):
        with patch.object(os, 'geteuid', return_value=1234), self.assertRaises(ValueError):
            sandbox.run_acceptance(self.spec)
        self.mocks['inspect_image'].assert_not_called()
        self.backend.launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
