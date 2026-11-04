"""Offline checks for fake input and host-process observation helpers."""
import json
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


class SupervisorTests(unittest.TestCase):
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
