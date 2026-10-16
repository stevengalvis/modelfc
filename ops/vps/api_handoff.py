"""Root-owned, no-argument helper: verify/adopt current on the API service only.

Input is exactly one lowercase SHA (40 bytes). No candidate code runs as root.
"""
import grp
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


# Fixed installed-unit contract; no candidate config or host mutations.
UNIT_FILE = Path('/etc/systemd/system') / UNIT
SECURITY = {
    'User': 'modelfc-api', 'Group': 'modelfc-api', 'SupplementaryGroups': '',
    'DynamicUser': 'no', 'PrivateNetwork': 'no', 'PrivateUsers': 'no',
    'NetworkNamespacePath': '', 'JoinsNamespaceOf': '', 'PAMName': '', 'NoNewPrivileges': 'yes', 'ProtectSystem': 'strict',
    'ProtectHome': 'yes', 'PrivateTmp': 'yes', 'PrivateDevices': 'yes',
    'RestrictSUIDSGID': 'yes', 'CapabilityBoundingSet': '', 'AmbientCapabilities': '',
    'ReadWritePaths': '', 'BindPaths': '', 'BindReadOnlyPaths': '', 'MountImages': '',
    'TemporaryFileSystem': '', 'RootDirectory': '', 'RootImage': '',
    'Environment': '', 'EnvironmentFiles': '', 'PassEnvironment': '',
    'LoadCredential': '', 'LoadCredentialEncrypted': '', 'ImportCredential': '',
    'SetCredential': '', 'SetCredentialEncrypted': '',
    'ExecCondition': '', 'ExecStartPre': '', 'ExecStartPost': '', 'ExecStop': '', 'ExecStopPost': '',
    'DropInPaths': '', 'FragmentPath': str(UNIT_FILE), 'NeedDaemonReload': 'no',
    'Type': 'simple', 'WorkingDirectory': '/srv/modelfc/current', 'UMask': '0077',
    'StandardInput': 'null', 'Delegate': 'no', 'KillMode': 'control-group',
    'TasksMax': '32', 'MemoryMax': '268435456', 'CPUQuotaPerSecUSec': '1s',
    'RuntimeMaxUSec': '30min', 'Restart': 'always', 'RestartUSec': '10s',
}
TOKEN_SECURITY = {
    'ReadOnlyPaths': {'/srv/modelfc', '/etc/modelfc', '/var/lib/modelfc/state'},
    'InaccessiblePaths': {'-/etc/modelfc/credentials', '-/run/credentials',
        '-/var/lib/modelfc/history/data/corner-refresh/backups', '-/var/lib/modelfc-deploy',
        '-/run/modelfc-acquisition', '-/etc/modelfc-validator', '-/var/lib/modelfc-validator',
        '-/opt/modelfc-deploy', '-/opt/modelfc-validator', '-/root/modelfc-state', '-/root'},
    'RestrictAddressFamilies': {'AF_INET', 'AF_UNIX'},
    'IPAddressDeny': {'0.0.0.0/0', '::/0'},
    'IPAddressAllow': {'127.0.0.0/8', '::1/128'},
    'UnsetEnvironment': set('GITHUB_TOKEN GH_TOKEN SSH_AUTH_SOCK PYTHONPATH PYTHONHOME LD_PRELOAD '
        'LD_LIBRARY_PATH LD_AUDIT ODDSPAPI_API_KEY MODELFC_DATA_CONFIG MODELFC_STATE_DIR MODELFC_CORS_ORIGINS'.split()),
}


def api_account():
    account = pwd.getpwnam('modelfc-api')
    gid = grp.getgrnam('modelfc-api').gr_gid
    if (account.pw_uid <= 0 or gid <= 0 or account.pw_gid != gid
            or set(os.getgrouplist('modelfc-api', gid)) != {gid}):
        raise ValueError('invalid account groups')
    # These installed roles must never alias the API's low-privilege identity.
    for name in ('modelfc-deploy', 'modelfc-runtime', 'modelfc-validator'):
        other = pwd.getpwnam(name)
        other_group = grp.getgrnam(name).gr_gid
        if account.pw_uid == other.pw_uid or gid in (other.pw_gid, other_group):
            raise ValueError('shared service identity')
    return account


