"""No Podman, sandbox or provider execution: controller and pipe boundaries mocked."""
import errno
import json
import os
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import sandbox
from ops.bounded_worker.agents_live_pilot import LiveFailure, _OperationDeadline
from test_controller_diagnostics import (
    container_summary, frame_summary, parse_result, pipe_summary, ResultRejected, test_failure)
from test_live import REFERENCE
import test_sandbox as fixtures

PRIVATE = 'PRIVATE_DIAGNOSTIC_SENTINEL'
VALID = b'{"failed": [], "passed": true}\n'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SandboxTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def modeled(self, *, mode='eof', raw=VALID, stderr=b'', attach_exit=0,
                cleanup=True, before=None, stop_error=None, cleanup_error=None,
                pipe_close_error=None, observation_error=False, wait_error=None,
                container_observation_error=False):
        executor = Mock()
        executor._create_attempted = executor._start_attempted = False
        executor.container_id = None
        executor.cgroup_fd = 42
        executor._test_pre_stop = None
        executor.cleanup_diagnostics = {'before_stop': before}
        executor.env = {'LANG': 'C.UTF-8'}
        executor._command.side_effect = [(1, b''), (0, b'd' * 64 + b'\n')]
        executor._inspect.return_value = {'State': {'Running': True, 'Pid': 123, 'CgroupPath': '/synthetic'}}
        executor.verify_empty.return_value = cleanup
        if stop_error:
            executor.stop.side_effect = stop_error
        if cleanup_error:
            executor.verify_empty.side_effect = cleanup_error
        process = Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 17
        process.stdin.close.side_effect = lambda: setattr(process.stdin, 'closed', True)
        process.wait.return_value = attach_exit
        if wait_error:
            process.wait.side_effect = wait_error
        process.poll.return_value = None if mode == 'stall' else attach_exit
        executor._popen.return_value = process

        class FakePipe:
            def __init__(self, unused):
                self.buffer = bytearray(raw)
                self.stderr = bytearray(stderr)
                self.counts = {'stdout': len(raw), 'stderr': len(stderr)}
                self.labels = {'stdout', 'stderr'}
                self.ready = Mock()
                self.ready.get_map.side_effect = lambda: {
                    name: Mock(data=name) for name in self.labels}
            def wait_ready(self, deadline):
                return 'verified'
            def pump(self, deadline):
                if mode == 'stall':
                    raise LiveFailure('acceptance_pipe_timeout')
                if mode == 'delayed_stdout':
                    self.buffer.extend(PRIVATE.encode())
                    self.counts['stdout'] += len(PRIVATE)
                if mode == 'delayed_stderr':
                    self.stderr.extend(PRIVATE.encode())
                    self.counts['stderr'] += len(PRIVATE)
                self.labels.clear()
            def close(self):
                if pipe_close_error:
                    raise pipe_close_error

        bounded = Mock(side_effect=lambda action, seconds: action())
        patches = [patch.object(sandbox, 'TestExecutor', return_value=executor),
                   patch.object(sandbox, '_Pipe', FakePipe),
                   patch.object(sandbox, '_bounded_call', bounded),
                   patch.object(sandbox.select, 'select', return_value=([], [17], [])),
                   patch.object(sandbox.os, 'write', side_effect=lambda fd, data: len(data))]
        if observation_error:
            patches.append(patch.object(sandbox, 'pipe_summary', side_effect=RuntimeError(PRIVATE)))
        if container_observation_error:
            patches.append(patch.object(sandbox, 'container_summary', side_effect=RuntimeError(PRIVATE)))
        for context in patches:
            context.start()
        try:
            result = sandbox.test_candidate(self.fixture.target, REFERENCE, 1)
        finally:
            for context in reversed(patches):
                context.stop()
        self.assertEqual([call.args[1] for call in bounded.call_args_list], [45, 15, 10])
        executor.stop.assert_called_once()
        executor.verify_empty.assert_called_once()
        executor.close.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(result))
        return result, executor, process

    def test_exited_container_with_open_attach_pipes_stays_blocked(self):
        result, _, process = self.modeled(mode='stall', before={
            'status': 'exited', 'running': False, 'exit_code': 0, 'oom_killed': False})
        self.assertEqual(result['status'], 'blocked')
        diagnostic = result['diagnostic']
        self.assertEqual(diagnostic['step'], 'output_drain')
        self.assertEqual(diagnostic['failure']['code'], 'pipe_timeout')
        streams = diagnostic['streams_before_stop']
        self.assertFalse(streams['stdout_eof'])
        self.assertFalse(streams['stderr_eof'])
        self.assertEqual(streams['frame']['class'], 'valid_pass')
        self.assertIsNone(streams['attach_returncode_before_stop'])
        self.assertEqual(diagnostic['container_before_stop']['exit_code'], 0)
        self.assertTrue(result['cleanup_verified'])
        process.wait.assert_not_called()  # No acceptance shortcut or retry.

    def test_unknown_container_state_does_not_turn_stall_into_pass(self):
        result, _, _ = self.modeled(mode='stall')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['container_before_stop']['status'], 'unknown')

    def test_valid_output_and_complete_eof_pass(self):
        result, _, _ = self.modeled()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['diagnostic']['step'], 'finished')
        streams = result['diagnostic']['streams_before_stop']
        self.assertTrue(streams['stdout_eof'])
        self.assertTrue(streams['stderr_eof'])
        self.assertEqual(streams['frame']['class'], 'valid_pass')

    def test_failed_group_frame_is_not_a_controller_block(self):
        result, _, _ = self.modeled(raw=b'{"failed":["test_groups"],"passed":false}')
        self.assertEqual(result['status'], 'tests_failed')
        self.assertEqual(result['failed'], ['test_groups'])
        self.assertEqual(result['diagnostic']['streams_before_stop']['frame']['class'], 'valid_fail')

    def test_delayed_trailing_output_is_rejected(self):
        result, _, _ = self.modeled(mode='delayed_stdout')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'invalid_json')
        self.assertEqual(result['diagnostic']['step'], 'result_parse')

    def test_delayed_stderr_is_rejected_without_retaining_text(self):
        result, _, _ = self.modeled(mode='delayed_stderr')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'stderr_nonempty')
        self.assertEqual(result['diagnostic']['streams_before_stop']['stderr_signatures'],
                         ['unclassified_nonempty'])

    def test_missing_truncated_invalid_and_duplicate_results_are_rejected(self):
        for raw, code in ((b'', 'empty'), (b'{"failed":', 'invalid_json'),
                          (b'\xff', 'invalid_encoding'),
                          (b'{"failed":[],"passed":true,"passed":true}', 'duplicate_keys'),
                          (b'{"failed":[],"passed":true,"other":1}', 'invalid_shape')):
            with self.subTest(code=code):
                result, _, _ = self.modeled(raw=raw)
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(result['diagnostic']['failure']['code'], code)

    def test_nonzero_attach_exit_and_oversize_are_distinct(self):
        result, _, _ = self.modeled(attach_exit=1)
        self.assertEqual(result['diagnostic']['failure']['code'], 'attach_exit_nonzero')
        self.assertEqual(result['diagnostic']['streams_before_stop']['attach_returncode_before_stop'], 1)
        result, _, _ = self.modeled(raw=b'x' * 4097)
        self.assertEqual(result['diagnostic']['failure']['code'], 'stdout_limit')
        self.assertEqual(result['diagnostic']['streams_before_stop']['frame']['class'], 'oversized')

    def test_attach_wait_timeout_is_specific(self):
        result, _, _ = self.modeled(wait_error=subprocess.TimeoutExpired(PRIVATE, 15))
        self.assertEqual(result['diagnostic']['step'], 'attach_wait')
        self.assertEqual(result['diagnostic']['failure']['code'], 'attach_wait_timeout')

    def test_cleanup_failure_blocks_valid_frame(self):
        result, _, _ = self.modeled(cleanup=False)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['cleanup_verified'])

    def test_secondary_cleanup_failures_do_not_replace_first_failure(self):
        result, _, _ = self.modeled(mode='stall',
            stop_error=PermissionError(errno.EACCES, PRIVATE),
            cleanup_error=OSError(errno.EIO, PRIVATE))
        diagnostic = result['diagnostic']
        self.assertEqual(diagnostic['failure']['code'], 'pipe_timeout')
        self.assertEqual(diagnostic['stop_failure']['category'], 'permission_denied')
        self.assertEqual(diagnostic['cleanup_failure']['category'], 'os_error')
        self.assertFalse(result['cleanup_verified'])

    def test_diagnostic_capture_failure_still_cleans_up(self):
        result, _, _ = self.modeled(observation_error=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['observation_failure']['category'], 'operation_failed')
        self.assertTrue(result['cleanup_verified'])

    def test_pipe_close_failure_still_closes_executor(self):
        result, _, _ = self.modeled(pipe_close_error=PermissionError(errno.EACCES, PRIVATE))
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['pipe_close_failure']['category'], 'permission_denied')

    def test_existing_stop_inspect_captures_safe_state_before_stop(self):
        executor = sandbox.TestExecutor(self.fixture.target, 'synthetic-no-key')
        self.addCleanup(executor.close)
        executor._test_stop_inspection = True
        first = {'State': {'Status': 'exited', 'Running': False, 'ExitCode': 0,
                          'OOMKilled': False, 'Error': 'permission denied ' + PRIVATE}}
        later = {'State': {'Status': 'stopped', 'Running': False, 'ExitCode': 137}}
        with patch.object(sandbox.PodmanExecutor, '_inspect', side_effect=[first, later]) as inspect:
            executor._inspect()
            executor._inspect()
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(executor._test_pre_stop['exit_code'], 0)
        self.assertEqual(executor._test_pre_stop['error_signatures'], ['permission_denied'])
        self.assertNotIn(PRIVATE, json.dumps(executor._test_pre_stop))

    def test_diagnostic_state_capture_error_cannot_prevent_stop(self):
        executor = sandbox.TestExecutor(self.fixture.target, 'synthetic-no-key')
        self.addCleanup(executor.close)
        stopped = []
        def stop(instance, binding):
            instance._stop_attempted = True
            instance._inspect()
            stopped.append(True)
        with patch.object(sandbox.PodmanExecutor, '_inspect', return_value={'State': {}}), \
                patch.object(sandbox.PodmanExecutor, 'stop', new=stop), \
                patch.object(sandbox, 'container_summary', side_effect=RuntimeError(PRIVATE)):
            executor.stop(self.fixture.fixture.binding)
        self.assertEqual(stopped, [True])
        self.assertEqual(executor._test_pre_stop_capture_error['category'], 'operation_failed')
        self.assertFalse(executor._test_stop_inspection)
        self.assertNotIn(PRIVATE, json.dumps(executor._test_pre_stop_capture_error))

    def test_postcleanup_observation_error_preserves_primary_failure_and_closes_resources(self):
        result, _, _ = self.modeled(mode='stall', container_observation_error=True)
        self.assertEqual(result['diagnostic']['failure']['code'], 'pipe_timeout')
        self.assertEqual(result['diagnostic']['container_observation_failure']['category'], 'operation_failed')
        self.assertTrue(result['cleanup_verified'])

    def test_diagnostic_capture_does_not_swallow_stop_deadline(self):
        executor = sandbox.TestExecutor(self.fixture.target, 'synthetic-no-key')
        self.addCleanup(executor.close)
        def stop(instance, binding):
            instance._stop_attempted = True
            instance._inspect()
        with patch.object(sandbox.PodmanExecutor, '_inspect', return_value={'State': {}}), \
                patch.object(sandbox.PodmanExecutor, 'stop', new=stop), \
                patch.object(sandbox, 'container_summary', side_effect=_OperationDeadline), \
                self.assertRaises(_OperationDeadline):
            executor.stop(self.fixture.fixture.binding)
        self.assertFalse(executor._test_stop_inspection)


