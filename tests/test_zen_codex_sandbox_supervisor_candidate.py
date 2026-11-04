"""Offline checks for fake input and host-process observation helpers."""
import json
import hashlib
import contextlib
import os
from pathlib import Path
import stat
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import codex_sandbox_supervisor_candidate as supervisor
from ops.bounded_worker import codex_sandbox_fixture_candidate as fixture_candidate
from ops.bounded_worker import storage_fixture
from ops.bounded_worker import storage_fixture_prepare
from ops.bounded_worker import launcher
from ops.bounded_worker.storage import StorageConfiguration, service_properties


class SupervisorTests(unittest.TestCase):
    def test_gate_identity_requires_exact_unit_uid_binary_and_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proc = root / 'proc'
            pid = proc / '1234'
            pid.mkdir(parents=True)
            python = root / 'python'
            python.write_bytes(b'fixture')
            info = python.stat()
            fake_python = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                st_mode=stat.S_IFREG | 0o755, st_uid=0))
            unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
            network = {'unit': unit, 'main_pid': 1234, 'starttime': 42, 'invocation': 'b' * 32}
            args = ['/usr/bin/python3', '-I', '-S', '-B', '-c', fixture_candidate.GATE_ARG, '["/opt/codex"]']
            (pid / 'stat').write_text('1234 (python) S ' + '0 ' * 18 + '42\n')
            (pid / 'status').write_text('Uid:\t1003\t1003\t1003\t1003\n')
            (pid / 'cgroup').write_text('0::/system.slice/' + unit + '\n')
            (pid / 'exe').symlink_to(python)
            (pid / 'cmdline').write_bytes(b'\0'.join(value.encode() for value in args) + b'\0')
            value = supervisor.bind_live_gate(unit, 1003, args, network, _proc_root=proc, _python=fake_python)
            self.assertEqual(value['main_pid'], 1234)
            (pid / 'cmdline').write_bytes(b'other\0')
            with self.assertRaisesRegex(ValueError, 'gate_identity_unverified'):
                supervisor.bind_live_gate(unit, 1003, args, network, _proc_root=proc, _python=fake_python)
            (pid / 'status').write_text('Uid:\t0\t0\t0\t0\n')
            with self.assertRaisesRegex(ValueError, 'gate_identity_unverified'):
                supervisor.bind_live_gate(unit, 1003, args, network, _proc_root=proc, _python=fake_python)

    def test_gate_verifier_rechecks_live_state_before_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            backend = supervisor.DiagnosticContainment(Path(temporary), 1003, 1003)
            network = {'unit': backend.unit, 'main_pid': 1234, 'starttime': 42, 'invocation': 'a' * 32}
            gate = {'unit': backend.unit, 'main_pid': 1234}
            run = {'unit': backend.unit, 'mount_id': 55}
            with patch.object(supervisor, 'verify_private_network', return_value=network), \
                 patch.object(supervisor, 'bind_live_gate', return_value=gate), \
                 patch.object(supervisor, 'verify_private_run', return_value=run):
                self.assertTrue(backend.verify_gate(['fixed']))
            self.assertEqual(json.loads((Path(temporary) / 'diagnostic-gate-observed.json').read_text()), gate)
        with tempfile.TemporaryDirectory() as temporary:
            backend = supervisor.DiagnosticContainment(Path(temporary), 1003, 1003)
            network = {'unit': backend.unit, 'main_pid': 1234, 'starttime': 42, 'invocation': 'a' * 32}
            with patch.object(supervisor, 'verify_private_network', side_effect=[network, {**network, 'main_pid': 999}]), \
                 patch.object(supervisor, 'bind_live_gate', return_value={}), \
                 patch.object(supervisor, 'verify_private_run', return_value={}):
                with self.assertRaisesRegex(ValueError, 'identity_changed'):
                    backend.verify_gate(['fixed'])
            self.assertFalse((Path(temporary) / 'diagnostic-gate-observed.json').exists())

    def test_private_run_preserves_real_generated_storage_properties(self):
        cfg = StorageConfiguration('/bound', '/secure/image', '/bound/work', '/bound/runtime',
            2049, 99, 100000, 51200, 1000, 1024, 10, '/home/worker/.codex/auth.json',
            '/home/worker/.codex/config.toml', 'synthetic-component')
        original = service_properties(cfg, work='/bound/work/test', runtime='/bound/runtime/test',
                                      home='/home/worker', unit='modelfc-bounded-' + 'a' * 32 + '.service')
        output = supervisor.private_run_properties(original)
        expected = [value.replace(' /run:/run:rbind', '') for value in original] + [supervisor.RUN_PROPERTY]
        self.assertEqual(output, expected)
        self.assertTrue(any(value.startswith('--property=TemporaryFileSystem=/dev/shm:') for value in output))

    def test_private_run_replaces_only_expected_bind_and_rejects_conflicts(self):
        source = ['--property=ReadOnlyPaths=/ /proc/sys', '--property=PrivateMounts=yes',
                  '--property=MountFlags=private', '--property=ReadWritePaths=/proc -/dev/pts -/dev/mqueue',
                  '--property=BindReadOnlyPaths=/dev:/dev:rbind /run:/run:rbind /sys:/sys:rbind /fixture',
                  '--property=BindPaths=/fixture/tmp:/tmp',
                  '--property=TemporaryFileSystem=/dev/shm:size=8M']
        output = supervisor.private_run_properties(source)
        self.assertEqual(source[0], output[0])
        self.assertEqual(output[1:4], source[1:4])
        self.assertEqual(output[4], '--property=BindReadOnlyPaths=/dev:/dev:rbind /sys:/sys:rbind /fixture')
        self.assertEqual(output[5:7], source[5:7])
        self.assertEqual(output[-1], supervisor.RUN_PROPERTY)
        for invalid in (source[:1], source + ['--property=BindPaths=/other:/run/alias'],
                        source + ['--property=TemporaryFileSystem=/run:ro'],
                        source + ['--property=ReadWritePaths=/run/foo'],
                        [*source[:4], source[4] + ' /run:/run:rbind', *source[5:]],
                        source + ['--property=BindPaths=/run:/run:rbind']):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                supervisor.private_run_properties(invalid)

    def test_private_run_requires_resolved_empty_readonly_tmpfs(self):
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        network = {'unit': unit, 'main_pid': 1234, 'starttime': 42, 'invocation': 'b' * 32}
        process = ('1234 (codex) S ' + '0 ' * 18 + '42\n').encode()
        mount = {'id': 97, 'path': '/run', 'root': '/', 'type': 'tmpfs',
                 'options': ['ro', 'nosuid', 'nodev', 'noexec']}
        def reader(path, maximum):
            if path.name == 'stat':
                return process
            if path.name == 'mountinfo':
                return b'mountinfo'
            return b'pos:\t0\nmnt_id:\t97\n'
        with patch.object(supervisor, '_read', side_effect=reader), \
             patch.object(supervisor, '_mounts', return_value=[mount]), \
             patch.object(supervisor.os, 'open', return_value=71), patch.object(supervisor.os, 'close'), \
             patch.object(supervisor.os, 'listdir', return_value=[]), \
             patch.object(supervisor.os, 'fstatvfs', return_value=SimpleNamespace(
                 f_flag=os.ST_RDONLY, f_blocks=256, f_frsize=4096, f_files=1024)):
            self.assertEqual(supervisor.verify_private_run(unit, network)['mount_id'], 97)
            mount['options'] = ['rw']
            with self.assertRaisesRegex(ValueError, 'mount_unverified'):
                supervisor.verify_private_run(unit, network)
            mount['options'] = ['ro', 'nosuid', 'nodev', 'noexec']
            for flag in ('nosuid', 'nodev', 'noexec'):
                mount['options'].remove(flag)
                with self.assertRaisesRegex(ValueError, 'mount_unverified'):
                    supervisor.verify_private_run(unit, network)
                mount['options'].append(flag)
            with patch.object(supervisor.os, 'fstatvfs', return_value=SimpleNamespace(
                    f_flag=os.ST_RDONLY, f_blocks=257, f_frsize=4096, f_files=1024)), \
                    self.assertRaisesRegex(ValueError, 'mount_unverified'):
                supervisor.verify_private_run(unit, network)
            with patch.object(supervisor.os, 'fstatvfs', return_value=SimpleNamespace(
                    f_flag=os.ST_RDONLY, f_blocks=256, f_frsize=4096, f_files=1025)), \
                    self.assertRaisesRegex(ValueError, 'mount_unverified'):
                supervisor.verify_private_run(unit, network)
            with patch.object(supervisor.os, 'listdir', return_value=['unexpected']), \
                    self.assertRaisesRegex(ValueError, 'not_empty'):
                supervisor.verify_private_run(unit, network)

    def test_run_candidate_requires_both_launcher_and_supervisor_acceptance(self):
        root = Path('/srv/modelfc-zen69-fixture-' + 'a' * 32)
        account = SimpleNamespace(pw_name=supervisor.TEST_ACCOUNT, pw_uid=983, pw_gid=983)
        production_account = SimpleNamespace(pw_name='zeno-worker', pw_uid=1003, pw_gid=1003, pw_dir='/home/zeno-worker')
        config = launcher.Configuration(str(root / 'fs/workspaces'), str(root / 'evidence'),
                                        supervisor.TEST_ACCOUNT, '/usr/bin/python3', '0' * 64)
        manifest = {'mount_id': 8, 'sources': {}}
        expected = {'username': supervisor.TEST_ACCOUNT, 'uid': 983, 'gid': 983}
        before = {'fixture.txt': ('file', 0o600, 'sha')}
        after = {**before, '.agents': ('dir', 0o700), '.aws': ('dir', 0o700), '.codex': ('dir', 0o700)}
        baseline = {'status': 'verified', 'directories': {name: [] for name in ('.agents', '.aws', '.codex')}}
        result = {'task_id': 'zen69-fixture', 'status': 'completed', 'diff_sha256': None}
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        creation_sha = hashlib.sha256(b'receipt').hexdigest()
        record = supervisor.fake_input_record(creation_sha)
        digest = hashlib.sha256(record).hexdigest()
        state = {'network': True, 'cleanup': True, 'sources': True, 'inspection': True,
                 'unit': True, 'record': True, 'launch_error': False, 'reinspect': True,
                 'binary': True, 'baseline': True, 'supervisor': True, 'network_record': True,
                 'network_observation': True, 'run_observation': True, 'scope': True}
        scope_calls = [0]
        def load_scope(*args):
            scope_calls[0] += 1
            return ('/root',) if state['scope'] or scope_calls[0] == 1 else ('/changed',)
        lock_counts = {'entered': 0, 'exited': 0}
        @contextlib.contextmanager
        def hold(_):
            lock_counts['entered'] += 1
            try:
                yield
            finally:
                lock_counts['exited'] += 1
        def submit(task, config, _worker_factory, _backend_factory):
            worker = _worker_factory()
            worker.supervisor = SimpleNamespace(unit=unit, network_verified=state['network'],
                                                cleanup_verified=lambda: state['cleanup'])
            if not state['supervisor']:
                worker.supervisor = None
            worker.backend = SimpleNamespace(unit=unit, network_observation={'unit': unit, 'invocation': 'a' * 32},
                                             run_observation={'unit': unit}, gate_observation={'unit': unit, 'invocation': 'a' * 32})
            if state['launch_error']:
                raise ValueError('after_launch')
            return result
        def read_evidence(path):
            if path.name == 'containment.json':
                return {'unit': unit if state['unit'] else 'other'}
            if path.name == 'diagnostic-network.json':
                return {'unit': unit, 'extra_property': supervisor.NETWORK_PROPERTY if state['network_record'] else 'changed',
                        'removed_bind': supervisor.RUN_BIND, 'added_property': supervisor.RUN_PROPERTY}
            if path.name == 'diagnostic-network-observed.json':
                return {'unit': unit if state['network_observation'] else 'other', 'invocation': 'a' * 32}
            if path.name == 'diagnostic-run-observed.json':
                return {'unit': unit if state['run_observation'] else 'other'}
            if path.name == 'diagnostic-gate-observed.json':
                return {'unit': unit, 'invocation': 'a' * 32}
            if path.name == 'codex-credential-scope.json':
                return {'version': 1, 'scope_sha256': 'd' * 64, 'test_uid': 983, 'test_gid': 983}
            return baseline if state['baseline'] else {'status': 'unverified'}
        def protected(path, **kwargs):
            return b'receipt' if path.name == 'fixture-creation.json' else (record if state['record'] else b'changed')
        snapshots = iter([value for _ in range(20) for value in (before, after)])
        def make_binary(*args):
            calls = iter([(1,), (1,) if state['binary'] else (2,)])
            return SimpleNamespace(_binary=lambda: next(calls))
        def exercise():
            args = (root, supervisor.TEST_ACCOUNT, '/dev/loop1', '/root/inventory', 'b' * 64, '/opt/codex', 'c' * 64, 'normal')
            options = {'credential_scope_path': '/root/scope', 'credential_scope_sha256': 'd' * 64,
                       'scope_evidence_path': '/root/evidence'}
            self.assertEqual(supervisor.run_candidate(*args, **options)['status'], 'passed')
            result['status'] = 'timeout'
            scope_calls[0] = 0
            self.assertEqual(supervisor.run_candidate(*args[:-1], 'interrupted', **options)['status'], 'passed')
            result['status'] = 'completed'
            for key in state:
                with self.subTest(key=key):
                    state[key] = not state[key]
                    scope_calls[0] = 0
                    self.assertEqual(supervisor.run_candidate(*args, **options)['status'], 'blocked')
                    state[key] = not state[key]
            self.assertEqual(lock_counts, {'entered': 17, 'exited': 17})
        with patch.multiple(supervisor.os, getuid=Mock(return_value=0), geteuid=Mock(return_value=0),
             stat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(7, 1)))), \
             patch.object(supervisor.storage_fixture, 'checked_root', return_value=root), \
             patch.object(supervisor.storage_fixture, 'verify_topology', return_value=8), \
             patch.object(supervisor.storage_fixture, 'load_manifest', return_value=(manifest, {}, expected, {}, 'component')), \
             patch.object(supervisor.storage_fixture, 'hold_bridge_lock', side_effect=hold), \
             patch.object(supervisor.storage_fixture, 'require_no_worker_units'), \
             patch.object(supervisor.storage_fixture, 'require_no_worker_processes'), \
             patch.multiple(supervisor.pwd,
                getpwnam=Mock(side_effect=lambda name: account if name == supervisor.TEST_ACCOUNT else production_account),
                getpwall=Mock(return_value=[account, production_account])), \
             patch.multiple(supervisor, load_scope=Mock(side_effect=load_scope), protected_dir=Mock(), _retain_exclusive=Mock()), \
             patch.object(supervisor.storage_fixture, 'fixture_configuration', return_value=(config, {'device': '7:1'}, ['fake'])), \
             patch.object(supervisor.storage_fixture, 'fake_sources', side_effect=lambda *a, **k: ['fake' if state['sources'] else 'changed']), \
             patch.object(supervisor.storage_fixture, 'create_checkout', return_value='a' * 40), \
             patch.object(supervisor.storage_fixture, 'reinspect_after_cleanup', side_effect=lambda *a: state['reinspect']), \
             patch.object(supervisor, 'Codex', side_effect=make_binary), \
             patch.object(supervisor, 'protected_bytes', side_effect=protected), \
             patch.object(supervisor, 'retain_fake_input_record', return_value=digest), \
             patch.object(supervisor, '_snapshot', side_effect=lambda _: next(snapshots)), \
             patch.object(supervisor.launcher, 'submit', side_effect=submit), \
             patch.object(supervisor.launcher, 'inspect', side_effect=lambda *a, **k: result if state['inspection'] else {}), \
             patch.object(supervisor.launcher, '_read', side_effect=read_evidence):
            exercise()

    def test_diagnostic_property_is_separate_durable_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'reservation.json').write_text('{}')
            backend = supervisor.DiagnosticContainment(root, 1003, 1003)
            backend.configure_storage(properties=['--property=ReadOnlyPaths=/ /proc/sys',
                '--property=PrivateMounts=yes', '--property=MountFlags=private',
                '--property=ReadWritePaths=/proc -/dev/pts -/dev/mqueue',
                '--property=BindPaths=/fixture/tmp:/tmp',
                '--property=BindReadOnlyPaths=/dev:/dev:rbind /run:/run:rbind /sys:/sys:rbind /fixture',
                '--property=TemporaryFileSystem=/dev/shm:size=8M'], expected={},
                                      guard_path='/opt/guard', revalidate=lambda: None)
            self.assertIn(supervisor.NETWORK_PROPERTY, backend.storage[0])
            self.assertIn(supervisor.RUN_PROPERTY, backend.storage[0])
            self.assertFalse(any(supervisor.RUN_BIND in value for value in backend.storage[0]))
            backend._record()
            record = json.loads((root / 'diagnostic-network.json').read_text())
            self.assertEqual(record, {'unit': backend.unit, 'extra_property': supervisor.NETWORK_PROPERTY,
                                      'removed_bind': supervisor.RUN_BIND, 'added_property': supervisor.RUN_PROPERTY})
            with self.assertRaises(FileExistsError):
                backend._record()

    def test_input_record_is_exclusive_and_digest_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('ops.bounded_worker.bridge_security.protected_dir', return_value=root):
                digest = supervisor.retain_fake_input_record(root, 'a' * 64)
                raw = (root / 'codex-fixture-inputs.json').read_bytes()
                self.assertTrue(supervisor.verify_fake_input_record(raw, 'a' * 64, digest))
                with self.assertRaises(FileExistsError):
                    supervisor.retain_fake_input_record(root, 'a' * 64)

    def test_network_fence_requires_private_unit_and_no_external_interfaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            for pid in ('1', '1234'):
                (proc / pid / 'ns').mkdir(parents=True)
                (proc / pid / 'ns/net').write_text(pid)
            unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
            path = proc / '1234'
            (path / 'stat').write_text('1234 (codex) S ' + '0 ' * 18 + '42\n')
            (path / 'cgroup').write_text('0::/system.slice/' + unit + '\n')
            (path / 'net').mkdir()
            (path / 'net/dev').write_text('header\nheader\nlo: 0\n')
            output = 'PrivateNetwork=yes\nMainPID=1234\nInvocationID=' + 'b' * 32 + '\nActiveState=active\n'
            runner = lambda *args, **kwargs: SimpleNamespace(stdout=output)
            self.assertEqual(supervisor.verify_private_network(unit, _proc_root=proc, _run=runner)['main_pid'], 1234)
            (path / 'net/dev').write_text('header\nheader\nlo: 0\neth0: 0\n')
            with self.assertRaisesRegex(ValueError, 'external_interface'):
                supervisor.verify_private_network(unit, _proc_root=proc, _run=runner)
            bad = lambda *args, **kwargs: SimpleNamespace(stdout=output.replace('PrivateNetwork=yes', 'PrivateNetwork=no'))
            with self.assertRaisesRegex(ValueError, 'manager_unverified'):
                supervisor.verify_private_network(unit, _proc_root=proc, _run=bad)

    def test_fake_input_record_binds_version_contents_and_creation(self):
        raw = supervisor.fake_input_record('a' * 64)
        digest = hashlib.sha256(raw).hexdigest()
        self.assertTrue(supervisor.verify_fake_input_record(raw, 'a' * 64, digest))
        self.assertFalse(supervisor.verify_fake_input_record(raw, 'b' * 64, digest))
        self.assertFalse(supervisor.verify_fake_input_record(raw + b' ', 'a' * 64, digest))
        with self.assertRaises(ValueError):
            supervisor.fake_input_record('bad')

    def test_acceptance_requires_launcher_baseline_and_independent_checks(self):
        before = {'fixture.txt': ('file', 0o600, 'sha'), '.git': ('dir', 0o700)}
        after = {**before, '.agents': ('dir', 0o700), '.aws': ('dir', 0o700), '.codex': ('dir', 0o700)}
        baseline = {'status': 'verified', 'directories': {k: [] for k in ('.agents', '.aws', '.codex')}}
        result = {'status': 'completed', 'task_id': 'zen69-fixture'}
        checks = dict(cleanup=True, sources_match=True, input_record_verified=True,
                      credential_scope_verified=True, network_fence_verified=True, private_run_verified=True, gate_verified=True,
                      binary_verified=True, observed_unit_matches=True)
        self.assertTrue(supervisor.accepted_case('normal', result, result, baseline, before, after, **checks))
        self.assertFalse(supervisor.accepted_case('interrupted', result, result, baseline, before, after, **checks))
        for key in checks:
            with self.subTest(key=key):
                self.assertFalse(supervisor.accepted_case('normal', result, result, baseline, before, after,
                                                         **{**checks, key: False}))
        self.assertFalse(supervisor.accepted_case('normal', result, result, baseline, before,
                                                 {**after, '.aws/file': ('file',)}, **checks))
        self.assertFalse(supervisor.accepted_case('normal', result, result, baseline, before,
                                                 {**after, 'fixture.txt': ('changed',)}, **checks))

    def test_supervisor_requires_stable_binding_and_independent_cleanup(self):
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        control = supervisor.ProbeSupervisor(unit, 1003, 'interrupted')
        self.assertFalse(control.cleanup_verified())
        with patch.object(supervisor, 'bind_live_probe', side_effect=[(1234, 42, 3, 4), (1234, 43, 3, 4)]):
            self.assertEqual(control.observe(1), (1234, 42, 3, 4))
            with self.assertRaisesRegex(ValueError, 'identity_changed'):
                control.observe(1)
        with patch.object(supervisor, 'independent_cleanup', return_value=False) as check:
            self.assertFalse(control.cleanup_verified())
            check.assert_called_once_with(unit, 1234)
        with patch.object(supervisor, 'independent_cleanup', return_value=True):
            self.assertTrue(control.cleanup_verified())

    def test_preparer_delegates_to_locked_admission_and_fixed_inputs(self):
        with patch.object(supervisor, 'prepare', return_value={'status': 'created'}) as prepare:
            self.assertEqual(supervisor.prepare_candidate('/srv/fixture', 'worker', 1003, 1003, 4, 5, 6, 7),
                             {'status': 'created'})
        self.assertEqual(prepare.call_args.kwargs, {'_auth': supervisor.FAKE_AUTH, '_config': supervisor.FAKE_CONFIG})

    def test_bounded_read_consumes_short_reads_and_rejects_oversize(self):
        with patch('os.open', return_value=42), patch('os.close'), patch('os.read', side_effect=[b'ab', b'c', b'']):
            self.assertEqual(supervisor._read('/unused', 3), b'abc')
        with patch('os.open', return_value=42), patch('os.close'), patch('os.read', side_effect=[b'ab', b'cd']):
            with self.assertRaisesRegex(ValueError, 'oversized'):
                supervisor._read('/unused', 3)

    def test_fake_sources_are_versioned_and_creation_is_fresh(self):
        self.assertEqual(supervisor.FAKE_INPUT_VERSION, 1)
        self.assertEqual(json.loads(supervisor.FAKE_AUTH), {})
        self.assertEqual(tomllib.loads(supervisor.FAKE_CONFIG.decode()), {})
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            name = 'modelfc-zen69-fixture-' + 'a' * 32
            try:
                with patch('ops.bounded_worker.storage_fixture_prepare._allocated_extents'):
                    storage_fixture_prepare.create_at(fd, name, os.getuid(), os.getgid(), {},
                        _auth=supervisor.FAKE_AUTH, _config=supervisor.FAKE_CONFIG)
                root = parent / name
                receipt = json.loads((root / 'fixture-creation.json').read_text())
                ids = {key: value['id'] for key, value in receipt['sources'].items()}
                fresh = receipt['sources']
                self.assertEqual(len(storage_fixture.fake_sources(root / 'home', os.getuid(), ids, fresh,
                    _auth=supervisor.FAKE_AUTH, _config=supervisor.FAKE_CONFIG)), 3)
                with self.assertRaises(ValueError):
                    storage_fixture.fake_sources(root / 'home', os.getuid(), ids, fresh)
                with self.assertRaises(FileExistsError):
                    storage_fixture_prepare.create_at(fd, name, os.getuid(), os.getgid(), {},
                        _auth=supervisor.FAKE_AUTH, _config=supervisor.FAKE_CONFIG)
            finally:
                os.close(fd)

    def test_unique_live_namespace_pid_is_bound_to_exact_cgroup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proc, groups = root / 'proc', root / 'groups'
            proc.mkdir()
            groups.mkdir()
            unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
            group = groups / unit
            group.mkdir()
            (group / 'cgroup.procs').write_text('1234\n')
            python = root / 'python'
            python.write_bytes(b'fake')
            executable = python.stat()
            fake_python = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=executable.st_dev,
                st_ino=executable.st_ino, st_mode=stat.S_IFREG | 0o755, st_uid=0))
            pid = proc / '1234'
            pid.mkdir()
            (pid / 'stat').write_text('1234 (python) S ' + '0 ' * 18 + '42\n')
            (pid / 'status').write_text('Uid:\t1003\t1003\t1003\t1003\nNSpid:\t1234\t1\n')
            (pid / 'cgroup').write_text('0::/system.slice/' + unit + '\n')
            (pid / 'exe').symlink_to(python)
            (pid / 'ns').mkdir()
            (pid / 'ns/pid').write_text('fake namespace')
            args = [b'/usr/bin/python3', b'-I', b'-S', b'-B', b'-c', supervisor.PROBE_ARG.encode(), b'interrupted']
            (pid / 'cmdline').write_bytes(b'\0'.join(args) + b'\0')
            kw = {'_group_root': groups, '_proc_root': proc, '_python': fake_python, '_verify': lambda: True}
            identity = supervisor.bind_live_probe(unit, 1003, 1, 'interrupted', **kw)
            self.assertEqual(identity[:2], (1234, 42))
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                supervisor.bind_live_probe(unit, 1003, 2, 'interrupted', **kw)
            (pid / 'cgroup').write_text('0::/system.slice/other.service\n')
            with self.assertRaisesRegex(ValueError, 'cgroup_mismatch'):
                supervisor.bind_live_probe(unit, 1003, 1, 'interrupted', **kw)


if __name__ == '__main__':
    unittest.main()
