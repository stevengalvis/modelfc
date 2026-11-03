"""Offline contract tests; these do not run systemd, a mount or an exhaustion test."""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_fixture as fixture
from ops.bounded_worker import storage_fixture_probe as probe


class FixtureTests(unittest.TestCase):
    def test_only_explicit_disposable_root_name_is_accepted(self):
        for path in ('/srv/modelfc-bounded', '/tmp/modelfc-zen69-fixture-' + 'a' * 32,
                     '/srv/modelfc-zen69-fixture-' + 'a' * 31):
            with self.subTest(path=path), self.assertRaises(ValueError):
                fixture.checked_root(path)
        with patch.object(fixture.LinuxStorageReader, 'protected_directory'):
            self.assertEqual(str(fixture.checked_root('/srv/modelfc-zen69-fixture-' + 'a' * 32)),
                             '/srv/modelfc-zen69-fixture-' + 'a' * 32)

    def test_real_root_and_exact_loop_device_are_required(self):
        with patch.object(fixture.os, 'getuid', return_value=1000):
            with self.assertRaisesRegex(ValueError, 'root_and_loop_required'):
                fixture.run('/srv/modelfc-zen69-fixture-' + 'a' * 32, 'worker', '/dev/loop0', '/root/inventory', '0' * 64)
        with patch.object(fixture.os, 'getuid', return_value=0), patch.object(fixture.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'root_and_loop_required'):
                fixture.run('/srv/modelfc-zen69-fixture-' + 'a' * 32, 'worker', '/dev/sda', '/root/inventory', '0' * 64)

    def test_fake_sources_reject_real_or_changed_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'home'
            home.mkdir(mode=0o700)
            (home / '.codex').mkdir(mode=0o700)
            for name, data in (('.codex/auth.json', probe.AUTH), ('.codex/config.toml', probe.CONFIG),
                               ('outside-sentinel', b'zen69-outside\n')):
                path = home / name
                path.write_bytes(data)
                path.chmod(0o600)
            expected = {}
            for name, path in (('auth', home / '.codex/auth.json'), ('config', home / '.codex/config.toml'),
                               ('sentinel', home / 'outside-sentinel')):
                info = path.stat()
                expected[name] = [info.st_dev, info.st_ino]
            self.assertEqual(len(fixture.fake_sources(home, os.getuid(), expected)), 3)
            wrong = {**expected, 'auth': [expected['auth'][0], expected['auth'][1] + 1]}
            with self.assertRaisesRegex(ValueError, 'fake_source_identity_rejected'):
                fixture.fake_sources(home, os.getuid(), wrong)
            (home / '.codex/auth.json').write_bytes(b'not-a-fake')
            with self.assertRaises(ValueError):
                fixture.fake_sources(home, os.getuid(), expected)

    def test_fake_sources_reject_symlinked_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / 'outside'
            outside.mkdir()
            (root / 'home').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                fixture.fake_sources(root / 'home', os.getuid(), {})

    def test_enospc_without_zero_availability_does_not_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(probe.os, 'write', side_effect=OSError(errno.ENOSPC, 'fixture')), \
                 patch.object(probe.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=5)):
                self.assertEqual(probe.exhaust_bytes(root, 4096), (False, 5))
            with patch.object(probe.os, 'open', side_effect=OSError(errno.ENOSPC, 'fixture')), \
                 patch.object(probe.os, 'statvfs', return_value=SimpleNamespace(f_favail=5)):
                self.assertEqual(probe.exhaust_inodes(root, 4), (False, 5))
            self.assertEqual(list(root.iterdir()), [])

    def test_zero_length_write_fails_and_cleans_fixture_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(probe.os, 'write', return_value=0):
                with self.assertRaisesRegex(ValueError, 'zero_length_write'):
                    probe.exhaust_bytes(root, 4096)
            self.assertEqual(list(root.iterdir()), [])

    def test_fixture_topology_rejects_overmounts_and_wrong_mount_id(self):
        root = Path('/srv/modelfc-zen69-fixture-' + 'a' * 32)
        base = f'42 1 7:2 / {root}/fs rw,nosuid,nodev - ext4 /dev/loop2 rw\n'
        self.assertEqual(fixture.verify_topology(root, base, 42), 42)
        for extra in (f'43 1 8:1 / {root}/fixture.img rw - ext4 /dev/sda1 rw\n',
                      f'44 1 8:1 / {root}/home/.codex/auth.json ro - ext4 /dev/sda1 rw\n'):
            with self.assertRaisesRegex(ValueError, 'unexpected_fixture_mount'):
                fixture.verify_topology(root, base + extra, 42)
        with self.assertRaisesRegex(ValueError, 'fixture_mount_identity_mismatch'):
            fixture.verify_topology(root, base, 43)

    def test_manifest_requires_pinned_separate_production_inventory(self):
        root = Path('/srv/modelfc-zen69-fixture-' + 'a' * 32)
        paths = ['/srv/modelfc-bounded', '/srv/modelfc-bounded-images', '/srv/modelfc-worker-work',
                 '/srv/modelfc', '/var/lib/modelfc', '/etc/modelfc', '/root']
        inventory = {'version': 1, 'protected_images': [[1, 2]], 'protected_files': [[1, 3]],
                     'protected_paths': paths, 'bridge_lock': {'path': '/root/state/bridge.lock', 'device': 7, 'inode': 8},
                     'worker': {'username': 'worker', 'uid': 1003, 'gid': 1003}}
        raw = json.dumps(inventory).encode()
        digest = hashlib.sha256(raw).hexdigest()
        manifest = {'version': 1, 'fixture_root': str(root), 'image': [3, 4], 'mount_id': 42,
                    'sources': {'auth': [5, 6], 'config': [5, 7], 'sentinel': [5, 8]},
                    'inventory_sha256': digest}
        with patch.object(fixture, 'protected_bytes', return_value=raw), \
             patch.object(fixture, 'protected_json', return_value=manifest):
            self.assertEqual(fixture.load_manifest(root, '/root/inventory.json', digest), (manifest, inventory['bridge_lock'], inventory['worker']))
            with self.assertRaisesRegex(ValueError, 'inventory_digest_mismatch'):
                fixture.load_manifest(root, '/root/inventory.json', '0' * 64)
            with patch.object(fixture, 'protected_json', return_value={**manifest, 'image': [1, 2]}):
                with self.assertRaisesRegex(ValueError, 'fixture_identity_rejected'):
                    fixture.load_manifest(root, '/root/inventory.json', digest)

    def test_cgroup_view_rejects_missing_mount_or_namespace_mismatch(self):
        with patch.object(Path, 'read_text', return_value='systemd\n'), \
             patch.object(fixture.os, 'stat', return_value=SimpleNamespace(st_dev=1, st_ino=2)), \
             patch.object(fixture.LinuxStorageReader, 'mountinfo',
                          return_value='10 1 0:2 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n'), \
             patch.object(Path, 'is_file', return_value=True):
            self.assertTrue(fixture.cgroup_view_verified())
            with patch.object(fixture.LinuxStorageReader, 'mountinfo', return_value=''):
                self.assertFalse(fixture.cgroup_view_verified())
            with patch.object(fixture.os, 'stat', side_effect=[SimpleNamespace(st_dev=1, st_ino=1),
                                                              SimpleNamespace(st_dev=1, st_ino=2)]):
                self.assertFalse(fixture.cgroup_view_verified())

    def test_post_cleanup_reinspection_blocks_replaced_mount(self):
        identity = {'mount_id': 42}
        worker = SimpleNamespace(account=SimpleNamespace(pw_uid=1003, pw_gid=1003))
        config = SimpleNamespace(storage=object())
        result = {'status': 'completed'}
        with patch.object(fixture.LinuxStorageReader, 'mountinfo', return_value=''), \
             patch.object(fixture, 'verify_topology'), \
             patch.object(fixture, 'admit', return_value={'mount_id': 43}), \
             patch.object(fixture.launcher, 'inspect') as inspect:
            self.assertFalse(fixture.reinspect_after_cleanup(Path('/srv/fixture'), config, identity, worker, result))
            inspect.assert_not_called()

    def test_receipt_reader_rejects_sidecars_and_wal_before_sqlite_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / 'bridge.lock'
            lock.touch(mode=0o600)
            lock.chmod(0o600)
            dbpath = root / 'receipts.sqlite'
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE fixture (x INTEGER)')
            dbpath.chmod(0o600)
            info = lock.stat()
            record = {'path': str(lock), 'device': info.st_dev, 'inode': info.st_ino}
            actual_fstat = os.fstat
            def root_owned(fd):
                info = actual_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink,
                                       st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                                       st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns)
            with patch.object(fixture, 'protected_dir'), patch.object(fixture.os, 'fstat', side_effect=root_owned), \
                 patch.object(fixture, 'safe', return_value=True):
                with fixture.hold_bridge_lock(record):
                    pass
                sidecar = Path(str(dbpath) + '-journal')
                sidecar.touch()
                with self.assertRaisesRegex(ValueError, 'bridge_receipts_sidecar_present'):
                    with fixture.hold_bridge_lock(record):
                        pass
                sidecar.unlink()
                with sqlite3.connect(dbpath) as db:
                    db.execute('PRAGMA journal_mode=WAL')
                with self.assertRaisesRegex(ValueError, 'bridge_receipts_journal_unverified'):
                    with fixture.hold_bridge_lock(record):
                        pass

    def test_unknown_or_remaining_worker_units_block_before_fixture(self):
        with patch.object(fixture, 'cgroup_view_verified', return_value=False):
            with self.assertRaisesRegex(ValueError, 'fixture_cgroup_view_unverified'):
                fixture.require_no_worker_units()
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(Path, 'is_dir', return_value=False), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '')):
            with self.assertRaisesRegex(ValueError, 'fixture_worker_boundary_not_clear'):
                fixture.require_no_worker_units()
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'unit.service loaded active running')):
            with self.assertRaisesRegex(ValueError, 'fixture_worker_boundary_not_clear'):
                fixture.require_no_worker_units()

    def test_existing_worker_process_blocks(self):
        class Scan:
            def __enter__(self):
                return iter([SimpleNamespace(name='123')])
            def __exit__(self, *args):
                return False
        with patch.object(fixture.os, 'scandir', return_value=Scan()), \
             patch.object(Path, 'read_text', return_value='Name:\tfixture\nUid:\t1003\t1003\t1003\t1003\n'):
            with self.assertRaisesRegex(ValueError, 'fixture_worker_process_exists'):
                fixture.require_no_worker_processes(1003)

    def test_worker_cgroup_scan_error_blocks(self):
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(Path, 'is_dir', return_value=True), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '')), \
             patch.object(fixture.os, 'scandir', side_effect=OSError(errno.EIO, 'fixture')):
            with self.assertRaises(OSError):
                fixture.require_no_worker_units()

    def test_submission_failure_attempts_independent_reconciliation(self):
        root = Path('/srv/modelfc-zen69-fixture-' + 'a' * 32)
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_name='worker')
        storage = SimpleNamespace(image_bytes=33554432, filesystem_inodes=512)
        config = SimpleNamespace(storage=storage, workspace_root=str(root / 'fs/workspaces'),
                                 evidence_root=str(root / 'evidence'))
        identity = {'device': '7:2', 'mount_id': 42}
        sources = [(4, 5), (4, 6), (4, 7)]
        manifest = {'sources': {}}
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        node = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(7, 2))
        with patch.object(fixture, 'require_no_worker_units'), \
             patch.object(fixture, 'require_no_worker_processes'), \
             patch.object(fixture.pwd, 'getpwnam', return_value=account), \
             patch.object(fixture, 'fixture_configuration', return_value=(config, identity, sources)), \
             patch.object(fixture.os, 'stat', return_value=node), \
             patch.object(fixture, 'probe_identity', return_value=(1, 2)), \
             patch.object(fixture, 'create_checkout', return_value='a' * 40), \
             patch.object(fixture.launcher, 'submit', side_effect=RuntimeError('fixture')), \
             patch.object(fixture.launcher, '_read', return_value={'unit': unit}), \
             patch.object(fixture, 'independent_cleanup', return_value=False) as cleanup, \
             patch.object(fixture, 'fake_sources', return_value=sources):
            result = fixture.run_locked(root, 'worker', '/dev/loop2', manifest, {'username': 'worker', 'uid': 1003, 'gid': 1003})
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['independent_cleanup'], 'unverified')
            self.assertEqual(result['unit'], unit)
            cleanup.assert_called_once_with(unit, None)

    def test_byte_exhaustion_removes_only_created_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_write = os.write
            calls = []
            def write(fd, data):
                calls.append(1)
                if len(calls) == 2:
                    raise OSError(errno.ENOSPC, 'fixture')
                return real_write(fd, data[:32])
            with patch.object(probe.os, 'write', side_effect=write), \
                 patch.object(probe.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=0)):
                self.assertEqual(probe.exhaust_bytes(root, 4096), (True, 0))
            self.assertEqual(list(root.iterdir()), [])

    def test_inode_exhaustion_removes_only_created_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_open = os.open
            calls = []
            def open_file(path, flags, *args, **kwargs):
                calls.append(1)
                if len(calls) == 3:
                    raise OSError(errno.ENOSPC, 'fixture')
                return real_open(path, flags, *args, **kwargs)
            with patch.object(probe.os, 'open', side_effect=open_file), \
                 patch.object(probe.os, 'statvfs', return_value=SimpleNamespace(f_favail=0)):
                self.assertEqual(probe.exhaust_inodes(root, 4), (True, 0))
            self.assertEqual(list(root.iterdir()), [])

    def test_independent_cleanup_requires_quiescent_unit_and_absent_pid(self):
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        good = ('LoadState=loaded\nActiveState=inactive\nMainPID=0\nControlPID=0\n'
                'ControlGroup=\nJob=\nTransient=yes\n')
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, good)), \
             patch.object(Path, 'lstat', side_effect=FileNotFoundError), \
             patch.object(fixture.os, 'stat', side_effect=FileNotFoundError):
            self.assertTrue(fixture.independent_cleanup(unit, 12345))
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, good.replace('Job=\n', 'Job=3\n'))), \
             patch.object(Path, 'exists', return_value=False):
            self.assertFalse(fixture.independent_cleanup(unit, 12345))
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, good.replace('MainPID=0\n', ''))), \
             patch.object(Path, 'exists', return_value=False):
            self.assertFalse(fixture.independent_cleanup(unit, 12345))
        with patch.object(fixture, 'cgroup_view_verified', return_value=True), \
             patch.object(fixture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, good)), \
             patch.object(Path, 'lstat', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                fixture.independent_cleanup(unit, 12345)
        self.assertFalse(fixture.independent_cleanup('unrelated.service', 12345))

    def test_probe_rejects_incomplete_or_effective_capabilities(self):
        status = ('CapInh:\t0000\nCapPrm:\t0000\nCapEff:\t0000\nCapBnd:\t0000\n'
                  'CapAmb:\t0000\nNoNewPrivs:\t1\n')
        with patch.object(probe.os, 'getuid', return_value=1003), \
             patch.object(probe.os, 'geteuid', return_value=1003), \
             patch.object(probe.os, 'getgid', return_value=1003), \
             patch.object(probe.os, 'getegid', return_value=1003), \
             patch.object(probe.os, 'getgroups', return_value=[]), \
             patch.object(Path, 'read_text', return_value=status):
            probe.verify_privileges(1003, 1003)
            with patch.object(Path, 'read_text', return_value=status.replace('CapEff:\t0000', 'CapEff:\t0001')):
                with self.assertRaisesRegex(ValueError, 'worker_privilege_drop_unverified'):
                    probe.verify_privileges(1003, 1003)
            with patch.object(probe.os, 'getgroups', return_value=[6]):
                with self.assertRaisesRegex(ValueError, 'unexpected_worker_identity'):
                    probe.verify_privileges(1003, 1003)

    def test_probe_requires_actual_worker_uid(self):
        with patch.object(probe.os, 'getuid', return_value=0), patch.object(probe.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'unexpected_worker_identity'):
                probe.run('/work', '/home', '/dev/loop0', 1003, 1003, 100, 100, [1, 2], [1, 2])


if __name__ == '__main__':
    unittest.main()
