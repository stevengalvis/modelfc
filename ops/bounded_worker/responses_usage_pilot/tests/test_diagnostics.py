"""Adversarial offline transport/receipt tests. All HTTP is injected and mocked."""
import errno
import http.client
import json
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch

from diagnostics import failure_details, transport_snapshot
from live_transport import ResponsesTransport, TransportFailure
from pilot import PilotJournal, propose, request_payload
from run import run_pipeline
from test_live import response

PRIVATE = 'SYNTHETIC_PRIVATE_SENTINEL'
REQUEST_ID = 'req_' + 'a' * 32


class Deadline(BaseException):
    pass


def fake_transport(body=None, status=200, request_id=REQUEST_ID, encoding='identity'):
    reply = Mock(status=status)
    reply.getheader.side_effect = lambda name, default=None: {
        'x-request-id': request_id, 'Content-Encoding': encoding}.get(name, default)
    reply.read.return_value = response() if body is None else body
    connection = Mock()
    connection.getresponse.return_value = reply
    factory = Mock(return_value=connection)
    bounded = Mock(side_effect=lambda operation, seconds: operation())
    transport = ResponsesTransport(PRIVATE, bounded, connection_factory=factory)
    return transport, connection, reply, factory, bounded


def invoke(transport):
    return transport(payload=request_payload(), timeout_seconds=60, max_response_bytes=65536)