def trusted_start(value, extended=False):
    # stdlib-only copy of the controller's strict systemctl command parser.
    match = re.fullmatch(r'\{\s*([^{}]*)\s*\}', value)
    if match is None: return False
    parts = [part.strip().partition('=') for part in match.group(1).split(';')]
    flag = 'flags' if extended else 'ignore_errors'
    keys = {'path', 'argv[]', flag, 'start_time', 'stop_time', 'pid', 'code', 'status'}
    if (len(parts) != len(keys) or {key for key, _, _ in parts} != keys
            or any(separator != '=' for _, separator, _ in parts)): return False
    fields = {key: item for key, _, item in parts}
    return (fields['path'] == '/usr/bin/python3'
            and fields['argv[]'] == '/usr/bin/python3 -I -B ' + str(LAUNCHER)
            and fields[flag] == ('' if extended else 'no'))



CREDENTIAL_TYPES = {
    'LoadCredential': 'a(ss)', 'LoadCredentialEncrypted': 'a(ss)',
    'SetCredential': 'a(say)', 'SetCredentialEncrypted': 'a(say)', 'ImportCredential': 'as',
}
OMITTED_EMPTY_TYPES = {'EnvironmentFiles': 'a(sb)', **{name: 'a(sasbttttuii)' for name in
    ('ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost')}}


def unique_properties(pairs):
    pairs = list(pairs)
    result = dict(pairs)
    if len(result) != len(pairs): raise ValueError('duplicate property')
    return result


def empty_bus_properties(signatures):
    # Fixed system bus and unit: never inherit a caller's bus address or load code.
    label = re.sub(r'[^a-zA-Z0-9]', lambda m: '_%02x' % ord(m[0]), UNIT)
    raw = command('/usr/bin/busctl', '--system', '--json=short', 'get-property',
                  'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/' + label,
                  'org.freedesktop.systemd1.Service', *signatures)
    rows = raw.splitlines()
    if len(raw) > 65536 or len(rows) != len(signatures): raise ValueError('invalid bus properties')
    for signature, row in zip(signatures.values(), rows):
        value = json.loads(row, object_pairs_hook=unique_properties)
        if (not isinstance(value, dict) or set(value) != {'type', 'data'}
                or value['type'] != signature or value['data'] != []):
            raise ValueError('nonempty or invalid bus property')


def normalize_properties(raw, keys):
    if len(raw) > 65536: raise ValueError('invalid service properties')
    properties = unique_properties(line.split('=', 1) for line in raw.splitlines())
    signatures = dict(CREDENTIAL_TYPES)
    for name in CREDENTIAL_TYPES:
        allowed = ('',) if name == 'ImportCredential' else ('', '[unprintable]')
        if properties.get(name) not in allowed: raise ValueError('invalid credential property')
    for name, signature in OMITTED_EMPTY_TYPES.items():
        if name not in properties: signatures[name] = signature
        elif properties[name] != '': raise ValueError('unexpected service hook')
    empty_bus_properties(signatures)
    properties.update({name: '' for name in signatures})
    if set(properties) != set(keys): raise ValueError('invalid service properties')
    return properties


def unit_properties():
    keys = (*SECURITY, *TOKEN_SECURITY, 'ExecStart', 'ExecStartEx',
            'MainPID', 'ActiveState', 'SubState', 'InvocationID', 'Job')
    args = ['/usr/bin/systemctl', 'show', UNIT, '--no-pager', '--all']
    for key in keys: args.extend(('-p', key))
    raw = command(*args)
    properties = normalize_properties(raw, keys)
    if (any(properties[k] != v for k, v in SECURITY.items())
            or any(set(properties[k].split()) != v for k, v in TOKEN_SECURITY.items())
            or not trusted_start(properties['ExecStart'])
            or not trusted_start(properties['ExecStartEx'], True)):
        raise ValueError('invalid service boundary')
    api_account()
    for path in (UNIT_FILE, *UNIT_FILE.parents): protected(path, 0, path != UNIT_FILE)
    return properties


