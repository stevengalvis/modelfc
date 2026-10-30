"""Check the retained outcomes of the native preparation subprocess wrappers."""
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_zen53_native_preparation as native


class CapturedResult(unittest.TestResult):
    def addFailure(self, test, err):
        self.exception_type = err[0]
        super().addFailure(test, err)

    def addError(self, test, err):
        self.exception_type = err[0]
        super().addError(test, err)


class NativePreparationDiagnostics(unittest.TestCase):
    def test_subprocess_outcomes_keep_distinct_source_identities(self):
        checks = {
            'test_inventory': [str(native.PACKAGE / 'verify_compact.py')],
            'test_synthetic_tests': ['-m', 'unittest', 'discover', '-s', str(native.PACKAGE / 'tests'), '-q'],
        }
        outcomes = (
            (subprocess.TimeoutExpired('private', 90), 0, 1, TimeoutError),
            (TimeoutError('private'), 0, 1, RuntimeError),
            (OSError('private'), 0, 1, OSError),
            (subprocess.CompletedProcess([], 1, 'out', 'err'), 1, 0, AssertionError),
            (subprocess.CompletedProcess([], 0, '', 'skipped=1'), 1, 0, AssertionError),
            (subprocess.CompletedProcess([], 0, '', ''), 0, 0, None),
        )
        for method, args in checks.items():
            for outcome, failures, errors, exception_type in outcomes:
                with self.subTest(method=method, outcome=type(outcome).__name__, failures=failures):
                    test = native.NativePreparation(method)
                    result = CapturedResult()
                    with patch('tests.test_zen53_native_preparation.subprocess.run') as run:
                        if isinstance(outcome, Exception):
                            run.side_effect = outcome
                        else:
                            run.return_value = outcome
                        test.run(result)
                    run.assert_called_once_with([sys.executable, '-B', *args],
                                                capture_output=True, text=True, timeout=90)
                    self.assertEqual((len(result.failures), len(result.errors)), (failures, errors))
                    self.assertIs(getattr(result, 'exception_type', None), exception_type)
                    for failed_test, _ in result.failures + result.errors:
                        self.assertEqual(failed_test.id(), test.id())


if __name__ == '__main__':
    unittest.main()
