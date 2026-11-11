"""Offline fake-file negatives for the compact package's read-only verifier."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_compact


class Integrity(unittest.TestCase):
    def test_bounded_hash_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'object'
            path.write_bytes(b'correct')
            self.assertEqual(verify_compact.digest_file(path, 7),
                             hashlib.sha256(b'correct').hexdigest())
            with self.assertRaisesRegex(ValueError, 'FILE_TYPE_OR_SIZE'):
                verify_compact.digest_file(path, 8)
            link = Path(tmp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                verify_compact.digest_file(link, 7)

    def test_external_set_and_hash_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'EXTERNAL_INPUTS.json').write_text(json.dumps({
                'external_inputs': {'source': {'bytes': 3,
                    'sha256': hashlib.sha256(b'abc').hexdigest()}}}))
            source = root / 'source'
            source.write_bytes(b'abc')
            original = verify_compact.ROOT
            verify_compact.ROOT = root
            try:
                self.assertEqual(verify_compact.check_external({'source': source}), 1)
                with self.assertRaisesRegex(ValueError, 'EXTERNAL_INPUT_SET'):
                    verify_compact.check_external({})
                source.write_bytes(b'abd')
                with self.assertRaisesRegex(ValueError, 'EXTERNAL_HASH'):
                    verify_compact.check_external({'source': source})
            finally:
                verify_compact.ROOT = original


if __name__ == '__main__':
    unittest.main()
