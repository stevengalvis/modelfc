"""Unwired test-only home mask; not a complete credential isolation proof.

This module is not called by the fixture or pre-exec gate. Operator-verified
credential locations and aliases outside the masked home remain unresolved.
"""
import os
from pathlib import Path
import re
import stat

from .codex_sandbox_supervisor_candidate import _read
from .storage_guard import _mounts


def home_mask(real_home, fixture_root, executable):
    """Build a fixed, bounded tmpfs mask for one canonical worker home."""
    if (type(real_home) is not str or not re.fullmatch(r'/home/[a-z_][a-z0-9_-]{0,31}', real_home)
            or type(fixture_root) is not str or not re.fullmatch(r'/srv/modelfc-zen69-fixture-[0-9a-f]{32}', fixture_root)
            or type(executable) is not str or not executable.startswith('/') or str(Path(executable)) != executable
            or '..' in Path(executable).parts):
        raise ValueError('invalid_home_mask')
    home = Path(real_home)
    for other in (Path(fixture_root), Path(executable)):
        if home == other or home in other.parents or other in home.parents:
            raise ValueError('overlapping_home_mask')
    return '--property=TemporaryFileSystem=' + real_home + ':ro,nosuid,nodev,noexec,size=1M,nr_inodes=1024,mode=0000'


def observe_home_mask(unit, real_home, network, host_identity, *, _proc_root=Path('/proc')):
    """Check a live gate's masked home without listing home contents."""
    if (type(unit) is not str or not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit)
            or type(real_home) is not str or not re.fullmatch(r'/home/[a-z_][a-z0-9_-]{0,31}', real_home)
            or type(network) is not dict or network.get('unit') != unit
            or type(network.get('main_pid')) is not int or network['main_pid'] <= 1
            or type(network.get('starttime')) is not int or network['starttime'] <= 0
            or type(network.get('invocation')) is not str
            or not re.fullmatch(r'[0-9a-f]{32}', network['invocation'])
            or type(host_identity) is not tuple or len(host_identity) != 2
            or any(type(value) is not int or value <= 0 for value in host_identity)):
        raise ValueError('home_mask_process_unverified')
    path = _proc_root / str(network['main_pid'])
    before = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(before) < 20 or before[0] in ('Z', 'X') or int(before[19]) != network['starttime']:
        raise ValueError('home_mask_process_changed')
    mounts = _mounts(_read(path / 'mountinfo', 8 * 1024 * 1024).decode('utf-8'))
    fd = os.open(path / 'root' / real_home.lstrip('/'),
                 os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info, vfs = os.fstat(fd), os.fstatvfs(fd)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0 or info.st_uid != 0 or info.st_gid != 0
                or (info.st_dev, info.st_ino) == host_identity):
            raise ValueError('home_mask_not_private')
        lines = _read(_proc_root / 'self/fdinfo' / str(fd), 4096).decode('ascii').splitlines()
        ids = [line.split(':', 1)[1].strip() for line in lines if line.startswith('mnt_id:')]
        if len(ids) != 1 or not ids[0].isdigit():
            raise ValueError('home_mask_mount_unverified')
        candidates = [item for item in mounts if item['id'] == int(ids[0])]
        if (len(candidates) != 1 or candidates[0]['path'] != real_home or candidates[0]['root'] != '/'
                or candidates[0]['type'] != 'tmpfs'
                or not {'ro', 'nosuid', 'nodev', 'noexec'} <= set(candidates[0]['options'])
                or 'rw' in candidates[0]['options'] or not vfs.f_flag & os.ST_RDONLY
                or not 0 < vfs.f_blocks * vfs.f_frsize <= 1048576 or not 0 < vfs.f_files <= 1024
                or vfs.f_ffree != vfs.f_files - 1):
            raise ValueError('home_mask_mount_unverified')
        mount_id = int(ids[0])
    finally:
        os.close(fd)
    after = _read(path / 'stat', 8192).decode('ascii').rsplit(')', 1)[1].split()
    if len(after) < 20 or after[0] in ('Z', 'X') or before[19] != after[19]:
        raise ValueError('home_mask_process_changed')
    return {'unit': unit, 'invocation': network['invocation'], 'main_pid': network['main_pid'],
            'starttime': network['starttime'], 'mount_id': mount_id}
