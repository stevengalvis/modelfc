import os
import errno
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from dataclasses import asdict, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker.storage import LinuxStorageReader, StorageConfiguration, admit, parse_mountinfo, service_properties, source_identity, _allocated_extents
from ops.bounded_worker.bridge_executor import Executor
from ops.bounded_worker import bridge_entry, launcher


class Reader:
    def __init__(self):
        self.mounts = '42 1 7:2 / /bound rw,nosuid,nodev - ext4 /dev/loop2 rw\n'
        self.identity = (2049, 99, 100000)
        self.backing = self.identity[:2]
        self.vfs = SimpleNamespace(f_blocks=100, f_frsize=512, f_bavail=50,
                                   f_files=1000, f_favail=500)

    def mountinfo(self):
        return self.mounts

    def protected_directory(self, path):
        return SimpleNamespace(st_dev=os.makedev(7, 2))

    def image_identity(self, path):
        return self.identity

    def backing_identity(self, device):
        return self.backing

    def statvfs(self, path):
        return self.vfs


class StorageAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.config = StorageConfiguration('/bound', '/secure/image', '/bound/work',
                                           '/bound/runtime', 2049, 99, 100000,
                                           51200, 1000, 1024, 10,
                                           '/home/worker/.codex/auth.json',
                                           '/home/worker/.codex/config.toml')
        self.reader = Reader()

    def test_admits_expected_mount(self):
        identity = admit(self.config, reader=self.reader)
        self.assertEqual(identity['mount_id'], 42)
        self.assertEqual(identity['image_inode'], 99)

    def test_mount_backing_nested_and_reserves_fail_closed(self):
        scenarios = [lambda r: setattr(r, 'mounts', ''),
                     lambda r: setattr(r, 'mounts', r.mounts.replace('ext4', 'xfs')),
                     lambda r: setattr(r, 'mounts', r.mounts.replace('/dev/loop2 rw', '/dev/loop2 rw,discard')),
                     lambda r: setattr(r, 'mounts', r.mounts.replace('/dev/loop2 rw', '/dev/loop2 rw,discard=async')),
                     lambda r: setattr(r, 'mounts', r.mounts.replace('7:2', '7:3')),
                     lambda r: setattr(r, 'mounts', r.mounts.replace('rw,nosuid,nodev', 'ro,nosuid,nodev')),
                     lambda r: setattr(r, 'mounts', r.mounts + r.mounts),
                     lambda r: setattr(r, 'identity', (2049, 99, 99999)),
                     lambda r: setattr(r, 'mounts', r.mounts + '43 42 7:3 / /bound/work/x rw - ext4 /dev/loop3 rw\n'),
                     lambda r: setattr(r, 'backing', (2049, 100)),
                     lambda r: setattr(r.vfs, 'f_bavail', 1),
                     lambda r: setattr(r.vfs, 'f_favail', 1),
                     lambda r: setattr(r.vfs, 'f_files', 999)]
        for change in scenarios:
            with self.subTest(change=change):
                reader = Reader()
                change(reader)
                with self.assertRaises(ValueError):
                    admit(self.config, reader=reader)

    def test_invalid_paths_and_limits_fail(self):
        for changes in ({'workspace': '/other/work'}, {'runtime': '/bound/work/runtime'},
                        {'image': '/bound/image'}, {'mount': '/bound/../elsewhere'},
                        {'reserve_inodes': 1000}, {'reserve_bytes': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                admit(replace(self.config, **changes), reader=self.reader)

    def test_kernel_loop_backing_identity_not_reopened_path(self):
        node = SimpleNamespace(st_mode=stat.S_IFBLK | 0o660, st_uid=0, st_gid=6, st_rdev=os.makedev(7, 2))
        def ioctl_for(inode, offset=0):
            def ioctl(_fd, request, buffer, _mutate):
                self.assertEqual(request, 0x4C05)
                struct.pack_into('=QQQQQ', buffer, 0, 2049, inode, 0, offset, 0)
            return ioctl
        reader = LinuxStorageReader(worker_uid=1003, worker_gid=1003)
        with patch.object(Path, 'resolve', return_value=Path('/sys/devices/virtual/block/loop2')), \
             patch.object(reader, 'protected_directory'), \
             patch('ops.bounded_worker.storage.os.open', return_value=10), \
             patch('ops.bounded_worker.storage.os.fstat', return_value=node), \
             patch('ops.bounded_worker.storage.os.close'), \
             patch('ops.bounded_worker.storage.os.getxattr', side_effect=OSError(errno.ENODATA, 'no acl')), \
             patch('ops.bounded_worker.storage.fcntl.ioctl', side_effect=ioctl_for(99)):
            self.assertEqual(reader.backing_identity('7:2'), (2049, 99))
        # The image pathname can still resolve to inode 99 after its mount is
        # overlaid, while the loop retains inode 100; admission must reject it.
        self.reader.backing = (2049, 100)
        with self.assertRaisesRegex(ValueError, 'storage_backing_mismatch'):
            admit(self.config, reader=self.reader)
        with patch.object(Path, 'resolve', return_value=Path('/sys/devices/virtual/block/loop2')), \
             patch.object(reader, 'protected_directory'), \
             patch('ops.bounded_worker.storage.os.open', return_value=10), \
             patch('ops.bounded_worker.storage.os.fstat', return_value=node), \
             patch('ops.bounded_worker.storage.os.close'), \
             patch('ops.bounded_worker.storage.os.getxattr', side_effect=OSError(errno.ENODATA, 'no acl')), \
             patch('ops.bounded_worker.storage.fcntl.ioctl', side_effect=ioctl_for(99, offset=4096)):
            with self.assertRaisesRegex(ValueError, 'unsupported_loop_extent'):
                reader.backing_identity('7:2')

    def test_worker_cannot_write_loop_via_primary_group_or_acl(self):
        node = SimpleNamespace(st_mode=stat.S_IFBLK | 0o660, st_uid=0, st_gid=6, st_rdev=os.makedev(7, 2))
        with patch.object(Path, 'resolve', return_value=Path('/sys/devices/virtual/block/loop2')), \
             patch.object(LinuxStorageReader, 'protected_directory'), \
             patch('ops.bounded_worker.storage.os.open', return_value=10), \
             patch('ops.bounded_worker.storage.os.fstat', return_value=node), \
             patch('ops.bounded_worker.storage.os.close'), \
             patch('ops.bounded_worker.storage.fcntl.ioctl') as ioctl:
            with self.assertRaisesRegex(ValueError, 'worker_can_write_loop_device'):
                LinuxStorageReader(worker_uid=1003, worker_gid=6).backing_identity('7:2')
            ioctl.assert_not_called()
            with patch('ops.bounded_worker.storage.os.getxattr', return_value=b'acl'):
                with self.assertRaisesRegex(ValueError, 'loop_acl_rejected'):
                    LinuxStorageReader(worker_uid=1003, worker_gid=1003).backing_identity('7:2')
            with patch('ops.bounded_worker.storage.os.getxattr', side_effect=OSError(errno.EOPNOTSUPP, 'unknown')):
                with self.assertRaisesRegex(ValueError, 'loop_acl_unverified'):
                    LinuxStorageReader(worker_uid=1003, worker_gid=1003).backing_identity('7:2')

    def test_linux_reader_rejects_unprotected_ancestors(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        unsafe = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)
        with patch.object(Path, 'lstat', side_effect=[directory, unsafe]):
            with self.assertRaisesRegex(ValueError, 'unprotected_storage_directory'):
                LinuxStorageReader().protected_directory('/safe/mount')

    def test_linux_reader_rejects_sparse_or_unsafe_image(self):
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1,
                               st_blocks=1, st_size=1024, st_dev=2049, st_ino=99)
        with patch.object(LinuxStorageReader, 'protected_directory'), \
             patch('ops.bounded_worker.storage.os.open', return_value=10) as opened, \
             patch('ops.bounded_worker.storage.os.fstat', return_value=info), \
             patch('ops.bounded_worker.storage.os.close'):
            with self.assertRaisesRegex(ValueError, 'unprotected_storage_image'):
                LinuxStorageReader().image_identity('/safe/image')
            self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)

    def test_fiemap_requires_fully_allocated_unshared_extents(self):
        header = struct.Struct('=QQIIII')
        extent = struct.Struct('=QQQQQIIII')
        def ioctl_for(logical, length, flags):
            def ioctl(_fd, _request, buffer, _mutate):
                header.pack_into(buffer, 0, 0, 1000, 0, 1, 128, 0)
                extent.pack_into(buffer, header.size, logical, 4096, length, 0, 0, flags, 0, 0, 0)
            return ioctl
        with patch('ops.bounded_worker.storage.fcntl.ioctl', side_effect=ioctl_for(0, 1000, 0x801)):
            _allocated_extents(10, 1000)
        for logical, length, flags in ((1, 999, 1), (0, 500, 1), (0, 1000, 0x2001)):
            with self.subTest(logical=logical, length=length, flags=flags), \
                 patch('ops.bounded_worker.storage.fcntl.ioctl', side_effect=ioctl_for(logical, length, flags)):
                with self.assertRaises(ValueError):
                    _allocated_extents(10, 1000)

    def test_credential_source_rejects_unsafe_identity(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
        good = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1,
                               st_dev=20, st_ino=30)
        with patch.object(Path, 'lstat', side_effect=[directory, directory, good]):
            self.assertEqual(source_identity('/home/auth', 1000), {'device': 20, 'inode': 30})
        for bad in (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=1000, st_nlink=1),
                    SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1001, st_nlink=1),
                    SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=2)):
            with self.subTest(bad=bad), patch.object(Path, 'lstat', side_effect=[directory, directory, bad]):
                with self.assertRaises(ValueError):
                    source_identity('/home/auth', 1000)

    def test_mountinfo_decodes_paths_and_rejects_bad_escapes(self):
        entry = parse_mountinfo(r'1 0 7:2 / /a\040b rw - ext4 /dev/loop2 rw')[0]
        self.assertEqual(entry['mount'], '/a b')
        with self.assertRaises(ValueError):
            parse_mountinfo(r'1 0 7:2 / /a\bad rw - ext4 /dev/loop2 rw')

    def test_service_properties_use_existing_mount_and_reject_aliases(self):
        args = service_properties(self.config, work='/bound/work/job',
                                  runtime='/bound/runtime/job', home='/home/worker')
        self.assertEqual(args, ['--property=ProtectSystem=strict', '--property=PrivateMounts=yes',
                         '--property=ReadOnlyPaths=/dev /sys',
                         '--property=ReadWritePaths=-/dev/pts -/dev/mqueue',
                         '--property=BindPaths=/bound/work/job:/bound/work/job '
                         '/bound/runtime/job/codex:/home/worker/.codex '
                         '/bound/runtime/job/tmp:/tmp /bound/runtime/job/var-tmp:/var/tmp',
                         '--property=BindReadOnlyPaths=/home/worker '
                         '/home/worker/.codex/auth.json:/home/worker/.codex/auth.json '
                         '/home/worker/.codex/config.toml:/home/worker/.codex/config.toml',
                         '--property=TemporaryFileSystem=/dev/shm:size=8M,nr_inodes=4096,mode=1777,nodev,nosuid,noexec'])
        self.assertFalse(any('MountImages' in arg for arg in args))
        with self.assertRaises(ValueError):
            service_properties(replace(self.config, auth_source='/safe/alias'),
                               work='/bound/work/job', runtime='/bound/runtime/job', home='/home/worker')
        with self.assertRaises(ValueError):
            service_properties(self.config, work='/bound/work/one',
                               runtime='/bound/runtime/two', home='/home/worker')

    def test_mount_replacement_changes_recorded_identity(self):
        before = admit(self.config, reader=self.reader)
        self.reader.mounts = self.reader.mounts.replace('42 1', '43 1')
        self.assertNotEqual(before, admit(self.config, reader=self.reader))

    def test_settings_default_off_and_trusted_storage_shape(self):
        settings = {'policy': '/safe/policy', 'state_root': '/safe/state',
                    'mirror': '/safe/mirror', 'workspace_root': '/bound/work',
                    'evidence_root': '/safe/evidence', 'publisher_root': '/safe/publisher',
                    'username': 'worker', 'executable': '/usr/bin/worker',
                    'executable_sha256': '0' * 64, 'reader_token_file': '/safe/read-token',
                    'publisher_token_file': '/safe/publish-token'}
        with patch.object(bridge_entry, 'protected_json', return_value=settings.copy()), \
             patch.object(bridge_entry, 'protected_dir'):
            self.assertNotIn('storage', bridge_entry._settings('/settings'))
        with patch.object(bridge_entry, 'protected_json',
                          return_value={**settings, 'storage': asdict(self.config)}), \
             patch.object(bridge_entry, 'protected_dir'):
            self.assertEqual(bridge_entry._settings('/settings')['storage'], self.config)
        with patch.object(bridge_entry, 'protected_json',
                          return_value={**settings, 'storage': {**asdict(self.config), 'privileged': True}}), \
             patch.object(bridge_entry, 'protected_dir'):
            with self.assertRaises(ValueError):
                bridge_entry._settings('/settings')

    def test_git_checkout_through_open_directory_fd(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, workspace = root / 'source', root / 'workspace'
            source.mkdir()
            workspace.mkdir()
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'config', 'user.name', 'Fixture'], check=True)
            subprocess.run(['git', '-C', str(source), 'config', 'user.email', 'fixture@example.invalid'], check=True)
            (source / 'file').write_text('fixture')
            subprocess.run(['git', '-C', str(source), 'add', 'file'], check=True)
            subprocess.run(['git', '-C', str(source), 'commit', '-qm', 'fixture'], check=True)
            revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD']).strip().decode()
            fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
            try:
                dest = Path('/proc/self/fd') / str(fd) / 'job'
                Executor._git('clone', '--no-local', '--no-checkout', '--', str(source), str(dest), pass_fds=(fd,))
                Executor._git('checkout', '--detach', revision, cwd=dest, pass_fds=(fd,))
                self.assertEqual((workspace / 'job' / 'file').read_text(), 'fixture')
            finally:
                os.close(fd)

    def test_bridge_admission_precedes_checkout(self):
        task = SimpleNamespace(validate=lambda: None)
        config = SimpleNamespace(username='worker', executable='/bin/worker', executable_sha256='0' * 64,
                                 workspace_root='/bound/work',
                                 storage=self.config)
        executor = object.__new__(Executor)
        executor.config, executor.mirror = config, '/mirror'
        with patch('ops.bounded_worker.bridge_executor.admit', side_effect=ValueError('blocked')) as admission, \
             patch.object(Executor, '_git') as git, \
             patch.object(launcher, 'Codex', return_value=SimpleNamespace(account=SimpleNamespace(pw_uid=1003, pw_gid=1003))):
            with self.assertRaises(ValueError):
                executor.run(task)
            admission.assert_called_once_with(self.config, worker_uid=1003, worker_gid=1003)
            git.assert_not_called()


if __name__ == '__main__':
    unittest.main()
