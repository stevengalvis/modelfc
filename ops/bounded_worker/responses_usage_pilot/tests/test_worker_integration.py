"""Actual Responses composition with synthetic transport and fake runtime only."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import run
from pilot import PilotJournal, request_payload
import test_completion_budget as completion_fixtures
from test_live import response


class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.controller = completion_fixtures.ControllerBudgetTests()
        self.controller.setUp()
        self.addCleanup(self.controller.doCleanups)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.journal = PilotJournal(self.root)
        self.addCleanup(self.journal.close)
        self.journal.reserve_invocation()
        self.transport = Mock(return_value=response())

    def execute(self, **kwargs):
        report, deadlines, process = self.controller.modeled(
            operation=lambda target: run.run_prepared_pipeline(
                self.journal, self.transport, target, self.root), **kwargs)
        self.transport.assert_called_once()
        self.assertEqual(report['calls'], 1)
        self.assertEqual(report['input_tokens'], 400)
        self.assertEqual(report['output_tokens'], 200)
        self.assertEqual(report['usage_state'], 'complete')
        receipt = json.loads((self.root / 'test-1.json').read_text())
        self.assertTrue((self.root / 'candidate-1.py').is_file())
        self.assertTrue((self.root / 'diagnostic-1.json').is_file())
        self.assertFalse((self.root / 'candidate-2.py').exists())
        self.assertNotIn('PRIVATE_COMPLETION_SENTINEL', json.dumps(report))
        self.assertNotIn('PRIVATE_COMPLETION_SENTINEL', json.dumps(receipt))
        rows = self.journal.db.execute(
            'SELECT n,state FROM responses_usage_pilot ORDER BY n').fetchall()
        self.assertEqual(rows, [(1, 'passed' if report['status'] == 'passed' else 'candidate')])
        return report, receipt, deadlines, process

    def blocked(self, code, **kwargs):
        report, receipt, _, _ = self.execute(**kwargs)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['reason'], 'test_or_cleanup_blocked')
        self.assertEqual(receipt['status'], 'blocked')
        if code:
            self.assertEqual(receipt['diagnostic']['failure']['code'], code)
        return report, receipt

    def test_actual_pipeline_passes_after_twenty_second_completion(self):
        report, receipt, deadlines, process = self.execute()
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['reason'], 'eight_tests_passed')
        self.assertTrue(report['cleanup_verified'])
        self.assertTrue(receipt['diagnostic']['completion_started'])
        self.assertEqual(receipt['diagnostic']['completion_budget_ms'], 30000)
        self.assertTrue(receipt['diagnostic']['streams_before_stop']['stdout_eof'])
        self.assertTrue(receipt['diagnostic']['streams_before_stop']['stderr_eof'])
        self.assertEqual(deadlines[:3], [('ready', 16), ('pump', 16), ('pump', 32)])
        process.wait.assert_called_once()

    def test_open_output_blocks_without_requesting_repair(self):
        self.blocked('pipe_timeout', complete_time=100)

    def test_late_result_blocks_without_requesting_repair(self):
        self.blocked('result_after_workload_deadline', result_time=16)

    def test_extra_stdout_blocks_without_requesting_repair(self):
        self.blocked('invalid_json', late_stdout=True)

    def test_extra_stderr_blocks_without_requesting_repair(self):
        self.blocked('stderr_nonempty', late_stderr=True)

    def test_nonzero_attachment_blocks_without_requesting_repair(self):
        self.blocked('attach_exit_nonzero', attach_exit=1)

    def test_cleanup_failure_blocks_without_requesting_repair(self):
        report, _ = self.blocked(None, cleanup=False)
        self.assertFalse(report['cleanup_verified'])
        self.assertEqual(report['cleanup_state'], 'unverified')

    def test_main_bound_still_includes_preflight(self):
        _, receipt = self.blocked('pipe_timeout', preflight=20,
                                  result_time=34, complete_time=46)
        self.assertEqual(receipt['diagnostic']['elapsed_before_stop_ms'], 45000)
        self.assertEqual(receipt['diagnostic']['completion_budget_ms'], 11000)

    def test_attachment_cannot_outlive_completion_budget(self):
        self.blocked('attach_wait_timeout', eof_with_frame=True, attach_exit_time=40)

    def test_missing_result_never_grants_completion_budget(self):
        _, receipt = self.blocked('pipe_timeout', raw=b'{"failed":')
        self.assertFalse(receipt['diagnostic']['completion_started'])

    def test_cli_calls_the_same_tested_composition(self):
        # Do not execute CLI admission or its saved-key reader in offline tests.
        tree = ast.parse(Path(run.__file__).read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        names = [n.func.id for n in ast.walk(main)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertEqual(names.count('run_prepared_pipeline'), 1)
        self.assertNotIn('run_pipeline', names)

    def test_request_limits_and_identity_unchanged_by_integration(self):
        import pilot
        payload = request_payload()
        self.assertEqual((pilot.MAX_CALLS, pilot.MAX_REQUEST_BYTES,
                          pilot.MAX_OUTPUT_TOKENS, pilot.MAX_RESPONSE_BYTES,
                          pilot.MAX_SOURCE_BYTES, pilot.TIMEOUT_SECONDS),
                         (3, 4096, 4096, 65536, 8192, 60))
        self.assertEqual(payload['model'], 'gpt-6-luna')
        self.assertEqual(payload['tools'], [])
        self.assertFalse(payload['store'])
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['reasoning'], {'effort': 'low'})
        self.assertEqual(payload['service_tier'], 'default')


if __name__ == '__main__':
    unittest.main()
