"""Read-only readiness and zero-provider-call regressions; all OS seams fake."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_credentials
from ops.bounded_worker.agents_live_pilot import PreparedTarget, LiveFailure, _OperationDeadline
import live_transport
import pilot
import readiness
import run
import sandbox
from test_live import response

PRIVATE = 'PRIVATE_READINESS_SENTINEL'


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.target = PreparedTarget('a' * 64, '/synthetic/home', '/synthetic/runtime', 'b' * 64)
        self.executor = Mock()
        self.executor._command.side_effect = [(0, b'a' * 64 + b'\n')] + [(1, b'')] * 3
        self.factory = self.enterContext(patch.object(readiness, 'TestExecutor', return_value=self.executor))
        self.bounded = self.enterContext(patch.object(readiness, '_bounded_call',
                                                      side_effect=lambda function, seconds: function()))

    def test_all_names_and_exact_image_only_are_read(self):
        self.assertTrue(readiness.verify_readiness(self.target, (1, 2, 3)))
        expected = [['image', 'inspect', '--format', '{{.Id}}', 'sha256:' + 'a' * 64]]
        expected.extend(['container', 'exists', 'agents-' + hashlib.sha256(
            (pilot.ATTEMPT + ':' + str(number)).encode()).hexdigest()] for number in (1, 2, 3))
        self.assertEqual([c.args[0] for c in self.executor._command.call_args_list], expected)
        self.executor.verify_prepared.assert_called_once()
        self.executor.close.assert_called_once()
        self.assertEqual(self.bounded.call_args.args[1], 25)
        self.executor._popen.assert_not_called()

    def test_check_only_requested_upcoming_name(self):
        readiness.verify_readiness(self.target, (2,))
        self.assertEqual(self.executor._command.call_count, 2)
        self.assertTrue(self.executor._command.call_args.args[0][-1].endswith(
            hashlib.sha256((pilot.ATTEMPT + ':2').encode()).hexdigest()))

    def test_exact_image_without_newline_is_allowed(self):
        self.executor._command.side_effect = [(0, b'a' * 64), (1, b'')]
        self.assertTrue(readiness.verify_readiness(self.target, (1,)))

    def test_absent_or_error_image_blocks_without_name_query(self):
        for code in (1, 125, -1, False, None):
            self.executor.reset_mock()
            self.executor._command.side_effect = [(code, PRIVATE.encode())]
            with self.subTest(code=code), self.assertRaises(readiness.ReadinessFailure) as failure:
                readiness.verify_readiness(self.target, (1,))
            self.assertEqual(failure.exception.code, 'image_unavailable')
            self.assertEqual(self.executor._command.call_count, 1)
            self.executor.close.assert_called_once()

    def test_wrong_ambiguous_or_malformed_image_id_blocks(self):
        for raw in (b'c' * 64, b'a' * 63, b'a' * 64 + b'\nextra', b'a' * 64 + b'\n\n',
                    b' ' + b'a' * 64, b'sha256:' + b'a' * 64, 'a' * 64, None):
            self.executor._command.side_effect = [(0, raw)]
            with self.subTest(raw=raw), self.assertRaisesRegex(readiness.ReadinessFailure,
                                                              '^image_identity_mismatch$'):
                readiness.verify_readiness(self.target, (1,))

    def test_existing_error_or_malformed_name_status_blocks(self):
        for code, raw in ((0, b''), (125, b''), (-1, b''), (True, b''),
                          (1, PRIVATE.encode()), (1, bytearray())):
            self.executor._command.side_effect = [(0, b'a' * 64), (code, raw)]
            with self.subTest(code=code), self.assertRaisesRegex(readiness.ReadinessFailure,
                                                                '^container_name_unavailable$'):
                readiness.verify_readiness(self.target, (1,))

    def test_invalid_call_numbers_never_construct_executor(self):
        for numbers in ((), [], (0,), (4,), (True,), (1, 1), (1, 2, 3, 1), ('1',)):
            with self.subTest(numbers=numbers), self.assertRaises(readiness.ReadinessFailure):
                readiness.verify_readiness(self.target, numbers)
        self.factory.assert_not_called()
        self.bounded.assert_not_called()

    def test_runtime_failure_closes_without_image_query(self):
        self.executor.verify_prepared.side_effect = PermissionError(PRIVATE)
        with self.assertRaises(PermissionError):
            readiness.verify_readiness(self.target, (1,))
        self.executor._command.assert_not_called()
        self.executor.close.assert_called_once()

    def test_deadline_closes_without_retry_or_raw_diagnostic(self):
        self.bounded.side_effect = _OperationDeadline(PRIVATE)
        with self.assertRaises(_OperationDeadline) as failure:
            readiness.verify_readiness(self.target, (1,))
        self.executor.close.assert_called_once()
        self.bounded.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(readiness.readiness_failure(failure.exception)))

    def test_command_exception_is_not_retried(self):
        self.executor._command.side_effect = PermissionError(PRIVATE)
        with self.assertRaises(PermissionError):
            readiness.verify_readiness(self.target, (1,))
        self.executor._command.assert_called_once()
        self.executor.close.assert_called_once()

    def test_nonaffirmative_bounded_result_blocks(self):
        self.bounded.side_effect = None
        self.bounded.return_value = None
        with self.assertRaisesRegex(readiness.ReadinessFailure, '^invalid_readiness_evidence$'):
            readiness.verify_readiness(self.target, (1,))
        self.executor.close.assert_called_once()

    def test_close_failure_is_not_accepted(self):
        self.executor.close.side_effect = OSError(PRIVATE)
        with self.assertRaises(OSError):
            readiness.verify_readiness(self.target, (1,))


class PipelineReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.journal = pilot.PilotJournal(self.root)
        self.addCleanup(self.journal.close)
        self.journal.reserve_invocation()
        self.target = PreparedTarget('a' * 64, '/synthetic/home', '/synthetic/runtime', 'b' * 64)
        self.transport = Mock(return_value=response())
        self.tester = self.enterContext(patch.object(sandbox, 'test_candidate', return_value={
            'status': 'passed', 'cleanup_verified': True, 'failed': []}))
        self.executor = Mock()
        self.executor._command.side_effect = [(0, b'a' * 64), (1, b'')]
        self.enterContext(patch.object(readiness, 'TestExecutor', return_value=self.executor))
        self.enterContext(patch.object(readiness, '_bounded_call',
                                side_effect=lambda function, seconds: function()))

    def execute(self):
        report = run.run_prepared_pipeline(self.journal, self.transport, self.target, self.root)
        self.assertNotIn(PRIVATE, json.dumps(report))
        return report

    def assert_zero_calls(self, report):
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['calls'], 0)
        self.assertEqual(report['pipeline_failure']['phase'], 'sandbox_readiness')
        self.transport.assert_not_called()
        self.tester.assert_not_called()
        self.assertEqual(self.journal.db.execute('SELECT * FROM responses_usage_pilot').fetchall(), [])
        self.assertFalse((self.root / 'candidate-1.py').exists())

    def test_missing_image_has_zero_calls_and_no_call_reservation(self):
        self.executor._command.side_effect = [(125, PRIVATE.encode())]
        report = self.execute()
        self.assert_zero_calls(report)
        self.assertEqual(report['readiness_failure']['code'], 'image_unavailable')

    def test_wrong_image_has_zero_calls(self):
        self.executor._command.side_effect = [(0, b'c' * 64)]
        self.assert_zero_calls(self.execute())

    def test_occupied_name_has_zero_calls(self):
        self.executor._command.side_effect = [(0, b'a' * 64), (0, b'')]
        self.assert_zero_calls(self.execute())

    def test_denied_metadata_has_zero_calls(self):
        self.executor._command.side_effect = PermissionError(PRIVATE)
        self.assert_zero_calls(self.execute())

    def test_deadline_has_zero_calls(self):
        self.executor.verify_prepared.side_effect = _OperationDeadline(PRIVATE)
        self.assert_zero_calls(self.execute())

    def test_affirmative_readiness_precedes_model_and_preserves_success(self):
        events = []
        self.executor.close.side_effect = lambda: events.append('readiness_done')
        self.transport.side_effect = lambda **kwargs: events.append('model') or response()
        report = self.execute()
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(events, ['readiness_done', 'model'])
        self.assertEqual(report['calls'], 1)

    def test_repair_rechecks_and_blocks_without_second_call(self):
        self.tester.return_value = {'status': 'tests_failed', 'cleanup_verified': True,
                                    'failed': ['test_groups']}
        self.executor._command.side_effect = [(0, b'a' * 64), (1, b''), (125, b'')]
        report = self.execute()
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['calls'], 1)
        self.transport.assert_called_once()
        self.tester.assert_called_once()
        self.assertEqual(self.journal.db.execute('SELECT * FROM responses_usage_pilot').fetchall(),
                         [(1, 'tests_failed')])

    def test_all_three_repairs_are_checked_without_changing_cap(self):
        self.tester.return_value = {'status': 'tests_failed', 'cleanup_verified': True,
                                    'failed': ['test_groups']}
        self.executor._command.side_effect = [(0, b'a' * 64), (1, b'')] * 3
        report = self.execute()
        self.assertEqual(report['calls'], 3)
        self.assertEqual(self.transport.call_count, 3)
        expected = ['agents-' + hashlib.sha256((pilot.ATTEMPT + ':' + str(n)).encode()).hexdigest()
                    for n in (1, 2, 3)]
        self.assertEqual([c.args[0][-1] for c in self.executor._command.call_args_list
                          if c.args[0][0] == 'container'], expected)

    def test_uncertain_provider_response_does_not_recheck_or_repair(self):
        self.transport.side_effect = TimeoutError(PRIVATE)
        report = self.execute()
        self.assertEqual(report['calls'], 1)
        self.assertEqual(report['reason'], 'api_failed_or_uncertain')
        self.transport.assert_called_once()
        self.tester.assert_not_called()
        self.assertEqual(self.executor._command.call_count, 2)


class BeforeKeyTests(unittest.TestCase):
    def test_initial_image_or_any_name_failure_blocks_key_and_journal(self):
        for replies in ([(125, b'')], [(0, b'a' * 64), (0, b'')],
                        [(0, b'a' * 64), (1, b''), (0, b'')],
                        [(0, b'a' * 64), (1, b''), (1, b''), (0, b'')]):
            with self.subTest(replies=replies), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(__file__).resolve().parents[4]
                argv = ['run.py', '--run-approved-once', '--repo-root', str(root),
                        '--image-id', 'a' * 64, '--runtime-home', '/synthetic/home',
                        '--runtime-dir', '/synthetic/runtime', '--acceptance-id', 'b' * 64,
                        '--state-directory', tmp]
                executor = Mock()
                executor._command.side_effect = replies
                stack.enter_context(patch.object(readiness, 'TestExecutor', return_value=executor))
                stack.enter_context(patch.object(readiness, '_bounded_call',
                                                 side_effect=lambda function, seconds: function()))
                stack.enter_context(patch.object(run.sys, 'argv', argv))
                stack.enter_context(patch.object(run.os, 'getuid', return_value=1003))
                stack.enter_context(patch.object(run.os, 'geteuid', return_value=1003))
                stack.enter_context(patch.object(run, 'trusted_directory', side_effect=lambda p, **kw: Path(p)))
                stack.enter_context(patch.object(run, 'PINNED_FILES', {}))
                key = stack.enter_context(patch.object(agents_credentials, 'read_application_key'))
                journal = stack.enter_context(patch.object(pilot, 'PilotJournal'))
                transport = stack.enter_context(patch.object(live_transport, 'ResponsesTransport'))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                self.assertEqual(run.main(), 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report['calls'], 0)
                self.assertIn('readiness_failure', report)
                key.assert_not_called()
                journal.assert_not_called()
                transport.assert_not_called()
                executor.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
