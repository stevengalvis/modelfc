"""Offline candidate for credential-free sandbox acceptance.

No command-line entrypoint or host acceptance is supplied. The test-only private
network does not block pathname UNIX socket relays; see the candidate notes.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
from dataclasses import replace
from types import SimpleNamespace

from . import launcher, storage_fixture
from .bridge_security import protected_bytes, protected_dir
from .codex import Codex
from .codex_credential_scope_candidate import TEST_ACCOUNT, load_scope
from .codex_sandbox_fixture_candidate import CandidateWorker, GATE_ARG, PROBE_ARG
from .storage import LinuxStorageReader
from .storage_fixture import cgroup_view_verified, independent_cleanup
from .storage_guard import _mounts
from .storage_fixture_prepare import prepare
from .systemd_backend import SystemdContainment
from .task import Task, _snapshot

FAKE_AUTH = b'{}\n'
FAKE_CONFIG = b'# disposable no-model fixture\n'
FAKE_INPUT_VERSION = 1
NETWORK_PROPERTY = '--property=PrivateNetwork=yes'
RUN_PROPERTY = '--property=TemporaryFileSystem=/run:ro,nosuid,nodev,noexec,size=1M,nr_inodes=1024,mode=0755'
RUN_BIND = '/run:/run:rbind'


def private_run_properties(properties):
    """Replace only the reviewed host /run self-bind; reject ambiguous maps."""
    if type(properties) not in (list, tuple) or any(type(value) is not str for value in properties):
        raise ValueError('invalid_diagnostic_properties')
    prefixes = ('--property=BindReadOnlyPaths=', '--property=BindPaths=')
    fixed = ('--property=PrivateMounts=yes', '--property=MountFlags=private',
             '--property=ReadOnlyPaths=/ /proc/sys', '--property=ReadWritePaths=/proc -/dev/pts -/dev/mqueue')
    dynamic = (*prefixes, '--property=TemporaryFileSystem=')
    if (len(properties) != 7 or any(properties.count(item) != 1 for item in fixed)
            or any(sum(value.startswith(prefix) for value in properties) != 1 for prefix in dynamic)):
        raise ValueError('invalid_diagnostic_properties')
    read_only = [value for value in properties if value.startswith(prefixes[0])]
    if len(read_only) != 1:
        raise ValueError('invalid_run_bind')
    tokens = read_only[0][len(prefixes[0]):].split(' ')
    if not tokens or '' in tokens or tokens.count(RUN_BIND) != 1:
        raise ValueError('invalid_run_bind')
    for value in properties:
        if value.startswith('--property=TemporaryFileSystem='):
            for token in value[len('--property=TemporaryFileSystem='):].split():
                if token.split(':', 1)[0] in ('/run', '-/run') or token.split(':', 1)[0].startswith(('/run/', '-/run/')):
                    raise ValueError('conflicting_run_mount')
        for prefix in prefixes:
            if value.startswith(prefix):
                for token in value[len(prefix):].split():
                    if token == RUN_BIND:
                        if prefix != prefixes[0] or value != read_only[0]:
                            raise ValueError('conflicting_run_mount')
                        continue
                    if any(part in ('/run', '-/run') or part.startswith(('/run/', '-/run/'))
                           for part in token.split(':')[:2]):
                        raise ValueError('conflicting_run_mount')
        if value.startswith(('--property=ReadWritePaths=', '--property=InaccessiblePaths=')):
            if any(part in ('/run', '-/run') or part.startswith(('/run/', '-/run/'))
                   for part in value.split('=', 2)[2].split()):
                raise ValueError('conflicting_run_mount')
    replacement = prefixes[0] + ' '.join(token for token in tokens if token != RUN_BIND)
    return [replacement if value == read_only[0] else value for value in properties] + [RUN_PROPERTY]


def _retain_exclusive(evidence, name, value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    fd = os.open(evidence / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise ValueError('diagnostic_record_write_failed')
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(evidence, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DiagnosticContainment(SystemdContainment):
    """Test-only private network; not the production service configuration."""
    def __init__(self, evidence, uid, gid):
        super().__init__(evidence, uid, gid)
        self.network_observation = None
        self.run_observation = None
        self.gate_observation = None

    def configure_storage(self, *, properties, expected, guard_path, revalidate):
        if any(value.startswith('--property=PrivateNetwork=') for value in properties):
            raise ValueError('network_property_conflict')
        super().configure_storage(properties=[*private_run_properties(properties), NETWORK_PROPERTY], expected=expected,
                                  guard_path=guard_path, revalidate=revalidate)

    def _record(self):
        super()._record()
        _retain_exclusive(self.evidence, 'diagnostic-network.json',
                          {'unit': self.unit, 'extra_property': NETWORK_PROPERTY,
                           'removed_bind': RUN_BIND, 'added_property': RUN_PROPERTY})

    def launch(self, *args, **kwargs):
        if (self.storage is None or self.storage[0].count(NETWORK_PROPERTY) != 1
                or self.storage[0].count(RUN_PROPERTY) != 1):
            raise ValueError('network_fence_missing')
        return super().launch(*args, **kwargs)

    def verify_gate(self, gated_args):
        self.network_observation = verify_private_network(self.unit)
        gate_identity = bind_live_gate(self.unit, self.uid, gated_args, self.network_observation)
        self.run_observation = verify_private_run(self.unit, self.network_observation)
        if (verify_private_network(self.unit) != self.network_observation
                or bind_live_gate(self.unit, self.uid, gated_args, self.network_observation) != gate_identity):
            raise ValueError('pre_exec_identity_changed')
        _retain_exclusive(self.evidence, 'diagnostic-network-observed.json', self.network_observation)
        _retain_exclusive(self.evidence, 'diagnostic-run-observed.json', self.run_observation)
        _retain_exclusive(self.evidence, 'diagnostic-gate-observed.json', gate_identity)
        self.gate_observation = gate_identity
        return True


class PreparedProbeWorker:
    """Connect a fixed probe to the backend actually selected by the launcher."""
    def __init__(self, pinned_binary, account, mode, credential_paths, revalidate_scope):
        self.pinned_binary, self.account, self.mode = pinned_binary, account, mode
        self.credential_paths, self.revalidate_scope = credential_paths, revalidate_scope
        self.worker = None
        self.supervisor = None

    def run(self, task, work, boundary):
        backend = boundary.backend
        if not isinstance(backend, DiagnosticContainment):
            raise ValueError('diagnostic_backend_required')
        self.backend = backend
        self.supervisor = FencedProbeSupervisor(backend, self.account.pw_uid, self.mode)
        self.worker = CandidateWorker(self.pinned_binary, self.account, self.mode, self.supervisor,
                                      self.credential_paths, self.revalidate_scope)
        return self.worker.run(task, work, boundary)


def fake_input_record(creation_sha256):
    """Bind the fixed fake bytes and version to a protected creation receipt."""
    if type(creation_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', creation_sha256):
        raise ValueError('invalid_creation_digest')
    value = {'version': FAKE_INPUT_VERSION, 'creation_sha256': creation_sha256,
             'auth_sha256': hashlib.sha256(FAKE_AUTH).hexdigest(),
             'config_sha256': hashlib.sha256(FAKE_CONFIG).hexdigest()}
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def retain_fake_input_record(evidence, creation_sha256):
    """Exclusively retain a version/content binding in a protected directory."""
    from .bridge_security import protected_dir
    directory = protected_dir(evidence, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = fake_input_record(creation_sha256)
        fd = os.open('codex-fixture-inputs.json', flags, 0o600, dir_fd=dirfd)
        try:
            if os.write(fd, raw) != len(raw):
                raise ValueError('short_input_record_write')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dirfd)
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(dirfd)


def verify_fake_input_record(raw, creation_sha256, digest):
    return (type(raw) is bytes and type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest) is not None
            and raw == fake_input_record(creation_sha256) and hashlib.sha256(raw).hexdigest() == digest)


def accepted_case(mode, result, inspected, baseline, before, after, *, cleanup, sources_match,
                  input_record_verified, credential_scope_verified, network_fence_verified, private_run_verified, gate_verified,
                  binary_verified, observed_unit_matches):
    """Fail closed unless launcher, independent and fixture checks all agree."""
    if (mode not in ('normal', 'interrupted') or type(result) is not dict or result != inspected
            or result.get('status') != ('completed' if mode == 'normal' else 'timeout')
            or type(baseline) is not dict or set(baseline) != {'status', 'directories'}
            or baseline['status'] != 'verified' or type(baseline['directories']) is not dict
            or type(before) is not dict or type(after) is not dict
            or not all(value is True for value in (cleanup, sources_match, input_record_verified, credential_scope_verified,
                network_fence_verified, private_run_verified, gate_verified, binary_verified, observed_unit_matches))):
        return False
    names = {'.agents', '.aws', '.codex'}
    if (set(baseline['directories']) != names or not names <= set(after) or names & set(before)
            or any(any(k.startswith(name + '/') for name in names) for k in after)):
        return False
    return before == {key: value for key, value in after.items() if key not in names}


def verify_private_run(unit, network, *, _proc_root=Path('/proc')):
    """Observe the resolved /run mount of the already verified service PID."""
    if (type(network) is not dict or network.get('unit') != unit
            or type(network.get('main_pid')) is not int or network['main_pid'] <= 1):
        raise ValueError('private_run_process_unverified')
    path = _proc_root / str(network['main_pid'])
    before = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(before) < 20 or before[0] in ('Z', 'X') or int(before[19]) != network.get('starttime'):
        raise ValueError('private_run_process_changed')
    mountinfo = _mounts(_read(path / 'mountinfo', 8 * 1024 * 1024).decode('utf-8'))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path / 'root/run', flags)
    try:
        if os.listdir(fd):
            raise ValueError('private_run_not_empty')
        fdinfo = _read(_proc_root / 'self/fdinfo' / str(fd), 4096).decode('ascii').splitlines()
        ids = [line.split(':', 1)[1].strip() for line in fdinfo if line.startswith('mnt_id:')]
        if len(ids) != 1 or not ids[0].isdigit():
            raise ValueError('private_run_mount_unverified')
        candidates = [item for item in mountinfo if item['id'] == int(ids[0])]
        vfs = os.fstatvfs(fd)
        if (len(candidates) != 1 or candidates[0]['path'] != '/run' or candidates[0]['type'] != 'tmpfs'
                or candidates[0]['root'] != '/'
                or not {'ro', 'nosuid', 'nodev', 'noexec'} <= set(candidates[0]['options'])
                or 'rw' in candidates[0]['options'] or not vfs.f_flag & os.ST_RDONLY
                or not 0 < vfs.f_blocks * vfs.f_frsize <= 1048576
                or not 0 < vfs.f_files <= 1024):
            raise ValueError('private_run_mount_unverified')
        mount_id = int(ids[0])
    finally:
        os.close(fd)
    after = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(after) < 20 or after[0] in ('Z', 'X') or before[19] != after[19]:
        raise ValueError('private_run_process_changed')
    return {'unit': unit, 'invocation': network['invocation'], 'main_pid': network['main_pid'],
            'starttime': network['starttime'], 'mount_id': mount_id}


def bind_live_gate(unit, uid, gated_args, network, *, _proc_root=Path('/proc'), _python=Path('/usr/bin/python3')):
    """Bind the fixed waiting gate to the live manager PID before release."""
    fixed = ['/usr/bin/python3', '-I', '-S', '-B', '-c', GATE_ARG]
    if (type(gated_args) is not list or len(gated_args) != 7 or gated_args[:6] != fixed
            or type(gated_args[6]) is not str or len(gated_args[6]) > 16384
            or type(uid) is not int or uid <= 0 or type(network) is not dict
            or network.get('unit') != unit or type(network.get('main_pid')) is not int
            or network['main_pid'] <= 1):
        raise ValueError('pre_exec_gate_identity_unverified')
    path = _proc_root / str(network['main_pid'])
    before = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(before) < 20 or before[0] in ('Z', 'X') or int(before[19]) != network.get('starttime'):
        raise ValueError('pre_exec_gate_identity_unverified')
    status = _read(path / 'status', 16384).decode('ascii').splitlines()
    uids = [line.split(':', 1)[1].split() for line in status if line.startswith('Uid:')]
    if (len(uids) != 1 or len(uids[0]) != 4
            or any(not value.isdigit() or int(value) != uid for value in uids[0])):
        raise ValueError('pre_exec_gate_identity_unverified')
    groups = _read(path / 'cgroup', 8192).decode('ascii').splitlines()
    group = '0::/system.slice/' + unit
    if len(groups) != 1 or not (groups[0] == group or groups[0].startswith(group + '/')):
        raise ValueError('pre_exec_gate_identity_unverified')
    expected = _python.stat()
    actual = os.stat(path / 'exe')
    if (not stat.S_ISREG(expected.st_mode) or expected.st_uid != 0 or expected.st_mode & 0o022
            or (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino)):
        raise ValueError('pre_exec_gate_identity_unverified')
    cmdline = _read(path / 'cmdline', 32768)
    if cmdline != b'\0'.join(value.encode('utf-8') for value in gated_args) + b'\0':
        raise ValueError('pre_exec_gate_identity_unverified')
    after = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(after) < 20 or after[0] in ('Z', 'X') or after[19] != before[19]:
        raise ValueError('pre_exec_gate_identity_unverified')
    return {'unit': unit, 'invocation': network['invocation'], 'main_pid': network['main_pid'],
            'starttime': network['starttime'], 'executable': [expected.st_dev, expected.st_ino]}


def verify_private_network(unit, *, _proc_root=Path('/proc'), _run=subprocess.run):
    """Observe the outer service namespace; pathname UNIX sockets remain a risk."""
    if type(unit) is not str or not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit):
        raise ValueError('invalid_probe_unit')
    cmd = ['/usr/bin/systemctl', '--no-ask-password', 'show', unit, '--all',
           '--property=PrivateNetwork,MainPID,InvocationID,ActiveState']
    def read_manager():
        result = _run(cmd, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True, text=True)
        fields = {}
        for line in result.stdout.splitlines():
            key, sep, value = line.partition('=')
            if not sep or key in fields:
                raise ValueError('network_manager_unverified')
            fields[key] = value
        if (set(fields) != {'PrivateNetwork', 'MainPID', 'InvocationID', 'ActiveState'}
                or fields['PrivateNetwork'] != 'yes' or fields['ActiveState'] != 'active'
                or not fields['MainPID'].isdigit() or int(fields['MainPID']) <= 1
                or not re.fullmatch(r'[0-9a-f]{32}', fields['InvocationID'])):
            raise ValueError('network_manager_unverified')
        return fields
    manager = read_manager()
    pid = int(manager['MainPID'])
    path = _proc_root / str(pid)
    before = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(before) < 20 or before[0] in ('Z', 'X'):
        raise ValueError('network_process_unverified')
    group = _read(path / 'cgroup', 8192).decode('ascii').splitlines()
    expected = '0::/system.slice/' + unit
    if len(group) != 1 or not (group[0] == expected or group[0].startswith(expected + '/')):
        raise ValueError('network_cgroup_mismatch')
    ours, host = os.stat(path / 'ns/net'), os.stat(_proc_root / '1/ns/net')
    if (ours.st_dev, ours.st_ino) == (host.st_dev, host.st_ino):
        raise ValueError('network_namespace_shared')
    lines = _read(path / 'net/dev', 16384).decode('ascii').splitlines()
    if len(lines) < 2 or any(':' not in line for line in lines[2:]):
        raise ValueError('network_interfaces_unverified')
    interfaces = [line.split(':', 1)[0].strip() for line in lines[2:]]
    if any(name != 'lo' for name in interfaces) or len(interfaces) > 1:
        raise ValueError('network_external_interface')
    after = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if (len(after) < 20 or after[0] in ('Z', 'X') or before[19] != after[19]
            or read_manager() != manager):
        raise ValueError('network_process_changed')
    return {'unit': unit, 'invocation': manager['InvocationID'], 'main_pid': pid,
            'starttime': int(before[19]), 'net_namespace': [ours.st_dev, ours.st_ino]}


def prepare_candidate(root, username, uid, gid, parent_device, parent_inode, lock_device, lock_inode):
    """Use the existing root, identity, lock and worker admission checks."""
    return prepare(root, username, uid, gid, parent_device, parent_inode, lock_device, lock_inode,
                   _auth=FAKE_AUTH, _config=FAKE_CONFIG)


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        result = bytearray()
        while True:
            part = os.read(fd, maximum + 1 - len(result))
            if not part:
                return bytes(result)
            result.extend(part)
            if len(result) > maximum:
                raise ValueError('process_observation_oversized')
    finally:
        os.close(fd)


def _cgroup_pids(root):
    pending, pids, seen = [root], set(), 0
    while pending:
        directory = pending.pop()
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('cgroup_view_invalid')
        seen += 1
        if seen > 128:
            raise ValueError('cgroup_view_oversized')
        raw = _read(directory / 'cgroup.procs', 8192).decode('ascii')
        for line in raw.splitlines():
            if not line.isdigit() or int(line) <= 1:
                raise ValueError('cgroup_pid_invalid')
            pids.add(int(line))
        if len(pids) > 64:
            raise ValueError('cgroup_pid_limit')
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError('cgroup_symlink_rejected')
                if entry.is_dir(follow_symlinks=False):
                    pending.append(directory / entry.name)
    return pids


def _process(pid, uid, unit, local_pid, executable, mode, proc_root):
    path = proc_root / str(pid)
    raw = _read(path / 'stat', 8192).decode('ascii')
    fields = raw.rsplit(')', 1)[1].split()
    if len(fields) < 20 or fields[0] in ('Z', 'X'):
        raise ValueError('probe_process_not_live')
    start = int(fields[19])
    status = _read(path / 'status', 16384).decode('ascii').splitlines()
    def one(prefix):
        lines = [s.split(':', 1)[1].split() for s in status if s.startswith(prefix + ':')]
        if len(lines) != 1:
            raise ValueError('process_status_unverified')
        return lines[0]
    uids, nspids = one('Uid'), one('NSpid')
    if len(uids) != 4 or any(not s.isdigit() or int(s) != uid for s in uids):
        return None
    if not nspids or any(not s.isdigit() for s in nspids) or int(nspids[-1]) != local_pid:
        return None
    group = _read(path / 'cgroup', 8192).decode('ascii').splitlines()
    expected = '0::/system.slice/' + unit
    if len(group) != 1 or not (group[0] == expected or group[0].startswith(expected + '/')):
        raise ValueError('probe_cgroup_mismatch')
    actual_exe = os.stat(path / 'exe')
    if (actual_exe.st_dev, actual_exe.st_ino) != executable:
        return None
    args = _read(path / 'cmdline', 16384).split(b'\0')
    expected_args = [b'/usr/bin/python3', b'-I', b'-S', b'-B', b'-c', PROBE_ARG.encode(), mode.encode(), b'']
    if args != expected_args:
        return None
    ns = os.stat(path / 'ns/pid')
    if start <= 0 or ns.st_dev <= 0 or ns.st_ino <= 0:
        raise ValueError('process_identity_invalid')
    # Re-read identity before accepting the observation to reject exit/reuse.
    again = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(again) < 20 or again[0] in ('Z', 'X') or int(again[19]) != start:
        raise ValueError('process_identity_changed')
    return (pid, start, ns.st_dev, ns.st_ino)


def bind_live_probe(unit, uid, local_pid, mode, *, _group_root=Path('/sys/fs/cgroup/system.slice'),
                    _proc_root=Path('/proc'), _python=Path('/usr/bin/python3'), _verify=cgroup_view_verified):
    if (not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit)
            or type(uid) is not int or uid <= 0 or type(local_pid) is not int or local_pid <= 0
            or mode not in ('normal', 'interrupted') or not _verify()):
        raise ValueError('probe_observation_unverified')
    info = _python.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('probe_python_unprotected')
    pids = _cgroup_pids(_group_root / unit)
    found = [result for pid in sorted(pids)
             if (result := _process(pid, uid, unit, local_pid, (info.st_dev, info.st_ino), mode, _proc_root))]
    if len(found) != 1 or pids != _cgroup_pids(_group_root / unit):
        raise ValueError('probe_process_ambiguous')
    return found[0]


class ProbeSupervisor:
    """Bind observed readiness to one unit and require independent cleanup."""
    def __init__(self, unit, uid, mode):
        if (type(unit) is not str or not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit)
                or type(uid) is not int or uid <= 0 or mode not in ('normal', 'interrupted')):
            raise ValueError('invalid_probe_supervisor')
        self.unit, self.uid, self.mode = unit, uid, mode
        self.local_pid = None
        self.identity = None

    def observe(self, local_pid):
        if self.local_pid is not None and self.local_pid != local_pid:
            raise ValueError('probe_identity_changed')
        identity = bind_live_probe(self.unit, self.uid, local_pid, self.mode)
        if self.identity is not None and self.identity != identity:
            raise ValueError('probe_identity_changed')
        self.local_pid, self.identity = local_pid, identity
        return identity

    def cleanup_verified(self):
        if self.identity is None:
            return False
        return independent_cleanup(self.unit, self.identity[0]) is True


class FencedProbeSupervisor(ProbeSupervisor):
    def __init__(self, backend, uid, mode):
        super().__init__(backend.unit, uid, mode)
        self.backend = backend
        self.network_verified = False

    def observe(self, local_pid):
        if (self.backend.network_observation is None
                or verify_private_network(self.unit) != self.backend.network_observation
                or self.backend.run_observation is None
                or verify_private_run(self.unit, self.backend.network_observation) != self.backend.run_observation):
            raise ValueError('network_fence_changed')
        identity = super().observe(local_pid)
        self.network_verified = True
        return identity


def run_candidate(root, username, loop, inventory_path, inventory_sha256, executable, executable_sha256, mode, *,
                  credential_scope_path, credential_scope_sha256, scope_evidence_path):
    """Run exactly one reviewed fake fixture; no model, retries or publication.

    This is an offline candidate, not an approved host procedure. PrivateNetwork
    does not block filesystem UNIX socket relays; the operator must separately
    establish the trusted-host no-relay assumption before any host test.
    """
    if (os.getuid() != 0 or os.geteuid() != 0 or mode not in ('normal', 'interrupted')
            or not re.fullmatch(r'/dev/loop[0-9]+', loop)):
        raise ValueError('candidate_request_rejected')
    root = storage_fixture.checked_root(str(root))
    observed = storage_fixture.verify_topology(root, LinuxStorageReader().mountinfo())
    manifest, lock, expected_worker, creation_sources, component = storage_fixture.load_manifest(
        root, inventory_path, inventory_sha256)
    if observed != manifest['mount_id']:
        raise ValueError('fixture_mount_identity_mismatch')
    with storage_fixture.hold_bridge_lock(lock):
        storage_fixture.require_no_worker_units()
        account = pwd.getpwnam(username)
        if (username != TEST_ACCOUNT or username != expected_worker['username'] or account.pw_uid != expected_worker['uid']
                or account.pw_gid != expected_worker['gid'] or account.pw_uid <= 0 or account.pw_gid <= 0):
            raise ValueError('fixture_worker_required')
        production_account = pwd.getpwnam('zeno-worker')
        if [item.pw_name for item in pwd.getpwall() if item.pw_uid == account.pw_uid] != [TEST_ACCOUNT]:
            raise ValueError('fixture_worker_uid_not_unique')
        storage_fixture.require_no_worker_processes(account.pw_uid)
        storage_fixture.require_no_worker_processes(production_account.pw_uid)
        def revalidate_scope():
            return load_scope(credential_scope_path, credential_scope_sha256, scope_evidence_path,
                              account, production_account)
        credential_paths = revalidate_scope()
        config, identity, sources = storage_fixture.fixture_configuration(
            root, account, manifest, creation_sources, component, _auth=FAKE_AUTH, _config=FAKE_CONFIG)
        loop_info = os.stat(loop, follow_symlinks=False)
        if (not stat.S_ISBLK(loop_info.st_mode)
                or f'{os.major(loop_info.st_rdev)}:{os.minor(loop_info.st_rdev)}' != identity['device']):
            raise ValueError('fixture_loop_mismatch')
        binary = Codex(username, executable, executable_sha256)
        binary_pin = binary._binary()
        config = replace(config, executable=executable, executable_sha256=executable_sha256)
        creation_sha = hashlib.sha256(protected_bytes(root / 'fixture-creation.json', maximum=16384)).hexdigest()
        input_digest = retain_fake_input_record(root / 'evidence', creation_sha)
        protected_dir(root / 'evidence', mode=0o700)
        scope_record = {'version': 1, 'scope_sha256': credential_scope_sha256,
                        'test_uid': account.pw_uid, 'test_gid': account.pw_gid}
        _retain_exclusive(root / 'evidence', 'codex-credential-scope.json', scope_record)
        base = storage_fixture.create_checkout(Path(config.workspace_root), identity, account.pw_uid,
                                               account.pw_gid, root / 'home')
        work = Path(config.workspace_root) / storage_fixture.TASK_ID
        before = _snapshot(work)
        task = Task(storage_fixture.TASK_ID, 'stevengalvis/modelfc', base, 'no-model sandbox fixture', ('fixture.txt',))
        fake_account = SimpleNamespace(pw_uid=account.pw_uid, pw_gid=account.pw_gid, pw_dir=str(root / 'home'))
        worker = PreparedProbeWorker(binary, fake_account, mode, credential_paths, revalidate_scope)
        result = inspected = None
        error = None
        try:
            result = launcher.submit(task, config, _worker_factory=lambda *_: worker,
                                     _backend_factory=DiagnosticContainment)
            inspected = launcher.inspect(task.task_id, config, _worker_factory=lambda *_: worker)
        except Exception as exc:
            error = type(exc).__name__
        cleanup = final_inspection = sources_match = record_ok = scope_ok = binary_ok = unit_ok = network_ok = run_ok = gate_ok = False
        baseline, after = None, None
        try:
            control = worker.supervisor
            cleanup = control is not None and control.cleanup_verified()
            final_inspection = bool(cleanup and storage_fixture.reinspect_after_cleanup(root, config, identity, worker, result))
            unit_file = launcher._read(Path(config.evidence_root) / task.task_id / 'attempt/containment.json')
            unit_ok = control is not None and unit_file.get('unit') == control.unit == worker.backend.unit
            network_record = launcher._read(Path(config.evidence_root) / task.task_id / 'attempt/diagnostic-network.json')
            network_observed = launcher._read(Path(config.evidence_root) / task.task_id / 'attempt/diagnostic-network-observed.json')
            run_observed = launcher._read(Path(config.evidence_root) / task.task_id / 'attempt/diagnostic-run-observed.json')
            gate_observed = launcher._read(Path(config.evidence_root) / task.task_id / 'attempt/diagnostic-gate-observed.json')
            network_ok = (control is not None and control.network_verified is True
                          and network_record == {'unit': control.unit, 'extra_property': NETWORK_PROPERTY,
                                                 'removed_bind': RUN_BIND, 'added_property': RUN_PROPERTY}
                          and type(network_observed) is dict and network_observed.get('unit') == control.unit
                          and network_observed == worker.backend.network_observation)
            run_ok = (control is not None and type(run_observed) is dict and run_observed.get('unit') == control.unit
                      and run_observed == worker.backend.run_observation)
            gate_ok = (control is not None and type(gate_observed) is dict and gate_observed.get('unit') == control.unit
                       and gate_observed == worker.backend.gate_observation
                       and type(network_observed) is dict
                       and gate_observed.get('invocation') == network_observed.get('invocation'))
            sources_match = sources == storage_fixture.fake_sources(root / 'home', account.pw_uid,
                manifest['sources'], creation_sources, _auth=FAKE_AUTH, _config=FAKE_CONFIG)
            record_ok = verify_fake_input_record(protected_bytes(root / 'evidence/codex-fixture-inputs.json'),
                                                 creation_sha, input_digest)
            scope_ok = (launcher._read(root / 'evidence/codex-credential-scope.json') == scope_record
                        and revalidate_scope() == credential_paths)
            binary_ok = binary._binary() == binary_pin
            baseline = launcher._read(Path(config.evidence_root) / task.task_id / 'attempt/baseline.json')
            after = _snapshot(work)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            pass
        finally:
            child = getattr(worker.worker, 'child', None)
            if child is not None:
                for pipe in (child.stdin, child.stdout, child.stderr):
                    if pipe is not None:
                        pipe.close()
        passed = (error is None and final_inspection and accepted_case(
            mode, result, inspected, baseline, before, after, cleanup=cleanup, sources_match=sources_match,
            input_record_verified=record_ok, credential_scope_verified=scope_ok,
            network_fence_verified=network_ok, private_run_verified=run_ok,
            gate_verified=gate_ok, binary_verified=binary_ok,
            observed_unit_matches=unit_ok))
        return {'status': 'passed' if passed else 'blocked', 'mode': mode, 'result': result,
                'independent_cleanup': 'verified' if cleanup else 'unverified',
                'post_cleanup_reinspection': 'verified' if final_inspection else 'unverified',
                'input_record_sha256': input_digest, 'test_only_network_property': NETWORK_PROPERTY,
                'error_class': error}
