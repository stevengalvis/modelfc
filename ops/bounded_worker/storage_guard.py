"""Static pre-exec verification of the service's effective mount namespace.

The service manager invokes this root-owned script before setpriv. It never
reads credential contents. A failed check exits without executing its child.
"""
import json
import os
from pathlib import Path
import platform
import re
import select
import signal
import stat
import sys
import threading
import time


class GuardFailure(ValueError):
    """A fixed internal guard predicate, never external exception text."""


def _decode(value):
    import re
    if re.search(r'\\(?![0-7]{3})', value):
        raise GuardFailure('invalid_mountinfo')
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def _mounts(data):
    result = []
    for line in data.splitlines():
        left, sep, right = line.partition(' - ')
        fields, tail = left.split(), right.split()
        if not sep or len(fields) < 6 or len(tail) < 3:
            raise GuardFailure('invalid_mountinfo')
        result.append({'id': int(fields[0]), 'path': _decode(fields[4]), 'root': _decode(fields[3]),
                       'device': fields[2], 'options': fields[5].split(','), 'type': tail[0],
                       'super_options': tail[2].split(',')})
    return result


def _exact(mounts, path):
    matches = [m for m in mounts if m['path'] == path]
    if len(matches) != 1:
        raise GuardFailure('unexpected_mount_topology')
    return matches[0]


