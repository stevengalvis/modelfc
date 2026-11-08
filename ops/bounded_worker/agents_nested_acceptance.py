"""Explicit no-key acceptance entry. Importing does not launch anything.

The fixed child waits for GO before invoking Codex's nested sandbox. The parent
captures live container identity/cgroup first. This prevents a short-probe race;
it does not diagnose any earlier VPS failure or reconstruct missing evidence.
"""
import os
import re
import select
import selectors
import subprocess
import time

from .agents_live_pilot import LIMIT, LiveFailure, PodmanExecutor, _bounded_call
from .agents_session_controller import ExecutorBinding

PYTHON = '/usr/local/bin/python3'
CODEX = '/opt/agents/bin/codex'
READY = b'NESTED_READY\n'
DONE = b'NESTED_OK\n'
SENTINEL = ('from pathlib import Path; p=Path("nested_sentinel.txt"); '
            'p.write_text("nested-ok\\n"); assert p.read_text()=="nested-ok\\n"; '
            'print("NESTED_OK", flush=True)')
SANDBOX_ARGV = [CODEX, 'sandbox', '--permission-profile', ':workspace', '--cd', '/workspace',
                '--', PYTHON, '-I', '-S', '-B', '-c', SENTINEL]
# Trusted fixed program. No argv, environment, key, shell or task text is interpreted.
GATE_PROGRAM = f'''import os, select
print("NESTED_READY", flush=True)
if not select.select([0], [], [], 20)[0]:
    raise SystemExit(74)
if os.read(0, 1) != b"G":
    raise SystemExit(75)
os.execve({CODEX!r}, {SANDBOX_ARGV!r}, dict(os.environ))
'''
_SAFE_FAILURES = frozenset((
    'acceptance_pipe_timeout', 'acceptance_output_limit', 'acceptance_ready_eof',
    'acceptance_ready_rejected', 'acceptance_go_timeout', 'acceptance_go_uncertain',
    'acceptance_sentinel_failed', 'acceptance_not_running', 'acceptance_capture_unverified',
    'acceptance_changed_before_go', 'container_cgroup_unverified', 'container_cgroup_unbound',
    'container_cgroup_pid_mismatch', 'container_cgroup_limits_unverified',
    'container_create_uncertain', 'container_name_not_available', 'prepared_runtime_unverified',
    'rootless_user_required', 'runtime_path_rejected',
))


def state_summary(value):
    """Safe enums only; never return actual PID, paths, errors or command text."""
    state = value.get('State') if isinstance(value, dict) else None
    state = state if isinstance(state, dict) else {}
    status, running, pid = state.get('Status'), state.get('Running'), state.get('Pid')
    path = state.get('CgroupPath')
    return {
        'status': status if status in ('configured', 'created', 'running', 'paused',
                                      'exited', 'stopped') else 'unknown',
        'running': 'yes' if running is True else 'no' if running is False else 'unknown',
        'pid': 'positive' if type(pid) is int and pid > 0 else
               'zero' if type(pid) is int and pid == 0 else 'unknown',
        'cgroup_path': 'missing' if 'CgroupPath' not in state else 'empty' if path == '' else
                       'present' if isinstance(path, str) and path.startswith('/') else 'invalid',
    }


