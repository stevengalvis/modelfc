import json
import errno
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker import production_image_stage as stage


class ProductionImageStageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        info = os.fstat(self.fd)
        self.pin = (info.st_dev, info.st_ino)
        self.name = 'worker-' + 'a' * 32 + '.img'

    def test_intent_is_durable_and_never_overwritten(self):
        data = {'version': 1, 'state': 'allocation_intent'}
        digest = stage._write_exclusive(self.fd, 'intent.json', data)
        self.assertEqual(len(digest), 64)
        self.assertEqual(json.loads((self.path / 'intent.json').read_text()), data)
        with self.assertRaises(FileExistsError):
            stage._write_exclusive(self.fd, 'intent.json', {'different': True})
        self.assertEqual(json.loads((self.path / 'intent.json').read_text()), data)

    def test_invalid_name_and_changed_parent_do_not_create_intent(self):
        for name, pin in [('../escape', self.pin), (self.name, (self.pin[0], self.pin[1] + 1))]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                stage.create_image(self.fd, name, parent_pin=pin, lock_pin=(1, 2))
        self.assertEqual(list(self.path.iterdir()), [])

    def test_insufficient_capacity_blocks_before_writing(self):
        with patch.object(stage.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=1, f_favail=0)):
            with self.assertRaisesRegex(ValueError, 'headroom'):
                stage.create_image(self.fd, self.name, parent_pin=self.pin, lock_pin=(1, 2))
        self.assertEqual(list(self.path.iterdir()), [])

    def test_existing_image_is_not_adopted_and_intent_is_retained(self):
        path = self.path / self.name
        path.write_bytes(b'unchanged')
        with patch.object(stage, '_space'):
            with self.assertRaises(FileExistsError):
                stage.create_image(self.fd, self.name, parent_pin=self.pin, lock_pin=(1, 2))
        self.assertEqual(path.read_bytes(), b'unchanged')
        self.assertTrue((self.path / (self.name + '.intent.json')).is_file())
        self.assertFalse((self.path / (self.name + '.allocated.json')).exists())
        with patch.object(stage, '_space'):
            with self.assertRaises(FileExistsError):
                stage.create_image(self.fd, 'worker-' + 'b' * 32 + '.img',
                                   parent_pin=self.pin, lock_pin=(1, 2))
        self.assertFalse((self.path / ('worker-' + 'b' * 32 + '.img')).exists())

    def test_active_timer_or_unknown_manager_output_fails_closed(self):
        active = SimpleNamespace(stdout='LoadState=loaded\nActiveState=active\nUnitFileState=disabled\nJob=\n')
        malformed = SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\nActiveState=inactive\nJob=\n')
        pending = SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nJob=123/start\n')
        missing = SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n')
        for result in (active, malformed, pending, missing):
            with self.subTest(result=result.stdout), patch.object(stage.subprocess, 'run', return_value=result):
                with self.assertRaises(ValueError):
                    stage.require_bridge_paused()

    def test_inactive_units_with_no_job_pass_mocked_manager_check(self):
        for job in ('', '0'):
            result = SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\n'
                                            f'UnitFileState=disabled\nJob={job}\n')
            with self.subTest(job=job), patch.object(stage.subprocess, 'run', return_value=result) as run:
                stage.require_bridge_paused()
                self.assertEqual(run.call_count, 6)

    def test_allocation_failure_retains_intent_and_never_certifies(self):
        original = os.fstat

        def root_owned_regular(fd):
            info = original(fd)
            if not __import__('stat').S_ISREG(info.st_mode):
                return info
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink,
                                   st_size=info.st_size, st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_blocks=info.st_blocks)

        with patch.object(stage, '_space'), patch.object(stage.os, 'fstat', side_effect=root_owned_regular), \
                patch.object(stage.os, 'posix_fallocate', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError):
                stage.create_image(self.fd, self.name, parent_pin=self.pin, lock_pin=(1, 2))
        self.assertTrue((self.path / (self.name + '.intent.json')).is_file())
        self.assertTrue((self.path / self.name).is_file())
        self.assertFalse((self.path / (self.name + '.allocated.json')).exists())

    def test_final_receipt_is_not_success_when_later_headroom_check_fails(self):
        original = os.fstat

        def root_owned_regular(fd):
            info = original(fd)
            if not __import__('stat').S_ISREG(info.st_mode):
                return info
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink,
                                   st_size=info.st_size, st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_blocks=info.st_blocks)

        with patch.object(stage, 'IMAGE_BYTES', 4096), \
                patch.object(stage, '_space', side_effect=[None, None, ValueError('headroom')]), \
                patch.object(stage, '_allocated_extents'), \
                patch.object(stage.os, 'fstat', side_effect=root_owned_regular):
            with self.assertRaisesRegex(ValueError, 'headroom'):
                stage.create_image(self.fd, self.name, parent_pin=self.pin, lock_pin=(1, 2))
        receipt = json.loads((self.path / (self.name + '.allocated.json')).read_text())
        self.assertEqual(receipt['state'], 'allocation_observed_capacity_pending')
        self.assertTrue((self.path / stage._UNRESOLVED).exists())
        self.assertTrue((self.path / self.name).exists())

    def test_mocked_allocation_checks_capacity_after_receipt_and_retains_marker(self):
        original = os.fstat

        def root_owned_regular(fd):
            info = original(fd)
            if not __import__('stat').S_ISREG(info.st_mode):
                return info
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink,
                                   st_size=info.st_size, st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_blocks=info.st_blocks)

        with patch.object(stage, 'IMAGE_BYTES', 4096), patch.object(stage, '_space') as space, \
                patch.object(stage, '_allocated_extents') as extents, \
                patch.object(stage.os, 'fstat', side_effect=root_owned_regular):
            digest = stage.create_image(self.fd, self.name, parent_pin=self.pin, lock_pin=(1, 2))
        self.assertEqual(len(digest), 64)
        self.assertEqual(space.call_count, 3)
        extents.assert_called_once()
        self.assertTrue((self.path / stage._UNRESOLVED).exists())
        self.assertEqual((self.path / self.name).stat().st_size, 4096)


if __name__ == '__main__':
    unittest.main()
