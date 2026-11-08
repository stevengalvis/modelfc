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

from .agents_live_pilot import LIMIT, PODMAN_PREFIX, LiveFailure, PodmanExecutor, _bounded_call, _error_evidence
from .agents_session_controller import ExecutorBinding

PYTHON = '/usr/local/bin/python3'
CODEX = '/opt/agents/bin/codex'
READY = b'NESTED_READY:verified\n'
MOUNT_STATES = frozenset(('verified', 'unreadable', 'oversized', 'invalid',
                          'missing', 'writable', 'unsupported'))
DONE = b'NESTED_OK\n'
SENTINEL = ('from pathlib import Path; p=Path("nested_sentinel.txt"); '
            'p.write_text("nested-ok\\n"); assert p.read_text()=="nested-ok\\n"; '
            'print("NESTED_OK", flush=True)')
SANDBOX_ARGV = [CODEX, 'sandbox', '--permission-profile', ':workspace', '--cd', '/workspace',
                '--', PYTHON, '-I', '-S', '-B', '-c', SENTINEL]
# Fixed self-read avoids a host ptrace permission requirement for the mapped UID.
# Only a closed enum crosses the pipe; raw mount paths/options never leave PID1.
MOUNT_CHECK = '''def _mount_check():
    try:
        with open("/proc/self/mountinfo", "rb") as source:
            raw = source.read(65537)
    except OSError:
        return "unreadable"
    if len(raw) > 65536:
        return "oversized"
    if not raw or not raw.endswith(b"\\n") or b"\\x00" in raw:
        return "invalid"
    expected = []
    for line in raw.splitlines():
        fields = line.split()
        if fields.count(b"-") != 1:
            return "invalid"
        split = fields.index(b"-")
        if (split < 6 or len(fields) != split + 4
                or not fields[0].isdigit() or not fields[1].isdigit()
                or not fields[3].startswith(b"/") or not fields[4].startswith(b"/")):
            return "invalid"
        filesystem = fields[split + 1]
        if fields[4] == b"/sys/fs/cgroup":
            expected.append(filesystem)
        if filesystem in (b"cgroup", b"cgroup2"):
            if filesystem != b"cgroup2":
                return "unsupported"
            options = fields[5].split(b",")
            if b"ro" not in options or b"rw" in options:
                return "writable"
    return "verified" if expected == [b"cgroup2"] else "missing"
'''
# Trusted fixed program. No argv, environment, key, shell or task text is interpreted.
# Even a failed mount check holds for capture/cleanup, but can never execute Codex.
GATE_PROGRAM = f'''import os, select
{MOUNT_CHECK}
mount_status = _mount_check()
print("NESTED_READY:" + mount_status, flush=True)
if not select.select([0], [], [], 20)[0]:
    raise SystemExit(74)
if os.read(0, 1) != b"G" or mount_status != "verified":
    raise SystemExit(75)
os.execve({CODEX!r}, {SANDBOX_ARGV!r}, dict(os.environ))
'''
_SAFE_FAILURES = frozenset((
    'acceptance_pipe_timeout', 'acceptance_output_limit', 'acceptance_ready_eof',
    'acceptance_ready_rejected', 'acceptance_go_timeout', 'acceptance_go_uncertain',
    'acceptance_sentinel_exit_nonzero', 'acceptance_sentinel_stdout_mismatch', 'acceptance_not_running', 'acceptance_capture_unverified',
    'acceptance_changed_before_go', 'container_cgroup_unverified', 'container_cgroup_unbound',
    'container_cgroup_pid_mismatch', 'container_cgroup_limits_unverified',
    'container_cgroup_events_invalid',
    'container_create_uncertain', 'container_name_not_available', 'prepared_runtime_unverified',
    'rootless_user_required', 'runtime_path_rejected',
    'container_manager_rejected', 'container_policy_rejected',
)) | frozenset('acceptance_mountinfo_' + state for state in MOUNT_STATES - {'verified'})


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


