"""Deterministic offline clocks and fake runtime; no Podman/model/source run."""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import sandbox
from ops.bounded_worker.agents_live_pilot import LiveFailure
from test_live import REFERENCE
import test_sandbox as fixtures

VALID = b'{"failed": [], "passed": true}\n'
FAILED = b'{"failed": ["test_groups"], "passed": false}\n'
PRIVATE = 'PRIVATE_COMPLETION_SENTINEL'


class BudgetTests(unittest.TestCase):
    def test_workload_deadline_is_fifteen_after_attach(self):
        b = sandbox.CompletionBudget(0, 1)
        self.assertEqual(b.deadline, 16)
        self.assertEqual(b.overall_deadline, 45)

    def test_provisional_frame_grants_thirty_seconds_only(self):
        b = sandbox.CompletionBudget(0, 1)
        self.assertTrue(b.observe(VALID, 2))
        self.assertEqual(b.deadline, 32)

    def test_completion_cannot_exceed_absolute_controller_deadline(self):
        b = sandbox.CompletionBudget(0, 20)
        self.assertTrue(b.observe(VALID, 34))
        self.assertEqual(b.deadline, 45)
        self.assertEqual(b.remaining(34), 11)

    def test_preflight_time_can_limit_even_workload_deadline(self):
        b = sandbox.CompletionBudget(0, 40)
        self.assertEqual(b.workload_deadline, 45)
        self.assertTrue(b.observe(VALID, 41))
        self.assertEqual(b.deadline, 45)

    def test_partial_invalid_duplicate_or_nonterminated_frame_grants_nothing(self):
        for raw in (b'', b'{"failed":', VALID[:-1], VALID + VALID,
                    b'{"failed": [], "passed": true, "passed": true}\n',
                    b'{"failed": [], "passed": false}\n'):
            b = sandbox.CompletionBudget(0, 1)
            self.assertFalse(b.observe(raw, 2))
            self.assertEqual(b.deadline, 16)
            self.assertIsNone(b.completion_deadline)

    def test_failed_test_frame_may_complete_without_becoming_pass(self):
        b = sandbox.CompletionBudget(0, 1)
        self.assertTrue(b.observe(FAILED, 2))
        self.assertEqual(b.deadline, 32)

    def test_frame_at_or_after_workload_deadline_cannot_extend_it(self):
        for now in (16, 16.1, 100):
            b = sandbox.CompletionBudget(0, 1)
            with self.assertRaises(LiveFailure):
                b.observe(VALID, now)
            self.assertIsNone(b.completion_deadline)

    def test_completion_allowance_never_refreshes(self):
        b = sandbox.CompletionBudget(0, 1)
        b.observe(VALID, 2)
        for now in (3, 20, 31, 100):
            self.assertFalse(b.observe(VALID, now))
            self.assertEqual(b.deadline, 32)

    def test_remaining_rejects_expiry_in_both_phases(self):
        b = sandbox.CompletionBudget(0, 1)
        with self.assertRaises(LiveFailure) as failure:
            b.remaining(16)
        self.assertEqual(failure.exception.args, ('test_workload_timeout',))
        b.observe(VALID, 2)
        with self.assertRaises(LiveFailure) as failure:
            b.remaining(32)
        self.assertEqual(failure.exception.args, ('test_completion_timeout',))


class ControllerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SandboxTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def modeled(self, *, preflight=1, result_time=2, complete_time=22, raw=VALID,
                late_stdout=False, late_stderr=False, attach_exit=0, cleanup=True,
                eof_with_frame=False, attach_exit_time=None, operation=None):
        now, observed_deadlines = [0.0], []
        executor = Mock()
        executor._create_attempted = executor._start_attempted = False
        executor.container_id = None
        executor.cgroup_fd = 42
        executor._test_pre_stop = None
        executor.cleanup_diagnostics = {}
        executor.env = {'LANG': 'C.UTF-8'}
        executor.verify_prepared.side_effect = lambda: now.__setitem__(0, preflight)
        executor._command.side_effect = [(1, b''), (0, b'd' * 64 + b'\n')]
        executor._inspect.return_value = {'State': {'Running': True, 'Pid': 123,
                                                  'CgroupPath': '/synthetic'}}
        executor.verify_empty.return_value = cleanup
        process = Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 98765
        process.stdin.close.side_effect = lambda: setattr(process.stdin, 'closed', True)
        process.poll.return_value = None
        def wait(timeout):
            observed_deadlines.append(('attach_wait', now[0] + timeout))
            if attach_exit_time is not None:
                if attach_exit_time >= now[0] + timeout:
                    now[0] += timeout
                    raise subprocess.TimeoutExpired(PRIVATE, timeout)
                now[0] = attach_exit_time
            return attach_exit
        process.wait.side_effect = wait
        executor._popen.return_value = process

        class FakePipe:
            def __init__(self, unused):
                self.buffer, self.stderr = bytearray(), bytearray()
                self.counts = {'stdout': 0, 'stderr': 0}
                self.labels = {'stdout', 'stderr'}
                self.round = 0
                self.ready = Mock()
                self.ready.get_map.side_effect = lambda: {n: Mock(data=n) for n in self.labels}
            def wait_ready(self, deadline):
                observed_deadlines.append(('ready', deadline))
                return 'verified'
            def pump(self, deadline):
                self.round += 1
                observed_deadlines.append(('pump', deadline))
                if self.round == 1:
                    # Deliberately allow a late fake read to verify observe()
                    # itself rejects granting allowance to a late frame.
                    now[0] = result_time
                    self.buffer.extend(raw)
                    self.counts['stdout'] = len(raw)
                    if eof_with_frame:
                        self.labels.clear()
                else:
                    if complete_time >= deadline:
                        now[0] = deadline
                        raise LiveFailure('acceptance_pipe_timeout')
                    now[0] = complete_time
                    if late_stdout:
                        self.buffer.extend(PRIVATE.encode())
                    if late_stderr:
                        self.stderr.extend(PRIVATE.encode())
                    self.counts = {'stdout':len(self.buffer), 'stderr':len(self.stderr)}
                    self.labels.clear()
            def close(self):
                pass

        original_write = sandbox.os.write
        bounded = Mock(side_effect=lambda f, seconds: f())
        with patch.object(sandbox, 'TestExecutor', return_value=executor), \
                patch.object(sandbox, '_Pipe', FakePipe), \
                patch.object(sandbox, '_bounded_call', bounded), \
                patch.object(sandbox.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(sandbox.select, 'select', return_value=([], [98765], [])), \
                patch.object(sandbox.os, 'write', side_effect=lambda fd, data:
                             len(data) if fd == 98765 else original_write(fd, data)):
            result = (operation(self.fixture.target) if operation is not None
                      else sandbox.test_candidate(self.fixture.target, REFERENCE, 1))
        self.assertEqual([c.args[1] for c in bounded.call_args_list], [45, 15, 10])
        executor.stop.assert_called_once()
        executor.verify_empty.assert_called_once()
        executor.close.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(result))
        return result, observed_deadlines, process

    def test_twenty_second_bookkeeping_delay_can_finish_with_full_validation(self):
        result, deadlines, process = self.modeled()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(deadlines[:3], [('ready', 16), ('pump', 16), ('pump', 32)])
        self.assertTrue(result['diagnostic']['completion_started'])
        self.assertEqual(result['diagnostic']['completion_budget_ms'], 30000)
        self.assertTrue(result['diagnostic']['streams_before_stop']['stdout_eof'])
        self.assertTrue(result['diagnostic']['streams_before_stop']['stderr_eof'])
        process.wait.assert_called_once()

    def test_late_frame_blocks_without_allowance_or_attach_wait(self):
        result, _, process = self.modeled(result_time=16)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['diagnostic']['completion_started'])
        self.assertEqual(result['diagnostic']['failure']['code'], 'result_after_workload_deadline')
        process.wait.assert_not_called()

    def test_late_frame_with_both_eofs_still_cannot_extend_or_pass(self):
        result, _, process = self.modeled(result_time=16, eof_with_frame=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['diagnostic']['completion_started'])
        self.assertEqual(result['diagnostic']['failure']['code'], 'result_after_workload_deadline')
        process.wait.assert_not_called()

    def test_missing_valid_frame_does_not_get_longer_deadline(self):
        result, deadlines, _ = self.modeled(raw=b'{"failed":')
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['diagnostic']['completion_started'])
        self.assertEqual(deadlines, [('ready', 16), ('pump', 16), ('pump', 16)])

    def test_open_pipes_block_at_completion_deadline_not_indefinitely(self):
        result, deadlines, process = self.modeled(complete_time=100)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['elapsed_before_stop_ms'], 32000)
        self.assertEqual(result['diagnostic']['failure']['code'], 'pipe_timeout')
        self.assertEqual(deadlines[-1], ('pump', 32))
        process.wait.assert_not_called()

    def test_preflight_consumes_absolute_controller_budget(self):
        result, deadlines, _ = self.modeled(preflight=20, result_time=34, complete_time=46)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['completion_budget_ms'], 11000)
        self.assertEqual(result['diagnostic']['elapsed_before_stop_ms'], 45000)
        self.assertEqual(deadlines[-1], ('pump', 45))

    def test_complete_pipes_still_allow_bounded_attach_exit_wait(self):
        result, deadlines, _ = self.modeled(eof_with_frame=True, attach_exit_time=22)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(deadlines[-1], ('attach_wait', 32))

    def test_attach_exit_wait_cannot_extend_completion_deadline(self):
        result, deadlines, _ = self.modeled(eof_with_frame=True, attach_exit_time=40)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'attach_wait_timeout')
        self.assertEqual(deadlines[-1], ('attach_wait', 32))

    def test_late_stdout_is_not_hidden_by_provisional_pass(self):
        result, _, _ = self.modeled(late_stdout=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'invalid_json')

    def test_late_stderr_remains_a_blocker(self):
        result, _, _ = self.modeled(late_stderr=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'stderr_nonempty')

    def test_nonzero_attach_exit_remains_a_blocker(self):
        result, _, _ = self.modeled(attach_exit=1)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['failure']['code'], 'attach_exit_nonzero')

    def test_failed_test_frame_completes_as_tests_failed(self):
        result, _, _ = self.modeled(raw=FAILED)
        self.assertEqual(result['status'], 'tests_failed')
        self.assertEqual(result['failed'], ['test_groups'])

    def test_cleanup_failure_cannot_be_hidden_by_completed_output(self):
        result, _, _ = self.modeled(cleanup=False)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['cleanup_verified'])


if __name__ == '__main__':
    unittest.main()
