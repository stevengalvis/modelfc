import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from live_transport import ResponsesTransport, TransportFailure
from pilot import PilotJournal, request_payload
from run import run_pipeline
from safe_task import run_tests, validate_source

REFERENCE = '''def summarize_usage(records):
    if not isinstance(records, list):
        raise ValueError('records')
    result = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('record')
        model = record.get('model')
        if not isinstance(model, str) or not model.strip():
            raise ValueError('model')
        input_tokens = record.get('input_tokens')
        output_tokens = record.get('output_tokens')
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError('tokens')
        item = result.setdefault(model, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0})
        item['calls'] += 1
        item['input_tokens'] += input_tokens
        item['output_tokens'] += output_tokens
        item['total_tokens'] += input_tokens + output_tokens
    return result
'''


def response(source=REFERENCE):
    return json.dumps({
        'status': 'completed', 'model': 'gpt-6-luna', 'error': None,
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': source}]}],
        'usage': {'input_tokens': 400, 'output_tokens': 200, 'total_tokens': 600},
    }).encode()


class HTTPTests(unittest.TestCase):
    def connection(self, body=b'{}', status=200, encoding='identity'):
        connection = Mock()
        reply = Mock(status=status)
        reply.getheader.return_value = encoding
        reply.read.return_value = body
        connection.getresponse.return_value = reply
        factory = Mock(return_value=connection)
        bound = Mock(side_effect=lambda function, seconds: function())
        transport = ResponsesTransport('synthetic-test-key', bound, connection_factory=factory)
        return transport, factory, connection, bound

    def call(self, transport, payload=None):
        return transport(payload=payload or request_payload(), timeout_seconds=60,
                         max_response_bytes=65536)

    def test_exact_host_path_no_proxy_or_retry_and_close(self):
        transport, factory, connection, bound = self.connection()
        self.assertEqual(self.call(transport), b'{}')
        self.assertEqual(factory.call_args.args, ('api.openai.com', 443))
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ('POST', '/v1/responses'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer synthetic-test-key')
        self.assertEqual(json.loads(kwargs['body']), request_payload())
        self.assertEqual(bound.call_args.args[1], 60)
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_redirect_encoded_body_overflow_and_http_failure_no_retry(self):
        for body, status, encoding in ((b'', 302, 'identity'), (b'', 200, 'gzip'),
                                       (b'x' * 65537, 200, 'identity'),
                                       (b'private provider details', 400, 'identity')):
            transport, _, connection, _ = self.connection(body, status, encoding)
            with self.assertRaisesRegex(TransportFailure, '^request_failed_or_uncertain$'):
                self.call(transport)
            connection.request.assert_called_once()
            connection.close.assert_called_once()

    def test_forbidden_content_model_and_settings_never_connect(self):
        for update in ({'model': 'other'}, {'input': 'private data'},
                       {'tools': [{'type': 'web_search'}]}, {'max_output_tokens': 99999}):
            transport, factory, _, _ = self.connection()
            with self.assertRaises(ValueError):
                self.call(transport, {**request_payload(), **update})
            factory.assert_not_called()

    def test_known_repair_allowed(self):
        transport, _, connection, _ = self.connection()
        self.call(transport, request_payload(('test_empty', 'test_groups')))
        connection.request.assert_called_once()

    def test_timeout_does_not_leak_error_or_retry(self):
        transport, _, connection, _ = self.connection()
        connection.request.side_effect = TimeoutError('synthetic-test-key')
        with self.assertRaisesRegex(TransportFailure, '^request_failed_or_uncertain$'):
            self.call(transport)
        connection.request.assert_called_once()
        connection.close.assert_called_once()
        transport.close()
        self.assertIsNone(transport.key)


class SourceTests(unittest.TestCase):
    def test_reference_passes_fixed_tests(self):
        # This is author-written test data, not model output executed on the host.
        self.assertEqual(run_tests(REFERENCE), [])

    def test_wrong_reference_reports_fixed_test_ids(self):
        failed = run_tests('def summarize_usage(records):\n    return {}\n')
        self.assertIn('test_groups', failed)
        self.assertNotIn('test_empty', failed)

    def test_escape_forms_rejected_without_execution(self):
        bodies = [
            'import os\ndef summarize_usage(records):\n    return {}',
            'def summarize_usage(records):\n    return records.__class__',
            'def summarize_usage(records):\n    return eval("1")',
            'def summarize_usage(records):\n    return __builtins__',
            'def summarize_usage(records):\n    print("forged output")',
            'def summarize_usage(records):\n    def helper():\n        return {}\n    return helper()',
            'def summarize_usage(records, extra=[]):\n    return {}',
            'def summarize_usage(records):\n    dict = 1\n    return {}',
        ]
        for source in bodies:
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_source(source)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.journal = PilotJournal(self.root)

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def test_live_invocation_cannot_restart(self):
        self.journal.reserve_invocation()
        self.journal.close()
        self.journal = PilotJournal(self.root)
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.reserve_invocation()

    def test_pass_stops_after_one_call_records_usage(self):
        transport = Mock(return_value=response())
        tester = Mock(return_value={'status': 'passed', 'cleanup_verified': True, 'failed': []})
        report = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['calls'], 1)
        self.assertEqual(report['input_tokens'], 400)
        self.assertTrue((self.root / 'usage-1.json').is_file())
        transport.assert_called_once()
        tester.assert_called_once()

    def test_three_failed_test_runs_then_stop(self):
        transport = Mock(return_value=response())
        tester = Mock(return_value={'status': 'tests_failed', 'cleanup_verified': True,
                                  'failed': ['test_groups']})
        report = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['calls'], 3)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(tester.call_count, 3)

    def test_no_second_call_when_cleanup_unverified(self):
        transport = Mock(return_value=response())
        tester = Mock(return_value={'status': 'tests_failed', 'cleanup_verified': False,
                                  'failed': ['test_groups']})
        report = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(report['status'], 'blocked')
        self.assertFalse(report['cleanup_verified'])
        transport.assert_called_once()

    def test_tester_error_retains_paid_call_count_and_usage(self):
        transport = Mock(return_value=response())
        tester = Mock(side_effect=ValueError('DO_NOT_LEAK'))
        report = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(report['calls'], 1)
        self.assertEqual(report['output_tokens'], 200)
        self.assertNotIn('DO_NOT_LEAK', json.dumps(report))
        self.assertEqual(report['status'], 'blocked')
        transport.assert_called_once()


if __name__ == '__main__':
    unittest.main()