def _observe_mounts():
    """Select the mount IDs resolved in this private, trusted-host namespace."""
    import ctypes
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise GuardFailure('unsupported_mount_observation')
    class OpenHow(ctypes.Structure):
        _fields_ = [('flags', ctypes.c_uint64), ('mode', ctypes.c_uint64), ('resolve', ctypes.c_uint64)]
    def snapshot():
        with open('/proc/thread-self/mountinfo', 'rb') as source:
            data = source.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise GuardFailure('invalid_mountinfo')
        text = data.decode('utf-8')
        for line in text.splitlines():
            left, sep, _ = line.partition(' - ')
            fields = left.split()
            if (not sep or len(fields) < 6 or any(
                    field.startswith(('shared:', 'master:', 'propagate_from:')) for field in fields[6:])):
                raise GuardFailure('mount_propagation_unverified')
        return data, _mounts(text)
    deadline = time.monotonic() + 10
    before_raw, before = snapshot()
    # Never trigger an automount while inspecting mountpoint paths.
    if any(m['type'] == 'autofs' for m in before):
        raise GuardFailure('mount_observation_failed')
    paths = {m['path'] for m in before}
    ids = {m['id']: m for m in before}
    if (not before or len(before) != len(ids) or len(paths) > 4096 or '/' not in paths
            or any(m['id'] <= 0 for m in before)
            or any(not p.startswith('/') or p.startswith('//') or len(p) > 4096
                   or str(Path(p)) != p or '..' in Path(p).parts
                   or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in paths)):
        raise GuardFailure('invalid_mountinfo')
    root_fd = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    proc_fd = None
    observed = {}
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        proc_fd = os.open('/proc', os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        def verify_proc():
            data = ctypes.create_string_buffer(256)
            if (libc.fstatfs(proc_fd, ctypes.byref(data)) != 0
                    or ctypes.cast(data, ctypes.POINTER(ctypes.c_long)).contents.value != 0x9fa0
                    or os.readlink('thread-self', dir_fd=proc_fd) !=
                    f'{os.getpid()}/task/{threading.get_native_id()}'):
                raise GuardFailure('mount_observation_failed')
            original, current = os.fstat(proc_fd), os.stat('/proc', follow_symlinks=False)
            if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                raise GuardFailure('mount_observation_changed')
        verify_proc()
        how = OpenHow(os.O_PATH | os.O_CLOEXEC, 0, 0x10 | 0x04 | 0x02)
        for path in sorted(paths):
            if time.monotonic() >= deadline:
                raise GuardFailure('mount_observation_timeout')
            fd = libc.syscall(437, root_fd, os.fsencode(path), ctypes.byref(how), ctypes.sizeof(how))
            if fd < 0:
                raise GuardFailure('mount_observation_failed')
            try:
                with open('/proc/thread-self/fdinfo/' + str(fd), 'rb') as source:
                    info = source.read(65537)
                matches = re.findall(rb'^mnt_id:[ \t]*([0-9]{1,10})[ \t]*$', info, re.MULTILINE)
                if len(info) > 65536 or len(matches) != 1:
                    raise GuardFailure('mount_observation_failed')
                observed[path] = int(matches[0])
            finally:
                os.close(fd)
        after_raw, _ = snapshot()
        verify_proc()
        if before_raw != after_raw or time.monotonic() >= deadline:
            raise GuardFailure('mount_observation_changed')
        for path, identity in observed.items():
            if identity not in ids:
                raise GuardFailure('mount_observation_failed')
            parent = ids[identity]['path']
            if ((path != parent and Path(parent) not in Path(path).parents)
                    or observed.get(parent) != identity):
                raise GuardFailure('mount_observation_failed')
        return [m for m in before if observed[m['path']] == m['id']]
    finally:
        if proc_fd is not None:
            os.close(proc_fd)
        os.close(root_fd)


_OBSERVATION_TIMEOUT = 8.0
_OBSERVATION_MAX_RESULT = 16 * 1024 * 1024


def _effective_mounts():
    """Keep potentially blocking filesystem lookups outside the guard process.

    A child stuck in uninterruptible kernel sleep may outlive this timeout; the
    service cgroup must still be stopped and independently verified empty.
    """
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        pid = os.fork()
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    if pid == 0:
        os.close(read_fd)
        try:
            payload = json.dumps(_observe_mounts(), separators=(',', ':')).encode('utf-8')
            if len(payload) > _OBSERVATION_MAX_RESULT:
                os._exit(1)
            view = memoryview(payload)
            deadline = time.monotonic() + _OBSERVATION_TIMEOUT
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [write_fd], [], remaining)[1]:
                    os._exit(1)
                try:
                    count = os.write(write_fd, view)
                except BlockingIOError:
                    continue
                if count <= 0:
                    os._exit(1)
                view = view[count:]
            os.close(write_fd)
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write_fd)
    reaped = False
    try:
        deadline = time.monotonic() + _OBSERVATION_TIMEOUT
        result = bytearray()
        eof = False
        status = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GuardFailure('mount_observation_timeout')
            if not eof:
                ready = select.select([read_fd], [], [], min(remaining, 0.05))[0]
                if ready:
                    try:
                        chunk = os.read(read_fd, 65536)
                    except BlockingIOError:
                        chunk = None
                    if chunk == b'':
                        eof = True
                    elif chunk:
                        result.extend(chunk)
                        if len(result) > _OBSERVATION_MAX_RESULT:
                            raise GuardFailure('mount_observation_failed')
            else:
                time.sleep(min(remaining, 0.01))
            if status is None:
                finished, child_status = os.waitpid(pid, os.WNOHANG)
                if finished == pid:
                    reaped = True
                    status = child_status
            if eof and status is not None:
                break
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise GuardFailure('mount_observation_failed')
        try:
            value = json.loads(result)
        except (ValueError, UnicodeError):
            raise GuardFailure('mount_observation_failed') from None
        if type(value) is not list:
            raise GuardFailure('mount_observation_failed')
        return value
    finally:
        os.close(read_fd)
        if not reaped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                reap_until = time.monotonic() + 0.1
                while time.monotonic() < reap_until:
                    finished, _ = os.waitpid(pid, os.WNOHANG)
                    if finished == pid:
                        break
                    time.sleep(0.005)
            except ChildProcessError:
                pass


