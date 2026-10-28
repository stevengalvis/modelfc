"""Test-only temporary storage under the installed deployment environment."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_zen53_python_closure as bridge


class ScratchTests(unittest.TestCase):
    def setUp(self):
        # The regression itself must also support a read-only installed tree.
        self.root = Path(self.enterContext(bridge.operator_scratch()))

    def test_missing_tmpdir_uses_private_disposable_child(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(bridge, 'ROOT', self.root):
            with bridge.operator_scratch() as directory:
                child = Path(directory)
                self.assertEqual(child.parent, self.root)
                self.assertEqual(child.stat().st_mode & 0o777, 0o700)
                bridge.trusted_scratch_parent(child)
            self.assertFalse(child.exists())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_readonly_release_falls_back_to_existing_reports_without_chmod(self):
        release, reports = self.root/'release', self.root/'reports'
        release.mkdir(mode=0o700)
        reports.mkdir(mode=0o700)
        real = tempfile.TemporaryDirectory
        def allocate(*args, **kwargs):
            if kwargs['dir'] == release:
                raise OSError(errno.EROFS, 'synthetic read-only release mount')
            return real(*args, **kwargs)
        with patch.dict(os.environ, {}, clear=True), patch.object(bridge, 'ROOT', release), \
                patch.object(bridge, 'DEPLOY_REPORTS', reports), \
                patch.object(bridge.tempfile, 'TemporaryDirectory', side_effect=allocate):
            with bridge.operator_scratch() as directory:
                self.assertEqual(Path(directory).parent, reports)
                (Path(directory)/'fixture').write_text('disposable')
        self.assertEqual(list(release.iterdir()), [])
        self.assertEqual(list(reports.iterdir()), [])
        self.assertEqual(reports.stat().st_mode & 0o777, 0o700)

    def test_explicit_safe_parent_is_honored_and_cleaned_on_error(self):
        with patch.dict(os.environ, {'TMPDIR': str(self.root)}):
            with self.assertRaisesRegex(ValueError, 'fixture failure'):
                with bridge.operator_scratch() as directory:
                    child = Path(directory)
                    raise ValueError('fixture failure')
            self.assertFalse(child.exists())

    def test_unsafe_or_symlink_parent_is_rejected_without_fallback(self):
        unsafe = self.root/'unsafe'
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        linked = self.root/'linked'
        linked.symlink_to(self.root, target_is_directory=True)
        for parent in (unsafe, linked, Path('/tmp')):
            with self.subTest(parent=parent), patch.dict(os.environ, {'TMPDIR': str(parent)}):
                with self.assertRaises((OSError, ValueError)):
                    with bridge.operator_scratch():
                        self.fail('unsafe parent accepted')
        self.assertEqual(unsafe.stat().st_mode & 0o777, 0o777)

    def test_no_valid_fallback_fails_without_creating_paths(self):
        missing = self.root/'missing'
        with patch.dict(os.environ, {}, clear=True), patch.object(bridge, 'ROOT', missing), \
                patch.object(bridge, 'DEPLOY_REPORTS', missing):
            with self.assertRaisesRegex(RuntimeError, 'no protected writable'):
                with bridge.operator_scratch():
                    self.fail('missing root accepted')
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