class _Pipe:
    """Bounded raw pipe reader. Stderr is counted/drained but never retained."""
    def __init__(self, process):
        self.process, self.total, self.buffer = process, 0, bytearray()
        self.ready = selectors.DefaultSelector()
        for name in ('stdin', 'stdout', 'stderr'):
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            if name != 'stdin':
                self.ready.register(stream, selectors.EVENT_READ, name)

    def pump(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise LiveFailure('acceptance_pipe_timeout')
        found = self.ready.select(left)
        if not found:
            raise LiveFailure('acceptance_pipe_timeout')
        for key, _ in found:
            try:
                chunk = os.read(key.fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                self.ready.unregister(key.fileobj)
                continue
            self.total += len(chunk)
            if self.total > LIMIT:
                raise LiveFailure('acceptance_output_limit')
            if key.data == 'stdout':
                self.buffer.extend(chunk)

    def wait_ready(self, deadline):
        while b'\n' not in self.buffer:
            if not any(key.data == 'stdout' for key in self.ready.get_map().values()):
                raise LiveFailure('acceptance_ready_eof')
            self.pump(deadline)
        line, _, rest = self.buffer.partition(b'\n')
        if line + b'\n' != READY or rest:
            raise LiveFailure('acceptance_ready_rejected')
        self.buffer.clear()

    def release(self, deadline):
        fd = self.process.stdin.fileno()
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([], [fd], [], left)[1]:
            raise LiveFailure('acceptance_go_timeout')
        if os.write(fd, b'G') != 1:
            raise LiveFailure('acceptance_go_uncertain')
        self.process.stdin.close()

    def finish(self, deadline):
        while self.ready.get_map():
            self.pump(deadline)
        if (self.process.wait(timeout=max(.001, deadline - time.monotonic())) != 0
                or bytes(self.buffer) != DONE):
            raise LiveFailure('acceptance_sentinel_failed')

    def close(self):
        self.ready.close()
        for name in ('stdin', 'stdout', 'stderr'):
            getattr(self.process, name).close()


def acceptance_arguments(executor, binding):
    """Reuse exact live container options; change only fixed entrypoint/argv."""
    args = executor.create_arguments(binding)
    image = 'sha256:' + executor.target.image_id
    index = args.index(image)
    profile = args[:index]
    profile[profile.index('--entrypoint=' + CODEX)] = '--entrypoint=' + PYTHON
    return [*profile, image, '-I', '-S', '-B', '-c', GATE_PROGRAM]


def run_nested_acceptance(target, attempt_id, *, command=None, popen=None):
    """EXPLICIT OS RUN, not called by tests without injected subprocess boundaries.

Requires prior-attempt current-state reconciliation and approved no-key setup.
No API, SDK stream, registration URL, real key or credential source is used.
Retain this private report and the stopped container; no automatic removal.
"""
    target.validate()
    if not isinstance(attempt_id, str) or not re.fullmatch(r'[a-f0-9]{64}', attempt_id):
        raise ValueError('invalid_acceptance_attempt')
    # The reusable backend validates a string but does not transmit it here.
    executor = PodmanExecutor(target, 'synthetic-no-key', command=command, popen=popen)
    binding = ExecutorBinding(attempt_id, 'acceptance_session', 'acceptance_environment',
                              'https://api.openai.com/never-called')
    report = {'attempt_id': attempt_id, 'container_id': None, 'stage': 'preflight',
              'reason': None, 'status': 'blocked', 'nested': 'not_run', 'cleanup': 'not_started',
              'startup_state': state_summary(None), 'go_sent': False}
    pipe = None
    deadline = time.monotonic() + 180

    def perform():
        nonlocal pipe
        executor.verify_prepared()
        executor.binding = binding
        code, _ = executor._command(['container', 'exists', 'agents-' + attempt_id])
        if code != 1:
            raise LiveFailure('container_name_not_available')
        report['stage'] = 'create'
        executor._create_attempted = True
        code, raw = executor._command(acceptance_arguments(executor, binding))
        if code or not re.fullmatch(rb'[a-f0-9]{64}\n?', raw):
            raise LiveFailure('container_create_uncertain')
        executor.container_id = raw.decode('ascii').strip()
        value = executor._inspect()
        executor._created_policy(value)
        report['stage'] = 'ready'
        executor._start_attempted = True
        executor.process = executor._popen(
            ['/usr/bin/podman', '--remote=false', 'start', '--attach', '--interactive',
             executor.container_id], env=executor.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, close_fds=True)
        pipe = _Pipe(executor.process)
        ready_deadline = min(deadline, time.monotonic() + 15)
        pipe.wait_ready(ready_deadline)
        report['stage'] = 'capture'
        value = executor._inspect()
        report['startup_state'] = state_summary(value)
        if (value['State'].get('Running') is not True
                or value['State'].get('Status') != 'running'
                or type(value['State'].get('Pid')) is not int or value['State']['Pid'] <= 0):
            raise LiveFailure('acceptance_not_running')
        executor._capture_cgroup(value)
        if executor.cgroup_fd is None:
            raise LiveFailure('acceptance_capture_unverified')
        # Recheck identity and liveness immediately before releasing the fixed child.
        current = executor._inspect()
        if (current['State'].get('Running') is not True
                or type(current['State'].get('Pid')) is not int
                or current['State'].get('Pid') != value['State']['Pid']
                or current['State'].get('CgroupPath') != value['State'].get('CgroupPath')):
            raise LiveFailure('acceptance_changed_before_go')
        report['stage'] = 'sentinel'
        pipe.release(ready_deadline)
        report['go_sent'] = True
        report['nested'] = 'running'
        pipe.finish(deadline)
        report['nested'] = 'passed'

    try:
        _bounded_call(perform, 180)
    except BaseException as error:
        # Stage/state summary distinguish failures without leaking exception/log text.
        reason = str(error) if isinstance(error, LiveFailure) else ''
        report['reason'] = reason if reason in _SAFE_FAILURES else 'acceptance_failed'
    finally:
        if executor._create_attempted:
            report['cleanup'] = 'unverified'
            try:
                _bounded_call(lambda: executor.stop(binding), 15)
            except BaseException:
                report['reason'] = report['reason'] or 'stop_uncertain'
            try:
                if _bounded_call(lambda: executor.verify_empty(binding), 10) is True:
                    report['cleanup'] = 'verified'
            except BaseException:
                pass
        report['container_id'] = executor.container_id  # Cleanup can resolve an uncertain create.
        if pipe is not None:
            pipe.close()
        executor.close()
    if report['nested'] == 'passed' and report['cleanup'] == 'verified' and report['reason'] is None:
        report['status'] = 'passed'
    return report
