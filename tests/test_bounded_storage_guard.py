import os
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_guard


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.dev = os.makedev(7, 2)
        self.expected = {'device': '7:2', 'home': '/home/w', 'workspace': {'path': '/work', 'inode': 11},
                         'codex_home': {'path': '/home/w/.codex', 'inode': 12},
                         'tmp': {'path': '/tmp', 'inode': 13},
                         'var_tmp': {'path': '/var/tmp', 'inode': 14}, 'shm': '/dev/shm',
                         'auth': {'path': '/home/w/.codex/auth.json', 'device': 4, 'inode': 21},
                         'config': {'path': '/home/w/.codex/config.toml', 'device': 4, 'inode': 22}}
        self.mounts = [self.mount('/', '8:1', 'ext4', 'ro'),
                       self.mount('/dev', '0:3', 'devtmpfs', 'ro'),
                       self.mount('/sys', '0:7', 'sysfs', 'ro'),
                       self.mount('/home/w', '8:1', 'ext4', 'ro'),
                       *[self.mount(p, '7:2', 'ext4', 'rw') for p in
                         ('/work', '/home/w/.codex', '/tmp', '/var/tmp')],
                       self.mount('/dev/shm', '0:1', 'tmpfs', 'rw,nosuid,nodev,noexec'),
                       self.mount('/home/w/.codex/auth.json', '0:4', 'ext4', 'ro'),
                       self.mount('/home/w/.codex/config.toml', '0:4', 'ext4', 'ro')]
        self.vfs = lambda path: SimpleNamespace(f_blocks=2048, f_frsize=4096, f_files=4096)
        self.infos = {item['path']: SimpleNamespace(st_dev=self.dev, st_ino=item['inode'],
                                                   st_mode=stat.S_IFDIR | 0o700)
                      for item in (self.expected[k] for k in ('workspace', 'codex_home', 'tmp', 'var_tmp'))}
        self.infos['/dev'] = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        for key in ('auth', 'config'):
            item = self.expected[key]
            self.infos[item['path']] = SimpleNamespace(st_dev=4, st_ino=item['inode'],
                                                       st_mode=stat.S_IFREG | 0o600)

    @staticmethod
    def mount(path, device, kind, options):
        return {'path': path, 'device': device, 'type': kind, 'options': options.split(','), 'root': '/', 'super_options': ['rw']}

    def test_expected_namespace_passes(self):
        storage_guard.verify(self.expected, self.mounts, self.infos.__getitem__, self.vfs)

    def test_wrong_mount_or_file_fails(self):
        for index, field, value in ((0, 'options', ['rw']), (1, 'options', ['rw']), (3, 'options', ['rw']),
                                    (4, 'device', '7:3'), (8, 'type', 'ext4'),
                                    (9, 'options', ['rw']), (4, 'super_options', ['rw', 'discard'])):
            with self.subTest(index=index):
                mounts = [dict(m) for m in self.mounts]
                mounts[index][field] = value
                with self.assertRaises(ValueError):
                    storage_guard.verify(self.expected, mounts, self.infos.__getitem__, self.vfs)
        self.infos['/home/w/.codex/auth.json'].st_ino = 999
        with self.assertRaises(ValueError):
            storage_guard.verify(self.expected, self.mounts, self.infos.__getitem__, self.vfs)

    def test_working_directory_mount_matches_bound_workspace(self):
        mounts = [dict(m, id=42 if m['path'] == '/work' else 1) for m in self.mounts]
        storage_guard.verify_cwd(self.expected, mounts, '/work', 'pos:\t0\nmnt_id:\t42\n')
        with self.assertRaises(ValueError):
            storage_guard.verify_cwd(self.expected, mounts, '/work', 'mnt_id:\t43\n')
        with self.assertRaises(ValueError):
            storage_guard.verify_cwd(self.expected, mounts, '/other', 'mnt_id:\t42\n')

    def test_sys_and_descendants_must_be_read_only(self):
        storage_guard.verify(self.expected, self.mounts, self.infos.__getitem__, self.vfs)
        for path in ('/sys', '/sys/devices', '/sys/fs/cgroup'):
            with self.subTest(path=path):
                mounts = [dict(m) for m in self.mounts]
                if path == '/sys':
                    next(m for m in mounts if m['path'] == path)['options'] = ['rw']
                else:
                    mounts.append(self.mount(path, '0:8', 'sysfs', 'rw'))
                with self.assertRaisesRegex(ValueError, 'unexpected_writable_mount'):
                    storage_guard.verify(self.expected, mounts, self.infos.__getitem__, self.vfs)
                if path != '/sys':
                    mounts[-1]['options'] = ['ro']
                    storage_guard.verify(self.expected, mounts, self.infos.__getitem__, self.vfs)

    def test_nested_mount_rejected(self):
        mounts = self.mounts + [self.mount('/work/escape', '8:1', 'ext4', 'rw')]
        with self.assertRaises(ValueError):
            storage_guard.verify(self.expected, mounts, self.infos.__getitem__, self.vfs)

    def test_explicit_api_mounts_are_permitted(self):
        mounts = self.mounts + [self.mount('/dev/pts', '0:4', 'devpts', 'rw'),
                                self.mount('/dev/mqueue', '0:5', 'mqueue', 'rw'),
                                self.mount('/proc', '0:6', 'proc', 'rw')]
        storage_guard.verify(self.expected, mounts, self.infos.__getitem__, self.vfs)

    def test_world_writable_device_directory_rejected(self):
        self.infos['/dev'].st_mode = stat.S_IFDIR | 0o777
        with self.assertRaisesRegex(ValueError, 'unprotected_device_directory'):
            storage_guard.verify(self.expected, self.mounts, self.infos.__getitem__, self.vfs)

    def test_unexpected_writable_api_descendants_rejected(self):
        for path in ('/dev/data', '/proc/unexpected', '/sys/unexpected'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                storage_guard.verify(self.expected, self.mounts + [self.mount(path, '8:1', 'ext4', 'rw')],
                                     self.infos.__getitem__, self.vfs)

    def test_predicate_diagnostic_is_fixed_and_failure_stays_closed(self):
        cases = [
            (storage_guard.GuardFailure('unexpected_writable_mount'), b'storage_guard:mounts:unexpected_writable_mount\n'),
            (storage_guard.GuardFailure('secret/\x1b[31m'), b'storage_guard:mounts\n'),
            (ValueError('unexpected_writable_mount'), b'storage_guard:mounts\n'),
            (OSError('secret/\x1b[31m'), b'storage_guard:mounts\n'),
        ]
        for failure, expected in cases:
            with self.subTest(failure=failure), \
                 patch.object(storage_guard.sys, 'argv', ['guard', '{}', '--', '/bin/false']), \
                 patch.object(storage_guard.os, 'getuid', return_value=0), \
                 patch.object(storage_guard.os, 'geteuid', return_value=0), \
                 patch.object(storage_guard, 'no_extra_descriptors'), \
                 patch.object(storage_guard.Path, 'read_text', return_value=''), \
                 patch.object(storage_guard, 'verify', side_effect=failure), \
                 patch.object(storage_guard.os, 'execv') as execute, \
                 patch.object(storage_guard.os, 'write') as write:
                self.assertEqual(storage_guard.main(), 127)
                execute.assert_not_called()
                write.assert_called_once_with(2, expected)

    def test_real_verify_reports_writable_sys_category_without_path(self):
        mounts = [dict(m) for m in self.mounts]
        next(m for m in mounts if m['path'] == '/sys')['options'] = ['rw']
        with patch.object(storage_guard.sys, 'argv', ['guard', '{}', '--', '/bin/false']), \
             patch.object(storage_guard.os, 'getuid', return_value=0), \
             patch.object(storage_guard.os, 'geteuid', return_value=0), \
             patch.object(storage_guard, 'no_extra_descriptors'), \
             patch.object(storage_guard.Path, 'read_text', return_value=''), \
             patch.object(storage_guard.json, 'loads', return_value=self.expected), \
             patch.object(storage_guard, '_mounts', return_value=mounts), \
             patch.object(storage_guard.os, 'stat', side_effect=self.infos.__getitem__), \
             patch.object(storage_guard.os, 'statvfs', side_effect=self.vfs), \
             patch.object(storage_guard.os, 'execv') as execute, \
             patch.object(storage_guard.os, 'write') as write:
            # verify's default statvfs is bound at definition, so pass the fake explicitly.
            original = storage_guard.verify
            with patch.object(storage_guard, 'verify', side_effect=lambda e, m, st: original(e, m, st, self.vfs)):
                self.assertEqual(storage_guard.main(), 127)
            execute.assert_not_called()
            write.assert_called_once_with(2, b'storage_guard:mounts:unexpected_writable_mount\n')

    def test_diagnostic_write_failure_still_returns_127(self):
        with patch.object(storage_guard.sys, 'argv', ['guard', '{}', '--', '/bin/false']), \
             patch.object(storage_guard.os, 'getuid', return_value=0), \
             patch.object(storage_guard.os, 'geteuid', return_value=0), \
             patch.object(storage_guard, 'no_extra_descriptors'), \
             patch.object(storage_guard.Path, 'read_text', return_value=''), \
             patch.object(storage_guard.os, 'write', side_effect=OSError('closed')), \
             patch.object(storage_guard.os, 'execv') as execute:
            self.assertEqual(storage_guard.main(), 127)
            execute.assert_not_called()

    def test_failed_guard_never_executes_child(self):
        with patch.object(storage_guard.sys, 'argv', ['guard', '{}', '--', '/bin/false']), \
             patch.object(storage_guard.os, 'getuid', return_value=0), \
             patch.object(storage_guard.os, 'geteuid', return_value=0), \
             patch.object(storage_guard, 'no_extra_descriptors'), \
             patch.object(storage_guard.Path, 'read_text', return_value=''), \
             patch.object(storage_guard.os, 'execv') as execute, \
             patch.object(storage_guard.os, 'write') as write:
            self.assertEqual(storage_guard.main(), 127)
            execute.assert_not_called()
            write.assert_called_once_with(2, b'storage_guard:mounts:invalid_guard_expectation\n')


if __name__ == '__main__':
    unittest.main()
