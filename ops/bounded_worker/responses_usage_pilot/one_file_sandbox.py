"""Single required_text task sandbox. Import is inert; execution is opt-in.

Controller mechanics mirror sandbox.py without altering historical source pins.
No provider or credential access; tests inject fake runtime operations.
"""
import os
import json
from pathlib import Path
import re
import select
import subprocess
import time

from ops.bounded_worker.agents_live_pilot import (
    PODMAN_PREFIX, PodmanExecutor, LiveFailure, _bounded_call)
from ops.bounded_worker.agents_nested_acceptance import MOUNT_CHECK, _Pipe
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from .canonical_admission import canonical, digest
from .one_file_adapter import (
    TEST_NAMES, prepare, bind_first_reservation, validate_candidate, candidate_function)
from ops.bounded_worker.agents_live_pilot import LIMIT, _HEX


class ResultRejected(ValueError):
    pass


def parse_result(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ResultRejected('duplicate_keys')
            value[key] = item
        return value
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        raise ResultRejected('invalid_frame')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ResultRejected('constant')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ResultRejected('invalid_frame') from exc
    if (type(value) is not dict or set(value) != {'failed', 'passed'}
            or type(value['failed']) is not list
            or any(type(name) is not str or name not in TEST_NAMES for name in value['failed'])
            or value['failed'] != sorted(set(value['failed']))
            or type(value['passed']) is not bool or value['passed'] != (not value['failed'])):
        raise ResultRejected('invalid_frame')
    return value


# Fixed controller reasons only. Unknown errors must never become report text.
FAILURE_CODES = frozenset((
    'container_manager_rejected', 'container_environment_rejected',
    'container_policy_rejected', 'container_tmpfs_rejected',
    'task_program_identity_mismatch', 'test_network_not_disabled',
    'container_not_bound', 'container_inspection_failed', 'container_binding_rejected',
    'container_name_not_available', 'container_create_uncertain',
    'container_identity_not_persisted', 'unexpected_base_profile',
    'test_not_running', 'test_isolation_unverified', 'test_changed_before_go',
    'test_input_timeout', 'test_input_no_progress', 'test_result_after_workload_deadline',
    'test_workload_timeout', 'test_completion_timeout', 'test_attach_exit_nonzero',
    'test_stderr_nonempty', 'test_stdout_limit',
    'acceptance_pipe_timeout', 'acceptance_output_limit',
    'acceptance_ready_eof', 'acceptance_ready_rejected',
))


def test_failure(exc):
    # Exact trusted class and a single plain allowlisted token, never str(exc),
    # raw args, candidate output, program text, environment or runtime paths.
    code = 'unclassified'
    if type(exc) is LiveFailure:
        args = exc.args
        if len(args) == 1 and type(args[0]) is str and args[0] in FAILURE_CODES:
            code = args[0]
    return {'category': 'deadline' if isinstance(exc, TimeoutError) else 'operation_failed',
            'code': code}


def container_summary(value):
    value = value if type(value) is dict else {}
    return {name: value.get(name) if type(value.get(name)) in (bool, int) else None
            for name in ('Running', 'ExitCode', 'OOMKilled')}


def pipe_summary(pipe, process):
    registered = {key.data for key in pipe.ready.get_map().values()}
    return {'stdout_eof': 'stdout' not in registered, 'stderr_eof': 'stderr' not in registered,
            'stdout_bytes': len(pipe.buffer), 'stderr_bytes': len(pipe.stderr),
            'stdin_closed': process.stdin.closed, 'attach_returncode': process.poll()}


INPUT_MAGIC = b'OFT1'
MAX_SOURCE_BYTES = 8192
WORKLOAD_SECONDS = 15
COMPLETION_SECONDS = 30
CONTROLLER_SECONDS = 45
PROFILE_HASH = digest(canonical({
    'adapter': 'required-text-v1', 'runtime': 'rootless-podman-4.9.3-systemd-v2',
    'network': 'none', 'read_only': True, 'capabilities': [], 'user': '65534:65534',
    'memory': 1073741824, 'pids': 64, 'cpu_quota': 100000, 'cpu_period': 100000,
    'workload_seconds': 15, 'completion_seconds': 30, 'controller_seconds': 45,
    'profile_base': 'agents_live_pilot.PodmanExecutor',
}).encode())


def runtime_identity(target):
    target.validate()
    return 'podman-4.9.3:' + digest(canonical([
        target.runtime_home, target.runtime_dir, target.acceptance_id]).encode())


def _settings(manifest, target):
    if (target.image_id != manifest.image_id or manifest.sandbox_seconds != CONTROLLER_SECONDS
            or manifest.profile_hash != PROFILE_HASH or manifest.runtime_id != runtime_identity(target)):
        raise ValueError('sandbox_settings_mismatch')


def verify_readiness(manifest, target, *, command=None, followup=False):
    """Read-only exact runtime/image/name checks before provider access; no reservation."""
    _settings(manifest, target)
    name = 'responses-task-' + manifest.identity + '-1'
    if followup:
        from .followup_allowance import sandbox_name
        name = sandbox_name(manifest)
    executor = TestExecutor(target, name, '', command=command)
    def inspect():
        executor.verify_prepared()
        code, raw = executor._command([
            'image', 'inspect', '--format', '{{.Id}}', 'sha256:' + manifest.image_id])
        expected = manifest.image_id.encode('ascii')
        if type(code) is not int or code != 0 or type(raw) is not bytes or raw not in (expected, expected + b'\n'):
            raise LiveFailure('image_identity_unverified')
        code, raw = executor._command(['container', 'exists', name])
        if type(code) is not int or code != 1 or type(raw) is not bytes or raw != b'':
            raise LiveFailure('container_name_not_available')
        return True
    try:
        if _bounded_call(inspect, 25) is not True:
            raise LiveFailure('readiness_unverified')
        return True
    finally:
        executor.close()



class CompletionBudget:
    """One workload budget, then at most one bounded completion allowance.

    A provisional result changes only the waiting deadline, never acceptance.
    The absolute controller deadline includes preflight and cannot be renewed.
    """
    def __init__(self, started, attached):
        self.overall_deadline = started + CONTROLLER_SECONDS
        self.workload_deadline = min(attached + WORKLOAD_SECONDS, self.overall_deadline)
        self.completion_deadline = None

    @property
    def deadline(self):
        return self.workload_deadline if self.completion_deadline is None else self.completion_deadline

    def observe(self, raw, now):
        if self.completion_deadline is not None or not raw.endswith(b'\n'):
            return False
        try:
            parse_result(bytes(raw))
        except ResultRejected:
            return False
        if now >= self.workload_deadline:
            raise LiveFailure('test_result_after_workload_deadline')
        self.completion_deadline = min(now + COMPLETION_SECONDS, self.overall_deadline)
        return True

    def remaining(self, now):
        left = self.deadline - now
        if left <= 0:
            raise LiveFailure('test_completion_timeout' if self.completion_deadline is not None
                              else 'test_workload_timeout')
        return left


def source_frame(source):
    """One fixed bounded frame; source is data, not a host executable."""
    if type(source) is not str:
        raise ValueError('source_limit')
    raw = source.encode('utf-8')
    if not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise ValueError('source_limit')
    return INPUT_MAGIC + len(raw).to_bytes(4, 'big') + raw


def close_input_after_result(pipe, process, diagnostic):
    """Release input after a complete provisional frame, never accept it early.

    Both output EOFs, normal attach exit, complete-output validation and cleanup
    remain mandatory. Later/trailing stdout or stderr must still be rejected.
    """
    if process.stdin.closed or not pipe.buffer.endswith(b'\n'):
        return
    try:
        parse_result(bytes(pipe.buffer))
    except ResultRejected:
        return
    process.stdin.close()
    diagnostic['input_close_reason'] = 'result_frame_observed'


def gate_program(manifest, source, tests, harness):
    prepare(manifest, source, tests, harness)
    # Only pinned immutable test/harness bytes are embedded, never candidate code.
    marker = 'from .trusted_tests import CASES\n'
    text = harness.decode('utf-8')
    if text.count(marker) != 1:
        raise ValueError('harness_import_shape')
    text = text.replace(marker, '')
    return f'''import os, select, sys, json, time
{MOUNT_CHECK}
mount_status = _mount_check()
print("NESTED_READY:" + mount_status, flush=True)
if mount_status != "verified":
    raise SystemExit(74)
input_deadline = time.monotonic() + 20
def _read_exact(size):
    data = bytearray()
    while len(data) < size:
        left = input_deadline - time.monotonic()
        if left <= 0 or not select.select([0], [], [], left)[0]:
            raise SystemExit(74)
        chunk = os.read(0, size - len(data))
        if not chunk:
            raise SystemExit(75)
        data.extend(chunk)
    return bytes(data)
header = _read_exact(8)
source_size = int.from_bytes(header[4:], "big")
if header[:4] != {INPUT_MAGIC!r} or not 0 < source_size <= {manifest.max_source_bytes}:
    raise SystemExit(75)
candidate = _read_exact(source_size)
{tests.decode('utf-8')}
{text}
try:
    results = run_candidate(candidate.decode("utf-8"))
    failed = sorted(name for name, passed in results if not passed)
    print(json.dumps({{"failed": failed, "passed": not failed}}, sort_keys=True), flush=True)
except BaseException:
    raise SystemExit(76)
'''


class TestExecutor(PodmanExecutor):
    def __init__(self, target, sandbox_name, program, *, command=None, popen=None):
        super().__init__(target, "task-no-key", command=command, popen=popen)
        self.sandbox_name, self.program = sandbox_name, program
        self._test_pre_stop = None
        self._test_stop_inspection = False
        self._test_pre_stop_capture_error = None

    def stop(self, binding):
        self._test_stop_inspection = True
        try:
            return super().stop(binding)
        finally:
            self._test_stop_inspection = False

    def create_arguments(self, binding):
        args = super().create_arguments(binding)
        args[args.index('--name') + 1] = self.sandbox_name
        network = '--network=slirp4netns:allow_host_loopback=false'
        if args.count(network) != 1:
            raise LiveFailure('unexpected_base_profile')
        args[args.index(network)] = '--network=none'
        image_index = args.index('sha256:' + self.target.image_id)
        return args[:image_index + 1] + ['-I', '-S', '-B', '-c', self.program]

    def _inspect(self):
        if self.binding is None:
            raise LiveFailure('container_not_bound')
        code, raw = self._command(['inspect', '--type=container',
                                   self.container_id or self.sandbox_name])
        if type(code) is not int or code != 0 or type(raw) is not bytes or len(raw) > LIMIT:
            raise LiveFailure('container_inspection_failed')
        try:
            values = json.loads(raw)
            value = values[0]
            labels = value['Config']['Labels']
            if (len(values) != 1 or not _HEX.fullmatch(value['Id'])
                    or (self.container_id is not None and value['Id'] != self.container_id)
                    or value['Name'] != self.sandbox_name
                    or value['Image'].removeprefix('sha256:') != self.target.image_id
                    or any(labels.get(key) != item for key, item in self._labels().items())):
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise LiveFailure('container_binding_rejected') from None
        self.container_id = value['Id']
        if self._test_stop_inspection and self._test_pre_stop is None:
            self._test_pre_stop = container_summary(value.get('State'))
        return value

    def _created_policy(self, value):
        super()._created_policy(value)
        # Podman v4.9.3 InspectContainerConfig.Entrypoint is a string;
        # libpod/container_inspect.go joins the OCI entrypoint array with spaces.
        # Require the exact pinned inspect representation, not Docker's list.
        if (value['Config'].get('Cmd') != ['-I', '-S', '-B', '-c', self.program]
                or type(value['Config'].get('Entrypoint')) is not str
                or value['Config']['Entrypoint'] != '/usr/local/bin/python3'):
            raise LiveFailure('task_program_identity_mismatch')
        if value['HostConfig'].get('NetworkMode') != 'none':
            raise LiveFailure('test_network_not_disabled')


def test_candidate(manifest, source, tests, harness, candidate, reservation, target,
                   *, command=None, popen=None, identity_recorder=None):
    """One reserved call; source only enters after exact running/cgroup checks.

    identity_recorder must durably bind the actual container ID before candidate
    execution. Missing recorder blocks before runtime access; no candidate result
    is accepted until both EOFs, normal exit and independent cleanup are verified.
    """
    prepared = prepare(manifest, source, tests, harness)
    bind_first_reservation(manifest, prepared, reservation, source, tests, harness)
    validate_candidate(manifest, source, tests, harness, candidate)
    if identity_recorder is None or not callable(identity_recorder):
        raise ValueError('durable_identity_recorder_required')
    _settings(manifest, target)
    source = candidate_function(candidate).decode('utf-8')
    number = reservation['number']
    binding = ExecutorBinding(manifest.identity, 'responses_one_file', 'no_environment_key',
                              'https://api.openai.com/never-called')
    executor = TestExecutor(target, reservation['sandbox_name'],
                            gate_program(manifest, prepared.package[0][1], tests, harness),
                            command=command, popen=popen)
    pipe = None
    result = {'status': 'blocked', 'cleanup_verified': False, 'failed': [],
              'call': number, 'stage': 'preflight', 'container_id': None,
              'diagnostic': {'schema_version': 1, 'step': 'preflight',
                             'input_bytes_written': 0}}
    diagnostic = result['diagnostic']
    started = time.monotonic()

    def perform():
        nonlocal pipe
        executor.verify_prepared()
        executor.binding = binding
        diagnostic['step'] = 'container_exists'
        code, _ = executor._command(['container', 'exists', reservation['sandbox_name']])
        if type(code) is not int or code != 1 or type(_) is not bytes or _ != b'':
            raise LiveFailure('container_name_not_available')
        result['stage'] = 'create'
        diagnostic['step'] = 'container_create'
        executor._create_attempted = True
        code, raw = executor._command(executor.create_arguments(binding))
        if type(code) is not int or code != 0 or type(raw) is not bytes or not re.fullmatch(rb'[a-f0-9]{64}\n?', raw):
            raise LiveFailure('container_create_uncertain')
        executor.container_id = raw.decode('ascii').strip()
        diagnostic['step'] = 'created_policy'
        executor._created_policy(executor._inspect())
        if identity_recorder(executor.container_id) is not True:
            raise LiveFailure('container_identity_not_persisted')
        result['stage'] = 'ready'
        diagnostic['step'] = 'attach_start'
        executor._start_attempted = True
        executor.process = executor._popen(
            [*PODMAN_PREFIX, 'start', '--attach', '--interactive', executor.container_id],
            env=executor.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, close_fds=True)
        pipe = _Pipe(executor.process)
        budget = CompletionBudget(started, time.monotonic())
        deadline = budget.workload_deadline
        diagnostic.update(workload_allowance_ms=WORKLOAD_SECONDS * 1000,
                          completion_allowance_ms=COMPLETION_SECONDS * 1000,
                          controller_allowance_ms=CONTROLLER_SECONDS * 1000,
                          completion_started=False)
        diagnostic['step'] = 'ready_wait'
        mount_status = pipe.wait_ready(deadline)
        result['stage'] = 'capture'
        diagnostic['step'] = 'cgroup_capture'
        value = executor._inspect()
        if value['State'].get('Running') is not True:
            raise LiveFailure('test_not_running')
        executor._capture_cgroup(value)
        if executor.cgroup_fd is None or mount_status != 'verified':
            raise LiveFailure('test_isolation_unverified')
        diagnostic['step'] = 'identity_recheck'
        current = executor._inspect()
        if (current['State'].get('Running') is not True
                or current['State'].get('Pid') != value['State'].get('Pid')
                or current['State'].get('CgroupPath') != value['State'].get('CgroupPath')):
            raise LiveFailure('test_changed_before_go')
        result['stage'] = 'tests'
        diagnostic['step'] = 'input_write'
        diagnostic['input_budget_remaining_ms'] = max(0, int((deadline - time.monotonic()) * 1000))
        framed = source_frame(source)
        diagnostic['input_protocol'] = 'oft1_length_prefixed'
        diagnostic['input_source_bytes'] = len(source.encode('utf-8'))
        diagnostic['input_close_reason'] = 'not_closed'
        data = memoryview(framed)
        fd = executor.process.stdin.fileno()
        while data:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [fd], [], left)[1]:
                raise LiveFailure('test_input_timeout')
            try:
                written = os.write(fd, data)
                if written <= 0:
                    raise LiveFailure('test_input_no_progress')
                diagnostic['input_bytes_written'] += written
                data = data[written:]
            except BlockingIOError:
                continue
        diagnostic['step'] = 'output_drain'
        while True:
            now = time.monotonic()
            if budget.observe(pipe.buffer, now):
                diagnostic.update(completion_started=True,
                    result_observed_elapsed_ms=max(0, int((now - started) * 1000)),
                    completion_budget_ms=max(0, int((budget.deadline - now) * 1000)))
            close_input_after_result(pipe, executor.process, diagnostic)
            if not pipe.ready.get_map():
                break
            pipe.pump(budget.deadline)
        if not executor.process.stdin.closed:
            executor.process.stdin.close()
            diagnostic['input_close_reason'] = 'streams_ended'
        diagnostic['step'] = 'attach_wait'
        code = executor.process.wait(timeout=budget.remaining(time.monotonic()))
        diagnostic['step'] = 'output_validate'
        if code != 0:
            raise LiveFailure('test_attach_exit_nonzero')
        if pipe.stderr:
            raise LiveFailure('test_stderr_nonempty')
        if len(pipe.buffer) > 4096:
            raise LiveFailure('test_stdout_limit')
        diagnostic['step'] = 'result_parse'
        observation = parse_result(bytes(pipe.buffer))
        budget.remaining(time.monotonic())
        result.update(status='passed' if observation['passed'] else 'tests_failed',
                      failed=observation['failed'], stage='finished')
        diagnostic['step'] = 'finished'

    try:
        _bounded_call(perform, CONTROLLER_SECONDS)
    except BaseException as exc:
        result['status'] = 'blocked'
        diagnostic['failure'] = test_failure(exc)
    finally:
        diagnostic['elapsed_before_stop_ms'] = max(0, int((time.monotonic() - started) * 1000))
        if pipe is not None:
            try:
                diagnostic['streams_before_stop'] = pipe_summary(pipe, executor.process)
            except BaseException as exc:
                diagnostic['observation_failure'] = test_failure(exc)
                result['status'] = 'blocked'
        if executor._create_attempted:
            try:
                _bounded_call(lambda: executor.stop(binding), 15)
            except BaseException as exc:
                diagnostic['stop_failure'] = test_failure(exc)
            try:
                result['cleanup_verified'] = _bounded_call(
                    lambda: executor.verify_empty(binding), 10) is True
            except BaseException as exc:
                diagnostic['cleanup_failure'] = test_failure(exc)
        try:
            pre_stop = getattr(executor, '_test_pre_stop', None)
            if type(pre_stop) is dict:
                diagnostic['container_before_stop'] = pre_stop
            else:
                existing = executor.cleanup_diagnostics
                before = existing.get('before_stop') if type(existing) is dict else None
                diagnostic['container_before_stop'] = container_summary(before)
            capture_error = getattr(executor, '_test_pre_stop_capture_error', None)
            if type(capture_error) is dict:
                diagnostic['stop_observation_failure'] = capture_error
        except BaseException as exc:
            diagnostic['container_observation_failure'] = test_failure(exc)
            result['status'] = 'blocked'
        result['container_id'] = executor.container_id
        result['sandbox_name'] = reservation['sandbox_name']
        result['image_id'] = target.image_id
        result['cleanup_evidence'] = executor.cleanup_diagnostics
        if pipe is not None:
            try:
                pipe.close()
            except BaseException as exc:
                diagnostic['pipe_close_failure'] = test_failure(exc)
                result['status'] = 'blocked'
        try:
            executor.close()
        except BaseException as exc:
            diagnostic['executor_close_failure'] = test_failure(exc)
            result['status'] = 'blocked'
    if not result['cleanup_verified']:
        result['status'] = 'blocked'
    return result
