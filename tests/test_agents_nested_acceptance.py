"""Real fixed Python pipes plus modeled Podman/cgroup observations, never VPS tests."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_nested_acceptance as gate
from ops.bounded_worker.agents_live_pilot import LiveFailure, PodmanExecutor
from tests import test_agents_live_pilot as fixtures

REAL_CAPTURE = PodmanExecutor._capture_cgroup


def local_gate(payload="print('NESTED_OK', flush=True)", *, wait=20):
    # Test-only substitution: no Codex/container binary or network is invoked.
    replacement = f'os.execve({sys.executable!r}, {[sys.executable, "-I", "-S", "-B", "-c", payload]!r}, dict(os.environ))'
    return gate.GATE_PROGRAM.replace(
        f'os.execve({gate.CODEX!r}, {gate.SANDBOX_ARGV!r}, dict(os.environ))', replacement
    ).replace('[], [], 20)', f'[], [], {wait})')


class GatePipeTests(unittest.TestCase):
    def start(self, program=None):
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c',
                                    program or local_gate()], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, env={'LANG': 'C.UTF-8'})
        def clean():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.addCleanup(clean)
        return process

    def test_no_execution_before_go_and_same_pid_survives_exec(self):
        process = self.start(local_gate('import os; print(os.getpid(), flush=True)'))
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        self.assertIsNone(process.poll())
        self.assertFalse(pipe.ready.select(.02))
        pipe.release(time.monotonic() + 2)
        while pipe.ready.get_map():
            pipe.pump(time.monotonic() + 2)
        self.assertEqual(process.wait(timeout=2), 0)
        self.assertEqual(bytes(pipe.buffer), (str(process.pid) + '\n').encode())

    def test_immediate_sentinel_completion(self):
        process = self.start()
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        pipe.release(time.monotonic() + 2)
        pipe.finish(time.monotonic() + 2)
        self.assertEqual(bytes(pipe.buffer), gate.DONE)

    def test_closed_stdin_never_executes_sentinel(self):
        process = self.start()
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        process.stdin.close()
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_failed'):
            pipe.finish(time.monotonic() + 2)
        self.assertEqual(process.returncode, 75)
        self.assertEqual(bytes(pipe.buffer), b'')

    def test_wrong_go_never_executes_sentinel(self):
        process = self.start()
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        os.write(process.stdin.fileno(), b'X')
        process.stdin.close()
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_failed'):
            pipe.finish(time.monotonic() + 2)
        self.assertEqual(process.returncode, 75)
        self.assertEqual(bytes(pipe.buffer), b'')

    def test_child_gate_has_its_own_timeout(self):
        process = self.start(local_gate(wait=.03))
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_failed'):
            pipe.finish(time.monotonic() + 2)
        self.assertEqual(process.returncode, 74)

    def test_stderr_counted_and_discarded(self):
        process = self.start(local_gate("import os; os.write(2, b'x' * 65537); print('NESTED_OK')"))
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        pipe.release(time.monotonic() + 2)
        with self.assertRaisesRegex(LiveFailure, 'acceptance_output_limit'):
            pipe.finish(time.monotonic() + 2)
        self.assertNotIn(b'x', pipe.buffer)

    def test_missing_ready_eof_and_timeout_distinct(self):
        for program, reason in (("pass", 'acceptance_ready_eof'),
                                ('import time; time.sleep(5)', 'acceptance_pipe_timeout')):
            with self.subTest(reason=reason):
                process = self.start(program)
                pipe = gate._Pipe(process)
                try:
                    with self.assertRaisesRegex(LiveFailure, reason):
                        pipe.wait_ready(time.monotonic() + .1)
                finally:
                    pipe.close()


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PodmanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.value = copy.deepcopy(self.fixture.value)
        self.value['Config']['Labels'].update({'agents.session': 'acceptance_session',
                                               'agents.environment': 'acceptance_environment'})
        self.attempt = self.fixture.binding.attempt_id
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        (self.path / 'cgroup.events').write_text('populated 1\n')
        self.process = None
        self.actions = []
        self.capture_error = self.inspect_change = False
        self.startup_path = None
        self.gate_program = local_gate()
        self.addCleanup(self.clean_process)

    def clean_process(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=2)

    def command(self, args):
        self.actions.append(args[0])
        if args[:2] == ['container', 'exists']:
            return 1, b''
        if args[0] == 'create':
            self.created_args = args
            return 0, (self.value['Id'] + '\n').encode()
        if args[0] == 'stop':
            self.clean_process()
            self.value['State'] = {'Running': False, 'Status': 'stopped', 'Pid': 0}
            (self.path / 'cgroup.events').write_text('populated 0\n')
            return 0, b''
        if args[0] == 'inspect':
            return 0, json.dumps([self.value]).encode()
        raise AssertionError('unexpected command')

    def popen(self, args, **kwargs):
        self.actions.append('start')
        self.assertEqual(args[:3], ['/usr/bin/podman', '--remote=false', 'start'])
        self.process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', self.gate_program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env={'LANG': 'C.UTF-8'})
        self.value['State'] = {'Running': True, 'Status': 'running', 'Pid': self.process.pid,
                              'CgroupPath': self.startup_path or
                              '/synthetic/libpod-' + self.value['Id'] + '.scope'}
        if self.capture_error is True:
            self.value['State'].pop('CgroupPath')
        return self.process

    def capture(self, backend, value):
        self.actions.append('capture')
        self.assertIsNone(self.process.poll())
        if self.capture_error is True:
            self.value['State'].pop('CgroupPath', None)
            raise LiveFailure('container_cgroup_unverified')
        if self.capture_error == 'unbound':
            REAL_CAPTURE(backend, value)
            raise AssertionError('capture should fail before opening a cgroup')
        if self.capture_error in ('pid', 'limits'):
            relative = value['State']['CgroupPath']
            full = '/sys/fs/cgroup' + relative
            reads = {'/proc/' + str(value['State']['Pid']) + '/cgroup':
                     '0::' + ('/unrelated' if self.capture_error == 'pid' else relative) + '\n',
                     full + '/memory.max': '0' if self.capture_error == 'limits' else '1073741824',
                     full + '/pids.max': '64', full + '/cpu.max': '100000 100000'}
            self.opened_fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
            with patch.object(Path, 'is_symlink', return_value=False), \
                    patch.object(Path, 'read_text', new=lambda path: reads[str(path)]), \
                    patch.object(os, 'open', return_value=self.opened_fd):
                REAL_CAPTURE(backend, value)
            raise AssertionError('capture should fail')
        backend.cgroup_fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        if self.inspect_change:
            self.value['State']['Pid'] += 1

    def run_acceptance(self):
        with patch.object(PodmanExecutor, 'verify_prepared'), \
                patch.object(PodmanExecutor, '_capture_cgroup', new=lambda backend, value: self.capture(backend, value)):
            return gate.run_nested_acceptance(self.fixture.target, self.attempt,
                                              command=self.command, popen=self.popen)

    def test_capture_precedes_go_and_fast_exit_keeps_inode_proof(self):
        result = self.run_acceptance()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['nested'], 'passed')
        self.assertEqual(result['cleanup'], 'verified')
        self.assertTrue(result['go_sent'])
        self.assertEqual(self.actions, ['container', 'create', 'inspect', 'start',
                                       'inspect', 'capture', 'inspect', 'inspect', 'stop', 'inspect'])
        self.assertEqual(result['startup_state']['cgroup_path'], 'present')

    def test_capture_failure_never_sends_go_or_claims_cleanup(self):
        self.capture_error = True
        result = self.run_acceptance()
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['stage'], 'capture')
        self.assertEqual(result['nested'], 'not_run')
        self.assertEqual(result['cleanup'], 'unverified')
        self.assertEqual(result['reason'], 'container_cgroup_unverified')
        self.assertEqual(result['startup_state']['cgroup_path'], 'missing')
        self.assertIn('stop', self.actions)

    def test_changed_pid_before_go_blocks_but_bound_cleanup_is_independent(self):
        self.inspect_change = True
        result = self.run_acceptance()
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['reason'], 'acceptance_changed_before_go')
        self.assertEqual(result['cleanup'], 'verified')
        self.assertEqual(result['status'], 'blocked')

    def test_unbound_failure_retains_sanitized_shape_and_runtime_before_guard(self):
        self.capture_error = 'unbound'
        self.startup_path = '/private-ancestor/shared-scope'
        self.value['OCIRuntime'] = 'crun'
        self.value['HostConfig'].update(CgroupManager='cgroupfs', Cgroups='default', CgroupMode='private')
        result = self.run_acceptance()
        summary = result['cgroup_diagnostics']
        self.assertEqual(summary['component'], 'no_match')
        self.assertEqual(summary['position'], 'none')
        self.assertEqual(summary['manager'], 'cgroupfs')
        self.assertEqual(summary['runtime'], 'crun')
        self.assertEqual(summary['requested_mode'], 'enabled')
        self.assertEqual(summary['inspect_cgroups'], 'default')
        self.assertEqual(summary['inspect_namespace'], 'private')
        self.assertEqual(result['reason'], 'container_cgroup_unbound')
        self.assertEqual(result['cleanup'], 'unverified')
        self.assertFalse(result['go_sent'])
        self.assertNotIn('private-ancestor', repr(summary))
        self.assertNotIn(self.value['Id'], repr(summary))

    def test_payload_is_diagnosed_without_relaxing_enabled_mode_guard(self):
        self.capture_error = 'unbound'
        self.startup_path = '/private-ancestor/libpod-payload-' + self.value['Id']
        result = self.run_acceptance()
        self.assertEqual(result['cgroup_diagnostics']['component'], 'split_payload_full_id')
        self.assertEqual(result['cgroup_diagnostics']['position'], 'leaf')
        self.assertEqual(result['reason'], 'container_cgroup_unbound')
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['cleanup'], 'unverified')

    def test_post_open_pid_mismatch_closes_fd_and_never_authorizes_go_or_cleanup(self):
        self.capture_error = 'pid'
        result = self.run_acceptance()
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['reason'], 'container_cgroup_pid_mismatch')
        self.assertEqual(result['cleanup'], 'unverified')
        with self.assertRaises(OSError):
            os.fstat(self.opened_fd)

    def test_post_open_limits_mismatch_closes_fd_and_never_authorizes_go_or_cleanup(self):
        self.capture_error = 'limits'
        result = self.run_acceptance()
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['reason'], 'container_cgroup_limits_unverified')
        self.assertEqual(result['cleanup'], 'unverified')
        with self.assertRaises(OSError):
            os.fstat(self.opened_fd)

    def test_timeout_after_capture_still_attempts_cleanup(self):
        self.gate_program = local_gate('import time; time.sleep(10)')
        with patch.object(gate._Pipe, 'finish', side_effect=LiveFailure('acceptance_pipe_timeout')):
            result = self.run_acceptance()
        self.assertTrue(result['go_sent'])
        self.assertEqual(result['cleanup'], 'verified')
        self.assertEqual(result['reason'], 'acceptance_pipe_timeout')

    def test_eof_before_ready_cleans_without_inventing_cgroup_proof(self):
        self.gate_program = 'raise SystemExit(0)'
        result = self.run_acceptance()
        self.assertFalse(result['go_sent'])
        self.assertNotIn('capture', self.actions)
        self.assertEqual(result['reason'], 'acceptance_ready_eof')
        self.assertEqual(result['cleanup'], 'unverified')
        self.assertIn('stop', self.actions)

    def test_uncertain_create_retains_identity_resolved_during_cleanup(self):
        original = self.command
        def command(args):
            result = original(args)
            return (0, b'invalid-create-reply') if args[0] == 'create' else result
        self.command = command
        result = self.run_acceptance()
        self.assertEqual(result['container_id'], self.value['Id'])
        self.assertEqual(result['reason'], 'container_create_uncertain')
        self.assertFalse(result['go_sent'])
        self.assertNotIn('start', self.actions)

    def test_missing_cgroup_is_not_accepted_from_another_json_level(self):
        backend = self.fixture.executor
        backend.container_id = self.value['Id']
        value = {'State': {'Status': 'running', 'Running': True, 'Pid': 123},
                 'CgroupPath': '/synthetic/libpod-' + self.value['Id'] + '.scope'}
        with self.assertRaisesRegex(LiveFailure, 'container_cgroup_unverified'):
            backend._capture_cgroup(value)
        self.assertIsNone(backend.cgroup_fd)

    def test_native_state_cgroup_shape_binds_without_fallback(self):
        backend = self.fixture.executor
        backend.container_id = self.value['Id']
        relative = '/synthetic/libpod-' + self.value['Id'] + '.scope'
        full = '/sys/fs/cgroup' + relative
        value = {'State': {'Status': 'running', 'Running': True, 'Pid': 123,
                           'CgroupPath': relative}}
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        reads = {'/proc/123/cgroup': '0::' + relative + '\n',
                 full + '/memory.max': '1073741824', full + '/pids.max': '64',
                 full + '/cpu.max': '100000 100000'}
        with patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(Path, 'read_text', new=lambda path: reads[str(path)]), \
                patch.object(os, 'open', return_value=fd) as opened:
            backend._capture_cgroup(value)
        self.assertEqual(backend.cgroup_fd, fd)
        self.assertEqual(str(opened.call_args.args[0]), full)

    def test_exact_profile_only_entrypoint_and_command_change(self):
        executor = self.fixture.executor
        binding = self.fixture.binding
        original = executor.create_arguments(binding)
        args = gate.acceptance_arguments(executor, binding)
        index = original.index('sha256:' + executor.target.image_id)
        expected = original[:index]
        expected[expected.index('--entrypoint=' + gate.CODEX)] = '--entrypoint=' + gate.PYTHON
        self.assertEqual(args[:index], expected)
        self.assertEqual(args[index + 1:], ['-I', '-S', '-B', '-c', gate.GATE_PROGRAM])
        self.assertNotIn(binding.remote_url, repr(args))
        self.assertNotIn('--registration-bearer-stdin', args)
        self.assertNotIn('synthetic-no-key', repr(args))

    def test_safe_state_summary_distinguishes_omitted_empty_and_stopped(self):
        value = {'State': {'Status': 'stopped', 'Running': False, 'Pid': 0}}
        self.assertEqual(gate.state_summary(value), {'status': 'stopped', 'running': 'no',
                                                     'pid': 'zero', 'cgroup_path': 'missing'})
        value['State']['CgroupPath'] = ''
        self.assertEqual(gate.state_summary(value)['cgroup_path'], 'empty')
        value['State']['CgroupPath'] = '/private/path'
        self.assertEqual(gate.state_summary(value)['cgroup_path'], 'present')
        self.assertNotIn('private', repr(gate.state_summary(value)))


class CgroupDiagnosticTests(unittest.TestCase):
    def summary(self, path):
        return gate.cgroup_summary({'State': {'CgroupPath': path}}, 'a' * 64)

    def test_exact_known_components_leaf_and_ancestor(self):
        for component, kind in (('a' * 64, 'bare_full_id'),
                ('libpod-' + 'a' * 64, 'cgroupfs_full_id'),
                ('libpod-' + 'a' * 64 + '.scope', 'systemd_full_id'),
                ('libpod-payload-' + 'a' * 64, 'split_payload_full_id')):
            for suffix, position in (('', 'leaf'), ('/child', 'ancestor')):
                with self.subTest(kind=kind, position=position):
                    result = self.summary('/private-prefix/' + component + suffix)
                    self.assertEqual(result['component'], kind)
                    self.assertEqual(result['position'], position)
                    self.assertNotIn(component, repr(result))
                    self.assertNotIn('private-prefix', repr(result))

    def test_different_and_short_ids_are_only_classified_not_matched(self):
        for component, kind in (('libpod-' + 'b' * 64, 'different_full_id'),
                               ('libpod-' + 'a' * 12, 'short_bound_id'),
                               ('libpod-' + 'b' * 12 + '.scope', 'different_short_id')):
            result = self.summary('/root/' + component)
            self.assertEqual(result['component'], kind)
            self.assertEqual(result['position'], 'leaf')
        for component in ('prefix-libpod-' + 'a' * 64, 'libpod-' + 'a' * 64 + '-suffix'):
            self.assertEqual(self.summary('/root/' + component)['component'], 'no_match')

    def test_multiple_and_mixed_components_retain_ambiguity(self):
        result = self.summary('/libpod-' + 'a' * 64 + '/libpod-payload-' + 'a' * 64)
        self.assertEqual(result['component'], 'multiple_bound_components')
        self.assertEqual(result['position'], 'multiple')
        result = self.summary('/libpod-' + 'a' * 64 + '/libpod-' + 'b' * 64 + '/' + 'a' * 12)
        self.assertEqual(result['component'], 'cgroupfs_full_id')
        self.assertEqual(result['position'], 'ancestor')
        self.assertEqual(result['other_full_id_present'], 'yes')
        self.assertEqual(result['short_id_present'], 'yes')

    def test_missing_malformed_root_and_dot_paths_do_not_reveal_input(self):
        for value in (None, '', 7, 'relative/private/path'):
            self.assertEqual(self.summary(value)['component'], 'unavailable')
        for value in ('/', '/sys/fs/cgroup/'):
            self.assertEqual(self.summary(value)['root_path'], 'yes')
        self.assertEqual(self.summary('/private/../scope')['dot_components'], 'yes')
        self.assertEqual(self.summary('/private/./scope')['dot_components'], 'yes')

    def test_runtime_and_manager_fields_use_closed_enums_without_new_reads(self):
        value = {'OCIRuntime': '/private/secret-runtime', 'HostConfig': {
            'CgroupManager': 'secret-manager', 'Cgroups': 'split', 'CgroupMode': 'ns:/private/ns'}}
        with patch.object(os, 'open', side_effect=AssertionError('no filesystem read')), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('no runtime call')):
            result = gate.cgroup_summary(value, 'a' * 64)
        for name in ('runtime', 'manager', 'inspect_cgroups', 'inspect_namespace'):
            self.assertEqual(result[name], 'other')
        self.assertEqual(result['requested_mode'], 'enabled')
        self.assertNotIn('secret', repr(result))
        self.assertNotIn('/private/', repr(result))


if __name__ == '__main__':
    unittest.main()
