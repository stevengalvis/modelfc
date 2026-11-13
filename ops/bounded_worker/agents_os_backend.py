"""Concrete, opt-in Linux no-network acceptance path, NOT an API executor.

Import/validation does not launch anything. run_acceptance requires separately
authorized root execution on a disposable systemd host and a prepared image.
No provisioning, credentials, network client, arbitrary command or host worktree.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time

from .bridge_envelope import _pairs
from .bridge_security import protected_dir
from .storage import parse_mountinfo
from .systemd_backend import SystemdContainment

_BWRAP = Path('/usr/bin/bwrap')
_NAMESPACES = ('user', 'mnt', 'pid', 'net', 'ipc', 'uts')
_CHECKS = ('identity', 'privileges', 'namespaces', 'environment', 'descriptors',
           'private_mounts', 'clean_home', 'workspace_write', 'readonly_root')

# Fixed source only; repr below escapes newlines for SystemdContainment's strict
# argv contract. No shell interprets this source or any caller-supplied text.
_PROBE = '''import errno,json,os,stat,sys,time
from pathlib import Path
attempt,uid,gid,host = sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),json.loads(sys.argv[4])
assert uid > 0 and gid > 0
assert (os.getuid(),os.geteuid(),os.getgid(),os.getegid(),os.getgroups()) == (uid,uid,gid,gid,[])
status = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
assert status['NoNewPrivs'].strip() == '1'
assert all(int(status[key].strip(),16) == 0 for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
observed = {name:os.readlink('/proc/self/ns/'+name) for name in host}
assert all(observed[name] != host[name] for name in host)
assert dict(os.environ) == {'PATH':'/usr/bin:/bin','HOME':'/tmp/agents-home','LANG':'C.UTF-8'}
for name in os.listdir('/proc/self/fd'):
    if int(name) <= 2:
        assert stat.S_ISFIFO(os.fstat(int(name)).st_mode)
        continue
    try:
        os.fstat(int(name))
    except OSError as exc:
        assert exc.errno == errno.EBADF
    else:
        raise AssertionError('unexpected descriptor')
mounts = {}
for line in Path('/proc/self/mountinfo').read_text().splitlines():
    left,right = line.split(' - ',1)
    fields = left.split()
    mounts[fields[4]] = (right.split()[0],set(fields[5].split(',')))
assert mounts['/workspace'][0] == 'tmpfs' and mounts['/tmp'][0] == 'tmpfs'
assert 'ro' in mounts['/'][1]
assert all(not Path(path).exists() for path in ('/root','/home','/srv','/run','/sys'))
home,work = Path('/tmp/agents-home'),Path('/workspace')
assert list(home.iterdir()) == [] and list(work.iterdir()) == []
assert stat.S_IMODE(home.stat().st_mode) == 0o700 and home.stat().st_uid == uid
assert work.stat().st_uid == uid and work.stat().st_dev != Path('/').stat().st_dev
marker = work/'synthetic.txt'
marker.write_bytes(b'offline acceptance only\\n')
assert marker.read_bytes() == b'offline acceptance only\\n'
try:
    Path('/root-write-test').write_bytes(b'x')
except OSError as exc:
    assert exc.errno in (errno.EROFS,errno.EACCES)
else:
    raise AssertionError('writable root')
report = {'schema':'agents-no-network-probe-v1','attempt_id':attempt,'namespaces':observed,
          'workspace_identity':[work.stat().st_dev,work.stat().st_ino],
          'checks':{name:True for name in ('identity','privileges','namespaces','environment','descriptors',
                   'private_mounts','clean_home','workspace_write','readonly_root')}}
print(json.dumps(report,sort_keys=True,separators=(',',':')),flush=True)
time.sleep(120)
'''


@dataclass(frozen=True)
class AcceptanceSpec:
    fixture_root: str
    image_sha256: str
    bubblewrap_sha256: str
    attempt_id: str
    uid: int
    gid: int

    def validate(self):
        if (type(self.fixture_root) is not str
                or not re.fullmatch(r'/srv/modelfc-agents-fixture-[a-f0-9]{32}', self.fixture_root)
                or any(type(value) is not str or not re.fullmatch(r'[a-f0-9]{64}', value)
                       for value in (self.image_sha256, self.bubblewrap_sha256, self.attempt_id))
                or any(type(value) is not int or not 1 <= value <= 2147483647
                       for value in (self.uid, self.gid))):
            raise ValueError('invalid_acceptance_spec')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file(path, maximum, *, modes):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in modes or not 0 <= before.st_size <= maximum):
        raise ValueError('unprotected_acceptance_file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        if _identity(os.fstat(source.fileno())) != _identity(before):
            raise ValueError('acceptance_file_changed')
        digest, total = hashlib.sha256(), 0
        while chunk := source.read(1048576):
            total += len(chunk)
            if total > maximum:
                raise ValueError('acceptance_file_limit')
            digest.update(chunk)
        if (total != before.st_size or _identity(os.fstat(source.fileno())) != _identity(before)
                or _identity(path.lstat()) != _identity(before)):
            raise ValueError('acceptance_file_changed')
    return digest.hexdigest(), _identity(before)


def inspect_image(image):
    """Read-only root-owned tree inventory; no symlinks, devices or nested mounts."""
    image = protected_dir(image, 0o555)
    mounts = parse_mountinfo(Path('/proc/self/mountinfo').read_text(encoding='ascii'))
    if any(image in Path(entry['mount']).parents for entry in mounts):
        raise ValueError('image_nested_mount')
    top = {entry.name for entry in image.iterdir()}
    if (not {'usr', 'proc', 'dev', 'tmp', 'workspace'} <= top
            or not top <= {'usr', 'lib', 'lib64', 'proc', 'dev', 'tmp', 'workspace'}):
        raise ValueError('image_layout_rejected')
    for name in ('proc', 'dev', 'tmp', 'workspace'):
        path = image / name
        if not path.is_dir() or path.is_symlink() or list(path.iterdir()):
            raise ValueError('image_mountpoint_not_empty')
    root = image.lstat()
    records, identities, total = [], [('.', _identity(root))], 0
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(image, followlinks=False, onerror=walk_error):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            rel, info = path.relative_to(image).as_posix(), path.lstat()
            if (not re.fullmatch(r'[A-Za-z0-9_./+-]{1,300}', rel)
                    or info.st_uid != 0 or info.st_dev != root.st_dev):
                raise ValueError('image_entry_rejected')
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode) and mode == 0o555:
                digest, identity = None, _identity(info)
            elif stat.S_ISREG(info.st_mode) and mode in (0o444, 0o555):
                digest, identity = _file(path, 134217728, modes=(0o444, 0o555))
                total += info.st_size
            else:
                raise ValueError('image_entry_rejected')
            records.append((rel, mode, digest))
            identities.append((rel, identity))
            if len(records) > 8192 or total > 536870912:
                raise ValueError('image_inventory_limit')
    executable = image / 'usr/bin/python3'
    executable_info = executable.lstat()
    if (not stat.S_ISREG(executable_info.st_mode)
            or stat.S_IMODE(executable_info.st_mode) != 0o555 or executable_info.st_size == 0):
        raise ValueError('image_python_not_executable')
    digest = hashlib.sha256(json.dumps(sorted(records), separators=(',', ':')).encode('ascii')).hexdigest()
    return digest, tuple(sorted(identities))


def _namespaces():
    result = {name: os.readlink('/proc/self/ns/' + name) for name in _NAMESPACES}
    if any(not re.fullmatch(name + r':\[[0-9]+\]', value) for name, value in result.items()):
        raise ValueError('host_namespaces_unverified')
    return result


def build_command(spec, host_namespaces):
    """Fixed executable/flags/probe only. Building does not invoke a process."""
    spec.validate()
    if (type(host_namespaces) is not dict or set(host_namespaces) != set(_NAMESPACES)
            or any(type(value) is not str or not re.fullmatch(name + r':\[[0-9]+\]', value)
                   for name, value in host_namespaces.items())):
        raise ValueError('invalid_namespace_binding')
    return [str(_BWRAP), '--unshare-all', '--disable-userns', '--die-with-parent',
            '--new-session', '--cap-drop', 'ALL', '--uid', str(spec.uid), '--gid', str(spec.gid),
            '--clearenv', '--ro-bind', spec.fixture_root + '/image', '/',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--perms', '0700', '--dir', '/tmp/agents-home', '--tmpfs', '/workspace',
            '--chdir', '/workspace', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp/agents-home', '--setenv', 'LANG', 'C.UTF-8',
            '--', '/usr/bin/python3', '-I', '-B', '-c', 'exec(' + repr(_PROBE) + ')',
            spec.attempt_id, str(spec.uid), str(spec.gid), json.dumps(host_namespaces, sort_keys=True)]


def _read_probe(process, timeout=10):
    deadline, output, total = time.monotonic() + timeout, bytearray(), 0
    streams = [process.stdout, process.stderr]
    while streams:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('probe_timeout')
        ready, _, _ = select.select(streams, [], [], remaining)
        if not ready:
            raise ValueError('probe_timeout')
        for stream in ready:
            chunk = os.read(stream.fileno(), 4096)
            total += len(chunk)
            if total > 16384:
                raise ValueError('probe_output_limit')
            if not chunk:
                streams.remove(stream)
            elif stream is process.stdout:
                output.extend(chunk)
                if len(output) > 4096:
                    raise ValueError('probe_output_limit')
                if b'\n' in output:
                    if not output.endswith(b'\n') or output.count(b'\n') != 1:
                        raise ValueError('probe_frame_rejected')
                    return json.loads(output.decode('ascii'), object_pairs_hook=_pairs)
    raise ValueError('probe_closed_before_report')


def _valid_probe(report, spec, host):
    if (type(report) is not dict
            or set(report) != {'schema', 'attempt_id', 'namespaces', 'workspace_identity', 'checks'}
            or report['schema'] != 'agents-no-network-probe-v1' or report['attempt_id'] != spec.attempt_id
            or type(report['checks']) is not dict or set(report['checks']) != set(_CHECKS)
            or any(value is not True for value in report['checks'].values())
            or type(report['workspace_identity']) is not list or len(report['workspace_identity']) != 2
            or any(type(value) is not int or value <= 0 for value in report['workspace_identity'])
            or type(report['namespaces']) is not dict or set(report['namespaces']) != set(host)):
        return False
    return all(type(value) is str and re.fullmatch(name + r':\[[0-9]+\]', value)
               and value != host[name] for name, value in report['namespaces'].items())


def _effective_limits(unit):
    if not re.fullmatch(r'modelfc-bounded-[a-f0-9]{32}\.service', unit):
        raise ValueError('invalid_unit_binding')
    expected = {'MemoryMax': '1073741824', 'TasksMax': '64', 'CPUQuotaPerSecUSec': '1s',
                'RuntimeMaxUSec': '3min', 'TimeoutStopUSec': '5s', 'KillMode': 'control-group',
                'SendSIGKILL': 'yes', 'NoNewPrivileges': 'yes', 'Delegate': 'no',
                'ProtectControlGroups': 'yes'}
    result = subprocess.run(['/usr/bin/systemctl', '--no-ask-password', 'show', unit,
                             '--property=' + ','.join(expected)],
                            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=5,
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if len(result.stdout) > 4096:
        raise ValueError('limit_readback_rejected')
    observed = {}
    for line in result.stdout.decode('ascii').splitlines():
        key, sep, value = line.partition('=')
        if not sep or key in observed:
            raise ValueError('limit_readback_rejected')
        observed[key] = value
    if observed != expected:
        raise ValueError('effective_limits_unverified')
    return observed


def _record(path, report):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as out:
        json.dump(report, out, sort_keys=True)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run_acceptance(spec):
    """Explicit real launch entrypoint. NOT authorized by import or unit tests.

    Retains a fresh exclusive attempt directory and a fixed-schema report. Never
    retries launch/stop or deletes evidence. Existing reservations fail closed.
    """
    spec.validate()
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError('root_acceptance_supervisor_required')
    root = protected_dir(spec.fixture_root, 0o755)
    parent = protected_dir(root / 'evidence', 0o700)
    image = root / 'image'
    inventory = inspect_image(image)
    protected_dir(_BWRAP.parent)
    bubblewrap = _file(_BWRAP, 16777216, modes=(0o755,))
    if inventory[0] != spec.image_sha256 or bubblewrap[0] != spec.bubblewrap_sha256:
        raise ValueError('acceptance_content_pin_mismatch')
    host = _namespaces()
    command = build_command(spec, host)
    attempt = parent / spec.attempt_id
    attempt.mkdir(mode=0o700)
    binding = {'attempt_id': spec.attempt_id, 'image_sha256': spec.image_sha256,
               'bubblewrap_sha256': spec.bubblewrap_sha256,
               'probe_sha256': hashlib.sha256(_PROBE.encode('ascii')).hexdigest(),
               'uid': spec.uid, 'gid': spec.gid, 'host_namespaces': host}
    _record(attempt / 'reservation.json', binding)
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    backend = SystemdContainment(attempt, spec.uid, spec.gid)
    backend.bind_attempt(attempt)
    report = {'schema': 'agents-no-network-acceptance-v1', **binding, 'unit': backend.unit,
              'result': 'blocked', 'ready_for_live': False, 'launch_confirmed': False,
              'probe_verified': False, 'stop_confirmed': False, 'cleanup_verified': False}
    process = None
    stage = 'input_revalidation'
    try:
        if inspect_image(image) != inventory or _file(_BWRAP, 16777216, modes=(0o755,)) != bubblewrap:
            raise ValueError('acceptance_inputs_changed')
        stage = 'launch'
        process = backend.launch(command, cwd='/', env={'PATH': '/usr/bin:/bin'},
                                 user=spec.uid, group=spec.gid, extra_groups=[], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True, bufsize=0)
        report['launch_confirmed'] = True
        stage = 'effective_limits'
        report['limits'] = _effective_limits(backend.unit)
        stage = 'probe'
        probe = _read_probe(process)
        if not _valid_probe(probe, spec, host):
            raise ValueError('probe_rejected')
        report['probe'] = probe
        report['probe_verified'] = True
    except Exception:
        report['error'] = 'acceptance_unverified'
        report['failure_stage'] = stage
    finally:
        try:
            report['stop_confirmed'] = backend.terminate() is True
        except Exception:
            report['stop_confirmed'] = False
        try:
            report['cleanup_verified'] = backend.verify_empty() is True
        except Exception:
            report['cleanup_verified'] = False
        try:
            unchanged = inspect_image(image) == inventory and _file(_BWRAP, 16777216, modes=(0o755,)) == bubblewrap
        except Exception:
            unchanged = False
        report['inputs_unchanged'] = unchanged
        if all(report[key] for key in ('launch_confirmed', 'probe_verified', 'stop_confirmed',
                                        'cleanup_verified', 'inputs_unchanged')):
            report['result'] = 'passed'
        process = process if process is not None else backend.process
        try:
            _record(attempt / 'acceptance.json', report)
        finally:
            if process is not None:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
    return report
