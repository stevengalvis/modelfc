import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops.bounded_worker import production_fstab_transaction as transaction


class ProductionFstabTransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.fstab = self.path / 'fstab'
        self.original = b'/dev/example /existing ext4 defaults 0 0\n'
        self.fstab.write_bytes(self.original)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.identifier = 'a' * 32

    @staticmethod
    def accepted(_fd, _entry):
        return True

    def test_one_exact_entry_preserves_original_and_mode(self):
        self.fstab.chmod(0o640)
        result = transaction.append_entry(self.fd, self.identifier, validate=self.accepted)
        value = self.fstab.read_bytes()
        self.assertTrue(value.startswith(self.original))
        self.assertEqual(value.count(b'X-fstrim.notrim'), 1)
        self.assertEqual(self.fstab.stat().st_mode & 0o777, 0o640)
        self.assertEqual(len(result['after_sha256']), 64)

    def test_parser_receives_descriptors_at_start(self):
        observed = []

        def validator(fd, _entry):
            observed.append(os.read(fd, 1048576))
            return True

        transaction.append_entry(self.fd, self.identifier, validate=validator)
        self.assertEqual(observed[0], self.original)
        self.assertEqual(observed[1], self.fstab.read_bytes())
        self.assertEqual(observed[2], self.fstab.read_bytes())

    def test_candidate_size_is_checked_before_replacement(self):
        with patch.object(transaction, 'MAX_FSTAB', len(self.original) + 2):
            with self.assertRaisesRegex(ValueError, 'candidate_too_large'):
                transaction.append_entry(self.fd, self.identifier, validate=self.accepted)
        self.assertEqual(self.fstab.read_bytes(), self.original)
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ['fstab'])

    def test_changed_preflight_digest_blocks_before_writing(self):
        with self.assertRaisesRegex(ValueError, 'preflight_changed'):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted,
                                     expected_before_sha256='0' * 64)
        self.assertEqual(self.fstab.read_bytes(), self.original)
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ['fstab'])

    def test_rejects_missing_newline_and_untrusted_parser(self):
        self.fstab.write_bytes(self.original.rstrip())
        with self.assertRaisesRegex(ValueError, 'newline'):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted)
        self.fstab.write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, 'unverified'):
            transaction.append_entry(self.fd, self.identifier, validate=lambda *_: False)
        self.assertEqual(self.fstab.read_bytes(), self.original)

    def test_changed_original_blocks_replacement(self):
        def change():
            self.fstab.write_bytes(b'new external change\n')

        with self.assertRaisesRegex(ValueError, 'changed'):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted, before_replace=change)
        self.assertEqual(self.fstab.read_bytes(), b'new external change\n')
        self.assertTrue((self.path / ('.fstab.modelfc-' + self.identifier)).exists())

    def test_changed_staged_candidate_blocks_replacement(self):
        def change():
            (self.path / ('.fstab.modelfc-' + self.identifier)).write_bytes(b'changed\n')

        with self.assertRaisesRegex(ValueError, 'candidate_changed'):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted, before_replace=change)
        self.assertEqual(self.fstab.read_bytes(), self.original)

    def test_staged_extended_attribute_blocks_replacement(self):
        with patch.object(transaction.os, 'listxattr', side_effect=[[], ['user.unexpected']]):
            with self.assertRaisesRegex(ValueError, 'candidate_changed'):
                transaction.append_entry(self.fd, self.identifier, validate=self.accepted)
        self.assertEqual(self.fstab.read_bytes(), self.original)

    def test_symlink_hardlink_and_invalid_identifier_block(self):
        for name in ('../bad', 'A' * 32):
            with self.assertRaisesRegex(ValueError, 'identifier'):
                transaction.append_entry(self.fd, name, validate=self.accepted)
        os.link(self.fstab, self.path / 'hardlink')
        with self.assertRaisesRegex(ValueError, 'unprotected'):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted)
        self.fstab.unlink()
        self.fstab.symlink_to(self.path / 'hardlink')
        with self.assertRaises(OSError):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted)

    def test_fifo_is_rejected_without_waiting(self):
        self.fstab.unlink()
        os.mkfifo(self.fstab)
        with self.assertRaisesRegex(ValueError, 'unprotected'):
            transaction.append_entry(self.fd, self.identifier, validate=self.accepted)


if __name__ == '__main__':
    unittest.main()