def cgroup_summary(value, container_id):
    """Diagnostic only: classify existing inspect data, never authorize capture.

    All returned values are fixed enums. No path component, ID, PID, arbitrary
    runtime name or host configuration value is copied into this summary.
    """
    value = value if isinstance(value, dict) else {}
    host = value.get('HostConfig')
    host = host if isinstance(host, dict) else {}
    state = value.get('State')
    state = state if isinstance(state, dict) else {}

    def known(item, choices):
        return item if isinstance(item, str) and item in choices else 'missing' if item is None else 'other'

    result = {
        'requested_mode': 'enabled', 'requested_namespace': 'private', 'requested_manager': 'systemd',
        'manager': known(host.get('CgroupManager'), ('systemd', 'cgroupfs')),
        'runtime': known(value.get('OCIRuntime'), ('crun', 'runc', 'runsc', 'youki', 'kata')),
        'inspect_cgroups': known(host.get('Cgroups'), ('default', 'disabled')),
        'inspect_namespace': known(host.get('CgroupMode'), ('private', 'host')),
        'component': 'unavailable', 'position': 'unknown',
        'other_full_id_present': 'unknown', 'short_id_present': 'unknown',
        'root_path': 'unknown', 'dot_components': 'unknown',
    }
    raw = state.get('CgroupPath')
    if (not isinstance(raw, str) or not raw.startswith('/')
            or not isinstance(container_id, str) or not re.fullmatch(r'[a-f0-9]{64}', container_id)):
        return result
    parts = [part for part in raw.split('/') if part]
    names = {container_id: 'bare_full_id', 'libpod-' + container_id: 'cgroupfs_full_id',
             'libpod-' + container_id + '.scope': 'systemd_full_id',
             'libpod-payload-' + container_id: 'split_payload_full_id'}
    bound = [(index, names[part]) for index, part in enumerate(parts) if part in names]
    other, short_bound, short_other = [], [], []
    for index, part in enumerate(parts):
        match = re.fullmatch(r'(?:libpod(?:-payload)?-)?([a-f0-9]{12,64})(?:\.scope)?', part)
        if match:
            candidate = match[1]
            if len(candidate) == 64 and candidate != container_id:
                other.append(index)
            elif len(candidate) < 64:
                (short_bound if container_id.startswith(candidate) else short_other).append(index)
    result.update(other_full_id_present='yes' if other else 'no',
                  short_id_present='yes' if short_bound or short_other else 'no',
                  root_path='yes' if raw.rstrip('/') in ('', '/sys/fs/cgroup') else 'no',
                  dot_components='yes' if any(p in ('.', '..') for p in parts) else 'no')
    indexes = [index for index, _ in bound]
    if bound:
        result['component'] = bound[0][1] if len(bound) == 1 else 'multiple_bound_components'
    else:
        for indexes, kind in ((other, 'different_full_id'), (short_bound, 'short_bound_id'),
                              (short_other, 'different_short_id')):
            if indexes:
                result['component'] = kind
                break
        else:
            result['component'] = 'no_match'
    result['position'] = ('multiple' if len(indexes) > 1 else
                          'leaf' if indexes and indexes[0] == len(parts) - 1 else
                          'ancestor' if indexes else 'none')
    return result


def stderr_summary(raw):
    """No-key diagnostics only. Keep useful bounded text, never a raw log dump.

    Redaction is defense in depth, not a guarantee for arbitrary secret-bearing
    output. This helper is deliberately not used by the live/keyed launcher.
    """
    text = bytes(raw).decode('utf-8', errors='replace')
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)', '', text)
    text = ''.join(char if char in '\n\t' or char.isprintable() else '?' for char in text)
    signatures = [name for name, pattern in (
        ('permission_denied', r'permission denied|operation not permitted'),
        ('namespace', r'namespace|unshare'),
        ('missing_path', r'no such file or directory|executable not found'),
        ('python_error', r'traceback|assertionerror|permissionerror|filenotfounderror'),
        ('digest_verification', r'digest verification'), ('panic', r'panicked at'))
        if re.search(pattern, text, re.IGNORECASE)]
    text = re.sub(r'(?im)(api[_-]?key|token|authorization|password|secret)\s*[:=][^\n]*',
                  r'\1=[redacted]', text)
    text = re.sub(r'(?i)bearer\s+\S+|\bsk-[a-z0-9_-]+', '[credential]', text)
    text = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+', '[url]', text)
    text = re.sub(r'/(?:[^\s\x22\x27<>])+', '[path]', text)
    text = re.sub(r'\b[a-fA-F0-9]{16,}\b|\b[0-9]{9,}\b', '[identifier]', text)
    text = '\n'.join(line.rstrip() for line in text.splitlines()).strip()
    truncated = len(text) > 2048
    detail = text if not truncated else text[:1024] + '\n[truncated]\n' + text[-1024:]
    return {'signatures': signatures or (['unclassified_nonempty'] if raw else []),
            'detail': detail, 'detail_truncated': truncated}


