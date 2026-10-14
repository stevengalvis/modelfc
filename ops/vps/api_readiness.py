"""Proposed root-installed read-only readiness probe; no workflow invokes it yet.

Only fixed systemctl show, bounded provenance/proc reads and loopback capabilities
GET. No restart, shell, Git invocation, candidate imports, provider calls or writes.
Identity checks adapted from api_handoff.py; its handoff/main are never imported.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import stat
import subprocess
import sys
import time

UNIT = 'modelfc-corner-api.service'
ROOT = Path('/srv/modelfc')
PROC = Path('/proc')
INSTALLED = Path('/opt/modelfc-deploy/api_readiness.py')
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': '/nonexistent'}
SHA = re.compile('[0-9a-f]{40}')
DIGEST = re.compile('[0-9a-f]{64}')
FIELDS = {'schema_version', 'requested_sha', 'nonce', 'helper_sha256', 'observed_at',
          'status', 'reason', 'serving_sha', 'pid', 'start_ticks', 'invocation_id',
          'capabilities_http_200', 'identity_stable'}


def bounded(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('not regular')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('oversize')
    return raw


def service_properties():
    value = subprocess.run(
        ['/usr/bin/systemctl', 'show', UNIT, '--no-pager',
         '-p', 'User', '-p', 'Group', '-p', 'MainPID', '-p', 'ActiveState',
         '-p', 'SubState', '-p', 'InvocationID', '-p', 'NoNewPrivileges', '-p', 'ProtectSystem'],
        cwd='/', env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=True, timeout=3).stdout
    if len(value) > 8192:
        raise ValueError('oversize service properties')
    return value.decode('ascii').strip()


def protected(path, owner, directory=False):
    value = path.lstat()
    if (value.st_uid != owner or value.st_mode & 0o022
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))):
        raise ValueError('invalid protected path')

def release(sha):
    if re.fullmatch('[0-9a-f]{40}', sha) is None:
        raise ValueError('invalid revision')
    protected(ROOT.parent, 0, True)
    owner = pwd.getpwnam('modelfc-deploy').pw_uid
    for path in (ROOT, ROOT / 'releases'):
        protected(path, owner, True)
    link = ROOT / 'current'
    if not link.is_symlink(): raise ValueError('invalid current')
    target = Path(os.readlink(link))
    if target.parent != ROOT / 'releases' or not re.fullmatch(sha + '-[0-9a-f]{12}', target.name):
        raise ValueError('wrong current')
    protected(target, owner, True)
    protected(target / '.git', owner, True)
    marker = target / '.git/modelfc-deployed-sha'
    protected(marker, owner)
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or source.read(41) != sha.encode()):
            raise ValueError('wrong marker')
    # Installed releases are checked out detached by deploy_main.checkout_release.
    # Read HEAD directly: do not load candidate Git configuration or execute Git.
    head = target / '.git/HEAD'
    protected(head, owner)
    if bounded(head, 41) not in (sha.encode(), (sha + '\n').encode()):
        raise ValueError('wrong detached HEAD')
    return target

def unit_properties():
    raw = service_properties()
    properties = dict(line.split('=', 1) for line in raw.splitlines())
    if (properties['User'] != 'modelfc-api' or properties['Group'] != 'modelfc-api'
            or properties['NoNewPrivileges'] != 'yes' or properties['ProtectSystem'] != 'strict'):
        raise ValueError('invalid service boundary')
    return properties

def process_identity(target):
    props = unit_properties()
    if props['ActiveState'] != 'active' or props['SubState'] != 'running':
        raise ValueError('not running')
    pid = int(props['MainPID'])
    if pid <= 1: raise ValueError('invalid pid')
    proc = PROC / str(pid)
    uid = pwd.getpwnam('modelfc-api').pw_uid
    if proc.stat().st_uid != uid or Path(os.readlink(proc / 'cwd')) != target:
        raise ValueError('wrong process')
    args = bounded(proc / 'cmdline', 4096).split(b'\0')
    expected = [str(target / '.venv/bin/python'), '-B', '-P', '-s', '-m', 'uvicorn',
                'modelfc.corner_api:app', '--host', '127.0.0.1', '--port', '8000',
                '--workers', '1', '--limit-concurrency', '16', '--timeout-keep-alive', '5']
    if args != [v.encode() for v in expected] + [b'']:
        raise ValueError('wrong entrypoint')
    start = bounded(proc / 'stat', 4096).decode('ascii').rsplit(')', 1)[1].split()[19]
    if not start.isdecimal() or int(start) <= 0:
        raise ValueError('invalid process start')
    invocation = props['InvocationID']
    if re.fullmatch('[0-9a-f]{32}', invocation) is None:
        raise ValueError('invalid invocation')
    sockets = set()
    for index, fd in enumerate((proc / 'fd').iterdir()):
        if index >= 256:
            raise ValueError('too many descriptors')
        sockets.add(os.readlink(fd))
    listeners = [line.split() for line in bounded(proc / 'net/tcp', 262144).decode('ascii').splitlines()[1:]]
    if not any(len(row) > 9 and row[1] == '0100007F:1F40' and row[3] == '0A'
               and f'socket:[{row[9]}]' in sockets for row in listeners):
        raise ValueError('listener not owned by api')
    return pid, int(start), invocation

def read_ready():
    connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=2)
    try:
        connection.request('GET', '/api/v1/capabilities', headers={'Connection': 'close'})
        response = connection.getresponse()
        raw = response.read(65_537)
        value = strict_json(raw) if len(raw) <= 65_536 else None
        if (response.status != 200 or len(raw) > 65_536
                or response.getheader('Content-Type', '').split(';')[0] != 'application/json'
                or not isinstance(value, dict) or value.get('api_version') != 'v1'
                or not isinstance(value.get('competitions'), list)
                or not isinstance(value.get('models'), list)):
            raise ValueError('api not ready')
    finally:
        connection.close()

def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate field')
            value[key] = item
        return value
    def constant(_):
        raise ValueError('nonfinite JSON')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid JSON') from error


def request(raw):
    if not isinstance(raw, bytes) or len(raw) > 256:
        raise ValueError('invalid request')
    value = strict_json(raw)
    if (not isinstance(value, dict) or set(value) != {'sha', 'nonce'}
            or not isinstance(value['sha'], str) or not SHA.fullmatch(value['sha'])
            or not isinstance(value['nonce'], str) or not DIGEST.fullmatch(value['nonce'])):
        raise ValueError('invalid request')
    return value['sha'], value['nonce']


def installation_revision():
    if Path(__file__).absolute() != INSTALLED:
        raise ValueError('not installed helper')
    for path in (INSTALLED, *INSTALLED.parents):
        protected(path, 0, directory=path != INSTALLED)
    if stat.S_IMODE(INSTALLED.lstat().st_mode) != 0o555:
        raise ValueError('helper mode')
    return hashlib.sha256(bounded(INSTALLED, 65536)).hexdigest()


def evidence(sha, nonce, revision):
    result = dict(schema_version=1, requested_sha=sha, nonce=nonce,
                  helper_sha256=revision, observed_at=int(time.time()), status='FAIL',
                  reason='NOT_READY', serving_sha=None, pid=None, start_ticks=None,
                  invocation_id=None, capabilities_http_200=False, identity_stable=False)
    try:
        target = release(sha)
        before = process_identity(target)
        read_ready()
        if release(sha) != target or process_identity(target) != before:
            raise ValueError('identity changed')
        result.update(status='PASS', reason='READY', serving_sha=sha,
                      pid=before[0], start_ticks=before[1], invocation_id=before[2],
                      capabilities_http_200=True, identity_stable=True)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError,
            subprocess.SubprocessError, http.client.HTTPException, TimeoutError):
        pass  # Never emit raw process/HTTP/error text.
    result['observed_at'] = int(time.time())
    return result


def validate_evidence(raw, *, expected_sha, current_main_sha, expected_nonce,
                      expected_helper_sha256, request_started_at, now):
    """Pure future-consumer gate. Caller must obtain fresh main and trusted pins.

    Artifact bytes are not authentication: use only the authenticated fixed-host
    transport, bind nonce to this invocation/run/attempt and never reuse PASS.
    """
    if (not isinstance(raw, bytes) or len(raw) > 4096
            or not isinstance(expected_sha, str) or not SHA.fullmatch(expected_sha)
            or current_main_sha != expected_sha
            or not isinstance(expected_nonce, str) or not DIGEST.fullmatch(expected_nonce)
            or not isinstance(expected_helper_sha256, str) or not DIGEST.fullmatch(expected_helper_sha256)
            or type(now) is not int or type(request_started_at) is not int
            or not 0 <= now - request_started_at <= 120):
        raise ValueError('invalid or superseded request')
    value = strict_json(raw)
    if (not isinstance(value, dict) or set(value) != FIELDS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['status'] != 'PASS' or value['reason'] != 'READY'
            or value['requested_sha'] != expected_sha or value['serving_sha'] != expected_sha
            or value['nonce'] != expected_nonce or value['helper_sha256'] != expected_helper_sha256
            or value['identity_stable'] is not True or value['capabilities_http_200'] is not True
            or type(value['observed_at']) is not int
            or not request_started_at - 5 <= value['observed_at'] <= now + 5
            or not 0 <= now - value['observed_at'] + 5 <= 125
            or type(value['pid']) is not int or value['pid'] <= 1
            or type(value['start_ticks']) is not int or value['start_ticks'] <= 0
            or not isinstance(value['invocation_id'], str)
            or not re.fullmatch('[0-9a-f]{32}', value['invocation_id'])):
        raise ValueError('invalid, stale or failed evidence')
    return value


def read_request():
    raw = b''
    until = time.monotonic() + 2
    while True:
        remaining = until - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
            raise ValueError('input timeout')
        chunk = os.read(sys.stdin.fileno(), 257 - len(raw))
        if not chunk:
            return request(raw)
        raw += chunk
        if len(raw) > 256:
            raise ValueError('oversize request')


def main():
    sha = nonce = revision = None
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1:
            raise ValueError('invalid invocation')
        revision = installation_revision()
        sha, nonce = read_request()
        result = evidence(sha, nonce, revision)
        if installation_revision() != revision:
            raise ValueError('helper changed')
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, TimeoutError):
        result = dict(schema_version=1, requested_sha=sha, nonce=nonce,
                      helper_sha256=revision, observed_at=int(time.time()), status='FAIL',
                      reason='INVALID_INVOCATION', serving_sha=None, pid=None,
                      start_ticks=None, invocation_id=None, capabilities_http_200=False,
                      identity_stable=False)
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    def deadline(_signal, _frame):
        raise TimeoutError()
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(15)
    raise SystemExit(main())
