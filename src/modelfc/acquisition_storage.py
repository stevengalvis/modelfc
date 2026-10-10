"""Private descriptor-anchored immutable tournament records, never public evidence."""
from contextlib import contextmanager
import fcntl
import ctypes
import ctypes.util
import pwd
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from modelfc.ledger_storage import _link_open_file

LIMIT = 24 * 1024 * 1024


class AcquisitionRejected(ValueError):
    """Sanitized failure boundary."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


def directory(path):
    """O_PATH traverses execute-only parents without listing them. No symlinks."""
    path = Path(path)
    if not path.is_absolute() or any(p in (".", "..") for p in path.parts):
        raise AcquisitionRejected("PATH_REJECTED")
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            # A root-owned sticky temporary ancestor is allowed for offline tests.
            sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, os.geteuid()) or (info.st_mode & 0o022 and not sticky):
                raise AcquisitionRejected("PATH_REJECTED")
        return fd
    except BaseException:
        os.close(fd)
        raise


def credential_snapshot(fd, info):
    """Accept only root's systemd copy with read ACL solely for runtime, not all 0440."""
    identity = pwd.getpwnam("modelfc-runtime")
    uid = identity.pw_uid
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o440 or os.geteuid() != uid:
        raise AcquisitionRejected("AUTHORIZATION_FILE_REJECTED")
    library = ctypes.util.find_library("acl")
    if not library: raise AcquisitionRejected("AUTHORIZATION_FILE_REJECTED")
    acl = ctypes.CDLL(library, use_errno=True)
    acl.acl_get_fd.argtypes = (ctypes.c_int,); acl.acl_get_fd.restype = ctypes.c_void_p
    acl.acl_to_text.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t))
    acl.acl_to_text.restype = ctypes.c_void_p
    acl.acl_free.argtypes = (ctypes.c_void_p,); acl.acl_free.restype = ctypes.c_int
    handle = acl.acl_get_fd(fd)
    if not handle: raise AcquisitionRejected("AUTHORIZATION_FILE_REJECTED")
    text = None
    try:
        length = ctypes.c_ssize_t()
        text = acl.acl_to_text(handle, ctypes.byref(length))
        if not text or not 0 < length.value <= 2048:
            raise AcquisitionRejected("AUTHORIZATION_FILE_REJECTED")
        lines = ctypes.string_at(text, length.value).decode("ascii").strip().splitlines()
        expected = {"user::r--", "group::---", "mask::r--", "other::---"}
        named = {f"user:{uid}:r--", f"user:{identity.pw_name}:r--"}
        if len(lines) != 5 or set(lines) - named != expected or len(set(lines) & named) != 1:
            raise AcquisitionRejected("AUTHORIZATION_FILE_REJECTED")
    finally:
        if text: acl.acl_free(text)
        acl.acl_free(handle)


def read_file(parent, name, limit=LIMIT, *, owner=None, mode=None, authorization=False):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in (".", ".."):
        raise AcquisitionRejected("PATH_REJECTED")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > limit or before.st_mode & 0o022
                or before.st_uid not in (0, os.geteuid())
                or owner is not None and before.st_uid != owner
                or mode is not None and stat.S_IMODE(before.st_mode) != mode):
            raise AcquisitionRejected("FILE_REJECTED")
        if authorization and stat.S_IMODE(before.st_mode) != 0o600:
            credential_snapshot(fd, before)
        chunks = []; remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk: break
            chunks.append(chunk); remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(raw) > limit or not raw or any(getattr(before, a) != getattr(after, a) for a in
                 ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")) or
                (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)):
            raise AcquisitionRejected("FILE_REJECTED")
        return raw
    finally:
        os.close(fd)


@contextmanager
def private_store(path):
    anchor = directory(path)
    fd = None
    try:
        info = os.fstat(anchor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AcquisitionRejected("PRIVATE_STORE_REJECTED")
        fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=anchor)
        yield Store(fd)
    finally:
        if fd is not None: os.close(fd)
        os.close(anchor)


class Store:
    def __init__(self, fd): self.fd = fd

    def get(self, name):
        try: raw = read_file(self.fd, name, mode=0o600, owner=os.geteuid())
        except FileNotFoundError: return None
        from modelfc.providers.oddspapi_saved_response import _json
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {"sha256", "record"}
                or not isinstance(value["record"], dict) or
                value["sha256"] != fingerprint(encoded(value["record"]))):
            raise AcquisitionRejected("RECORD_INTEGRITY_REJECTED")
        return value["record"]

    def put(self, name, record):
        if not re.fullmatch(r"[a-z]+-[0-9a-f]{64}\.json", name):
            raise AcquisitionRejected("RECORD_NAME_REJECTED")
        old = self.get(name)
        if old is not None:
            if old != record: raise AcquisitionRejected("RECORD_CONFLICT")
            return False
        raw = encoded({"sha256": fingerprint(encoded(record)), "record": record})
        if len(raw) > LIMIT: raise AcquisitionRejected("RECORD_SIZE_REJECTED")
        # Linux unnamed inode: no visible partial record, no temporary-path race.
        fd = os.open(".", os.O_RDWR | os.O_TMPFILE | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(os.dup(fd), "wb") as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            _link_open_file(fd, Path(f"/proc/self/fd/{self.fd}") / name)
            os.fsync(self.fd)
        finally: os.close(fd)
        return True


@contextmanager
def shared_runner(state):
    """Require the existing collector namespace and lock; create nothing."""
    parent = directory(Path(state) / "prospective")
    lock = None
    try:
        lock = os.open("runner.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise AcquisitionRejected("LOCK_REJECTED")
        current = os.stat("runner.lock", dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AcquisitionRejected("LOCK_REJECTED")
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise AcquisitionRejected("BUSY") from None
        read_file(parent, "control.json", 2_000_000, owner=os.geteuid())
        yield Path(f"/proc/self/fd/{parent}") / "control.json"
    finally:
        if lock is not None: os.close(lock)
        os.close(parent)
