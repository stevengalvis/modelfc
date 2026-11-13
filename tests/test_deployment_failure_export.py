"""Synthetic controller/workflow/observer transport; no network or host services."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = load('export_controller', 'ops/vps/deploy_main.py')
observer = load('export_observer', 'ops/github/deployment_status.py')
SHA = 'a' * 40
NAME = 'test_sample.Sample.test_one'
SCRIPT = textwrap.dedent((ROOT / '.github/workflows/tests.yml').read_text().split(
    "<<'PY'\n", 1)[1].split('\n          PY', 1)[0])


def report():
    info = dict(classification='TIMEOUT', count_known=False, failures=None, errors=None,
                test_ids=[NAME], truncated=True, last_test_id=NAME,
                progress=dict(observed_completed=140, complete=False, last_test_id=NAME,
                              last_progress_us=17037973, slowest=[[NAME, 100]]))
    value = deploy.report(SHA)
    value.update(status='FAIL', reason='TESTS_FAILED', tests_status='FAIL',
                 state_boundary_enforced=True,
                 failure_diagnostics=deploy.public_failure_diagnostics(info, 0))
    return value


class FailureExportTests(unittest.TestCase):
    def transport(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'raw.json'
            path.write_text(json.dumps(value))
            child = subprocess.run([sys.executable, '-c', SCRIPT, str(path), SHA,
                                    'INVALID_DEPLOYMENT_REPORT', '1'], capture_output=True,
                                   text=True, env={**os.environ, 'RUNNER_TEMP': directory}, timeout=5)
            self.assertEqual(child.returncode, 1)  # Reporting never changes deployment failure.
            sanitized = json.loads(child.stdout)
            path.write_text(child.stdout)
            return sanitized, observer.read_report(path, SHA)

    def test_timeout_reaches_observer_with_unknown_total_and_partial_progress(self):
        value = report()
        sanitized, accepted = self.transport(value)
        self.assertEqual(accepted, value)
        self.assertEqual(sanitized, value)
        body = observer.failure_summary(accepted)
        self.assertIn('Final test count: UNKNOWN', body)
        self.assertIn('Observed completed tests: 140; stream complete: False', body)
        self.assertIn('not a proven culprit', body)
        self.assertIn(NAME, body)

    def test_missing_progress_is_explicit_without_invented_counts(self):
        value = report()
        del value['failure_diagnostics']['progress']
        _, accepted = self.transport(value)
        self.assertEqual(accepted, value)
        body = observer.failure_summary(accepted)
        self.assertIn('Progress observations: UNAVAILABLE (not supplied)', body)
        self.assertIn('completed count and stream completeness UNKNOWN', body)
        self.assertNotIn('Observed completed tests:', body)
        self.assertNotIn('stream complete: False', body)
        self.assertIn('Final test count: UNKNOWN', body)

    def test_null_progress_is_invalid_not_missing_or_zero(self):
        value = report()
        value['failure_diagnostics']['progress'] = None
        sanitized, accepted = self.transport(value)
        self.assertNotIn('failure_diagnostics', sanitized)
        self.assertNotIn('failure_diagnostics', accepted)
        self.assertEqual(accepted['status'], 'FAIL')
        self.assertFalse(observer.valid_public_failure(value))

    def test_old_failure_report_stays_compatible(self):
        value = report()
        del value['failure_diagnostics']
        self.assertEqual(self.transport(value)[1], value)
        self.assertEqual(observer.failure_summary(value), '')

    def test_workflow_observer_validators_are_identical(self):
        def validator(source):
            return ast.dump(next(node for node in ast.parse(source).body
                                 if isinstance(node, ast.FunctionDef) and node.name == 'valid_public_failure'))
        self.assertEqual(validator(SCRIPT), validator((ROOT / 'ops/github/deployment_status.py').read_text()))

    def test_rejects_payloads_wrong_sha_success_and_invalid_nested_types(self):
        cases = []
        for key, bad in [('classification', 'secret/path'), ('test_ids', ['secret/path']),
                         ('test_ids', [NAME, NAME]), ('tests_run', 0), ('count_known', 1),
                         ('last_test_id', NAME + '\nsecret'), ('extra', 'secret/path')]:
            value = report(); value['failure_diagnostics'][key] = bad; cases.append(value)
        for key, bad in [('slowest', [[NAME, True]]), ('complete', 1), ('last_progress_us', -1),
                         ('observed_completed', 10001), ('extra', 'secret/path')]:
            value = report(); value['failure_diagnostics']['progress'][key] = bad; cases.append(value)
        for key, bad in [('requested_sha', 'b'*40), ('status', 'PASS'), ('reason', 'INTERNAL_ERROR'),
                         ('promotion_status', 'PROMOTED'), ('failure_diagnostics', None)]:
            value = report(); value[key] = bad; cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                sanitized, accepted = self.transport(value)
                self.assertNotIn('failure_diagnostics', sanitized)
                self.assertNotIn('secret/path', json.dumps(sanitized))
                self.assertEqual(accepted['status'], 'FAIL')
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'report'; path.write_text(json.dumps(value))
                    self.assertIsNone(observer.read_report(path, SHA))

    def test_maximum_names_and_lists_fit_unchanged_report_cap_without_mutation(self):
        names = ['test_' + 'x'*140 + '.C.test_' + str(i) for i in range(8)]
        self.assertTrue(all(len(n) <= 160 for n in names))
        diagnostic = report()['failure_diagnostics']
        del diagnostic['tests_run']
        diagnostic.update(test_ids=names, last_test_id=names[0], truncated=False)
        diagnostic['progress'].update(last_test_id=names[0], slowest=[[n, 630000000] for n in names])
        original = copy.deepcopy(diagnostic)
        value = report()
        value.update(previous_sha='b'*40, final_sha='b'*40,
                     failure_diagnostics=deploy.public_failure_diagnostics(diagnostic, 0))
        self.assertEqual(diagnostic, original)
        self.assertTrue(value['failure_diagnostics']['truncated'])
        self.assertLess(len(json.dumps(value).encode()) + 1, 4096)
        self.assertEqual(self.transport(value)[1], value)

    def test_known_count_is_bound_to_outer_summary(self):
        value = report()
        value['tests_run'] = 4
        value['failure_diagnostics'].update(count_known=True, tests_run=4)
        self.assertEqual(self.transport(value)[1], value)
        value['failure_diagnostics']['tests_run'] = 3
        self.assertNotIn('failure_diagnostics', self.transport(value)[0])
