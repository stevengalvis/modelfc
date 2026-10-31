"""Root-only no-follow input snapshots. Never reads OAuth or provider credentials."""
import hashlib
import json
import os
from pathlib import PurePosixPath
import stat

ERROR = 'EXECUTOR_PROTECTED_INPUT_REJECTED'
SECRET_PARTS = {'.ssh', '.aws', '.codex', 'auth.json', '.env', 'credentials', 'credentials.json'}


def need(value):
    if not value:
        raise ValueError(ERROR)


def identity(info):
    return tuple(getattr(info, name) for name in ('st_dev','st_ino','st_mode','st_uid','st_gid',
                 'st_nlink','st_size','st_mtime_ns','st_ctime_ns'))


def hexhash(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def components(path):
    need(type(path) is str and path.startswith('/') and path != '/')
    parts = path.split('/')[1:]
    need(all(part and part not in ('.', '..') and part not in SECRET_PARTS
             and not any(ord(c) < 32 for c in part) for part in parts))
    need(str(PurePosixPath(path)) == path)
    return parts


def open_root_directory(path):
    """Every ancestor root-owned and not group/other writable; held FD returned."""
    need(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0)
    parts = [] if path == '/' else components(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in [None, *parts]:
            if part is not None:
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                old, fd = fd, new
                os.close(old)
            info = os.fstat(fd)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022)
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def open_pinned(record, maximum=320*1024*1024):
    """Authenticate bytes and retain the exact FD, never a mutable pathname claim."""
    need(type(record) is dict and set(record) == {'path','sha256','size','mode'})
    need(hexhash(record['sha256']) and type(record['size']) is int and 0 <= record['size'] <= maximum
         and type(record['mode']) is int and record['mode'] in (0o400,0o444,0o500,0o555,0o600,0o644,0o700,0o755))
    parts = components(record['path'])
    parent = open_root_directory('/' + '/'.join(parts[:-1]) if len(parts)>1 else '/')
    fd = None
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and before.st_nlink == 1
             and stat.S_IMODE(before.st_mode) == record['mode'] and before.st_size == record['size'])
        data = bytearray()
        while len(data) <= record['size']:
            chunk = os.read(fd, min(65536, record['size']+1-len(data)))
            if not chunk:
                break
            data.extend(chunk)
        need(len(data) == record['size'] and hashlib.sha256(data).hexdigest() == record['sha256'])
        need(identity(before) == identity(os.fstat(fd)) == identity(os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)))
        os.lseek(fd,0,os.SEEK_SET)
        result, fd = fd, None
        return result, bytes(data), identity(before)
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            os.close(parent)


def read_pinned(record, maximum=320*1024*1024):
    fd, raw, info = open_pinned(record, maximum)
    try:
        return raw, info
    finally:
        os.close(fd)


def strict_json(raw, maximum):
    need(type(raw) is bytes and 0 < len(raw) <= maximum)
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value)
            value[key] = item
        return value
    try:
        return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
                          parse_constant=lambda _:need(False))
    except (ValueError,UnicodeError,RecursionError):
        raise ValueError(ERROR) from None
