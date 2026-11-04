"""Synthetic tests only; no credentials or systemd are accessed."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import codex_credential_isolation_candidate as candidate


class CredentialIsolationTests(unittest.TestCase):
    def test_home_mask_only_accepts_narrow_disjoint_canonical_paths(self):
        fixture = '/srv/modelfc-zen69-fixture-' + 'a' * 32
        value = candidate.home_mask('/home/worker', fixture, '/opt/codex/bin/codex')
        self.assertEqual(value, '--property=TemporaryFileSystem=/home/worker:'
                                'ro,nosuid,nodev,noexec,size=1M,nr_inodes=1024,mode=0000')
        for home, root, executable in (('/home/../root', fixture, '/opt/codex'),
                                      ('/home', fixture, '/opt/codex'),
                                      ('/home/worker', fixture, '/home/worker/codex'),
                                      ('/home/worker', fixture, '/opt/../home/worker/codex'),
                                      ('/home/worker', '/srv/not-a-fixture', '/opt/codex')):
            with self.subTest(home=home), self.assertRaises(ValueError):
                candidate.home_mask(home, root, executable)

    def test_observer_never_lists_unverified_home(self):
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        net = {'unit': unit, 'main_pid': 1234, 'starttime': 42, 'invocation': 'b' * 32}
        process = ('1234 (gate) S ' + '0 ' * 18 + '42\n').encode()
        mount = {'id': 97, 'path': '/home/worker', 'root': '/', 'type': 'tmpfs',
                 'options': ['ro', 'nosuid', 'nodev', 'noexec']}
        def read(path, maximum):
            if path.name == 'stat':
                return process
            if path.name == 'mountinfo':
                return b'mountinfo'
            return b'mnt_id:\t97\n'
        with patch.object(candidate, '_read', side_effect=read), \
             patch.object(candidate, '_mounts', return_value=[mount]), \
             patch.object(candidate.os, 'open', return_value=71), patch.object(candidate.os, 'close'), \
             patch.object(candidate.os, 'listdir', return_value=[]) as listing, \
             patch.object(candidate.os, 'fstat', return_value=SimpleNamespace(
                 st_mode=0o040000, st_uid=0, st_gid=0, st_dev=11, st_ino=21)), \
             patch.object(candidate.os, 'fstatvfs', return_value=SimpleNamespace(
                 f_flag=os.ST_RDONLY, f_blocks=256, f_frsize=4096, f_files=1024, f_ffree=1023)):
            self.assertEqual(candidate.observe_home_mask(unit, '/home/worker', net, (10, 20))['mount_id'], 97)
            listing.assert_not_called()
            mount['type'] = 'ext4'
            with self.assertRaisesRegex(ValueError, 'mount_unverified'):
                candidate.observe_home_mask(unit, '/home/worker', net, (10, 20))
            listing.assert_not_called()
            mount['type'] = 'tmpfs'
            mount['options'].remove('noexec')
            with self.assertRaisesRegex(ValueError, 'mount_unverified'):
                candidate.observe_home_mask(unit, '/home/worker', net, (10, 20))
            listing.assert_not_called()
            mount['options'].append('noexec')
            with self.assertRaisesRegex(ValueError, 'not_private'):
                candidate.observe_home_mask(unit, '/home/worker', net, (11, 21))
            listing.assert_not_called()
            with patch.object(candidate.os, 'fstatvfs', return_value=SimpleNamespace(
                    f_flag=os.ST_RDONLY, f_blocks=256, f_frsize=4096, f_files=1024, f_ffree=1022)), \
                    self.assertRaisesRegex(ValueError, 'mount_unverified'):
                candidate.observe_home_mask(unit, '/home/worker', net, (10, 20))
            listing.assert_not_called()


if __name__ == '__main__':
    unittest.main()
