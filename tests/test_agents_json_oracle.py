"""Offline oracle tests: fake Podman/attach only, no candidate host execution."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_json_oracle as oracle
from ops.bounded_worker import agents_live_pilot as live
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from tests import test_agents_live_pilot as live_tests


def policy(cases=None, **updates):
    value = dict(version=1, task_sha256='1' * 64, image_sha256='a' * 64,
        acceptance_sha256='b' * 64, profile_sha256=oracle.digest(oracle.canonical(oracle.PROFILE)),
        max_seconds=60, max_output_bytes=4096,
        cases=cases if cases is not None else [dict(name='test_sum', module='package.maths', callable='add',
                            args=[2, 3], kwargs={}, expected=5)])
    value.update(updates)
    return oracle.JSONCallPolicy(oracle.canonical(value))


def response(values=(5,)):
    return oracle.canonical(dict(version=1, responses=[dict(index=n, value=v) for n, v in enumerate(values)])) + b'\n'


class PolicyTests(unittest.TestCase):
    def test_policy_is_immutable_and_finite(self):
        item = policy()
        obj = item.value()
        obj['cases'][0]['expected'] = 'changed'
        self.assertEqual(item.value()['cases'][0]['expected'], 5)
        self.assertEqual(len(item.sha256), 64)
        for update in ({'max_seconds': 30}, {'max_seconds': True}, {'max_output_bytes': 65537},
                       {'profile_sha256': '0' * 64}, {'cases': []}, {'cases': [obj['cases'][0]] * 2}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                policy(**update).value()
        with self.assertRaises(ValueError):
            oracle.JSONCallPolicy(item.raw + b' ').value()
        with self.assertRaises(ValueError):
            oracle.JSONCallPolicy(item.raw.replace(b'"expected":5', b'"expected":NaN')).value()

    def test_data_only_plan_projection_and_digest(self):
        value = dict(task_sha256='1' * 64, runtime_sha256='2' * 64,
            harness_sha256=oracle.harness_sha256(policy()), test_image_sha256='a' * 64,
            test_acceptance_sha256='b' * 64, test_names=['test_sum'], assertion_protocol=oracle.PROTOCOL,
            max_seconds=60, max_output_bytes=4096, enabled=True)
        plan = oracle.decode_plan(value)
        self.assertEqual(plan.test_names, ('test_sum',))
        self.assertEqual(plan.sha256, oracle.digest(oracle.canonical(value)))
        for update in ({'command': 'pytest'}, {'max_seconds': 20}, {'test_names': []},
                       {'test_names': ('test_sum',)}, {'test_names': ['test_sum', 'test_sum']}):
            with self.assertRaises(ValueError):
                oracle.decode_plan(value | update)

    def test_case_names_entrypoints_and_inputs_are_bounded(self):
        base = policy().value()['cases'][0]
        for update in ({'name': ''}, {'name': '-x'}, {'module': '../x'}, {'module': 'a;ls'},
                       {'callable': 'x.y'}, {'args': {}}, {'kwargs': []}, {'command': 'pytest'},
                       {'expected': 'x' * 20000}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                policy(cases=[base | update]).value()
        duplicate = policy().raw.replace(b'"version":1', b'"version":1,"version":1')
        with self.assertRaises(ValueError):
            oracle.JSONCallPolicy(duplicate).value()

    def test_harness_digest_binds_case_values_gate_and_profile(self):
        item = policy()
        changed = item.value()['cases']
        changed[0]['expected'] = 7
        self.assertNotEqual(oracle.harness_sha256(item), oracle.harness_sha256(policy(cases=changed)))
        original = oracle.harness_sha256(item)
        with patch.object(oracle, 'GATE_PROGRAM', oracle.GATE_PROGRAM + '\n'):
            self.assertNotEqual(original, oracle.harness_sha256(item))

    def test_payload_has_no_expected_values_names_or_pass_markers(self):
        case = policy().value()['cases'][0] | {'name': 'secret_test_name', 'expected': 'hidden_assertion'}
        item = policy(cases=[case])
        framed = oracle.prepare_payload({'package/__init__.py': b'', 'package/maths.py': b'data only'}, item)
        self.assertEqual(framed[:4], b'JCO1')
        self.assertEqual(int.from_bytes(framed[4:8], 'big'), len(framed) - 8)
        value = json.loads(framed[8:])
        self.assertEqual(set(value), {'version', 'source', 'calls'})
        self.assertEqual(set(value['calls'][0]), {'module', 'callable', 'args', 'kwargs'})
        for forbidden in (b'hidden_assertion', b'secret_test_name', b'expected', b'passed', b'failed'):
            self.assertNotIn(forbidden, framed)
        self.assertNotIn('run_tests', oracle.GATE_PROGRAM)
        self.assertNotIn('hidden_assertion', oracle.GATE_PROGRAM)

    def test_source_paths_and_limits_fail_before_os(self):
        for files in ({'../x.py': b''}, {'a': b'', 'a/b.py': b''}, {'a.py': b'x' * (oracle.MAX_FILE + 1)},
                      {'a.py': 'not bytes'}, {'.git/config': b''}, {'foo.py': b''}):
            with self.subTest(files=list(files)), self.assertRaises(ValueError):
                oracle.prepare_payload(files, policy())
        with patch('subprocess.Popen', side_effect=AssertionError('no host candidate execution')):
            oracle.prepare_payload({'package/maths.py': b'raise Exception("not executed")'}, policy())

    def test_outer_assertions_compare_exact_json_types(self):
        self.assertEqual(oracle.compare_responses(response(), policy()), {'test_sum': 'passed'})
        for value in (4, True, '5', 5.0, {'passed': True}):
            self.assertEqual(oracle.compare_responses(response((value,)), policy()), {'test_sum': 'failed'})

    def test_forged_markers_trailing_output_duplicate_keys_and_wrong_indexes_rejected(self):
        invalid = [b'{"passed":true}\n', response() + b'\n', response() + b'evil', response()[:-1],
                   b'{"version":1,"responses":[]}\n',
                   response().replace(b'"index":0', b'"index":true'),
                   response().replace(b'"index":0', b'"index":1'),
                   response().replace(b'"value":5', b'"value":5,"value":5'),
                   response().replace(b'"value":5', b'"value":Infinity')]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                oracle.compare_responses(raw, policy())


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = live_tests.PodmanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.executor = oracle.OracleExecutor(self.fixture.target, 'synthetic', command=Mock())
        self.addCleanup(self.executor.close)
        self.value = copy.deepcopy(self.fixture.value)
        self.value['HostConfig']['NetworkMode'] = 'none'
        self.value['Config']['Entrypoint'] = '/usr/local/bin/python3'
        self.value['Config']['Cmd'] = ['-I', '-S', '-B', '-c', oracle.GATE_PROGRAM]

    def test_fixed_no_network_no_host_mount_command(self):
        args = self.executor.create_arguments(self.fixture.binding)
        self.assertIn('--network=none', args)
        self.assertIn('--pull=never', args)
        self.assertNotIn('--network=slirp4netns:allow_host_loopback=false', args)
        self.assertEqual(args[-5:], ['-I', '-S', '-B', '-c', oracle.GATE_PROGRAM])
        self.assertNotIn('--registration-bearer-stdin', args)
        self.assertEqual(args.count('--mount'), 3)
        self.assertNotIn('type=bind', repr(args))

    def test_effective_profile_rejects_network_or_harness_mutation(self):
        self.executor._created_policy(self.value)
        for section, field, changed in [('HostConfig', 'NetworkMode', 'host'),
                ('Config', 'Entrypoint', ['/usr/local/bin/python3']),
                ('Config', 'Entrypoint', '/bin/sh'), ('Config', 'Cmd', ['pytest']),
                ('HostConfig', 'Memory', 0)]:
            bad = copy.deepcopy(self.value)
            bad[section][field] = changed
            with self.assertRaises(live.LiveFailure):
                self.executor._created_policy(bad)

    def test_stop_metadata_allowance_exceeds_podman_grace(self):
        for operation, allowance in (('stop', 7), ('inspect', 5)):
            with self.subTest(operation=operation):
                self.executor.command_deadline = 120
                process = Mock()
                process.wait.return_value = process.poll.return_value = 0
                with patch.object(oracle.time, 'monotonic', return_value=100), \
                        patch.object(oracle.subprocess, 'Popen', return_value=process), \
                        patch.object(oracle.os, 'set_blocking'), patch.object(oracle.os, 'read', return_value=b''), \
                        patch.object(oracle.selectors, 'DefaultSelector'):
                    self.assertEqual(self.executor._run([operation]), (0, b''))
                self.assertEqual(process.wait.call_args_list[0].kwargs['timeout'], allowance)

    def test_metadata_deadline_prevents_spawn(self):
        self.executor.command_deadline = 0
        with patch('subprocess.Popen') as popen, self.assertRaises(live.LiveFailure):
            self.executor._run(['inspect'])
        popen.assert_not_called()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = live_tests.PodmanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cgroup = Path(self.tmp.name)
        self.events = self.cgroup / 'cgroup.events'
        self.events.write_text('populated 1\n')
        self.policy = policy()
        self.files = {'package/maths.py': b'def add(a, b): return a + b\n'}
        self.plan = types.SimpleNamespace(validate=Mock(), task_sha256='1' * 64, runtime_sha256='2' * 64,
            harness_sha256=oracle.harness_sha256(self.policy), test_image_sha256='a' * 64,
            test_acceptance_sha256='b' * 64, test_names=('test_sum',), assertion_protocol=oracle.PROTOCOL,
            max_seconds=60, max_output_bytes=4096, enabled=True, sha256='3' * 64)
        self.stage = dict(task_sha256='1' * 64, runtime_sha256='2' * 64, verification_plan_sha256='3' * 64,
                          candidate_tree_sha256=oracle.tree_digest(self.files), attempt_id='4' * 64)
        self.value = copy.deepcopy(self.fixture.value)
        self.value['Name'] = 'agents-' + '5' * 64
        self.value['Config']['Labels'].update({'agents.attempt': '5' * 64,
            'agents.session': 'json_oracle', 'agents.environment': 'no_environment_key'})
        self.value['Config']['Entrypoint'] = '/usr/local/bin/python3'
        self.value['Config']['Cmd'] = ['-I', '-S', '-B', '-c', oracle.GATE_PROGRAM]
        self.value['HostConfig']['NetworkMode'] = 'none'
        self.value['State'].update(ExitCode=0, OOMKilled=False)
        self.calls, self.sent = [], []
        self.returned = response()
        self.finish_failure = self.stop_failure = self.empty_failure = False
        self.missing_capture = self.identity_change = False
        self.clock = 100.0
        owner = self
        self.process = Mock()
        self.process.poll.return_value = 0
        self.process.wait.return_value = 0

        class Pipe:
            def __init__(self, process, limit):
                self.buffer = bytearray(owner.returned)
                self.stderr = bytearray()
                self.total = len(owner.returned) + len(b'NESTED_READY:verified\n')
                self.eof = set()
            def wait_ready(self, deadline):
                owner.calls.append('ready')
            def send(self, frame, deadline):
                owner.sent.append(bytes(frame))
            def finish(self, deadline):
                if owner.finish_failure:
                    owner.clock = deadline
                    raise TimeoutError('untrusted secret error')
                self.eof = {'stdout', 'stderr'}
                owner.value['State'].update(Running=False, Pid=0, Status='exited')
                return 0
            def close(self):
                owner.calls.append('pipe_close')

        self.pipe = Pipe

    def command(self, args):
        self.calls.append(args[0])
        if args[:2] == ['container', 'exists']:
            return 1, b''
        if args[0] == 'create':
            return 0, b'd' * 64 + b'\n'
        if args[0] == 'stop':
            if self.stop_failure:
                raise TimeoutError('untrusted secret error')
            self.events.write_text('populated 1\n' if self.empty_failure else 'populated 0\n')
            self.value['State'].update(Running=False, Pid=0, Status='stopped')
        return 0, json.dumps([self.value]).encode()

    def popen(self, *args, **kwargs):
        self.calls.append('attach')
        self.value['State'].update(Running=True, Pid=123, Status='running', CgroupPath='/full/bound')
        return self.process

    def capture(self, executor, value):
        self.calls.append('capture')
        if self.missing_capture:
            return
        executor.cgroup_fd = os.open(self.cgroup, os.O_RDONLY | os.O_DIRECTORY)
        executor.cgroup_events_fd = os.open(self.events, os.O_RDONLY)
        if self.identity_change:
            self.value['State']['Pid'] = 999

    def run_case(self):
        with patch.object(oracle.OracleExecutor, 'verify_prepared'), \
                patch.object(oracle.OracleExecutor, '_capture_cgroup', lambda executor, value: self.capture(executor, value)), \
                patch.object(oracle, 'OraclePipe', self.pipe), \
                patch.object(oracle.time, 'monotonic', side_effect=lambda: self.clock), \
                patch('subprocess.Popen', side_effect=AssertionError('no real processes')):
            return oracle.run_oracle(self.fixture.target, self.plan, self.stage, self.files, self.policy,
                                     '5' * 64, command=self.command, popen=self.popen)

    def test_real_lifecycle_fake_os_emits_bound_receipt_after_cleanup(self):
        receipt, evidence = self.run_case()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt['results'], {'test_sum': 'passed'})
        self.assertEqual(receipt['stage_sha256'], oracle.digest(oracle.canonical(self.stage)))
        self.assertEqual(receipt['evidence_sha256'], oracle.digest(oracle.canonical(evidence)))
        self.assertEqual(evidence['candidate_attach_exit_code'], 0)
        self.assertEqual(evidence['candidate_container_exit_code'], 0)
        self.assertTrue(evidence['cleanup_verified'])
        self.assertLess(self.calls.index('capture'), self.calls.index('stop'))
        self.assertEqual(self.calls.count('stop'), 1)
        self.assertEqual(self.calls[-3:], ['stop', 'inspect', 'pipe_close'])
        self.assertNotIn(b'expected', self.sent[0])

    def test_known_wrong_response_is_outer_failed_not_execution_failure(self):
        self.returned = response((0,))
        receipt, evidence = self.run_case()
        self.assertEqual(receipt['exit_code'], 1)
        self.assertEqual(evidence['candidate_attach_exit_code'], 0)
        self.assertEqual(receipt['results'], {'test_sum': 'failed'})

    def test_forged_pass_marker_never_creates_receipt(self):
        self.returned = b'{"passed":true}\n'
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertTrue(evidence['cleanup_verified'])

    def test_deadline_still_performs_stop_and_independent_readback(self):
        self.finish_failure = True
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertIn('stop', self.calls)
        self.assertTrue(evidence['cleanup_verified'])
        self.assertNotIn('secret', repr(evidence))

    def test_failed_stop_does_not_skip_cleanup_readback_or_close(self):
        self.stop_failure = True
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertFalse(evidence['stop_acknowledged'])
        self.assertEqual(self.calls[-3:], ['stop', 'inspect', 'pipe_close'])

    def test_populated_cgroup_blocks_even_correct_response(self):
        self.empty_failure = True
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertFalse(evidence['cleanup_verified'])

    def test_uncaptured_cgroup_and_identity_changes_prevent_source_transfer(self):
        for field in ('missing_capture', 'identity_change'):
            with self.subTest(field=field):
                setattr(self, field, True)
                receipt, evidence = self.run_case()
                self.assertIsNone(receipt)
                self.assertEqual(self.sent, [])
                setattr(self, field, False)

    def test_binding_changes_fail_before_any_os_action(self):
        for target, key, value in [(self.plan, 'harness_sha256', '9' * 64),
                (self.plan, 'test_names', ('wrong',)), (self.plan, 'max_seconds', 30),
                (self.plan, 'enabled', False), (self.stage, 'candidate_tree_sha256', '9' * 64)]:
            original = target[key] if isinstance(target, dict) else getattr(target, key)
            if isinstance(target, dict): target[key] = value
            else: setattr(target, key, value)
            with self.assertRaises(ValueError):
                self.run_case()
            if isinstance(target, dict): target[key] = original
            else: setattr(target, key, original)
        self.assertEqual(self.calls, [])

    def test_expired_total_budget_still_cleans_but_does_not_accept(self):
        original = self.command
        def command(args):
            result = original(args)
            if args[0] == 'stop':
                self.clock += 61
            return result
        self.command = command
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertTrue(evidence['cleanup_verified'])
        self.assertEqual(evidence['elapsed_ms'], 61000)

    def test_uncertain_create_still_identity_checks_and_stops_once(self):
        original = self.command
        def command(args):
            result = original(args)
            if args[0] == 'create':
                raise TimeoutError('lost create response')
            return result
        self.command = command
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertTrue(evidence['cleanup_verified'])
        self.assertEqual(self.calls.count('create'), 1)
        self.assertEqual(self.calls.count('stop'), 1)
        self.assertEqual(self.sent, [])

    def test_successful_response_but_candidate_nonzero_exit_is_blocked(self):
        self.value['State']['ExitCode'] = 1
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertTrue(evidence['cleanup_verified'])

    def test_successful_response_but_candidate_oom_is_blocked(self):
        self.value['State']['OOMKilled'] = True
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertTrue(evidence['cleanup_verified'])

    def test_incomplete_eof_observation_blocks_receipt(self):
        original = self.pipe.finish
        def finish(pipe, deadline):
            code = original(pipe, deadline)
            pipe.eof.remove('stderr')
            return code
        self.pipe.finish = finish
        receipt, evidence = self.run_case()
        self.assertIsNone(receipt)
        self.assertFalse(evidence['stderr_eof'])

    def test_nested_outer_alarm_is_not_replaced(self):
        with patch.object(live.signal, 'getitimer', return_value=(90.0, 0.0)), \
                patch.object(live.signal, 'setitimer') as timer:
            receipt, _ = self.run_case()
        self.assertIsNotNone(receipt)
        timer.assert_not_called()


class PipeTests(unittest.TestCase):
    def setUp(self):
        self.in_read, in_write = os.pipe()
        out_read, self.out_write = os.pipe()
        err_read, self.err_write = os.pipe()
        self.process = types.SimpleNamespace(stdin=os.fdopen(in_write, 'wb', buffering=0),
            stdout=os.fdopen(out_read, 'rb', buffering=0), stderr=os.fdopen(err_read, 'rb', buffering=0),
            wait=Mock(return_value=0))
        self.pipe = oracle.OraclePipe(self.process, 1024)
        self.addCleanup(self.pipe.close)
        self.addCleanup(os.close, self.in_read)
        self.addCleanup(self.close_writers)

    def close_writers(self):
        for fd in (self.out_write, self.err_write):
            try: os.close(fd)
            except OSError: pass

    def test_readiness_and_both_eofs_are_required(self):
        deadline = oracle.time.monotonic() + 1
        os.write(self.out_write, b'NESTED_READY:verified\n')
        self.pipe.wait_ready(deadline)
        os.write(self.out_write, response())
        self.close_writers()
        self.assertEqual(self.pipe.finish(deadline), 0)
        self.assertEqual(self.pipe.eof, {'stdout', 'stderr'})
        self.assertEqual(bytes(self.pipe.buffer), response())
        self.assertEqual(self.pipe.total, len(response()) + len(b'NESTED_READY:verified\n'))

    def test_exact_task_limit_includes_readiness(self):
        os.write(self.out_write, b'x' * 1025)
        with self.assertRaisesRegex(live.LiveFailure, 'oracle_output_limit'):
            self.pipe.pump(oracle.time.monotonic() + 1)

    def test_stderr_disqualifies_otherwise_complete_output(self):
        os.write(self.out_write, response())
        os.write(self.err_write, b'forged output')
        self.close_writers()
        with self.assertRaisesRegex(live.LiveFailure, 'oracle_execution_failed'):
            self.pipe.finish(oracle.time.monotonic() + 1)

    def test_frame_write_closes_input(self):
        self.pipe.send(b'frame', oracle.time.monotonic() + 1)
        self.assertEqual(os.read(self.in_read, 20), b'frame')
        self.assertEqual(os.read(self.in_read, 20), b'')

    def test_complete_line_without_eof_times_out(self):
        os.write(self.out_write, response())
        with self.assertRaisesRegex(live.LiveFailure, 'oracle_output_timeout'):
            self.pipe.finish(oracle.time.monotonic() + .01)


if __name__ == '__main__':
    unittest.main()
