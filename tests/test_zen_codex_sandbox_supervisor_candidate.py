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
from unittest.mock import patch

from ops.bounded_worker import codex_sandbox_supervisor_candidate as supervisor
from ops.bounded_worker import storage_fixture
from ops.bounded_worker import storage_fixture_prepare
from ops.bounded_worker import launcher


class SupervisorTests(unittest.TestCase):
    def test_run_candidate_requires_both_launcher_and_supervisor_acceptance(self):
        root = Path('/srv/modelfc-zen69-fixture-' + 'a' * 32)
        account = SimpleNamespace(pw_name='worker', pw_uid=1003, pw_gid=1003)
        config = launcher.Configuration(str(root / 'fs/workspaces'), str(root / 'evidence'),
                                        'worker', '/usr/bin/python3', '0' * 64)
        manifest = {'mount_id': 8, 'sources': {}}
        expected = {'username': 'worker', 'uid': 1003, 'gid': 1003}
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
                 'network_observation': True}
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
            worker.backend = SimpleNamespace(unit=unit, network_observation={'unit': unit})
            if state['launch_error']:
                raise ValueError('after_launch')
            return result
        def read_evidence(path):
            if path.name == 'containment.json':
                return {'unit': unit if state['unit'] else 'other'}
            if path.name == 'diagnostic-network.json':
                return {'unit': unit, 'extra_property': supervisor.NETWORK_PROPERTY if state['network_record'] else 'changed'}
            if path.name == 'diagnostic-network-observed.json':
                return {'unit': unit if state['network_observation'] else 'other'}
            return baseline if state['baseline'] else {'status': 'unverified'}
        def protected(path, **kwargs):
            return b'receipt' if path.name == 'fixture-creation.json' else (record if state['record'] else b'changed')
        snapshots = iter([value for _ in range(20) for value in (before, after)])
        def make_binary(*args):
            calls = iter([(1,), (1,) if state['binary'] else (2,)])
            return SimpleNamespace(_binary=lambda: next(calls))
        def exercise():
            args = (root, 'worker', '/dev/loop1', '/root/inventory', 'b' * 64, '/opt/codex', 'c' * 64, 'normal')
            self.assertEqual(supervisor.run_candidate(*args)['status'], 'passed')
            result['status'] = 'timeout'
            self.assertEqual(supervisor.run_candidate(*args[:-1], 'interrupted')['status'], 'passed')
            result['status'] = 'completed'
            for key in state:
                with self.subTest(key=key):
                    state[key] = not state[key]
                    self.assertEqual(supervisor.run_candidate(*args)['status'], 'blocked')
                    state[key] = not state[key]
            self.assertEqual(lock_counts, {'entered': 15, 'exited': 15})
        with patch.object(supervisor.os, 'getuid', return_value=0), \
             patch.object(supervisor.os, 'geteuid', return_value=0), \
             patch.object(supervisor.os, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(7, 1))), \
             patch.object(supervisor.storage_fixture, 'checked_root', return_value=root), \
             patch.object(supervisor.storage_fixture, 'verify_topology', return_value=8), \
             patch.object(supervisor.storage_fixture, 'load_manifest', return_value=(manifest, {}, expected, {}, 'component')), \
             patch.object(supervisor.storage_fixture, 'hold_bridge_lock', side_effect=hold), \
             patch.object(supervisor.storage_fixture, 'require_no_worker_units'), \
             patch.object(supervisor.storage_fixture, 'require_no_worker_processes'), \
             patch.object(supervisor.pwd, 'getpwnam', return_value=account), \
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
            backend.configure_storage(properties=['--property=ReadOnlyPaths=/'], expected={},
                                      guard_path='/opt/guard', revalidate=lambda: None)
            self.assertIn(supervisor.NETWORK_PROPERTY, backend.storage[0])
            backend._record()
            record = json.loads((root / 'diagnostic-network.json').read_text())
            self.assertEqual(record, {'unit': backend.unit, 'extra_property': supervisor.NETWORK_PROPERTY})
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
                      network_fence_verified=True, binary_verified=True, observed_unit_matches=True)
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
