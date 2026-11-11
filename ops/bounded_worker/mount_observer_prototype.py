"""Experimental Linux mount observer; never used for bridge admission.

Identical snapshots cannot prove freedom from concurrent or later mount changes.
This observer does not establish worker access, privileges, propagation or safety.
"""
import ctypes
import os
import platform
import re
import threading
import time

from . import mount_visibility_prototype as visibility
from . import storage_guard

MAX_MOUNTINFO = 8 * 1024 * 1024
MAX_FDINFO = 65536
MAX_MOUNTPOINTS = 4096
MAX_SECONDS = 10
_O_PATH = 0o10000000
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_IN_ROOT = 0x10
_PROC_SUPER_MAGIC = 0x9fa0


class ObservationBlocked(ValueError):
    pass


class _OpenHow(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint64), ('mode', ctypes.c_uint64), ('resolve', ctypes.c_uint64)]


def _open_in_root(root_fd, path):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise ObservationBlocked('unsupported_platform')
    how = _OpenHow(_O_PATH | os.O_CLOEXEC, 0,
                   _RESOLVE_IN_ROOT | _RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_SYMLINKS)
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(437, root_fd, os.fsencode(path), ctypes.byref(how), ctypes.sizeof(how))
    if result < 0:
        raise ObservationBlocked('path_unresolved')
    return result


def _read_bounded(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ObservationBlocked('observation_oversized')
    return data


def _mount_id(fd):
    data = _read_bounded('/proc/thread-self/fdinfo/' + str(fd), MAX_FDINFO)
    matches = re.findall(rb'^mnt_id:[ \t]*([0-9]{1,10})[ \t]*$', data, re.MULTILINE)
    if len(matches) != 1 or not 0 < int(matches[0]) <= 0xffffffff:
        raise ObservationBlocked('invalid_fdinfo')
    return int(matches[0])


def _namespace_ids():
    result = []
    for name in ('mnt', 'user', 'pid'):
        info = os.stat('/proc/thread-self/ns/' + name)
        result.append((info.st_dev, info.st_ino))
    return tuple(result)


def _fd_id(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _verify_proc(proc_fd):
    """Check the pinned proc filesystem and its view of the current thread.

    This does not authenticate proc contents against a malicious host root or
    prevent later mount changes. Those actors remain outside this prototype.
    """
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise ObservationBlocked('unsupported_platform')
    data = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(proc_fd, ctypes.byref(data)) != 0:
        raise ObservationBlocked('proc_unverified')
    if ctypes.cast(data, ctypes.POINTER(ctypes.c_long)).contents.value != _PROC_SUPER_MAGIC:
        raise ObservationBlocked('proc_unverified')
    expected = f'{os.getpid()}/task/{threading.get_native_id()}'
    if os.readlink('thread-self', dir_fd=proc_fd) != expected:
        raise ObservationBlocked('proc_identity_mismatch')
    info = os.stat('/proc', follow_symlinks=False)
    if (info.st_dev, info.st_ino) != _fd_id(proc_fd):
        raise ObservationBlocked('proc_changed')


def observe():
    """Return a candidate visibility list, never a safety or admission result.

    Requires trusted procfs, no privileged mount/namespace changes during or
    after observation and a separately reviewed worker/descriptor threat model.
    Any resolution failure aborts. All opened descriptors are closed on return.
    """
    root_fd = None
    proc_fd = None
    ns_fds = []
    try:
        deadline = time.monotonic() + MAX_SECONDS
        proc_fd = os.open('/proc', _O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        _verify_proc(proc_fd)
        for name in ('mnt', 'user', 'pid'):
            ns_fds.append(os.open('/proc/thread-self/ns/' + name, os.O_RDONLY | os.O_CLOEXEC))
        namespace_before = _namespace_ids()
        if namespace_before != tuple(_fd_id(fd) for fd in ns_fds):
            raise ObservationBlocked('namespace_changed')
        root_fd = os.open('/', _O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        raw_before = _read_bounded('/proc/thread-self/mountinfo', MAX_MOUNTINFO)
        before = storage_guard._mounts(raw_before.decode('utf-8'))
        paths = {mount['path'] for mount in before}
        if len(paths) > MAX_MOUNTPOINTS:
            raise ObservationBlocked('too_many_mounts')
        observed = {}
        for path in sorted(paths):
            if time.monotonic() >= deadline:
                raise ObservationBlocked('observation_timeout')
            fd = _open_in_root(root_fd, path)
            try:
                observed[path] = _mount_id(fd)
            finally:
                os.close(fd)
        raw_after = _read_bounded('/proc/thread-self/mountinfo', MAX_MOUNTINFO)
        namespace_after = _namespace_ids()
        _verify_proc(proc_fd)
        if time.monotonic() >= deadline:
            raise ObservationBlocked('observation_timeout')
        if namespace_before != namespace_after or raw_before != raw_after:
            raise ObservationBlocked('observation_changed')
        after = storage_guard._mounts(raw_after.decode('utf-8'))
        return visibility.visible(before, after, observed)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ObservationBlocked):
            raise
        raise ObservationBlocked('observation_failed') from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
        for fd in ns_fds:
            os.close(fd)
        if proc_fd is not None:
            os.close(proc_fd)
