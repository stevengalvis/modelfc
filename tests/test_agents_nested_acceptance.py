"""Real fixed Python pipes plus modeled Podman/cgroup observations, never VPS tests."""
import copy
from contextlib import contextmanager
import errno
import json
from io import BytesIO
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
CONTROL_VALUES = {'memory.max': '1073741824', 'memory.swap.max': '0',
                  'pids.max': '64', 'cpu.max': '100000 100000', 'cpu.max.burst': '0'}


def local_gate(payload="print('NESTED_OK', flush=True)", *, wait=20, mount_status="verified"):
    # Test-only substitution: no Codex/container binary or network is invoked.
    replacement = f'os.execve({sys.executable!r}, {[sys.executable, "-I", "-S", "-B", "-c", payload]!r}, dict(os.environ))'
    return gate.GATE_PROGRAM.replace(
        f'os.execve({gate.CODEX!r}, {gate.SANDBOX_ARGV!r}, dict(os.environ))', replacement
    ).replace('[], [], 20)', f'[], [], {wait})').replace(
        'mount_status = _mount_check()', f'mount_status = {mount_status!r}').replace(
            gate.CODEX_HOME_SETUP, '')


class StderrDiagnosticTests(unittest.TestCase):
    def test_unknown_failure_keeps_useful_detail_with_redaction(self):
        raw = (b'Unexpected helper failure: invalid resource descriptor\n'
               b'path /home/private/work; endpoint https://private.example/token\n'
               b'Authorization: Bearer private-token\nAPI_KEY=sk-private-value\n')
        result = gate.stderr_summary(raw)
        self.assertEqual(result['signatures'], ['unclassified_nonempty'])
        self.assertIn('invalid resource descriptor', result['detail'])
        self.assertNotIn('private', result['detail'])
        self.assertIn('[path]', result['detail'])
        self.assertIn('[url]', result['detail'])
        self.assertIn('[redacted]', result['detail'])

    def test_diagnostic_signatures_and_head_tail_are_bounded(self):
        raw = b'\x1b[31mbwrap: Creating new namespace failed: Operation not permitted\x1b[0m\n'
        result = gate.stderr_summary(raw)
        self.assertEqual(result['signatures'], ['permission_denied', 'namespace'])
        self.assertNotIn('\x1b', result['detail'])
        result = gate.stderr_summary(b'head ' + b'x' * 4096 + b' tail')
        self.assertTrue(result['detail_truncated'])
        self.assertLess(len(result['detail']), 2100)
        self.assertTrue(result['detail'].startswith('head '))
        self.assertTrue(result['detail'].endswith(' tail'))


