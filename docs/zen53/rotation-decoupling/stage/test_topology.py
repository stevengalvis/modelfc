"""Synthetic sysfs topology tests; never inspect the executing host's devices."""
import os
import pathlib
import stat
import tempfile
import types
import unittest
from unittest import mock

import operator_entry


class DirectTopologyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'sys'
        self.disk = (self.root / 'devices/pci0000:00/0000:00:01.0'
                     / 'block/sda')
        self.partition = self.disk / 'sda1'
        self.partition.mkdir(parents=True)
        (self.disk / 'dev').write_text('8:0\n', encoding='ascii')
        (self.partition / 'dev').write_text('8:1\n', encoding='ascii')
        (self.partition / 'partition').write_text('1\n', encoding='ascii')
        (self.disk / 'slaves').mkdir()
        (self.root / 'dev/block').mkdir(parents=True)
        (self.root / 'block').mkdir()
        (self.root / 'dev/block/8:0').symlink_to(self.disk)
        (self.root / 'dev/block/8:1').symlink_to(self.partition)
        (self.root / 'block/sda').symlink_to(self.disk)

    def check(self, device='8:1', *, disk_node=None):
        original = os.lstat
        def lstat(path):
            if str(path) in ('/dev/sda', '/dev/nvme0n1'):
                major_minor = (8, 0) if str(path) == '/dev/sda' else (259, 0)
                return disk_node or types.SimpleNamespace(
                    st_mode=stat.S_IFBLK | 0o600,
                    st_rdev=os.makedev(*major_minor), st_dev=3, st_ino=41,
                    st_uid=0, st_gid=0, st_nlink=1, st_ctime_ns=1)
            return original(path)
        with mock.patch.object(operator_entry.os, 'lstat', side_effect=lstat):
            return operator_entry._verify_direct_block_topology(
                device, str(self.root))

    def test_direct_partition_and_whole_disk(self):
        self.assertEqual(self.check('8:1'), ('8:0', '/dev/sda', '/dev/sda1'))
        self.assertEqual(self.check('8:0'), ('8:0', '/dev/sda', '/dev/sda'))

    def test_partition_has_no_slaves_but_parent_must_have_empty_directory(self):
        (self.disk / 'slaves').rmdir()
        with self.assertRaises(FileNotFoundError):
            self.check()
        (self.disk / 'slaves').mkdir()
        (self.disk / 'slaves/loop0').touch()
        with self.assertRaisesRegex(ValueError, 'COMPLEX_BLOCK_DEVICE'):
            self.check()

    def test_parent_slaves_symlink_is_rejected(self):
        (self.disk / 'slaves').rmdir()
        (self.disk / 'slaves').symlink_to(self.root / 'block')
        with self.assertRaisesRegex(ValueError, 'COMPLEX_BLOCK_DEVICE'):
            self.check()

    def test_missing_parent_link_or_mismatched_parent_identity(self):
        (self.root / 'dev/block/8:0').unlink()
        with self.assertRaises(FileNotFoundError):
            self.check()
        (self.root / 'dev/block/8:0').symlink_to(self.disk)
        (self.disk / 'dev').write_text('8:2\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'ORIGIN_BLOCK_NODE'):
            self.check()

    def test_partition_number_or_device_identity_mismatch(self):
        (self.partition / 'partition').write_text('11\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
            self.check()
        (self.partition / 'partition').write_text('1\n', encoding='ascii')
        (self.partition / 'dev').write_text('8:2\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
            self.check()

    def test_rejects_partition_with_unexpected_slaves_or_parent_partition(self):
        (self.partition / 'slaves').mkdir()
        with self.assertRaisesRegex(ValueError, 'COMPLEX_BLOCK_DEVICE'):
            self.check()
        (self.partition / 'slaves').rmdir()
        (self.disk / 'partition').write_text('1\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
            self.check()

    def test_rejects_non_symlink_and_redirected_device_link(self):
        (self.root / 'dev/block/8:1').unlink()
        (self.root / 'dev/block/8:1').write_text('8:1\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
            self.check()
        (self.root / 'dev/block/8:1').unlink()
        (self.root / 'dev/block/8:1').symlink_to(self.root / 'block/sda')
        with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
            self.check()

    def test_rejects_layered_virtual_and_unexpected_disk_names(self):
        virtual = self.root / 'devices/virtual/block/dm-0'
        virtual.mkdir(parents=True)
        (virtual / 'dev').write_text('253:0\n', encoding='ascii')
        (self.root / 'dev/block/253:0').symlink_to(virtual)
        with self.assertRaisesRegex(ValueError, 'COMPLEX_BLOCK_DEVICE'):
            self.check('253:0')
        (self.root / 'dev/block/8:1').unlink()
        unknown = self.root / 'devices/pci0000:00/0000:00:01.0/block/raid0'
        unknown.mkdir(parents=True)
        (self.root / 'dev/block/8:1').symlink_to(unknown)
        with self.assertRaisesRegex(ValueError, 'COMPLEX_BLOCK_DEVICE'):
            self.check()

    def test_rejects_identity_change_between_first_and_final_read(self):
        original = operator_entry._sysfs_attribute
        target_reads = 0
        def changed(path):
            nonlocal target_reads
            if path == str(self.partition / 'dev'):
                target_reads += 1
                if target_reads > 1:
                    return '8:2'
            return original(path)
        with mock.patch.object(operator_entry, '_sysfs_attribute',
                               side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
                self.check()

    def test_rejects_device_link_substitution_during_check(self):
        original = operator_entry.os.path.realpath
        link = str(self.root / 'dev/block/8:1')
        link_reads = 0
        def changed(path):
            nonlocal link_reads
            if path == link:
                link_reads += 1
                if link_reads > 1:
                    return str(self.disk)
            return original(path)
        with mock.patch.object(operator_entry.os.path, 'realpath',
                               side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'SYSFS_IDENTITY'):
                self.check()

    def test_nvme_partition_layout(self):
        disk = (self.root / 'devices/pci0000:00/0000:00:02.0'
                / 'block/nvme0n1')
        part = disk / 'nvme0n1p2'
        part.mkdir(parents=True)
        (disk / 'dev').write_text('259:0\n', encoding='ascii')
        (part / 'dev').write_text('259:2\n', encoding='ascii')
        (part / 'partition').write_text('2\n', encoding='ascii')
        (disk / 'slaves').mkdir()
        (self.root / 'dev/block/259:0').symlink_to(disk)
        (self.root / 'dev/block/259:2').symlink_to(part)
        (self.root / 'block/nvme0n1').symlink_to(disk)
        self.assertEqual(self.check('259:2'),
                         ('259:0', '/dev/nvme0n1', '/dev/nvme0n1p2'))

    def test_originating_block_node_must_match_parent_device(self):
        wrong = types.SimpleNamespace(
            st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(8, 1),
            st_dev=3, st_ino=41, st_uid=0, st_gid=0,
            st_nlink=1, st_ctime_ns=1)
        with self.assertRaisesRegex(ValueError, 'ORIGIN_BLOCK_NODE'):
            self.check(disk_node=wrong)
        ordinary = types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_rdev=os.makedev(8, 0),
            st_dev=3, st_ino=41, st_uid=0, st_gid=0,
            st_nlink=1, st_ctime_ns=1)
        with self.assertRaisesRegex(ValueError, 'ORIGIN_BLOCK_NODE'):
            self.check(disk_node=ordinary)
        nonroot = types.SimpleNamespace(
            st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(8, 0),
            st_dev=3, st_ino=41, st_uid=1001, st_gid=0,
            st_nlink=1, st_ctime_ns=1)
        with self.assertRaisesRegex(ValueError, 'ORIGIN_BLOCK_NODE'):
            self.check(disk_node=nonroot)

    def test_originating_block_node_replacement_is_rejected(self):
        original = os.lstat
        reads = 0
        def lstat(path):
            nonlocal reads
            if str(path) == '/dev/sda':
                reads += 1
                return types.SimpleNamespace(
                    st_mode=stat.S_IFBLK | 0o600,
                    st_rdev=os.makedev(8, 0), st_dev=3,
                    st_ino=41 if reads == 1 else 42, st_uid=0, st_gid=0,
                    st_nlink=1, st_ctime_ns=1)
            return original(path)
        with mock.patch.object(operator_entry.os, 'lstat', side_effect=lstat):
            with self.assertRaisesRegex(ValueError, 'ORIGIN_BLOCK_NODE'):
                operator_entry._verify_direct_block_topology('8:1', str(self.root))


if __name__ == '__main__':
    unittest.main()
