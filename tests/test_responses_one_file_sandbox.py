"""Author-written fixtures and fake runtime only: no container or candidate execution."""
from dataclasses import replace
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
from ops.bounded_worker.responses_usage_pilot import one_file_adapter as a
from ops.bounded_worker.responses_usage_pilot import one_file_sandbox as s
from ops.bounded_worker.agents_live_pilot import LiveFailure, PreparedTarget
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from tests import test_agents_live_pilot as runtime_fixtures

ROOT = Path(__file__).resolve().parents[1]


def bundle():
    source = (ROOT / a.SOURCE_PATH).read_bytes()
    tests = (ROOT / a.FIXTURES / 'trusted_tests.py').read_bytes()
    harness = (ROOT / a.FIXTURES / 'trusted_harness.py').read_bytes()
    target = PreparedTarget('b' * 64, '/fixture/home', '/fixture/runtime', 'c' * 64)
    manifest = c.Manifest('offline-one-file', 'stevengalvis/modelfc', a.BASE,
        a.SOURCE_PATH, 'required_text', a.SOURCE_HASH, a.FIXTURES + 'trusted_tests.py',
        a.TEST_HASH, a.FIXTURES + 'trusted_harness.py', a.HARNESS_HASH, a.TEST_NAMES,
        a.INSTRUCTIONS, target.image_id, s.PROFILE_HASH, s.runtime_identity(target),
        'credential-ref:responses-pilot-saved-key', max_calls=1)
    reservation = {'number': 1, 'request_hash': c.digest(c.canonical([manifest.identity, 1, []]).encode()),
                   'sandbox_name': 'responses-task-' + manifest.identity + '-1'}
    candidate = source.replace(b'    if value is None or not value.strip():',
        b'    if value is None:\n        raise ValueError(f"{field} is required")\n    stripped = value.strip()\n    if not stripped:').replace(
        b'    return value.strip()', b'    return stripped')
    return manifest, source, tests, harness, candidate, reservation, target


