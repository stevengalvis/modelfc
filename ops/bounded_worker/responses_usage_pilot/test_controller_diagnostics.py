"""Closed test-controller diagnostics. No raw output, error text, IDs or paths."""
import json
import re
import subprocess

from diagnostics import failure_details
from ops.bounded_worker.agents_live_pilot import LiveFailure, _OperationDeadline
from pilot import TESTS

ERROR_CODES = {
    'acceptance_pipe_timeout': 'pipe_timeout',
    'acceptance_output_limit': 'combined_output_limit',
    'acceptance_ready_eof': 'ready_eof',
    'acceptance_ready_rejected': 'ready_rejected',
    'test_input_timeout': 'input_timeout',
    'test_input_no_progress': 'input_no_progress',
    'test_result_after_workload_deadline': 'result_after_workload_deadline',
    'test_workload_timeout': 'workload_timeout',
    'test_completion_timeout': 'completion_timeout',
    'test_attach_exit_nonzero': 'attach_exit_nonzero',
    'test_stderr_nonempty': 'stderr_nonempty',
    'test_stdout_limit': 'stdout_limit',
    'test_not_running': 'container_not_running',
    'test_isolation_unverified': 'isolation_unverified',
    'test_changed_before_go': 'identity_changed',
    'container_name_not_available': 'container_name_unavailable',
    'container_create_uncertain': 'container_create_uncertain',
}
FRAME_CODES = frozenset(('empty', 'oversized', 'invalid_encoding', 'invalid_json',
                         'duplicate_keys', 'invalid_shape'))
CONTAINER_STATES = frozenset(('created', 'configured', 'running', 'paused', 'stopped', 'exited'))


class ResultRejected(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in FRAME_CODES else 'invalid_shape'
        super().__init__(self.code)


def test_failure(exc):
    detail = failure_details(exc)
    code = 'unclassified'
    if isinstance(exc, _OperationDeadline):
        detail = {'category': 'interrupted_or_deadline', 'os_errno': None}
        code = 'overall_deadline'
    elif isinstance(exc, subprocess.TimeoutExpired):
        code = 'attach_wait_timeout'
    elif type(exc) is ResultRejected:
        code = exc.code if type(exc.code) is str and exc.code in FRAME_CODES else 'unclassified'
    elif type(exc) is LiveFailure:
        # Compare a trusted exception against fixed tokens. Never serialize its
        # message/args, nor an arbitrary supplied exception-class name.
        args = exc.args
        if len(args) == 1 and type(args[0]) is str:
            code = ERROR_CODES.get(args[0], 'unclassified')
    return {**detail, 'code': code}


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ResultRejected('duplicate_keys')
        value[key] = item
    return value


def _constant(_):
    raise ResultRejected('invalid_json')


def parse_result(raw):
    if type(raw) is not bytes:
        raise ResultRejected('invalid_shape')
    if not raw:
        raise ResultRejected('empty')
    if len(raw) > 4096:
        raise ResultRejected('oversized')
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise ResultRejected('invalid_encoding') from None
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except json.JSONDecodeError:
        raise ResultRejected('invalid_json') from None
    if (type(value) is not dict or set(value) != {'failed', 'passed'}
            or type(value['failed']) is not list or len(value['failed']) > len(TESTS)
            or any(type(name) is not str or name not in TESTS for name in value['failed'])
            or value['failed'] != sorted(set(value['failed']))
            or type(value['passed']) is not bool
            or value['passed'] != (not value['failed'])):
        raise ResultRejected('invalid_shape')
    return value


def frame_summary(raw):
    try:
        value = parse_result(raw)
        return {'class': 'valid_pass' if value['passed'] else 'valid_fail',
                'observed_failed': value['failed']}
    except ResultRejected as exc:
        return {'class': exc.code, 'observed_failed': None}


def text_signatures(value):
    if type(value) not in (str, bytes):
        return ['unavailable']
    if not value:
        return []
    text = value[:4096].decode('utf-8', errors='replace') if type(value) is bytes else value[:4096]
    patterns = (
        ('permission_denied', r'permission denied|operation not permitted'),
        ('namespace', r'namespace|unshare'),
        ('missing_path', r'no such file|not found'),
        ('python_error', r'traceback|assertionerror|valueerror|typeerror|nameerror'),
        ('timeout', r'timeout|timed out'),
        ('closed_stream', r'closed pipe|broken pipe|stream closed'),
    )
    return [name for name, pattern in patterns if re.search(pattern, text, re.IGNORECASE)] or ['unclassified_nonempty']


def integer(value, *, signed=False):
    low = -(2 ** 31) if signed else 0
    return value if type(value) is int and low <= value < 2 ** 31 else None


def container_summary(value):
    """Accept either raw State fields or the existing stop's closed observation."""
    value = value if type(value) is dict else {}
    status = value.get('Status', value.get('status'))
    running = value.get('Running', value.get('running'))
    oom = value.get('OOMKilled', value.get('oom_killed'))
    return {
        'status': status if type(status) is str and status in CONTAINER_STATES else 'unknown',
        'running': running if type(running) is bool else None,
        'exit_code': integer(value.get('ExitCode', value.get('exit_code')), signed=True),
        'oom_killed': oom if type(oom) is bool else None,
        'error_signatures': text_signatures(value.get('Error')),
    }


def pipe_summary(pipe, process):
    registered = {key.data for key in pipe.ready.get_map().values()}
    raw = bytes(pipe.buffer)
    return {
        'stdout_bytes': integer(pipe.counts.get('stdout')),
        'stderr_bytes': integer(pipe.counts.get('stderr')),
        'retained_result_bytes': len(raw),
        'stdout_eof': 'stdout' not in registered,
        'stderr_eof': 'stderr' not in registered,
        'stdin_closed': process.stdin.closed if type(process.stdin.closed) is bool else None,
        'attach_returncode_before_stop': integer(process.poll(), signed=True),
        'frame': frame_summary(raw),
        'stderr_signatures': text_signatures(bytes(pipe.stderr)),
    }