class SafeTransportTests(unittest.TestCase):
    def setUp(self):
        # A mistakenly unmocked connection fails locally rather than reaching a network.
        self.guard = patch('socket.create_connection', side_effect=AssertionError('network forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_connect_failure_is_not_sent_and_never_retries(self):
        cases = [(socket.gaierror(-2, PRIVATE), 'dns_error', None),
                 (PermissionError(errno.EACCES, PRIVATE), 'permission_denied', errno.EACCES),
                 (ssl.SSLCertVerificationError(1, PRIVATE), 'tls_verification_error', None),
                 (ConnectionRefusedError(errno.ECONNREFUSED, PRIVATE), 'connection_refused',
                  errno.ECONNREFUSED)]
        for error, category, number in cases:
            with self.subTest(category=category):
                transport, connection, _, _, _ = fake_transport()
                connection.connect.side_effect = error
                with self.assertRaises(TransportFailure):
                    invoke(transport)
                diagnostic = transport.diagnostic_snapshot()
                self.assertEqual(diagnostic['phase'], 'connect')
                self.assertEqual(diagnostic['delivery'], 'not_sent')
                self.assertEqual(diagnostic['category'], category)
                self.assertEqual(diagnostic['os_errno'], number)
                connection.connect.assert_called_once()
                connection.request.assert_not_called()
                connection.close.assert_called_once()
                self.assertNotIn(PRIVATE, json.dumps(diagnostic))

    def test_send_failure_and_deadline_always_possibly_sent(self):
        for error, category in [(TimeoutError(PRIVATE), 'timeout'),
                                (BrokenPipeError(errno.EPIPE, PRIVATE), 'broken_pipe'),
                                (Deadline(PRIVATE), 'interrupted_or_deadline')]:
            with self.subTest(category=category):
                transport, connection, _, _, _ = fake_transport()
                connection.request.side_effect = error
                with self.assertRaises(TransportFailure):
                    invoke(transport)
                diagnostic = transport.diagnostic_snapshot()
                self.assertEqual(diagnostic['phase'], 'send')
                self.assertEqual(diagnostic['delivery'], 'possibly_sent')
                self.assertEqual(diagnostic['category'], category)
                self.assertIsNone(diagnostic['http_status'])
                connection.request.assert_called_once()
                connection.getresponse.assert_not_called()
                self.assertEqual(connection.auto_open, 0)

    def test_headers_timeout_preserves_uncertainty(self):
        transport, connection, _, _, _ = fake_transport()
        connection.getresponse.side_effect = TimeoutError(PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        diagnostic = transport.diagnostic_snapshot()
        self.assertEqual(diagnostic['phase'], 'response_headers')
        self.assertEqual(diagnostic['delivery'], 'possibly_sent')
        self.assertIsNone(diagnostic['http_status'])

    def test_remote_disconnect_is_distinct_from_connection_reset(self):
        transport, connection, _, _, _ = fake_transport()
        connection.getresponse.side_effect = http.client.RemoteDisconnected(PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        diagnostic = transport.diagnostic_snapshot()
        self.assertEqual(diagnostic['category'], 'remote_disconnected')
        self.assertEqual(diagnostic['delivery'], 'possibly_sent')
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))

    def test_body_timeout_retains_http_metadata_not_raw_error(self):
        transport, _, reply, _, _ = fake_transport()
        reply.read.side_effect = TimeoutError(PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        diagnostic = transport.diagnostic_snapshot()
        self.assertEqual(diagnostic['phase'], 'response_body')
        self.assertEqual(diagnostic['delivery'], 'response_received')
        self.assertEqual(diagnostic['http_status'], 200)
        self.assertEqual(diagnostic['request_id'], REQUEST_ID)
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))

    def test_http_error_never_reads_or_records_provider_body(self):
        for status in (302, 400, 401, 429, 500):
            transport, connection, reply, _, _ = fake_transport(PRIVATE.encode(), status)
            with self.assertRaises(TransportFailure):
                invoke(transport)
            diagnostic = transport.diagnostic_snapshot()
            self.assertEqual(diagnostic['http_status'], status)
            self.assertEqual(diagnostic['category'], 'http_status_rejected')
            self.assertEqual(diagnostic['delivery'], 'response_received')
            reply.read.assert_not_called()
            connection.request.assert_called_once()
            self.assertNotIn(PRIVATE, json.dumps(diagnostic))

    def test_malformed_duplicate_or_oversized_request_id_is_discarded(self):
        for value in (None, True, PRIVATE, REQUEST_ID + ', ' + REQUEST_ID,
                      REQUEST_ID + '\n' + PRIVATE, 'req_' + 'a' * 10000):
            transport, _, _, _, _ = fake_transport(request_id=value)
            invoke(transport)
            self.assertIsNone(transport.diagnostic_snapshot()['request_id'])

    def test_request_id_cannot_echo_the_application_key(self):
        for key in (REQUEST_ID, 'a' * 16):
            transport, _, _, _, _ = fake_transport()
            transport.key = key  # Synthetic adversarial key, never a saved secret.
            invoke(transport)
            self.assertIsNone(transport.diagnostic_snapshot()['request_id'])
            self.assertNotIn(key, json.dumps(transport.diagnostic_snapshot()))

    def test_repair_resets_status_and_request_id_before_connect(self):
        transport, connection, _, _, _ = fake_transport(status=429)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        connection.connect.side_effect = TimeoutError(PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        diagnostic = transport.diagnostic_snapshot()
        self.assertIsNone(transport.http_status)
        self.assertIsNone(diagnostic['http_status'])
        self.assertIsNone(diagnostic['request_id'])
        self.assertEqual(diagnostic['delivery'], 'not_sent')
        self.assertEqual(connection.request.call_count, 1)

    def test_close_error_does_not_mask_first_failure(self):
        transport, connection, _, _, _ = fake_transport()
        connection.request.side_effect = TimeoutError(PRIVATE)
        connection.close.side_effect = PermissionError(errno.EACCES, PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        diagnostic = transport.diagnostic_snapshot()
        self.assertEqual(diagnostic['category'], 'timeout')
        self.assertEqual(diagnostic['phase'], 'send')
        self.assertEqual(diagnostic['connection_close'], 'failed')

    def test_close_error_after_response_retains_response_evidence(self):
        transport, connection, _, _, _ = fake_transport()
        connection.close.side_effect = OSError(errno.EIO, PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        diagnostic = transport.diagnostic_snapshot()
        self.assertEqual(diagnostic['delivery'], 'response_received')
        self.assertEqual(diagnostic['http_status'], 200)
        self.assertEqual(diagnostic['connection_close'], 'failed')
        self.assertEqual(diagnostic['category'], 'os_error')

    def test_factory_tls_and_bounded_setup_failures_are_not_sent(self):
        transport, connection, _, factory, bounded = fake_transport()
        with patch('live_transport.ssl.create_default_context', side_effect=PermissionError(errno.EACCES, PRIVATE)):
            with self.assertRaises(TransportFailure):
                invoke(transport)
        self.assertEqual(transport.diagnostic_snapshot()['phase'], 'tls_context')
        factory.assert_not_called()
        transport, connection, _, factory, bounded = fake_transport()
        bounded.side_effect = Deadline(PRIVATE)
        with self.assertRaises(TransportFailure):
            invoke(transport)
        self.assertEqual(transport.diagnostic_snapshot()['delivery'], 'not_sent')
        factory.assert_not_called()

    def test_invalid_status_and_encoding_are_classified(self):
        for status in (True, 99, 600, PRIVATE):
            transport, _, _, _, _ = fake_transport(status=status)
            with self.assertRaises(TransportFailure):
                invoke(transport)
            self.assertEqual(transport.diagnostic_snapshot()['category'], 'invalid_http_status')
            self.assertIsNone(transport.http_status)
        transport, _, _, _, _ = fake_transport(encoding='gzip')
        with self.assertRaises(TransportFailure):
            invoke(transport)
        self.assertEqual(transport.diagnostic_snapshot()['category'], 'content_encoding_rejected')

    def test_validation_failure_never_connects(self):
        transport, _, _, factory, _ = fake_transport()
        with self.assertRaises(ValueError):
            transport(payload={**request_payload(), 'input': PRIVATE},
                      timeout_seconds=60, max_response_bytes=65536)
        diagnostic = transport.diagnostic_snapshot()
        self.assertEqual(diagnostic['category'], 'validation_error')
        self.assertEqual(diagnostic['delivery'], 'not_sent')
        factory.assert_not_called()
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))

    def test_snapshot_is_closed_schema_and_exact_primitive_types(self):
        diagnostic = transport_snapshot({
            'phase': PRIVATE, 'category': PRIVATE, 'delivery': PRIVATE,
            'http_status': True, 'request_id': PRIVATE, 'os_errno': True,
            'connection_close': PRIVATE, 'headers': {'Authorization': PRIVATE}})
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))
        self.assertNotIn('headers', diagnostic)
        self.assertIsNone(diagnostic['http_status'])
        self.assertIsNone(diagnostic['os_errno'])
        self.assertEqual(diagnostic['delivery'], 'unknown')
        self.assertIsNone(failure_details(OSError(999999, PRIVATE))['os_errno'])


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.journal = PilotJournal(self.root)

    def tearDown(self):
        self.journal.close()
        self.temporary.cleanup()

    def test_unknown_request_retains_private_receipt_and_no_false_cleanup_or_usage(self):
        transport, connection, _, _, _ = fake_transport()
        connection.getresponse.side_effect = TimeoutError(PRIVATE)
        tester = Mock()
        result = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(result['calls'], 1)
        self.assertEqual(result['usage_state'], 'unknown')
        self.assertIsNone(result['input_tokens'])
        self.assertIsNone(result['output_tokens'])
        self.assertIsNone(result['cleanup_verified'])
        self.assertEqual(result['cleanup_state'], 'not_started')
        receipt = self.root / 'diagnostic-1.json'
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        diagnostic = json.loads(receipt.read_text())
        self.assertEqual(diagnostic['transport']['delivery'], 'possibly_sent')
        self.assertNotIn(PRIVATE, receipt.read_text() + json.dumps(result))
        tester.assert_not_called()
        self.assertEqual(self.journal.db.execute('SELECT n,state FROM responses_usage_pilot').fetchall(),
                         [(1, 'reserved')])
        with self.assertRaises(ValueError):
            propose(self.journal, transport, ('test_zero',))
        connection.request.assert_called_once()

    def test_parse_failure_has_distinct_stage_and_http_metadata(self):
        transport, _, _, _, _ = fake_transport(body=('not JSON ' + PRIVATE).encode())
        tester = Mock()
        result = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(result['diagnostic']['phase'], 'response_parse')
        self.assertEqual(result['reason'], 'response_validation_failed')
        self.assertEqual(result['diagnostic']['category'], 'invalid_json')
        self.assertEqual(result['diagnostic']['transport']['http_status'], 200)
        self.assertEqual(result['diagnostic']['transport']['request_id'], REQUEST_ID)
        self.assertIsNone(result['input_tokens'])
        self.assertNotIn(PRIVATE, json.dumps(result))
        self.assertFalse((self.root / 'candidate-1.py').exists())
        tester.assert_not_called()

    def test_journal_transition_failure_retains_already_validated_usage(self):
        transport, _, _, _, _ = fake_transport()
        with patch.object(self.journal, 'transition', side_effect=OSError(errno.EIO, PRIVATE)):
            result = run_pipeline(self.journal, transport, Mock(), self.root)
        self.assertEqual(result['diagnostic']['phase'], 'journal_transition')
        self.assertEqual(result['reason'], 'journal_transition_failed')
        self.assertEqual(result['input_tokens'], 400)
        self.assertEqual(result['output_tokens'], 200)
        self.assertEqual(result['usage_state'], 'complete')
        self.assertEqual(result['cleanup_state'], 'not_started')

    def test_patch_generation_failure_is_not_mislabeled_as_parse_or_transport(self):
        transport, _, _, _, _ = fake_transport()
        with patch('pilot.candidate_patch', side_effect=ValueError(PRIVATE)):
            result = run_pipeline(self.journal, transport, Mock(), self.root)
        self.assertEqual(result['diagnostic']['phase'], 'patch_generation')
        self.assertEqual(result['reason'], 'patch_generation_failed')
        self.assertEqual(result['input_tokens'], 400)
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_second_call_unknown_usage_is_not_reported_as_complete_total(self):
        transport, first, reply, factory, _ = fake_transport()
        second = Mock()
        second.connect.side_effect = TimeoutError(PRIVATE)
        factory.side_effect = [first, second]
        tester = Mock(return_value={'status': 'tests_failed', 'cleanup_verified': True,
                                    'failed': ['test_groups']})
        result = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(result['calls'], 2)
        self.assertEqual(result['usage_state'], 'partial')
        self.assertIsNone(result['input_tokens'])
        self.assertEqual(result['validated_input_tokens'], 400)
        self.assertEqual(result['validated_output_tokens'], 200)
        diagnostic = json.loads((self.root / 'diagnostic-2.json').read_text())
        self.assertIsNone(diagnostic['transport']['http_status'])
        self.assertEqual(diagnostic['transport']['delivery'], 'not_sent')
        first.request.assert_called_once()
        second.request.assert_not_called()
        tester.assert_called_once()

    def test_diagnostic_write_failure_never_retries_or_exposes_exception(self):
        transport, connection, _, _, _ = fake_transport()
        tester = Mock()
        with patch('run.save_result', side_effect=PermissionError(errno.EACCES, PRIVATE)):
            result = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(result['pipeline_failure']['phase'], 'save_call_diagnostic')
        self.assertEqual(result['pipeline_failure']['category'], 'permission_denied')
        self.assertEqual(result['calls'], 1)
        self.assertEqual(result['input_tokens'], 400)
        self.assertNotIn(PRIVATE, json.dumps(result))
        tester.assert_not_called()
        connection.request.assert_called_once()

    def test_existing_receipt_is_never_overwritten(self):
        path = self.root / 'diagnostic-1.json'
        path.write_text('original synthetic evidence')
        transport, connection, _, _, _ = fake_transport()
        result = run_pipeline(self.journal, transport, Mock(), self.root)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['pipeline_failure']['phase'], 'save_call_diagnostic')
        self.assertEqual(path.read_text(), 'original synthetic evidence')
        connection.request.assert_called_once()

    def test_test_receipt_write_failure_retains_confirmed_cleanup(self):
        import run
        original = run.save_result
        def save(directory, name, data):
            if name == 'test-1.json':
                raise PermissionError(errno.EACCES, PRIVATE)
            return original(directory, name, data)
        transport, _, _, _, _ = fake_transport()
        tester = Mock(return_value={'status': 'passed', 'cleanup_verified': True, 'failed': []})
        with patch('run.save_result', side_effect=save):
            result = run_pipeline(self.journal, transport, tester, self.root)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['pipeline_failure']['phase'], 'save_test_result')
        self.assertEqual(result['cleanup_state'], 'verified')
        self.assertTrue(result['cleanup_verified'])
        self.assertNotIn(PRIVATE, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
