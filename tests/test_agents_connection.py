import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
                if method == 'submit':
                    self.client.submit_task('sess_test', self.plan)
                elif method == 'cancel':
                    self.client.cancel_turn('sess_test')
                else:
                    self.client.retrieve_session('sess_test')
                request = self.opener.open.call_args.args[0]
                self.assertTrue(request.full_url.endswith('/sess_test' + suffix))
                self.assertEqual(request.method, 'GET' if method == 'retrieve' else 'POST')

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
    url = 'wss://codex-cloud-environments.chatgpt.com/example?x=1'

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
                    'wss://codex-cloud-environments.chatgpt.com@bad.example/x',
                    self.url + '#fragment', self.url + '\n'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                connection_arguments('/opt/agents/bin/codex', 'env_test', url)

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