class MountCheckTests(unittest.TestCase):
    def setUp(self):
        self.namespace = {}
        exec(gate.MOUNT_CHECK, self.namespace)
        self.check = self.namespace['_mount_check']
        # Per-mount ro is decisive; a shared superblock may still report rw.
        self.valid = b'31 20 0:28 / /sys/fs/cgroup ro,nosuid,nodev,noexec - cgroup2 cgroup rw\n'

    def check_raw(self, raw):
        with patch('builtins.open', return_value=BytesIO(raw)) as opened:
            result = self.check()
        opened.assert_called_once_with('/proc/self/mountinfo', 'rb')
        return result

    def test_readonly_per_mount_with_writable_superblock_is_accepted(self):
        self.assertEqual(self.check_raw(self.valid), 'verified')

    def test_writable_primary_or_alias_is_rejected(self):
        for raw in (self.valid.replace(b'ro,nosuid', b'rw,nosuid'),
                self.valid + b'32 20 0:28 / /alternate rw - cgroup2 cgroup rw\n'):
            with self.subTest(raw=raw):
                self.assertEqual(self.check_raw(raw), 'writable')

    def test_missing_stacked_wrong_type_and_malformed_are_rejected(self):
        self.assertEqual(self.check_raw(self.valid.replace(b'cgroup2', b'cgroup')), 'unsupported')
        self.assertEqual(self.check_raw(self.valid.replace(b'/sys/fs/cgroup', b'/elsewhere')), 'missing')
        self.assertEqual(self.check_raw(self.valid +
            b'32 31 0:29 / /sys/fs/cgroup rw - tmpfs tmpfs rw\n'), 'missing')
        for raw in (b'', self.valid.rstrip(), b'private-invalid\n', self.valid + b'bad\n'):
            with self.subTest(raw=raw):
                self.assertEqual(self.check_raw(raw), 'invalid')

    def test_unreadable_and_overflow_are_fixed_enums(self):
        with patch('builtins.open', side_effect=PermissionError('private details')):
            self.assertEqual(self.check(), 'unreadable')
        self.assertEqual(self.check_raw(b'x' * 65537), 'oversized')


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

    def test_failed_self_mount_check_holds_but_rejects_go(self):
        process = self.start(local_gate(mount_status='writable'))
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        self.assertEqual(pipe.wait_ready(time.monotonic() + 2), 'writable')
        self.assertIsNone(process.poll())
        pipe.release(time.monotonic() + 2)
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_exit_nonzero'):
            pipe.finish(time.monotonic() + 2)
        self.assertEqual(process.returncode, 75)
        self.assertEqual(bytes(pipe.buffer), b'')

    def test_closed_stdin_never_executes_sentinel(self):
        process = self.start()
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        process.stdin.close()
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_exit_nonzero'):
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
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_exit_nonzero'):
            pipe.finish(time.monotonic() + 2)
        self.assertEqual(process.returncode, 75)
        self.assertEqual(bytes(pipe.buffer), b'')

    def test_child_gate_has_its_own_timeout(self):
        process = self.start(local_gate(wait=.03))
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        with self.assertRaisesRegex(LiveFailure, 'acceptance_sentinel_exit_nonzero'):
            pipe.finish(time.monotonic() + 2)
        self.assertEqual(process.returncode, 74)

    def test_stderr_combined_budget_and_separation(self):
        process = self.start(local_gate("import os; os.write(2, b'x' * 65537); print('NESTED_OK')"))
        pipe = gate._Pipe(process)
        self.addCleanup(pipe.close)
        pipe.wait_ready(time.monotonic() + 2)
        pipe.release(time.monotonic() + 2)
        with self.assertRaisesRegex(LiveFailure, 'acceptance_output_limit'):
            pipe.finish(time.monotonic() + 2)
        self.assertNotIn(b'x', pipe.buffer)
        self.assertLessEqual(len(pipe.stderr) + len(pipe.buffer), gate.LIMIT)
        self.assertTrue(pipe.summary()['combined_limit_exceeded'])

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
        self.preflight_error = False
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
            self.value['State'] = {'Running': False, 'Status': 'stopped', 'Pid': 0,
                                   'ExitCode': self.process.returncode if self.process else 0,
                                   'OOMKilled': False}
            (self.path / 'cgroup.events').write_text('populated 0\n')
            return 0, b''
        if args[0] == 'inspect':
            if self.process is not None and self.process.poll() is not None:
                self.value['State'].update(Running=False, Status='exited', Pid=0,
                                            ExitCode=self.process.returncode, OOMKilled=False)
            return 0, json.dumps([self.value]).encode()
        raise AssertionError('unexpected command')

    def popen(self, args, **kwargs):
        self.actions.append('start')
        self.assertEqual(args[:4], ['/usr/bin/podman', '--remote=false', '--cgroup-manager=systemd', 'start'])
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
        if self.capture_error in ('pid', 'limits', 'descendant', 'events'):
            relative = value['State']['CgroupPath']
            suffix = '/container' if self.capture_error == 'descendant' else ''
            membership = '0::' + ('/unrelated' if self.capture_error == 'pid' else relative + suffix) + '\n'
            payload = {'memory.max': '0'} if self.capture_error == 'limits' else {}
            with self.capture_files(value, membership, suffix=suffix,
                                    scope={'pids.max': 'max'} if suffix else {}, payload=payload):
                if self.capture_error == 'events':
                    (self.path / 'cgroup.events').write_text('populated invalid\n')
                REAL_CAPTURE(backend, value)
            if self.capture_error == 'descendant':
                return
            raise AssertionError('capture should fail')
        backend.cgroup_fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        backend.cgroup_events_fd = os.open(self.path / 'cgroup.events', os.O_RDONLY)
        if self.inspect_change:
            self.value['State']['Pid'] += 1

    def capture_value(self, relative=None):
        value = copy.deepcopy(self.value)
        value['State'] = {'Running': True, 'Status': 'running', 'Pid': 123,
                          'CgroupPath': relative or '/synthetic/libpod-' + self.value['Id'] + '.scope'}
        self.fixture.executor.container_id = self.value['Id']
        return value

    @contextmanager
    def capture_files(self, value, membership, *, suffix='', scope=None, payload=None):
        # Ordinary temp files model cgroup controls; only host path/procfs lookup
        # is redirected. Descriptor-relative traversal/read/close are real.
        for directory, overrides in ((self.path, scope or {}),
                (self.path / suffix.lstrip('/'), payload or {})):
            directory.mkdir(parents=True, exist_ok=True)
            for name, default in CONTROL_VALUES.items():
                control = directory / name
                val = overrides.get(name, default)
                if val is None:
                    control.unlink(missing_ok=True)
                else:
                    control.write_text(val)
        initial = '/sys/fs/cgroup' + value['State']['CgroupPath']
        proc = '/proc/' + str(value['State']['Pid']) + '/cgroup'
        real_open, real_read = os.open, Path.read_text
        observations = iter(membership) if isinstance(membership, list) else None

        def opened(path, flags, *args, **kwargs):
            if str(path) == initial:
                self.opened_fd = real_open(self.path, flags, *args, **kwargs)
                return self.opened_fd
            fd = real_open(path, flags, *args, **kwargs)
            if str(path) == 'cgroup.events':
                self.opened_events_fd = fd
            return fd

        def read(path, *args, **kwargs):
            if str(path) == proc:
                return next(observations) if observations is not None else membership
            return real_read(path, *args, **kwargs)

        with patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(Path, 'read_text', new=read), patch.object(os, 'open', new=opened):
            yield

    def run_acceptance(self):
        with patch.object(PodmanExecutor, 'verify_prepared', side_effect=
                          LiveFailure('prepared_runtime_unverified') if self.preflight_error else None), \
                patch.object(PodmanExecutor, '_capture_cgroup', new=lambda backend, value: self.capture(backend, value)):
            return gate.run_nested_acceptance(self.fixture.target, self.attempt,
                                              command=self.command, popen=self.popen)

    def test_capture_precedes_go_and_fast_exit_keeps_inode_proof(self):
        result = self.run_acceptance()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['nested'], 'passed')
        self.assertEqual(result['cleanup'], 'verified')
        self.assertTrue(result['go_sent'])
        self.assertEqual(result['mount_check'], 'verified')
        self.assertEqual(self.actions, ['container', 'create', 'inspect', 'start',
                                       'inspect', 'capture', 'inspect', 'inspect', 'stop', 'inspect'])
        self.assertEqual(result['startup_state']['cgroup_path'], 'present')

    def test_nonzero_exit_retains_distinct_attach_container_and_stderr_evidence(self):
        self.gate_program = local_gate('import sys; print("helper failed: novel diagnostic", file=sys.stderr); sys.exit(7)')
        result = self.run_acceptance()
        self.assertEqual(result['reason'], 'acceptance_sentinel_exit_nonzero')
        self.assertEqual(result['nested'], 'failed')
        process = result['process_result']
        self.assertEqual(process['attach_returncode_before_stop'], 7)
        self.assertEqual(process['stdout_class'], 'empty')
        self.assertEqual(process['sentinel_stdout_bytes'], 0)
        self.assertGreater(process['stderr_bytes'], 0)
        self.assertIn('novel diagnostic', process['stderr']['detail'])
        self.assertEqual(result['cleanup_diagnostics']['before_stop']['exit_code'], 7)
        self.assertFalse(result['cleanup_diagnostics']['before_stop']['oom_killed'])
        self.assertEqual(result['cleanup'], 'verified')

    def test_zero_exit_unexpected_stdout_is_distinct_from_process_failure(self):
        self.gate_program = local_gate("print('NESTED_OK'); print('extra')")
        result = self.run_acceptance()
        self.assertEqual(result['reason'], 'acceptance_sentinel_stdout_mismatch')
        self.assertEqual(result['process_result']['attach_returncode_before_stop'], 0)
        self.assertEqual(result['process_result']['stdout_class'], 'sentinel_plus_other')
        self.assertEqual(result['process_result']['stderr']['detail'], '')
        self.assertEqual(result['cleanup'], 'verified')

    def test_stop_error_and_independent_readback_error_remain_separate(self):
        original = self.command
        def command(args):
            if args[0] == 'stop':
                original(args)  # Model a completed stop whose reply is lost.
                raise OSError(errno.EIO, 'private stop details')
            return original(args)
        self.command = command
        with patch.object(os, 'pread', side_effect=OSError(errno.EACCES, 'private read details')):
            result = self.run_acceptance()
        self.assertEqual(result['reason'], 'stop_uncertain')
        self.assertEqual(result['cleanup'], 'unverified')
        self.assertEqual(result['cleanup_diagnostics']['stop']['error']['errno'], 'EIO')
        self.assertEqual(result['cleanup_diagnostics']['readback']['error']['errno'], 'EACCES')
        self.assertEqual(result['cleanup_diagnostics']['readback']['stage'], 'events_pread')
        self.assertNotIn('private stop details', repr(result))
        self.assertNotIn('private read details', repr(result))

    def test_same_gate_mount_failure_never_goes_but_scope_cleanup_is_verified(self):
        self.gate_program = local_gate(mount_status='writable')
        result = self.run_acceptance()
        self.assertEqual(result['mount_check'], 'writable')
        self.assertEqual(result['reason'], 'acceptance_mountinfo_writable')
        self.assertEqual(result['stage'], 'mount_check')
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['nested'], 'not_run')
        self.assertEqual(result['cleanup'], 'verified')
        self.assertEqual(self.actions.count('create'), 1)
        self.assertIn('capture', self.actions)

    def test_effective_runtime_failure_blocks_before_container_create(self):
        self.preflight_error = True
        result = self.run_acceptance()
        self.assertEqual(result['stage'], 'preflight')
        self.assertEqual(result['reason'], 'prepared_runtime_unverified')
        self.assertEqual(result['cleanup'], 'not_started')
        self.assertEqual(self.actions, [])
        self.assertFalse(result['go_sent'])

    def test_created_container_fallback_blocks_start_without_retry(self):
        self.value['HostConfig']['CgroupManager'] = 'cgroupfs'
        result = self.run_acceptance()
        self.assertEqual(result['reason'], 'container_manager_rejected')
        self.assertEqual(result['cgroup_diagnostics']['manager'], 'cgroupfs')
        self.assertEqual(result['cgroup_diagnostics']['requested_manager'], 'systemd')
        self.assertNotIn('start', self.actions)
        self.assertEqual(self.actions.count('create'), 1)
        self.assertFalse(result['go_sent'])

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
        self.value['HostConfig'].update(CgroupManager='systemd', Cgroups='default', CgroupMode='private')
        result = self.run_acceptance()
        summary = result['cgroup_diagnostics']
        self.assertEqual(summary['component'], 'no_match')
        self.assertEqual(summary['position'], 'none')
        self.assertEqual(summary['manager'], 'systemd')
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
        self.assertEqual(result['resource_diagnostics']['payload']['memory.max'], '0')
        self.assertEqual(result['resource_diagnostics']['mismatches'], ['memory.max'])
        self.assertEqual(set(result['resource_diagnostics']['scope']), set(CONTROL_VALUES))
        with self.assertRaises(OSError):
            os.fstat(self.opened_fd)

    def test_initial_events_validation_failure_closes_both_fds_and_never_goes(self):
        self.capture_error = 'events'
        result = self.run_acceptance()
        self.assertEqual(result['reason'], 'container_cgroup_events_invalid')
        self.assertFalse(result['go_sent'])
        self.assertEqual(result['cleanup'], 'unverified')
        for fd in (self.opened_fd, self.opened_events_fd):
            with self.assertRaises(OSError):
                os.fstat(fd)

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
        value = self.capture_value()
        value['CgroupPath'] = value['State'].pop('CgroupPath')
        with self.assertRaisesRegex(LiveFailure, 'container_cgroup_unverified'):
            backend._capture_cgroup(value)
        self.assertIsNone(backend.cgroup_fd)

    def test_native_state_cgroup_shape_binds_without_fallback(self):
        backend = self.fixture.executor
        value = self.capture_value()
        relative = value['State']['CgroupPath']
        for suffix in ('', '/container', '/container/init.scope'):
            with self.subTest(suffix=suffix), self.capture_files(value,
                    '0::' + relative + suffix + '\n', suffix=suffix):
                backend._capture_cgroup(value)
                self.assertEqual(backend.cgroup_fd, self.opened_fd)
                self.assertEqual(os.fstat(backend.cgroup_fd).st_ino, self.path.stat().st_ino)
                backend.close()

    def test_membership_rejects_outside_ambiguous_and_noncanonical_paths(self):
        backend = self.fixture.executor
        backend.container_id = self.value['Id']
        relative = '/synthetic/libpod-' + self.value['Id'] + '.scope'
        value = self.capture_value(relative)
        rejected = ['', '0::/\n', '0::/container\n', '0::/synthetic\n',
                    '0::/other-root' + relative + '\n',
                    '0::' + relative + '-sibling/container\n',
                    '0::' + relative + '/container/../other\n',
                    '0::' + relative + '/./container\n',
                    '0::' + relative + '//container\n',
                    '0::' + relative + '/container/\n',
                    '0::' + relative + '/bad\x00name\n',
                    '1:cpu:' + relative + '\n',
                    '0::' + relative + '\n0::' + relative + '/container\n',
                    '0::' + relative + '\nmalformed\n']
        for membership in rejected:
            with self.subTest(membership=membership):
                with self.capture_files(value, membership):
                    with self.assertRaisesRegex(LiveFailure, 'container_cgroup_pid_mismatch'):
                        backend._capture_cgroup(value)
                self.assertIsNone(backend.cgroup_fd)
                with self.assertRaises(OSError):
                    os.fstat(self.opened_fd)

    def test_inspect_path_must_be_canonical_before_open(self):
        backend = self.fixture.executor
        backend.container_id = self.value['Id']
        for prefix in ('/synthetic/./', '/synthetic/../', '/synthetic//'):
            value = self.capture_value(prefix + 'libpod-' + self.value['Id'] + '.scope')
            with self.subTest(prefix=prefix), patch.object(os, 'open') as opened:
                with self.assertRaisesRegex(LiveFailure, 'container_cgroup_unverified'):
                    backend._capture_cgroup(value)
                opened.assert_not_called()

    def test_descendant_membership_releases_gate_and_keeps_scope_cleanup(self):
        self.capture_error = 'descendant'
        result = self.run_acceptance()
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['go_sent'])
        self.assertEqual(result['nested'], 'passed')
        self.assertEqual(result['cleanup'], 'verified')

    def test_payload_limits_bound_but_scope_inode_retained_for_recursive_cleanup(self):
        backend = self.fixture.executor
        value = self.capture_value()
        member = '0::' + value['State']['CgroupPath'] + '/container\n'
        with self.capture_files(value, member, suffix='/container', scope={'pids.max': 'max'}):
            backend._capture_cgroup(value)
        self.assertEqual(backend.resource_diagnostics['scope']['pids.max'], 'max')
        self.assertEqual(backend.resource_diagnostics['payload'], CONTROL_VALUES)
        self.assertEqual(backend.resource_diagnostics['membership'], 'descendant')
        self.assertEqual(os.fstat(backend.cgroup_fd).st_ino, self.path.stat().st_ino)
        self.assertNotEqual(os.fstat(backend.cgroup_fd).st_ino, (self.path / 'container').stat().st_ino)
        backend.binding = self.fixture.binding
        backend._create_attempted = backend._start_attempted = backend._stop_attempted = True
        stopped = copy.deepcopy(value)
        stopped['State'] = {'Running': False, 'Status': 'stopped', 'Pid': 0}
        (self.path / 'container' / 'cgroup.events').write_text('populated 0\n')
        with patch.object(backend, '_inspect', return_value=stopped):
            self.assertFalse(backend.verify_empty(backend.binding))  # Scope still populated.
            (self.path / 'cgroup.events').write_text('populated 0\n')
            self.assertTrue(backend.verify_empty(backend.binding))

    def test_payload_mismatches_retain_all_numeric_controls_and_close_scope(self):
        backend = self.fixture.executor
        value = self.capture_value()
        member = '0::' + value['State']['CgroupPath'] + '/container\n'
        changes = (('memory.max', 'max'), ('memory.max', '2147483648'),
                   ('memory.swap.max', '1'), ('pids.max', 'max'), ('pids.max', '65'),
                   ('cpu.max', '200000 100000'), ('cpu.max', '50000 50000'),
                   ('cpu.max.burst', '1'), ('cpu.max.burst', 'max'))
        for name, actual in changes:
            with self.subTest(name=name, actual=actual), self.capture_files(value, member,
                    suffix='/container', payload={name: actual}):
                with self.assertRaisesRegex(LiveFailure, 'container_cgroup_limits_unverified'):
                    backend._capture_cgroup(value)
            self.assertEqual(backend.resource_diagnostics['payload'], CONTROL_VALUES | {name: actual})
            self.assertEqual(backend.resource_diagnostics['mismatches'], [name])
            self.assertIsNone(backend.cgroup_fd)
            with self.assertRaises(OSError):
                os.fstat(self.opened_fd)

    def test_limit_errors_are_sanitized_without_losing_later_values(self):
        backend = self.fixture.executor
        value = self.capture_value()
        member = '0::' + value['State']['CgroupPath'] + '/container\n'
        with self.capture_files(value, member, suffix='/container', payload={
                'memory.max': 'private-secret', 'memory.swap.max': None,
                'pids.max': '9' * 129}):
            (self.path / 'container' / 'cpu.max.burst').unlink()
            (self.path / 'container' / 'cpu.max.burst').symlink_to(self.path / 'cpu.max.burst')
            with self.assertRaisesRegex(LiveFailure, 'container_cgroup_limits_unverified'):
                backend._capture_cgroup(value)
        actual = backend.resource_diagnostics['payload']
        self.assertEqual(actual, {'memory.max': 'invalid', 'memory.swap.max': 'missing',
            'pids.max': 'invalid', 'cpu.max': '100000 100000', 'cpu.max.burst': 'unreadable'})
        self.assertNotIn('private-secret', repr(backend.resource_diagnostics))
        self.assertEqual(len(backend.resource_diagnostics['mismatches']), 4)

    def test_optional_burst_absent_only_when_unavailable_at_both_levels(self):
        backend = self.fixture.executor
        value = self.capture_value()
        member = '0::' + value['State']['CgroupPath'] + '/container\n'
        with self.capture_files(value, member, suffix='/container',
                scope={'cpu.max.burst': None}, payload={'cpu.max.burst': None}):
            backend._capture_cgroup(value)
        self.assertEqual(backend.resource_diagnostics['payload']['cpu.max.burst'], 'missing')
        self.assertEqual(backend.resource_diagnostics['mismatches'], [])
        backend.close()
        with self.capture_files(value, member, suffix='/container', payload={'cpu.max.burst': None}):
            with self.assertRaisesRegex(LiveFailure, 'container_cgroup_limits_unverified'):
                backend._capture_cgroup(value)

    def test_zero_payload_burst_is_not_weakened_by_scope_diagnostics(self):
        backend = self.fixture.executor
        value = self.capture_value()
        member = '0::' + value['State']['CgroupPath'] + '/container\n'
        for scope_value in (None, 'private-malformed', '1000'):
            with self.subTest(scope_value=scope_value), self.capture_files(value, member,
                    suffix='/container', scope={'cpu.max.burst': scope_value}):
                backend._capture_cgroup(value)
            self.assertEqual(backend.resource_diagnostics['payload']['cpu.max.burst'], '0')
            self.assertEqual(backend.resource_diagnostics['mismatches'], [])
            backend.close()

    def test_observed_subgroup_only_no_search_or_symlink_fallback(self):
        backend = self.fixture.executor
        value = self.capture_value()
        member = '0::' + value['State']['CgroupPath'] + '/missing\n'
        with self.capture_files(value, member, suffix='/good'):
            with self.assertRaises(FileNotFoundError):
                backend._capture_cgroup(value)
        self.assertIsNone(backend.cgroup_fd)
        with self.assertRaises(OSError):
            os.fstat(self.opened_fd)
        (self.path / 'missing').symlink_to(self.path / 'good', target_is_directory=True)
        with self.capture_files(value, member, suffix='/good'):
            with self.assertRaises(OSError):
                backend._capture_cgroup(value)
        self.assertIsNone(backend.cgroup_fd)

    def test_changed_membership_during_limit_read_blocks_capture(self):
        backend = self.fixture.executor
        value = self.capture_value()
        before = '0::' + value['State']['CgroupPath'] + '/container\n'
        after = '0::' + value['State']['CgroupPath'] + '\n'
        with self.capture_files(value, [before, after], suffix='/container'):
            with self.assertRaisesRegex(LiveFailure, 'container_cgroup_pid_mismatch'):
                backend._capture_cgroup(value)
        self.assertIsNone(backend.cgroup_fd)
        with self.assertRaises(OSError):
            os.fstat(self.opened_fd)

    def test_scope_must_be_exact_full_id_leaf_not_any_ancestor(self):
        backend = self.fixture.executor
        for suffix in ('/child', '-lookalike'):
            value = self.capture_value('/synthetic/libpod-' + self.value['Id'] + '.scope' + suffix)
            with self.subTest(suffix=suffix), patch.object(os, 'open') as opened:
                with self.assertRaisesRegex(LiveFailure, 'container_cgroup_unbound'):
                    backend._capture_cgroup(value)
                opened.assert_not_called()

    def test_exact_profile_and_python_entrypoint_only_command_changes(self):
        executor = self.fixture.executor
        binding = self.fixture.binding
        original = executor.create_arguments(binding)
        args = gate.acceptance_arguments(executor, binding)
        index = original.index('sha256:' + executor.target.image_id)
        expected = original[:index]
        self.assertEqual(args[:index], expected)
        self.assertEqual(args[index + 1:], ['-I', '-S', '-B', '-c', gate.GATE_PROGRAM])
        self.assertNotIn(binding.remote_url, repr(args))
        self.assertNotIn('--registration-bearer-stdin', args)
        self.assertNotIn('synthetic-no-key', repr(args))
        self.assertIn(gate.CODEX_HOME_SETUP, gate.GATE_PROGRAM)
        self.assertLess(gate.GATE_PROGRAM.index('os.read(0, 1)'),
                        gate.GATE_PROGRAM.index(gate.CODEX_HOME_SETUP))

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
