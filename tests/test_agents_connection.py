import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from ops.bounded_worker.agents_connection import AgentsConnection, ConnectionFailure, _NoRedirect
from ops.bounded_worker.agents_pilot import Plan
from ops.bounded_worker.task import Task
from ops.bounded_worker.executor_entry import connection_arguments, enter_executor, read_executor_key


class FakeResponse(io.BytesIO):
    status = 201
    def geturl(self):
        return self.url


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.response = FakeResponse(b'{"id":"sess_test"}')
        self.opener = Mock()
        def open_request(request, timeout):
            self.response.url = request.full_url
            return self.response
        self.opener.open.side_effect = open_request
        self.client = AgentsConnection('synthetic_application_key', opener=self.opener)
        self.plan = Plan(Task('fake', 'stevengalvis/modelfc', 'a' * 40,
                             'Synthetic task', ('clamp.py',)), 'test-model')

    def test_create_headers_and_no_input(self):
        self.assertEqual(self.client.create_session(self.plan), {'id': 'sess_test'})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/agents/sessions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic_application_key')
        self.assertEqual(request.get_header('Openai-beta'), 'agents=v1')
        self.assertNotIn('input', json.loads(request.data))
        self.opener.open.assert_called_once()

    def test_submit_cancel_and_retrieve_paths(self):
        for method, suffix in [('submit', '/events'), ('cancel', '/events'), ('retrieve', '')]:
            with self.subTest(method=method):
                self.setUp()
                if method != 'retrieve':
                    self.response = FakeResponse(b'')
                    self.response.status = 202
                if method == 'submit':
                    self.assertIsNone(self.client.submit_task('sess_test', self.plan))
                elif method == 'cancel':
                    self.assertIsNone(self.client.cancel_turn('sess_test'))
                else:
                    self.client.retrieve_session('sess_test')
                request = self.opener.open.call_args.args[0]
                self.assertTrue(request.full_url.endswith('/sess_test' + suffix))
                self.assertEqual(request.method, 'GET' if method == 'retrieve' else 'POST')

    def test_event_ack_requires_202_and_empty_body(self):
        for status, body in ((200, b''), (201, b''), (204, b''), (202, b'{}'), (202, b' ')):
            for operation in ('submit', 'cancel'):
                with self.subTest(status=status, body=body, operation=operation):
                    self.setUp()
                    self.response = FakeResponse(body)
                    self.response.status = status
                    with self.assertRaises(ConnectionFailure):
                        if operation == 'submit':
                            self.client.submit_task('sess_test', self.plan)
                        else:
                            self.client.cancel_turn('sess_test')
                    self.opener.open.assert_called_once()

    def test_empty_ack_is_not_valid_for_session_reads_or_creation(self):
        for operation in ('create', 'retrieve'):
            with self.subTest(operation=operation):
                self.setUp()
                self.response = FakeResponse(b'')
                self.response.status = 202
                with self.assertRaises(ConnectionFailure):
                    if operation == 'create':
                        self.client.create_session(self.plan)
                    else:
                        self.client.retrieve_session('sess_test')

    def test_environment_read_uses_fixed_official_resource(self):
        self.response = FakeResponse(b'{"id":"env_test","status":"connected"}')
        self.assertEqual(self.client.retrieve_environment('env_test')['status'], 'connected')
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/agents/environments/env_test')
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.get_header('Openai-beta'), 'agents=v1')
        self.opener.open.assert_called_once()

    def test_budget_update_has_its_own_operation_and_private_400_explanation(self):
        sink = Mock(return_value=True)
        self.client = AgentsConnection('synthetic_application_key', opener=self.opener,
                                       private_error_sink=sink)
        self.opener.open.side_effect = urllib.error.HTTPError(
            'https://api.openai.com/v1/agents/sessions/sess_test', 400, '', {},
            io.BytesIO(b'{"error":{"type":"invalid_request_error","param":"spend_control","message":"Unknown parameter"}}'))
        with self.assertRaises(ConnectionFailure):
            self.client.update_spend_limit('sess_test', 100)
        request = self.opener.open.call_args.args[0]
        self.assertEqual((request.method, request.full_url),
                         ('POST', 'https://api.openai.com/v1/agents/sessions/sess_test'))
        self.assertEqual(json.loads(request.data), {'spend_control': {'limit': 100}})
        self.assertEqual(self.client.diagnostic['operation'], 'update_spend')
        self.assertEqual(self.client.diagnostic['private_explanation'], 'displayed')
        sink.assert_called_once()
        self.opener.open.assert_called_once()
        for invalid in (None, True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                self.client.update_spend_limit('sess_test', invalid)
        self.opener.open.assert_called_once()

    def test_environment_path_injection_never_calls_transport(self):
        for value in ('../other', 'x?token=secret', 'x\n', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client.retrieve_environment(value)
        self.opener.open.assert_not_called()

    def test_path_injection_rejected_before_network(self):
        for value in ('../other', 'x?token=secret', 'x\n', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client.retrieve_session(value)
        self.opener.open.assert_not_called()

    def test_network_failure_has_no_retry_or_secret(self):
        self.opener.open.side_effect = OSError('synthetic_application_key')
        with self.assertRaises(ConnectionFailure) as caught:
            self.client.create_session(self.plan)
        self.assertNotIn('synthetic_application_key', str(caught.exception))
        self.opener.open.assert_called_once()
        self.assertEqual(self.client.diagnostic, {'operation': 'create', 'outcome': 'transport_error',
            'http_status': None, 'request_id': None, 'provider_code': None})

    def test_http_failure_retains_only_bounded_safe_metadata_without_retry(self):
        body = io.BytesIO(json.dumps({'error': {'code': 'insufficient_permissions',
            'message': 'synthetic_application_key', 'param': 'private'}}).encode())
        self.opener.open.side_effect = urllib.error.HTTPError(
            'https://api.openai.com/v1/agents/sessions', 403, 'private exception',
            {'x-request-id': 'req_synthetic'}, body)
        with self.assertRaises(ConnectionFailure) as caught:
            self.client.create_session(self.plan)
        self.assertEqual(self.client.diagnostic, {'operation': 'create', 'outcome': 'http_error',
            'http_status': 403, 'request_id': 'req_synthetic', 'provider_code': 'insufficient_permissions'})
        self.assertTrue(body.closed)
        self.assertNotIn('private', str(caught.exception))
        self.assertNotIn('synthetic_application_key', repr(self.client.diagnostic))
        self.opener.open.assert_called_once()

    def test_malformed_oversized_and_reflective_error_bodies_do_not_leak(self):
        for raw, code in ((b'not json', 'unavailable'), (b'x' * 5000, 'unavailable'),
            (b'{"error":{"code":"synthetic_application_key"}}', 'other'),
            (b'{"error":{"code":[]}}', 'other'),
            (b'{"error":{"code":"a","code":"invalid_api_key"}}', 'unavailable')):
            with self.subTest(code=code, length=len(raw)):
                self.setUp()
                body = io.BytesIO(raw)
                read = Mock(wraps=body.read)
                body.read = read
                self.opener.open.side_effect = urllib.error.HTTPError(
                    'https://api.openai.com/v1/agents/sessions', 401, 'private',
                    {'x-request-id': 'req_synthetic_application_key'}, body)
                with self.assertRaises(ConnectionFailure):
                    self.client.create_session(self.plan)
                self.assertEqual(self.client.diagnostic['provider_code'], code)
                self.assertIsNone(self.client.diagnostic['request_id'])
                self.assertNotIn('synthetic_application_key', repr(self.client.diagnostic))
                read.assert_called_once_with(4097)
                self.opener.open.assert_called_once()

    def test_success_keeps_metadata_for_later_policy_failure(self):
        self.response.headers = {'x-request-id': 'req_synthetic'}
        self.assertEqual(self.client.create_session(self.plan), {'id': 'sess_test'})
        self.assertEqual(self.client.diagnostic, {'operation': 'create', 'outcome': 'response_received',
            'http_status': 201, 'request_id': 'req_synthetic', 'provider_code': None})

    def test_create_400_private_explanation_is_transient_not_a_diagnostic_or_exception(self):
        received = []
        sink = Mock(side_effect=lambda private: received.append(repr(private)))
        self.client = AgentsConnection('synthetic_application_key', opener=self.opener,
                                       private_error_sink=sink)
        body = io.BytesIO(json.dumps({'error': {'code': None, 'type': 'invalid_request_error',
            'param': 'agent.model', 'message': 'private synthetic_application_key'}}).encode())
        self.opener.open.side_effect = urllib.error.HTTPError(
            'https://api.openai.com/v1/agents/sessions', 400, 'raw private text', {}, body)
        with self.assertRaises(ConnectionFailure) as caught:
            self.client.create_session(self.plan)
        self.assertEqual(self.client.diagnostic['provider_type'], 'invalid_request_error')
        self.assertEqual(self.client.diagnostic['provider_param'], 'agent.model')
        self.assertEqual(self.client.diagnostic['provider_code'], 'other')
        sink.assert_called_once()
        self.assertEqual(sink.call_args.args[0].take_fields(), {})
        self.assertNotIn('synthetic_application_key', repr(sink.call_args))
        self.assertNotIn('synthetic_application_key', repr(self.client.diagnostic))
        self.assertNotIn('raw private text', str(caught.exception))
        self.opener.open.assert_called_once()

    def test_private_sink_gets_no_unparseable_oversized_or_duplicate_body_fallback(self):
        for raw in (b'private not json', b'x' * 4097,
                    b'{"error":{"message":"private","message":"other"}}'):
            with self.subTest(size=len(raw)):
                self.setUp()
                received = []
                self.client = AgentsConnection('synthetic_application_key', opener=self.opener,
                    private_error_sink=lambda private: received.append(private.take_fields()))
                self.opener.open.side_effect = urllib.error.HTTPError(
                    'https://api.openai.com/v1/agents/sessions', 400, '', {}, io.BytesIO(raw))
                with self.assertRaises(ConnectionFailure):
                    self.client.create_session(self.plan)
                self.assertEqual(received, [{}])
                self.assertEqual(self.client.diagnostic['provider_type'], 'unavailable')
                self.opener.open.assert_called_once()

    def test_private_sink_only_handles_create_400_and_its_failure_does_not_replay(self):
        for operation, status in (('create', 403), ('retrieve', 400), ('create', 400)):
            with self.subTest(operation=operation, status=status):
                self.setUp()
                sink = Mock(side_effect=RuntimeError('private sink failure'))
                self.client = AgentsConnection('synthetic_application_key', opener=self.opener,
                                               private_error_sink=sink)
                suffix = '' if operation == 'create' else '/sess_test'
                self.opener.open.side_effect = urllib.error.HTTPError(
                    'https://api.openai.com/v1/agents/sessions' + suffix, status, '', {},
                    io.BytesIO(b'{"error":{"code":"unknown_code","type":"private_type"}}'))
                with self.assertRaises(ConnectionFailure) as caught:
                    if operation == 'create':
                        self.client.create_session(self.plan)
                    else:
                        self.client.retrieve_session('sess_test')
                self.assertEqual(sink.call_count, int(operation == 'create' and status == 400))
                self.assertNotIn('private', str(caught.exception))
                self.assertEqual(self.client.diagnostic['provider_code'], 'other')
                self.opener.open.assert_called_once()

    def test_response_read_transport_failure_keeps_status_without_claiming_policy_rejection(self):
        for error in (TimeoutError('private'), ConnectionResetError('private'),
                      http.client.IncompleteRead(b'private')):
            with self.subTest(kind=type(error).__name__):
                self.setUp()
                self.response.status = 200
                self.response.headers = {'x-request-id': 'req_synthetic'}
                self.response.read = Mock(side_effect=error)
                with self.assertRaises(ConnectionFailure):
                    self.client.create_session(self.plan)
                self.assertEqual(self.client.diagnostic, {'operation': 'create',
                    'outcome': 'transport_error', 'http_status': 200,
                    'request_id': 'req_synthetic', 'provider_code': None})
                self.opener.open.assert_called_once()

    def test_later_cleanup_request_does_not_erase_first_rest_failure(self):
        self.opener.open.side_effect = OSError('private')
        with self.assertRaises(ConnectionFailure):
            self.client.submit_task('sess_test', self.plan)
        first = self.client.diagnostic
        response = FakeResponse(b'')
        response.status = 202
        response.url = 'https://api.openai.com/v1/agents/sessions/sess_test/events'
        self.opener.open.side_effect = None
        self.opener.open.return_value = response
        self.client.cancel_turn('sess_test')
        self.assertEqual(self.client.diagnostic, first)

    def test_bad_responses_fail(self):
        for body in (b'[]', b'{"id":1,"id":2}', b'{"v":NaN}', b'{"v":1e999}', b'{"v":{"nested":-1e999}}', b'x' * 262145):
            with self.subTest(size=len(body)):
                self.response = FakeResponse(body)
                with self.assertRaises(ConnectionFailure):
                    self.client.retrieve_session('sess_test')

    def test_redirect_denied(self):
        with self.assertRaises(ConnectionFailure):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')

    def test_default_opener_ignores_proxy_environment(self):
        with patch('urllib.request.build_opener') as build:
            AgentsConnection('synthetic')
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], _NoRedirect)


class ExecutorTests(unittest.TestCase):
    url = 'https://api.openai.com/example?x=1'

    def pipe_key(self, value):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, value)
        os.close(write_fd)
        return read_fd

    def test_pipe_key_consumed_and_closed(self):
        fd = self.pipe_key(b'synthetic_executor_key')
        self.assertEqual(read_executor_key(fd), 'synthetic_executor_key')
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_reject_file_key(self):
        with tempfile.TemporaryFile() as file:
            file.write(b'fake')
            with self.assertRaises(ValueError):
                read_executor_key(file.fileno())

    def test_key_validation(self):
        for raw in (b'', b'fake\n', b'x' * 4097, b'\xff'):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                read_executor_key(self.pipe_key(raw))

    def test_remote_url_preserved_and_no_key_in_argv(self):
        args = connection_arguments('/opt/agents/bin/codex', 'env_test', self.url)
        self.assertEqual(args, ['/opt/agents/bin/codex', 'exec-server', '--remote', self.url,
                                '--environment-id', 'env_test'])
        for url in ('http://bad', 'wss://bad.example/x',
                    'wss://codex-cloud-environments.chatgpt.com/x',
                    'https://api.openai.com.evil.example/x', 'https://other.example/x',
                    'https://api.openai.com:444/x', 'https://user@api.openai.com/x',
                    'wss://codex-cloud-environments.chatgpt.com@bad.example/x',
                    self.url + '#fragment', self.url + '\n'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                connection_arguments('/opt/agents/bin/codex', 'env_test', url)

    def test_registration_url_preserved_without_relay_derivation(self):
        for url in ('https://api.openai.com/opaque?binding=synthetic',
                    'https://api.openai.com:443/opaque?binding=synthetic'):
            with self.subTest(url=url):
                args = connection_arguments('/opt/agents/bin/codex', 'env_test', url)
                self.assertEqual(args[3], url)

    def test_inheritable_descriptor_and_standard_streams_are_closed(self):
        with tempfile.TemporaryFile() as sentinel:
            code = """
import os, sys
from ops.bounded_worker.executor_entry import sanitize_fds
sentinel = int(sys.argv[1])
sanitize_fds()
try:
    os.fstat(sentinel)
except OSError:
    pass
else:
    os._exit(10)
if any(os.readlink('/proc/self/fd/' + str(fd)) != '/dev/null' for fd in (0, 1, 2)):
    os._exit(11)
os._exit(0)
"""
            result = subprocess.run([sys.executable, '-B', '-c', code, str(sentinel.fileno())],
                                    pass_fds=(sentinel.fileno(),), timeout=5, check=False)
        self.assertEqual(result.returncode, 0)

    def test_closed_standard_streams_remain_open_across_exec(self):
        verifier = """
import os
try:
    valid = all(os.readlink('/proc/self/fd/' + str(fd)) == '/dev/null' for fd in (0, 1, 2))
except OSError:
    os._exit(12)
os._exit(0 if valid else 13)
"""
        code = """
import os, sys
from ops.bounded_worker.executor_entry import sanitize_fds
for fd in sys.argv[1].split(','):
    os.close(int(fd))
sanitize_fds()
os.execv(sys.executable, [sys.executable, '-B', '-c', sys.argv[2]])
"""
        for closed in ('0', '1', '2', '0,1,2'):
            with self.subTest(closed=closed):
                result = subprocess.run([sys.executable, '-B', '-c', code, closed, verifier],
                                        capture_output=True, timeout=5, check=False)
                self.assertEqual(result.returncode, 0)

    def test_enter_clears_environment_and_uses_exec_not_shell(self):
        fd = self.pipe_key(b'synthetic_executor_key')
        with patch('os.getuid', return_value=1000), patch('os.geteuid', return_value=1000), \
             patch('os.chdir') as chdir, patch('os.execve') as execute, \
             patch('ops.bounded_worker.executor_entry.sanitize_fds') as sanitize, \
             patch.dict(os.environ, {'OPENAI_API_KEY': 'never_forward_this'}):
            enter_executor(fd, 'env_test', self.url)
        sanitize.assert_called_once_with()
        chdir.assert_called_once_with('/workspace')
        args = execute.call_args.args
        self.assertNotIn('synthetic_executor_key', repr(args[:2]))
        self.assertNotIn('OPENAI_API_KEY', args[2])
        self.assertEqual(args[2]['CODEX_API_KEY'], 'synthetic_executor_key')
        self.assertNotIn('never_forward_this', repr(args))


if __name__ == '__main__':
    unittest.main()
