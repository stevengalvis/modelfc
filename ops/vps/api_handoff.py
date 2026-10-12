"""Root-owned, no-argument helper: verify/adopt current on the API service only.

Input is exactly one lowercase SHA (40 bytes). No candidate code runs as root.
"""
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import select
import stat
import subprocess
import sys
import time

UNIT = 'modelfc-corner-api.service'
ROOT = Path('/srv/modelfc')
PROC = Path('/proc')
LAUNCHER = Path('/usr/local/libexec/modelfc-api-launch.py')
DEADLINE_SECONDS = 120
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': '/nonexistent'}


def command(*args):
    return subprocess.run(args, env=ENV, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=True, timeout=3).stdout.decode('utf-8').strip()


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
    return target


def unit_properties():
    raw = command('/usr/bin/systemctl', 'show', UNIT, '--no-pager',
                  '-p', 'User', '-p', 'Group', '-p', 'MainPID', '-p', 'ActiveState',
                  '-p', 'SubState', '-p', 'NoNewPrivileges', '-p', 'ProtectSystem')
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
    args = (proc / 'cmdline').read_bytes().split(b'\0')
    expected = [str(target / '.venv/bin/python'), '-B', '-P', '-s', '-m', 'uvicorn',
                'modelfc.corner_api:app', '--host', '127.0.0.1', '--port', '8000',
                '--workers', '1', '--limit-concurrency', '16', '--timeout-keep-alive', '5']
    if args != [v.encode() for v in expected] + [b'']:
        raise ValueError('wrong entrypoint')
    start = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    sockets = {os.readlink(fd) for fd in (proc / 'fd').iterdir()}
    listeners = [line.split() for line in (proc / 'net/tcp').read_text().splitlines()[1:]]
    if not any(row[1] == '0100007F:1F40' and row[3] == '0A'
               and f'socket:[{row[9]}]' in sockets for row in listeners):
        raise ValueError('listener not owned by api')
    return pid, start


def read_ready():
    connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=2)
    try:
        connection.request('GET', '/api/v1/predictions', headers={'Connection': 'close'})
        response = connection.getresponse()
        raw = response.read(2_000_001)
        if (response.status != 200 or len(raw) > 2_000_000
                or response.getheader('Content-Type', '').split(';')[0] != 'application/json'
                or not isinstance(json.loads(raw), list)):
            raise ValueError('api not ready')
    finally:
        connection.close()


def ready(sha, target):
    try:
        if release(sha) != target: return False
        before = process_identity(target)
        read_ready()
        return process_identity(target) == before and release(sha) == target
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, http.client.HTTPException):
        return False


def handoff(sha):
    target = release(sha)
    protected(LAUNCHER, 0)
    for path in (LAUNCHER.parent, *LAUNCHER.parent.parents): protected(path, 0, True)
    unit_properties()  # Refuse a changed identity/sandbox even before restart.
    if ready(sha, target): return True
    if release(sha) != target: return False
    command('/usr/bin/systemctl', 'restart', '--no-block', UNIT)
    until = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < until:
        if release(sha) != target: return False
        if ready(sha, target): return True
        time.sleep(1)
    return False


def main():
    passed = False
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1 or not select.select([sys.stdin], [], [], 2)[0]:
            raise ValueError('invalid invocation')
        raw = os.read(sys.stdin.fileno(), 41)
        sha = raw.decode('ascii')
        if re.fullmatch('[0-9a-f]{40}', sha) is None: raise ValueError('invalid revision')
        passed = handoff(sha)
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError):
        pass
    print(json.dumps({'status': 'PASS' if passed else 'FAIL'}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
