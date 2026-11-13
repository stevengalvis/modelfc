"""Offline input-lifetime regressions. No Podman or generated candidate runs."""
import json
import os
import select
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import sandbox
from ops.bounded_worker.agents_live_pilot import LiveFailure
from test_live import REFERENCE
import test_sandbox as fixtures


VALID = b'{"failed": [], "passed": true}\n'
PRIVATE = 'PRIVATE_DIAGNOSTIC_SENTINEL'


class FramingTests(unittest.TestCase):
    def test_frame_is_exact_magic_length_and_source_bytes(self):
        raw = REFERENCE.encode()
        self.assertEqual(sandbox.source_frame(REFERENCE), b'USG1' + len(raw).to_bytes(4, 'big') + raw)

    def test_frame_length_counts_utf8_bytes(self):
        raw = 'a\u00e9'.encode()
        self.assertEqual(sandbox.source_frame('a\u00e9'), b'USG1' + b'\0\0\0\3' + raw)

    def test_frame_rejects_empty_overlimit_or_wrong_type(self):
        for source in ('', 'x' * 8193, '\u00e9' * 4097, b'x', None):
            with self.subTest(kind=type(source).__name__), self.assertRaises(ValueError):
                sandbox.source_frame(source)
        self.assertEqual(len(sandbox.source_frame('x' * 8192)), 8200)

    def process(self):
        # This is the fixed harness plus an author-written reference fixture.
        # No VPS/model-generated code is executed on this host.
        program = sandbox.gate_program().replace('mount_status = _mount_check()',
                                                'mount_status = "verified"')
        p = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env={'LANG': 'C.UTF-8'})
        def close():
            if p.poll() is None:
                p.kill()
            p.wait(timeout=2)
            for stream in (p.stdin, p.stdout, p.stderr):
                stream.close()
        self.addCleanup(close)
        self.assertEqual(self.line(p.stdout), b'NESTED_READY:verified\n')
        return p

    def line(self, stream):
        data, deadline = bytearray(), time.monotonic() + 2
        while not data.endswith(b'\n'):
            left = deadline - time.monotonic()
            self.assertGreater(left, 0)
            self.assertTrue(select.select([stream], [], [], left)[0])
            chunk = os.read(stream.fileno(), 1)
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def test_gate_finishes_and_closes_outputs_while_parent_input_stays_open(self):
        p = self.process()
        os.write(p.stdin.fileno(), sandbox.source_frame(REFERENCE))
        self.assertEqual(self.line(p.stdout), VALID)
        self.assertFalse(p.stdin.closed)
        self.assertEqual(p.wait(timeout=2), 0)
        self.assertEqual(p.stdout.read(), b'')
        self.assertEqual(p.stderr.read(), b'')

    def test_gate_handles_fragmented_header_and_body_without_input_eof(self):
        p = self.process()
        data = sandbox.source_frame(REFERENCE)
        for part in (data[:1], data[1:5], data[5:8], data[8:25], data[25:]):
            os.write(p.stdin.fileno(), part)
        self.assertEqual(self.line(p.stdout), VALID)
        self.assertFalse(p.stdin.closed)
        self.assertEqual(p.wait(timeout=2), 0)

    def test_gate_rejects_bad_magic_zero_and_oversized_declared_length(self):
        for header in (b'NOPE\0\0\0\1', b'USG1\0\0\0\0', b'USG1\0\0 \1'):
            with self.subTest(header=header):
                p = self.process()
                os.write(p.stdin.fileno(), header)
                self.assertEqual(p.wait(timeout=2), 75)
                self.assertEqual(p.stdout.read(), b'')

    def test_gate_rejects_truncated_header_or_body_without_result(self):
        for frame in (b'USG', b'USG1\0\0\0\4x'):
            with self.subTest(frame=frame):
                p = self.process()
                os.write(p.stdin.fileno(), frame)
                p.stdin.close()
                self.assertEqual(p.wait(timeout=2), 75)
                self.assertEqual(p.stdout.read(), b'')

    def test_gate_input_has_one_total_deadline_without_eof(self):
        program = sandbox.gate_program().replace('mount_status = _mount_check()',
            'mount_status = "verified"').replace('time.monotonic() + 20', 'time.monotonic() + 0.05')
        p = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env={'LANG': 'C.UTF-8'})
        try:
            self.assertEqual(self.line(p.stdout), b'NESTED_READY:verified\n')
            os.write(p.stdin.fileno(), b'USG1\0\0\0\4x')
            self.assertFalse(p.stdin.closed)
            self.assertEqual(p.wait(timeout=2), 74)
            self.assertEqual(p.stdout.read(), b'')
        finally:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=2)
            for stream in (p.stdin, p.stdout, p.stderr):
                stream.close()

    def test_no_input_close_for_partial_invalid_or_duplicate_frames(self):
        for raw in (b'', b'{"failed":', b'{"failed": [], "passed": true}',
                    b'{"failed": [],\n "passed": true}', b'not json\n', VALID + VALID):
            process = Mock()
            process.stdin.closed = False
            diagnostic = {}
            sandbox.close_input_after_result(SimpleNamespace(buffer=bytearray(raw)), process, diagnostic)
            process.stdin.close.assert_not_called()
            self.assertEqual(diagnostic, {})

    def test_input_close_is_provisional_for_pass_or_fail_frame(self):
        for raw in (VALID, b'{"failed": ["test_groups"], "passed": false}\n'):
            process = Mock()
            process.stdin.closed = False
            process.stdin.close.side_effect = lambda: setattr(process.stdin, 'closed', True)
            diagnostic = {}
            pipe = SimpleNamespace(buffer=bytearray(raw))
            sandbox.close_input_after_result(pipe, process, diagnostic)
            sandbox.close_input_after_result(pipe, process, diagnostic)
            process.stdin.close.assert_called_once()
            self.assertEqual(diagnostic, {'input_close_reason': 'result_frame_observed'})


class LifetimeControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SandboxTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def modeled(self, *, mode='complete', written=None, close_error=None, cleanup=True):
        executor = Mock()
        executor._create_attempted = executor._start_attempted = False
        executor.container_id = None
        executor.cgroup_fd = 42
        executor._test_pre_stop = None
        executor.cleanup_diagnostics = {}
        executor.env = {'LANG': 'C.UTF-8'}
        executor._command.side_effect = [(1, b''), (0, b'd' * 64 + b'\n')]
        executor._inspect.return_value = {'State': {'Running': True, 'Pid': 123,
                                                  'CgroupPath': '/synthetic'}}
        executor.verify_empty.return_value = cleanup
        process = Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 17
        def close():
            if close_error:
                raise close_error
            process.stdin.closed = True
        process.stdin.close.side_effect = close
        process.wait.return_value = 0
        process.poll.return_value = None
        executor._popen.return_value = process
        trace, sent = [], bytearray()

        class FakePipe:
            def __init__(self, unused):
                self.buffer, self.stderr = bytearray(), bytearray()
                self.counts = {'stdout': 0, 'stderr': 0}
                self.labels = {'stdout', 'stderr'}
                self.round = 0
                self.ready = Mock()
                self.ready.get_map.side_effect = lambda: {name: Mock(data=name) for name in self.labels}
            def wait_ready(self, deadline):
                return 'verified'
            def pump(self, deadline):
                self.round += 1
                trace.append(('pump', self.round, process.stdin.closed))
                if self.round <= 2:
                    # Regression model: early EOF leaves the attach client held.
                    # This models a possible bug, not proof of Podman internals.
                    if process.stdin.closed:
                        raise LiveFailure('acceptance_pipe_timeout')
                    self.buffer.extend(VALID[:10] if self.round == 1 else VALID[10:])
                    self.counts['stdout'] = len(self.buffer)
                    return
                if not process.stdin.closed or mode == 'still_held':
                    raise LiveFailure('acceptance_pipe_timeout')
                if mode == 'late_stdout':
                    self.buffer.extend(PRIVATE.encode())
                    self.counts['stdout'] = len(self.buffer)
                if mode == 'late_stderr':
                    self.stderr.extend(PRIVATE.encode())
                    self.counts['stderr'] = len(self.stderr)
                if mode == 'one_eof':
                    self.labels.discard('stdout')
                    raise LiveFailure('acceptance_pipe_timeout')
                self.labels.clear()
            def close(self):
                pass

        def write(fd, data):
            value = min(3, len(data)) if written is None else written
            if value > 0:
                sent.extend(bytes(data[:value]))
            return value

        with patch.object(sandbox, 'TestExecutor', return_value=executor), \
                patch.object(sandbox, '_Pipe', FakePipe), \
                patch.object(sandbox, '_bounded_call', side_effect=lambda f, seconds: f()), \
                patch.object(sandbox.select, 'select', return_value=([], [17], [])), \
                patch.object(sandbox.os, 'write', side_effect=write):
            result = sandbox.test_candidate(self.fixture.target, REFERENCE, 1)
        executor.stop.assert_called_once()
        executor.verify_empty.assert_called_once()
        executor.close.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(result))
        return result, process, trace, bytes(sent)

    def test_input_stays_open_until_full_frame_then_drains_both_streams(self):
        result, process, trace, sent = self.modeled()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(trace, [('pump', 1, False), ('pump', 2, False), ('pump', 3, True)])
        self.assertEqual(sent, sandbox.source_frame(REFERENCE))
        self.assertEqual(result['diagnostic']['input_bytes_written'], len(sent))
        self.assertEqual(result['diagnostic']['input_source_bytes'], len(REFERENCE.encode()))
        self.assertEqual(result['diagnostic']['input_close_reason'], 'result_frame_observed')
        process.wait.assert_called_once()

    def test_still_held_pipe_does_not_accept_pass_frame(self):
        result, process, _, _ = self.modeled(mode='still_held')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'pipe_timeout')
        self.assertEqual(result['diagnostic']['streams_before_stop']['frame']['class'], 'valid_pass')
        process.wait.assert_not_called()

    def test_one_eof_is_insufficient(self):
        result, process, _, _ = self.modeled(mode='one_eof')
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(result['diagnostic']['streams_before_stop']['stdout_eof'])
        self.assertFalse(result['diagnostic']['streams_before_stop']['stderr_eof'])
        process.wait.assert_not_called()

    def test_late_stdout_after_input_closed_is_rejected(self):
        result, _, _, _ = self.modeled(mode='late_stdout')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'invalid_json')

    def test_late_stderr_after_input_closed_is_rejected(self):
        result, _, _, _ = self.modeled(mode='late_stderr')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'stderr_nonempty')

    def test_input_close_error_preserves_cleanup_and_safe_diagnostic(self):
        result, process, _, _ = self.modeled(close_error=PermissionError(13, PRIVATE))
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['category'], 'permission_denied')
        self.assertTrue(result['cleanup_verified'])
        process.wait.assert_not_called()

    def test_zero_byte_write_blocks_without_spinning(self):
        result, process, trace, _ = self.modeled(written=0)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'input_no_progress')
        self.assertEqual(trace, [])
        process.wait.assert_not_called()

    def test_cleanup_failure_still_blocks_complete_new_transport(self):
        result, _, _, _ = self.modeled(cleanup=False)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['cleanup_verified'])


if __name__ == '__main__':
    unittest.main()
