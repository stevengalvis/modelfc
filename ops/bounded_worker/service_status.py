"""Fixed local system-manager read; inactive until given protected host policy."""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

from .bridge_security import protected_json
from .linear_status import DiagnosticUncertain, summarize, summarize_health

LIMIT = 4096
TIMEOUT = 5


def _protected_regular(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('unprotected_host_file')
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('unprotected_host_file')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('unprotected_host_file')


def _host_matches(machine_id):
    _protected_regular('/etc/machine-id')
    with open('/etc/machine-id', 'rb') as source:
        actual = source.read(34)
    if actual not in (machine_id.encode(), machine_id.encode() + b'\n'):
        raise ValueError('host_mismatch')
    _protected_regular('/usr/bin/systemctl')
    if not os.access('/usr/bin/systemctl', os.X_OK):
        raise ValueError('invalid_systemctl')


def _read(argv):
    """Bound stdout in memory and time; discard stderr rather than retain logs."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, cwd='/', close_fds=True,
                               env={'LC_ALL': 'C', 'SYSTEMD_PAGER': '', 'SYSTEMD_COLORS': '0'},
                               shell=False)
    deadline = time.monotonic() + TIMEOUT
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError('status_timeout')
                part = os.read(process.stdout.fileno(), LIMIT + 1 - len(output))
                if not part:
                    break
                output.extend(part)
                if len(output) > LIMIT:
                    raise ValueError('status_output_limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError('status_failed')
        return bytes(output)
    finally:
        process.stdout.close()
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=1)
            except Exception:
                raise DiagnosticUncertain('status_cleanup_unknown') from None


def parse(raw, *, health=False):
    if type(raw) is not bytes or len(raw) > LIMIT:
        raise ValueError('invalid_status')
    try:
        lines = raw.decode('ascii').splitlines()
        properties = {}
        for line in lines:
            key, value = line.split('=', 1)
            if key in properties:
                raise ValueError('duplicate_status')
            properties[key] = value
        (summarize_health if health else summarize)(properties)
        return properties
    except (UnicodeError, ValueError):
        raise ValueError('invalid_status') from None


class ServiceStatus:
    operation = 'service-status'
    properties = 'LoadState,ActiveState'
    def __init__(self, policy_path):
        # Root-owned 0600 operator input with protected ancestors, never request data.
        policy = protected_json(policy_path)
        if (type(policy) is not dict or set(policy) != {'version', 'unit', 'machine_id'}
                or type(policy['version']) is not int or policy['version'] != 1
                or type(policy['unit']) is not str
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.service', policy['unit'])
                or type(policy['machine_id']) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', policy['machine_id'])
                or policy['machine_id'] == '0' * 32):
            raise ValueError('invalid_host_policy')
        self._unit, self._machine_id = policy['unit'], policy['machine_id']

    @property
    def approval_scope(self):
        # Changes to the fixed command's meaning require a new operation version.
        value = [f'local-system-{self.operation}-v1', self._machine_id, self._unit]
        return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()

    def status(self):
        _host_matches(self._machine_id)
        argv = ['/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
                'show', '--property=' + self.properties, '--', self._unit]
        result = parse(_read(argv), health=self.operation == 'service-health')
        _host_matches(self._machine_id)
        return result


class ServiceHealth(ServiceStatus):
    operation = 'service-health'
    properties = 'LoadState,ActiveState,Result,ExecMainCode,ExecMainStatus,NRestarts'