def process_credentials(pid):
    account = api_account()
    proc = PROC / str(pid)
    if proc.stat().st_uid != account.pw_uid: raise ValueError('wrong process owner')
    lines = [line.partition(':') for line in (proc / 'status').read_text().splitlines()]
    fields = {k: v.split() for k, sep, v in lines if sep}
    expected = {'Uid': [str(account.pw_uid)]*4, 'Gid': [str(account.pw_gid)]*4,
                'NoNewPrivs': ['1'], 'Seccomp': ['2']}
    if (any(fields.get(k) != v for k, v in expected.items())
            or 'Groups' not in fields
            or not set(fields['Groups']) <= {str(account.pw_gid)}
            or any(fields.get(k) != ['0000000000000000'] for k in
                   ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))):
        raise ValueError('invalid process credentials')


def boundary_before_restart():
    props = unit_properties()
    pid = int(props['MainPID'])
    if pid > 1: process_credentials(pid)
    elif pid != 0: raise ValueError('invalid pid')
    return props



def no_pending_job(props):
    # systemd 255 prints Job= for the typed no-job tuple; never infer emptiness.
    if props.get('Job') == '0': return
    if props.get('Job') != '': raise ValueError('pending service job')
    label = re.sub(r'[^a-zA-Z0-9]', lambda m: '_%02x' % ord(m[0]), UNIT)
    raw = command('/usr/bin/busctl', '--system', '--json=short', 'get-property',
                  'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/' + label,
                  'org.freedesktop.systemd1.Unit', 'Job')
    if len(raw) > 4096: raise ValueError('invalid service job')
    value = json.loads(raw, object_pairs_hook=unique_properties)
    if (not isinstance(value, dict) or set(value) != {'type', 'data'} or value['type'] != '(uo)'
            or not isinstance(value['data'], list) or len(value['data']) != 2
            or type(value['data'][0]) is not int or value['data'] != [0, '/']):
        raise ValueError('pending or invalid service job')


def process_identity(target):
    props = unit_properties()
    if props['ActiveState'] != 'active' or props['SubState'] != 'running':
        raise ValueError('not running')
    no_pending_job(props)
    pid = int(props['MainPID'])
    if pid <= 1: raise ValueError('invalid pid')
    process_credentials(pid)
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
        connection.request('GET', '/api/v1/capabilities', headers={'Connection': 'close'})
        response = connection.getresponse()
        raw = response.read(65_537)
        value = json.loads(raw) if len(raw) <= 65_536 else None
        if (response.status != 200 or len(raw) > 65_536
                or response.getheader('Content-Type', '').split(';')[0] != 'application/json'
                or not isinstance(value, dict) or value.get('api_version') != 'v1'
                or not isinstance(value.get('competitions'), list)
                or not isinstance(value.get('models'), list)):
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
    until = time.monotonic() + DEADLINE_SECONDS
    target = release(sha)
    protected(LAUNCHER, 0)
    for path in (LAUNCHER.parent, *LAUNCHER.parent.parents): protected(path, 0, True)
    before = boundary_before_restart()['InvocationID']  # Refuse changed service boundaries before restart.
    if ready(sha, target): return time.monotonic() < until
    if release(sha) != target: return False
    props = boundary_before_restart()  # Recheck drift and automatic rotation during readiness.
    before = props['InvocationID']
    no_pending_job(props)
    if time.monotonic() >= until: return False
    command('/usr/bin/systemctl', 'restart', '--no-block', UNIT)
    while time.monotonic() < until:
        if release(sha) != target: return False
        invocation = unit_properties()['InvocationID']
        if (re.fullmatch('[0-9a-f]{32}', invocation) and invocation != before
                and ready(sha, target)):
            return True
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
