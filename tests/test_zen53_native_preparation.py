"""Expose inert native-candidate fixture tests and source packaging in CI."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'docs/zen53/native-compatibility'


class NativePreparation(unittest.TestCase):
    def _run_native_check(self, args):
        try:
            result = subprocess.run([sys.executable, '-B', *args], capture_output=True,
                                    text=True, timeout=90)
        except subprocess.TimeoutExpired:
            raise TimeoutError('native check timed out') from None
        except TimeoutError:
            # A launch error is not evidence that the subprocess exceeded its deadline.
            raise RuntimeError('native check launch error') from None
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('skipped=', result.stderr)

    def test_inventory(self):
        self._run_native_check([str(PACKAGE / 'verify_compact.py')])

    def test_synthetic_tests(self):
        self._run_native_check(['-m', 'unittest', 'discover', '-s', str(PACKAGE / 'tests'), '-q'])

    def test_archive_is_reproducible_and_corruption_rejected(self):
        spec = importlib.util.spec_from_file_location('native_package_builder',
            ROOT / 'docs/zen53/package_native_compatibility.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.build(), module.build())
        # Exercise verifier against altered bytes without mutating source files.
        from unittest.mock import patch
        original = Path.read_bytes
        def altered(path):
            data = original(path)
            return data + b' ' if path.name == 'owner_binding_v4.py' else data
        with patch.object(Path, 'read_bytes', altered), self.assertRaisesRegex(ValueError, 'PACKAGE_PIN'):
            module.build()
