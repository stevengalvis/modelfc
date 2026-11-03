"""Static pre-exec verification of the service's effective mount namespace.

The service manager invokes this root-owned script before setpriv. It never
reads credential contents. A failed check exits without executing its child.
"""
import json
import os
from pathlib import Path
import stat
import sys


def _decode(value):
    import re
    if re.search(r'\\(?![0-7]{3})', value):
        raise ValueError('invalid_mountinfo')
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def _mounts(data):
    result = []
    for line in data.splitlines():
        left, sep, right = line.partition(' - ')
        fields, tail = left.split(), right.split()
        if not sep or len(fields) < 6 or len(tail) < 3:
            raise ValueError('invalid_mountinfo')
        result.append({'id': int(fields[0]), 'path': _decode(fields[4]), 'root': _decode(fields[3]),
                       'device': fields[2], 'options': fields[5].split(','), 'type': tail[0],
                       'super_options': tail[2].split(',')})
    return result


def _exact(mounts, path):
    matches = [m for m in mounts if m['path'] == path]
    if len(matches) != 1:
        raise ValueError('unexpected_mount_topology')
    return matches[0]


def verify(expected, mounts, stat_path, stat_vfs=os.statvfs):
    """Check only the declared topology; no credential bytes are opened."""
    if type(expected) is not dict or set(expected) != {
            'device', 'workspace', 'home', 'codex_home', 'tmp', 'var_tmp', 'shm', 'auth', 'config'}:
        raise ValueError('invalid_guard_expectation')
    root = _exact(mounts, '/')
    if 'ro' not in root['options']:
        raise ValueError('host_root_not_readonly')
    if 'ro' not in _exact(mounts, expected['home'])['options']:
        raise ValueError('home_not_readonly')
    bounded = (expected['workspace'], expected['codex_home'], expected['tmp'], expected['var_tmp'])
    if any(type(item) is not dict or set(item) != {'path', 'inode'} for item in bounded):
        raise ValueError('invalid_bounded_identity')
    paths = [item['path'] for item in bounded]
    if len(set(paths)) != len(paths):
        raise ValueError('duplicate_mount_destination')
    for item in bounded:
        path = item['path']
        mount = _exact(mounts, path)
        info = stat_path(path)
        device = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
        if (mount['device'] != expected['device'] or device != expected['device']
                or mount['type'] != 'ext4' or 'rw' not in mount['options']
                or any(option == 'discard' or option.startswith('discard=')
                       for option in mount['super_options'])
                or not stat.S_ISDIR(info.st_mode) or info.st_ino != item['inode']):
            raise ValueError('bounded_mount_mismatch')
    shm = _exact(mounts, expected['shm'])
    shm_vfs = stat_vfs(expected['shm'])
    if (shm['type'] != 'tmpfs' or not {'rw', 'nosuid', 'nodev', 'noexec'} <= set(shm['options'])
            or not 0 < shm_vfs.f_blocks * shm_vfs.f_frsize <= 8388608
            or shm_vfs.f_files > 4096 or shm_vfs.f_files <= 0):
        raise ValueError('invalid_shared_memory_mount')
    files = (expected['auth'], expected['config'])
    for item in files:
        if type(item) is not dict or set(item) != {'path', 'device', 'inode'}:
            raise ValueError('invalid_source_identity')
        info = stat_path(item['path'])
        mount = _exact(mounts, item['path'])
        if (not stat.S_ISREG(info.st_mode) or info.st_dev != item['device']
                or info.st_ino != item['inode'] or 'ro' not in mount['options']
                or mount['device'] != f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'):
            raise ValueError('readonly_source_mismatch')
    permitted = {item['path'] for item in files}
    allowed_writable = set(paths) | {expected['shm']}
    api_mounts = {('/dev/pts', 'devpts'),
                  ('/dev/mqueue', 'mqueue'), ('/proc', 'proc')}
    if 'ro' not in _exact(mounts, '/dev')['options']:
        raise ValueError('device_mount_not_readonly')
    dev_info = stat_path('/dev')
    if (not stat.S_ISDIR(dev_info.st_mode) or dev_info.st_uid != 0 or dev_info.st_mode & 0o022):
        raise ValueError('unprotected_device_directory')
    for mount in mounts:
        path = Path(mount['path'])
        if any(Path(p) in path.parents for p in paths) and str(path) not in permitted:
            raise ValueError('unexpected_nested_mount')
        # These known API mounts are exceptions, not complete IPC/device isolation.
        # A writable descendant, even below /dev or /proc, is otherwise unknown.
        if ('rw' in mount['options'] and str(path) not in allowed_writable
                and (str(path), mount['type']) not in api_mounts):
            raise ValueError('unexpected_writable_mount')


def verify_cwd(expected, mounts, cwd, fdinfo):
    path = expected['workspace']['path']
    ids = [line.split(':', 1)[1].strip() for line in fdinfo.splitlines()
           if line.startswith('mnt_id:')]
    if cwd != path or len(ids) != 1 or int(ids[0]) != _exact(mounts, path)['id']:
        raise ValueError('working_mount_mismatch')


def no_extra_descriptors():
    for value in os.listdir('/proc/self/fd'):
        number = int(value)
        if number <= 2:
            continue
        try:
            os.fstat(number)
        except OSError:
            # The descriptor used by listdir itself may already be closed.
            continue
        raise ValueError('unexpected_inherited_descriptor')


def main():
    stage = 'arguments'
    try:
        if os.getuid() != 0 or os.geteuid() != 0 or len(sys.argv) < 4 or sys.argv[2] != '--':
            raise ValueError('invalid_guard_arguments')
        stage = 'expectation'
        expected = json.loads(sys.argv[1])
        stage = 'descriptors'
        no_extra_descriptors()
        stage = 'mountinfo'
        mounts = _mounts(Path('/proc/self/mountinfo').read_text(encoding='utf-8'))
        stage = 'mounts'
        verify(expected, mounts, os.stat)
        stage = 'cwd'
        fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = Path('/proc/self/fdinfo/' + str(fd)).read_text(encoding='ascii')
            verify_cwd(expected, mounts, os.getcwd(), info)
        finally:
            os.close(fd)
        stage = 'exec'
        os.execv(sys.argv[3], sys.argv[3:])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        # Fixed codes only: stderr is shared with the untrusted worker.
        try:
            os.write(2, ('storage_guard:' + stage + '\n').encode('ascii'))
        except OSError:
            pass
        return 127
    return 127


if __name__ == '__main__':
    sys.exit(main())