def verify(expected, mounts, stat_path, stat_vfs=os.statvfs):
    """Check only the declared topology; no credential bytes are opened."""
    if type(expected) is not dict or set(expected) != {
            'device', 'workspace', 'home', 'codex_home', 'tmp', 'var_tmp', 'shm', 'auth', 'config'}:
        raise GuardFailure('invalid_guard_expectation')
    root = _exact(mounts, '/')
    if 'ro' not in root['options']:
        raise GuardFailure('host_root_not_readonly')
    if 'ro' not in _exact(mounts, expected['home'])['options']:
        raise GuardFailure('home_not_readonly')
    bounded = (expected['workspace'], expected['codex_home'], expected['tmp'], expected['var_tmp'])
    if any(type(item) is not dict or set(item) != {'path', 'inode'} for item in bounded):
        raise GuardFailure('invalid_bounded_identity')
    paths = [item['path'] for item in bounded]
    if len(set(paths)) != len(paths):
        raise GuardFailure('duplicate_mount_destination')
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
            raise GuardFailure('bounded_mount_mismatch')
    shm = _exact(mounts, expected['shm'])
    shm_vfs = stat_vfs(expected['shm'])
    if (shm['type'] != 'tmpfs' or not {'rw', 'nosuid', 'nodev', 'noexec'} <= set(shm['options'])
            or not 0 < shm_vfs.f_blocks * shm_vfs.f_frsize <= 8388608
            or shm_vfs.f_files > 4096 or shm_vfs.f_files <= 0):
        raise GuardFailure('invalid_shared_memory_mount')
    files = (expected['auth'], expected['config'])
    for item in files:
        if type(item) is not dict or set(item) != {'path', 'device', 'inode'}:
            raise GuardFailure('invalid_source_identity')
        info = stat_path(item['path'])
        mount = _exact(mounts, item['path'])
        if (not stat.S_ISREG(info.st_mode) or info.st_dev != item['device']
                or info.st_ino != item['inode'] or 'ro' not in mount['options']
                or mount['device'] != f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'):
            raise GuardFailure('readonly_source_mismatch')
    permitted = {item['path'] for item in files}
    allowed_writable = set(paths) | {expected['shm']}
    api_mounts = {('/dev/pts', 'devpts'),
                  ('/dev/mqueue', 'mqueue'), ('/proc', 'proc')}
    if 'ro' not in _exact(mounts, '/dev')['options']:
        raise GuardFailure('device_mount_not_readonly')
    dev_info = stat_path('/dev')
    if (not stat.S_ISDIR(dev_info.st_mode) or dev_info.st_uid != 0 or dev_info.st_mode & 0o022):
        raise GuardFailure('unprotected_device_directory')
    for mount in mounts:
        path = Path(mount['path'])
        if any(Path(p) in path.parents for p in paths) and str(path) not in permitted:
            raise GuardFailure('unexpected_nested_mount')
        # These known API mounts are exceptions, not complete IPC/device isolation.
        # A writable descendant, even below /dev or /proc, is otherwise unknown.
        if ('rw' in mount['options'] and str(path) not in allowed_writable
                and (str(path), mount['type']) not in api_mounts):
            raise GuardFailure('unexpected_writable_mount')


def verify_cwd(expected, mounts, cwd, fdinfo):
    path = expected['workspace']['path']
    ids = [line.split(':', 1)[1].strip() for line in fdinfo.splitlines()
           if line.startswith('mnt_id:')]
    if cwd != path or len(ids) != 1 or int(ids[0]) != _exact(mounts, path)['id']:
        raise GuardFailure('working_mount_mismatch')


def no_extra_descriptors():
    for number in (0, 1, 2):
        if not stat.S_ISFIFO(os.fstat(number).st_mode):
            raise GuardFailure('invalid_standard_descriptor')
    for value in os.listdir('/proc/self/fd'):
        number = int(value)
        if number <= 2:
            continue
        try:
            os.fstat(number)
        except OSError:
            # The descriptor used by listdir itself may already be closed.
            continue
        raise GuardFailure('unexpected_inherited_descriptor')


def main():
    stage = 'arguments'
    try:
        if os.getuid() != 0 or os.geteuid() != 0 or len(sys.argv) < 4 or sys.argv[2] != '--':
            raise GuardFailure('invalid_guard_arguments')
        stage = 'expectation'
        expected = json.loads(sys.argv[1])
        stage = 'descriptors'
        no_extra_descriptors()
        stage = 'mountinfo'
        mounts = _effective_mounts()
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
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Only our own finite predicate constants may be emitted; stderr is
        # shared with the untrusted worker and must not be treated as proof.
        predicates = {'invalid_guard_expectation', 'unexpected_mount_topology',
                      'host_root_not_readonly', 'home_not_readonly', 'invalid_bounded_identity',
                      'duplicate_mount_destination', 'bounded_mount_mismatch',
                      'invalid_shared_memory_mount', 'invalid_source_identity',
                      'readonly_source_mismatch', 'device_mount_not_readonly',
                      'unprotected_device_directory', 'unexpected_nested_mount',
                      'unexpected_writable_mount'}
        reason = exc.args[0] if type(exc) is GuardFailure and len(exc.args) == 1 else None
        suffix = ':' + reason if stage == 'mounts' and type(reason) is str and reason in predicates else ''
        try:
            os.write(2, ('storage_guard:' + stage + suffix + '\n').encode('ascii'))
        except OSError:
            pass
        return 127
    return 127


if __name__ == '__main__':
    sys.exit(main())