class _Pipe:
    """Bounded no-key pipes; raw stderr exists only until private redacted summary."""
    def __init__(self, process):
        self.process, self.total, self.buffer = process, 0, bytearray()
        self.stderr = bytearray()
        self.counts = {'stdout': 0, 'stderr': 0}
        self.ready_seen = False
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
            self.counts[key.data] += len(chunk)
            retained = chunk[:max(0, LIMIT - self.total)]
            (self.buffer if key.data == 'stdout' else self.stderr).extend(retained)
            self.total += len(chunk)
            if self.total > LIMIT:
                raise LiveFailure('acceptance_output_limit')

    def wait_ready(self, deadline):
        while b'\n' not in self.buffer:
            if not any(key.data == 'stdout' for key in self.ready.get_map().values()):
                raise LiveFailure('acceptance_ready_eof')
            self.pump(deadline)
        line, _, rest = self.buffer.partition(b'\n')
        prefix = b'NESTED_READY:'
        status = line[len(prefix):].decode('ascii', errors='replace')
        if not line.startswith(prefix) or status not in MOUNT_STATES or rest:
            raise LiveFailure('acceptance_ready_rejected')
        self.buffer.clear()
        self.ready_seen = True
        return status

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
        code = self.process.wait(timeout=max(.001, deadline - time.monotonic()))
        if code != 0:
            raise LiveFailure('acceptance_sentinel_exit_nonzero')
        if bytes(self.buffer) != DONE:
            raise LiveFailure('acceptance_sentinel_stdout_mismatch')

    def summary(self):
        output = bytes(self.buffer)
        kind = ('not_reached' if not self.ready_seen else 'empty' if not output else
                'exact_sentinel' if output == DONE else
                'sentinel_plus_other' if DONE in output else 'other')
        return {'attach_returncode_before_stop': self.process.poll(),
                'stdout_bytes': self.counts['stdout'], 'stderr_bytes': self.counts['stderr'],
                'sentinel_stdout_bytes': len(output) if self.ready_seen else None,
                'stdout_class': kind, 'combined_limit_exceeded': self.total > LIMIT,
                'stderr': stderr_summary(self.stderr)}

    def close(self):
        self.stderr.clear()
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
              'startup_state': state_summary(None), 'go_sent': False, 'mount_check': 'not_run',
              'cgroup_diagnostics': cgroup_summary(None, None), 'process_result': None,
              'failure_error': None}
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
        report['cgroup_diagnostics'] = cgroup_summary(value, executor.container_id)
        executor._created_policy(value)
        report['stage'] = 'ready'
        executor._start_attempted = True
        executor.process = executor._popen(
            [*PODMAN_PREFIX, 'start', '--attach', '--interactive',
             executor.container_id], env=executor.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, close_fds=True)
        pipe = _Pipe(executor.process)
        ready_deadline = min(deadline, time.monotonic() + 15)
        report['mount_check'] = pipe.wait_ready(ready_deadline)
        report['stage'] = 'capture'
        value = executor._inspect()
        report['startup_state'] = state_summary(value)
        report['cgroup_diagnostics'] = cgroup_summary(value, executor.container_id)
        if (value['State'].get('Running') is not True
                or value['State'].get('Status') != 'running'
                or type(value['State'].get('Pid')) is not int or value['State']['Pid'] <= 0):
            raise LiveFailure('acceptance_not_running')
        executor._capture_cgroup(value)
        if executor.cgroup_fd is None:
            raise LiveFailure('acceptance_capture_unverified')
        # The trusted READY peer reports its own namespace, avoiding host ptrace
        # access. Capture first so a denied mount check can still prove cleanup.
        report['stage'] = 'mount_check'
        if report['mount_check'] != 'verified':
            raise LiveFailure('acceptance_mountinfo_' + report['mount_check'])
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
        report['failure_error'] = _error_evidence(error)
        if report['go_sent']:
            report['nested'] = ('failed' if reason in ('acceptance_sentinel_exit_nonzero',
                'acceptance_sentinel_stdout_mismatch') else 'unknown')
    finally:
        if pipe is not None:
            report['process_result'] = pipe.summary()  # Before stop can terminate the attach client.
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
        report['resource_diagnostics'] = executor.resource_diagnostics
        report['cleanup_diagnostics'] = executor.cleanup_diagnostics
        if pipe is not None:
            pipe.close()
        executor.close()
    if report['nested'] == 'passed' and report['cleanup'] == 'verified' and report['reason'] is None:
        report['status'] = 'passed'
    return report
