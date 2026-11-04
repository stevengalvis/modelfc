"""Unwired helpers for a credential-free sandbox acceptance candidate.

Not an operator entrypoint. Host execution, fixture setup, independent cleanup,
network isolation and acceptance orchestration are deliberately not implemented.
"""
import os
from pathlib import Path
import re
import stat

from .codex_sandbox_fixture_candidate import PROBE_ARG
from .storage_fixture import cgroup_view_verified, independent_cleanup
from .storage_fixture_prepare import prepare

FAKE_AUTH = b'{}\n'
FAKE_CONFIG = b'# disposable no-model fixture\n'
FAKE_INPUT_VERSION = 1


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