class OneFileSandboxTests(unittest.TestCase):
    def setUp(self):
        self.args = bundle()

    def modeled(self, *, raw=b'{"failed": [], "passed": true}\n', stderr=b'', exitcode=0,
                cleanup=True, record=True, occupied=False, capture=False, trailing=False,
                eof_timeout=False, stop_error=False, cleanup_error=False):
        ex = Mock()
        ex._create_attempted = ex._start_attempted = False
        ex.container_id = None
        ex.cgroup_fd = 42
        ex.env = {'LANG': 'C.UTF-8'}
        ex.cleanup_diagnostics = {'readback': {'outcome': 'verified' if cleanup else 'unverified'}}
        ex._test_pre_stop = None
        ex._test_pre_stop_capture_error = None
        ex._command.side_effect = [(0 if occupied else 1, b''), (0, b'd' * 64 + b'\n')]
        ex._inspect.return_value = {'State': {'Running': True, 'Pid': 123, 'CgroupPath': '/fixture'}}
        if capture:
            ex._capture_cgroup.side_effect = LiveFailure('capture_failed')
        ex.verify_empty.return_value = cleanup
        if stop_error:
            ex.stop.side_effect = LiveFailure("stop_uncertain")
        if cleanup_error:
            ex.verify_empty.side_effect = LiveFailure("readback_uncertain")
        process = Mock()
        process.stdin.closed = False
        process.stdin.fileno.return_value = 10
        process.stdin.close.side_effect = lambda: setattr(process.stdin, 'closed', True)
        process.wait.return_value = exitcode
        process.poll.return_value = exitcode
        ex._popen.return_value = process
        pipe = Mock()
        pipe.buffer = bytearray(raw)
        pipe.stderr = bytearray(stderr)
        active = {'out': SimpleNamespace(data='stdout'), 'err': SimpleNamespace(data='stderr')}
        pipe.ready.get_map.side_effect = lambda: active
        def pump(_):
            if eof_timeout:
                raise LiveFailure("acceptance_pipe_timeout")
            if trailing:
                pipe.buffer.extend(b'garbage')
            active.clear()
        pipe.pump.side_effect = pump
        pipe.wait_ready.return_value = 'verified'
        recorder = Mock(return_value=record)
        with patch.object(s, 'TestExecutor', return_value=ex), patch.object(s, '_Pipe', return_value=pipe), \
                patch.object(s.select, 'select', return_value=([], [10], [])), \
                patch.object(s.os, 'write', side_effect=lambda _, data: len(data)):
            result = s.test_candidate(*self.args, identity_recorder=recorder)
        return result, ex, recorder

    def test_success_checks_eof_exit_and_independent_cleanup(self):
        result, ex, recorder = self.modeled()
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['sandbox_name'], self.args[5]['sandbox_name'])
        self.assertTrue(result['diagnostic']['streams_before_stop']['stdout_eof'])
        self.assertTrue(result['diagnostic']['streams_before_stop']['stderr_eof'])
        recorder.assert_called_once_with('d' * 64)
        names = [item[0] for item in ex.method_calls]
        self.assertLess(names.index('stop'), names.index('verify_empty'))

    def test_named_failure_is_not_success(self):
        result, _, _ = self.modeled(raw=b'{"failed":["test_plain"],"passed":false}\n')
        self.assertEqual(result['status'], 'tests_failed')
        self.assertEqual(result['failed'], ['test_plain'])

    def test_output_exit_cleanup_fail_closed(self):
        for kwargs in ({'stderr': b'bad'}, {'exitcode': 1}, {'cleanup': False},
                       {'raw': b'{"failed":[],"passed":true,"x":1}'}, {'trailing': True}):
            with self.subTest(kwargs=kwargs):
                result, ex, _ = self.modeled(**kwargs)
                self.assertEqual(result['status'], 'blocked')
                ex.stop.assert_called_once()
                ex.verify_empty.assert_called_once()

    def test_identity_record_failure_cleans_without_start(self):
        result, ex, _ = self.modeled(record=False)
        self.assertEqual(result['status'], 'blocked')
        ex._popen.assert_not_called()
        ex.stop.assert_called_once()
        ex.verify_empty.assert_called_once()

    def test_occupied_and_capture_fail_before_input(self):
        result, ex, _ = self.modeled(occupied=True)
        self.assertEqual(result['status'], 'blocked')
        ex.stop.assert_not_called()
        result, ex, _ = self.modeled(capture=True)
        self.assertEqual(result['diagnostic']['input_bytes_written'], 0)
        ex.stop.assert_called_once()

    def test_result_parser_closed_schema(self):
        for raw in (b'', b'{}', b'{"failed":[],"passed":true,"passed":true}',
                    b'{"failed":["invented"],"passed":false}',
                    b'{"failed":["test_plain","test_plain"],"passed":false}',
                    b'{"failed":[],"passed":1}', b'{"failed":[],"passed":false}'):
            with self.subTest(raw=raw), self.assertRaises(s.ResultRejected):
                s.parse_result(raw)

    def test_budget_one_completion_no_renewal(self):
        budget = s.CompletionBudget(0, 1)
        self.assertTrue(budget.observe(b'{"failed":[],"passed":true}\n', 10))
        self.assertEqual(budget.deadline, 40)
        self.assertFalse(budget.observe(b'{"failed":[],"passed":true}\n', 20))
        self.assertEqual(budget.deadline, 40)
        with self.assertRaises(LiveFailure):
            budget.remaining(40)
        with self.assertRaises(LiveFailure):
            s.CompletionBudget(0, 1).observe(b'{"failed":[],"passed":true}\n', 16)

    def test_readiness_exact_name_image_and_runtime(self):
        m, _, _, _, _, reservation, target = self.args
        ex = Mock()
        ex._command.side_effect = [(0, m.image_id.encode()), (1, b'')]
        with patch.object(s, 'TestExecutor', return_value=ex):
            self.assertTrue(s.verify_readiness(m, target))
        self.assertEqual(ex._command.call_args.args[0], ['container', 'exists', reservation['sandbox_name']])
        for replies in ([(0, b'wrong')], [(0, m.image_id.encode()), (0, b'')],
                        [(0, m.image_id.encode()), (1, b'warning')]):
            ex = Mock()
            ex._command.side_effect = replies
            with patch.object(s, 'TestExecutor', return_value=ex), self.assertRaises(LiveFailure):
                s.verify_readiness(m, target)
            ex.close.assert_called_once()
        with self.assertRaises(ValueError):
            s.verify_readiness(replace(m, runtime_id='wrong'), target)

    def test_profile_and_exact_name_inspection(self):
        fixture = runtime_fixtures.PodmanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        m, source, tests, harness, _, reservation, target = self.args
        program = s.gate_program(m, source, tests, harness)
        ex = s.TestExecutor(fixture.target, reservation['sandbox_name'], program)
        self.addCleanup(ex.close)
        binding = ExecutorBinding(m.identity, 'responses_one_file', 'no_environment_key',
                                  'https://api.openai.com/never-called')
        args = ex.create_arguments(binding)
        self.assertEqual(args[args.index('--name') + 1], reservation['sandbox_name'])
        self.assertIn('--network=none', args)
        self.assertEqual(args[-1], program)
        self.assertNotIn('--registration-bearer-stdin', args)
        value = copy.deepcopy(fixture.value)
        value['HostConfig']['NetworkMode'] = 'none'
        value['Config']['Cmd'] = ['-I', '-S', '-B', '-c', program]
        value['Config']['Entrypoint'] = ['/usr/local/bin/python3']
        ex._created_policy(value)
        value['Config']['Cmd'][-1] = 'print("fake")'
        with self.assertRaises(LiveFailure):
            ex._created_policy(value)


    def test_missing_eof_and_uncertain_cleanup_never_pass(self):
        for kwargs in ({'eof_timeout': True}, {'cleanup_error': True}):
            with self.subTest(kwargs=kwargs):
                result, ex, _ = self.modeled(**kwargs)
                self.assertEqual(result['status'], 'blocked')
                ex.stop.assert_called_once()
                ex.verify_empty.assert_called_once()

    def test_stop_error_still_independently_verifies_cleanup(self):
        result, ex, _ = self.modeled(stop_error=True, cleanup=False)
        self.assertEqual(result['status'], 'blocked')
        ex.verify_empty.assert_called_once()
        self.assertIn('stop_failure', result['diagnostic'])

    def test_exact_name_image_id_labels_required_for_inspection(self):
        fixture = runtime_fixtures.PodmanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        m, _, _, _, _, reservation, _ = self.args
        binding = ExecutorBinding(m.identity, 'responses_one_file', 'no_environment_key',
                                  'https://api.openai.com/never-called')
        ex = s.TestExecutor(fixture.target, reservation['sandbox_name'], 'fixed')
        self.addCleanup(ex.close)
        ex.binding = binding
        value = copy.deepcopy(fixture.value)
        value['Name'] = reservation['sandbox_name']
        value['Config']['Labels'] = ex._labels()
        ex._command = Mock(return_value=(0, json.dumps([value]).encode()))
        self.assertEqual(ex._inspect()['Id'], value['Id'])
        for change in ({'Name': 'agents-' + m.identity}, {'Id': 'a' * 64}, {'Image': 'e' * 64}):
            bad = copy.deepcopy(value)
            bad.update(change)
            ex._command.return_value = (0, json.dumps([bad]).encode())
            with self.subTest(change=change), self.assertRaises(LiveFailure):
                ex._inspect()
        bad = copy.deepcopy(value)
        bad['Config']['Labels']['agents.acceptance'] = 'wrong'
        ex._command.return_value = (0, json.dumps([bad]).encode())
        with self.assertRaises(LiveFailure):
            ex._inspect()

    def test_missing_identity_recorder_never_touches_runtime(self):
        with patch.object(s, 'TestExecutor') as executor, self.assertRaises(ValueError):
            s.test_candidate(*self.args)
        executor.assert_not_called()
