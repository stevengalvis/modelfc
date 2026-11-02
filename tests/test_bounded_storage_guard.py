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
        for index, field, value in ((0, 'options', ['rw']), (1, 'options', ['rw']), (2, 'options', ['rw']),
                                    (3, 'device', '7:3'), (7, 'type', 'ext4'),
                                    (8, 'options', ['rw']), (3, 'super_options', ['rw', 'discard'])):
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

    def test_failed_guard_never_executes_child(self):
        with patch.object(storage_guard.sys, 'argv', ['guard', '{}', '--', '/bin/false']), \
             patch.object(storage_guard.os, 'getuid', return_value=0), \
             patch.object(storage_guard.os, 'geteuid', return_value=0), \
             patch.object(storage_guard, 'no_extra_descriptors'), \
             patch.object(storage_guard.Path, 'read_text', return_value=''), \
             patch.object(storage_guard.os, 'execv') as execute:
            self.assertEqual(storage_guard.main(), 127)
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
