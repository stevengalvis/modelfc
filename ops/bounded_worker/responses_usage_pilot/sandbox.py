"""Explicit existing-runtime test adapter; no shell, key, network or host mounts."""
import os
from pathlib import Path
import re
import select
import subprocess
import time

from ops.bounded_worker.agents_live_pilot import (
    PODMAN_PREFIX, PodmanExecutor, LiveFailure, _bounded_call)
from ops.bounded_worker.agents_nested_acceptance import MOUNT_CHECK, _Pipe
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from pilot import ATTEMPT, MAX_SOURCE_BYTES
from safe_task import validate_source
from test_controller_diagnostics import (
    container_summary, parse_result, pipe_summary, test_failure, ResultRejected)


INPUT_MAGIC = b'USG1'
WORKLOAD_SECONDS = 15
COMPLETION_SECONDS = 30
CONTROLLER_SECONDS = 45


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


def gate_program():
    # This file is in the verified immutable bundle, never a model-selected path.
    harness = Path(__file__).with_name('safe_task.py').read_text()
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
if header[:4] != {INPUT_MAGIC!r} or not 0 < source_size <= {MAX_SOURCE_BYTES}:
    raise SystemExit(75)
source = _read_exact(source_size)
{harness}
try:
    failed = run_tests(source.decode("utf-8"))
    print(json.dumps({{"failed": failed, "passed": not failed}}, sort_keys=True), flush=True)
except BaseException:
    raise SystemExit(76)
'''


class TestExecutor(PodmanExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._test_pre_stop = None
        self._test_stop_inspection = False
        self._test_pre_stop_capture_error = None

    def stop(self, binding):
        self._test_stop_inspection = True
        try:
            return super().stop(binding)
        finally:
            self._test_stop_inspection = False

    def _inspect(self):
        value = super()._inspect()
        if self._test_stop_inspection and self._test_pre_stop is None:
            # stop() sets the flag before its existing identity-checked inspect,
            # then issues stop. No extra inspection or execution time is added.
            try:
                self._test_pre_stop = container_summary(value.get('State'))
            except Exception:
                # Optional diagnostics must not prevent the actual stop command.
                # BaseException deadlines still propagate to preserve its bound.
                self._test_pre_stop_capture_error = {
                    'category': 'operation_failed', 'os_errno': None,
                    'code': 'diagnostic_capture_failed'}
        return value

    def create_arguments(self, binding):
        args = super().create_arguments(binding)
        network = '--network=slirp4netns:allow_host_loopback=false'
        if args.count(network) != 1:
            raise LiveFailure('unexpected_base_profile')
        args[args.index(network)] = '--network=none'
        image_index = args.index('sha256:' + self.target.image_id)
        return args[:image_index + 1] + ['-I', '-S', '-B', '-c', gate_program()]

    def _created_policy(self, value):
        super()._created_policy(value)
        if value['HostConfig'].get('NetworkMode') != 'none':
            raise LiveFailure('test_network_not_disabled')


def test_candidate(target, source, number, *, command=None, popen=None, identity=None):
    """Launch only the fixed synthetic harness in the retained rootless profile."""
    if type(number) is not int or not 1 <= number <= 3:
        raise ValueError('call_number')
    if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise ValueError('source_limit')
    # A parse/grammar check on the host does not execute source. Recheck inside.
    validate_source(source)
    import hashlib
    legacy = {hashlib.sha256((ATTEMPT + ':' + str(n)).encode()).hexdigest() for n in (1, 2, 3)}
    if identity is not None and (type(identity) is not str
            or not re.fullmatch(r'[a-f0-9]{64}', identity) or identity in legacy):
        raise ValueError('fresh_test_identity_required')
    attempt = identity or hashlib.sha256((ATTEMPT + ':' + str(number)).encode()).hexdigest()
    binding = ExecutorBinding(attempt, 'responses_sandbox_only' if identity else 'responses_usage', 'no_environment_key',
                              'https://api.openai.com/never-called')
    executor = TestExecutor(target, 'synthetic-no-key', command=command, popen=popen)
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
        code, _ = executor._command(['container', 'exists', 'agents-' + attempt])
        if code != 1:
            raise LiveFailure('container_name_not_available')
        result['stage'] = 'create'
        diagnostic['step'] = 'container_create'
        executor._create_attempted = True
        code, raw = executor._command(executor.create_arguments(binding))
        if code or not re.fullmatch(rb'[a-f0-9]{64}\n?', raw):
            raise LiveFailure('container_create_uncertain')
        executor.container_id = raw.decode('ascii').strip()
        diagnostic['step'] = 'created_policy'
        executor._created_policy(executor._inspect())
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
        diagnostic['input_protocol'] = 'usg1_length_prefixed'
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