class DiagnosticSchemaTests(unittest.TestCase):
    def test_real_held_attach_pipes_time_out_after_observed_valid_frame(self):
        # Real kernel pipe EOF semantics, no subprocess, container or candidate execution.
        from ops.bounded_worker.agents_nested_acceptance import _Pipe
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        error_read, error_write = os.pipe()
        process = SimpleNamespace(stdin=os.fdopen(input_write, 'wb', buffering=0),
            stdout=os.fdopen(output_read, 'rb', buffering=0),
            stderr=os.fdopen(error_read, 'rb', buffering=0), poll=lambda: None)
        pipe = _Pipe(process)
        try:
            process.stdin.close()
            os.write(output_write, VALID)
            pipe.pump(time.monotonic() + 1)
            with self.assertRaises(LiveFailure) as failure:
                pipe.pump(time.monotonic() + .01)
            self.assertEqual(test_failure(failure.exception)['code'], 'pipe_timeout')
            observed = pipe_summary(pipe, process)
            self.assertEqual(observed['frame']['class'], 'valid_pass')
            self.assertFalse(observed['stdout_eof'])
            self.assertFalse(observed['stderr_eof'])
        finally:
            pipe.close()
            for descriptor in (input_read, output_write, error_write):
                os.close(descriptor)

    def test_unknown_errors_never_serialize_args_or_class_names(self):
        class SECRET_PRIVATE_ERROR(RuntimeError):
            pass
        for error in (SECRET_PRIVATE_ERROR(PRIVATE), LiveFailure(PRIVATE),
                      PermissionError(errno.EACCES, PRIVATE),
                      _OperationDeadline(PRIVATE)):
            value = test_failure(error)
            self.assertNotIn(PRIVATE, json.dumps(value))
            self.assertNotIn('SECRET_PRIVATE_ERROR', json.dumps(value))
        self.assertEqual(test_failure(_OperationDeadline())['code'], 'overall_deadline')

    def test_container_summary_rejects_arbitrary_values(self):
        value = container_summary({'Status': PRIVATE, 'Running': 1, 'ExitCode': True,
                                   'OOMKilled': 0, 'Error': PRIVATE, 'Path': PRIVATE})
        self.assertEqual(value['status'], 'unknown')
        self.assertIsNone(value['running'])
        self.assertIsNone(value['exit_code'])
        self.assertIsNone(value['oom_killed'])
        self.assertNotIn(PRIVATE, json.dumps(value))

    def test_result_schema_rejects_unexpected_groups_and_nonfinite_numbers(self):
        for raw in (b'{"failed":["private"],"passed":false}',
                    b'{"failed":["test_groups","test_groups"],"passed":false}',
                    b'{"failed":[],"passed":1}', b'{"failed":[],"passed":NaN}',
                    b'{"failed":[{}],"passed":false}'):
            with self.subTest(raw=raw), self.assertRaises(ResultRejected):
                parse_result(raw)

    def test_frame_observation_is_not_an_acceptance_claim(self):
        result = frame_summary(VALID)
        self.assertEqual(result, {'class': 'valid_pass', 'observed_failed': []})
        self.assertNotIn('accepted', result)


if __name__ == '__main__':
    unittest.main()
