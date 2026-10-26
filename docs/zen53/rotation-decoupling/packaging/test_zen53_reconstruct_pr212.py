import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import subprocess

spec = importlib.util.spec_from_file_location(
    'zen53_reconstruct_pr212_archives',
    str(Path(__file__).with_name('zen53_reconstruct_pr212_archives.py')))
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


CHECKOUT = Path(os.environ['ZEN53_TEST_CHECKOUT'])
GZIP_SHA = hashlib.sha256(Path('/usr/bin/gzip').read_bytes()).hexdigest()
GZIP_OWNER = Path('/usr/bin/gzip').stat()


def reconstruct(output, checkout=CHECKOUT, gzip_sha=GZIP_SHA):
    return recipe.reconstruct(checkout, output, gzip_sha,
                              GZIP_OWNER.st_uid, GZIP_OWNER.st_gid)


class ReconstructionTests(unittest.TestCase):
    def test_exact_archives_from_clean_checkout_with_poisoned_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'new'
            prior = os.environ.get('GZIP')
            os.environ['GZIP'] = '-1'
            try:
                reconstruct(output)
            finally:
                if prior is None:
                    os.environ.pop('GZIP', None)
                else:
                    os.environ['GZIP'] = prior
            self.assertEqual(
                hashlib.sha256((output / recipe.STAGE_ARCHIVE).read_bytes()).hexdigest(),
                recipe.STAGE_SHA)
            self.assertEqual(
                hashlib.sha256((output / recipe.OPERATOR_ARCHIVE).read_bytes()).hexdigest(),
                recipe.OPERATOR_SHA)

    def test_changed_source_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'stage'
            shutil.copytree(CHECKOUT / recipe.BASE / 'stage', stage)
            (stage / 'admission.py').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SOURCE_HASH'):
                recipe.checked_group(stage, recipe.STAGE_INVENTORY_SHA)

    def test_extra_or_symlink_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'stage'
            shutil.copytree(CHECKOUT / recipe.BASE / 'stage', stage)
            extra = stage / 'unexpected.py'
            extra.write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'SOURCE_MEMBER_SET'):
                recipe.checked_group(stage, recipe.STAGE_INVENTORY_SHA)
            extra.unlink()
            target = stage / 'admission.py'
            target.unlink()
            target.symlink_to(stage / 'test_admission.py')
            with self.assertRaisesRegex(ValueError, 'SOURCE_METADATA'):
                recipe.checked_group(stage, recipe.STAGE_INVENTORY_SHA)

    def test_wrong_checkout_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'never-created'
            with self.assertRaises(Exception):
                reconstruct(output, checkout=Path(tmp))
            self.assertFalse(output.exists())

    def test_wrong_gzip_pin_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'never-created'
            with self.assertRaisesRegex(ValueError, 'GZIP_HASH'):
                reconstruct(output, gzip_sha='0' * 64)
            self.assertFalse(output.exists())

    def test_wrong_gzip_owner_and_oversized_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'GZIP_METADATA'):
            recipe.verified_gzip(GZIP_SHA, owner_uid=-1)
        with self.assertRaisesRegex(ValueError, 'TAR_SIZE'):
            recipe.fixed_gzip(b'x' * (recipe.MAX_TAR_BYTES + 1), GZIP_SHA,
                              GZIP_OWNER.st_uid, GZIP_OWNER.st_gid)

    def test_gzip_timeout_is_killed_and_rejected(self):
        child = mock.Mock()
        child.communicate.side_effect = [subprocess.TimeoutExpired('gzip', 30),
                                         (b'', None)]
        with mock.patch.object(recipe.subprocess, 'Popen', return_value=child) as spawn:
            with self.assertRaisesRegex(ValueError, 'GZIP_TIMEOUT'):
                recipe.fixed_gzip(b'x', GZIP_SHA,
                                  GZIP_OWNER.st_uid, GZIP_OWNER.st_gid)
        child.kill.assert_called_once()
        self.assertEqual(spawn.call_args.args[0],
                         ['/usr/bin/gzip', '-n', '-6', '-c'])
        self.assertEqual(spawn.call_args.kwargs['env'],
                         {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC'})

    def test_gzip_nonzero_exit_is_rejected(self):
        child = mock.Mock(returncode=2)
        with mock.patch.object(recipe.subprocess, 'Popen', return_value=child):
            with self.assertRaisesRegex(ValueError, 'GZIP_EXIT'):
                recipe.fixed_gzip(b'x', GZIP_SHA,
                                  GZIP_OWNER.st_uid, GZIP_OWNER.st_gid)


if __name__ == '__main__':
    unittest.main()
