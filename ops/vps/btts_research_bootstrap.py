"""Authorized, fixed-path bootstrap for an empty BTTS research namespace.

This helper creates no evidence and grants no ACL.  It is installed and invoked
only during the separately reviewed host activation documented in
BTTS_RESEARCH_API.md.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import sys


STATE = Path("/var/lib/modelfc/state")
NAMESPACE = "btts-research"
RUNNER_DIRECTORY = "prospective"
RUNNER_LOCK = "runner.lock"
BTTS_LOCK = ".lock"
ACL_ACCESS = "system.posix_acl_access"
ACL_DEFAULT = "system.posix_acl_default"


class BootstrapError(RuntimeError):
    """A host precondition or safe-publication check failed."""


class BootstrapRequired(BootstrapError):
    """The check-only invocation found an absent bootstrap object."""


def _acl(descriptor: int, name: str) -> bytes | None:
    try:
        return os.getxattr(descriptor, name)
    except OSError as error:
        if error.errno in (errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA)):
            return None
        raise


def _same_object(directory_descriptor: int, name: str, info: os.stat_result) -> bool:
    current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    return (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)


def _validate_root_ancestor(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022):
        raise BootstrapError("invalid root-owned ancestor")


def _validate_directory(descriptor: int, owner: int, *, private: bool) -> os.stat_result:
    info = os.fstat(descriptor)
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or mode & 0o007
            or mode & 0o020 or mode & 0o700 != 0o700):
        raise BootstrapError("invalid directory boundary")
    if private and (mode != 0o700 or _acl(descriptor, ACL_ACCESS) is not None
                    or _acl(descriptor, ACL_DEFAULT) is not None):
        raise BootstrapError("invalid private namespace")
    return info


def _validate_lock(descriptor: int, owner: int, *, private: bool) -> os.stat_result:
    info = os.fstat(descriptor)
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1
            or info.st_size != 0 or mode & 0o737 != 0o600
            or mode & 0o600 != 0o600):
        raise BootstrapError("invalid lock boundary")
    if private and (mode != 0o600 or _acl(descriptor, ACL_ACCESS) is not None):
        raise BootstrapError("invalid private lock")
    return info


def _open_chain(path: Path) -> list[int]:
    if not path.is_absolute():
        raise BootstrapError("state path must be absolute")
    descriptors = [os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)]
    try:
        for component in path.parts[1:]:
            descriptors.append(os.open(component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        path_descriptor = descriptors[-1]
        descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=path_descriptor)
        path_info = os.fstat(path_descriptor)
        info = os.fstat(descriptor)
        if ((path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)):
            os.close(descriptor)
            raise BootstrapError("state directory changed")
        os.close(descriptors.pop())
        descriptors.append(descriptor)
        return descriptors
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def bootstrap_empty_namespace(state: Path, *, runtime_uid: int, apply: bool,
                              parent_uid: int = 0, validate_ancestors: bool = True) -> str:
    """Check or create only the empty private namespace and its empty lock.

    The existing prospective runner lock serializes this operation with the only
    supported writer.  Existing ACL bytes on the state root are compared after
    the operation; this helper never calls an ACL tool or changes its parent.
    """
    descriptors: list[int] = []
    namespace_created = False
    lock_created = False
    namespace_info = None
    lock_info = None
    state_descriptor = None
    namespace_descriptor = None
    try:
        descriptors = _open_chain(state)
        if len(descriptors) < 2:
            raise BootstrapError("invalid state path")
        parent_descriptor, state_descriptor = descriptors[-2:]
        if validate_ancestors:
            for descriptor in descriptors[:-2]:
                _validate_root_ancestor(descriptor)
        _validate_directory(parent_descriptor, parent_uid, private=False)
        _validate_directory(state_descriptor, runtime_uid, private=False)
        if _acl(state_descriptor, ACL_DEFAULT) is not None:
            raise BootstrapError("default ACLs are not permitted")
        state_acl = _acl(state_descriptor, ACL_ACCESS)

        prospective_descriptor = os.open(
            RUNNER_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=state_descriptor)
        descriptors.append(prospective_descriptor)
        _validate_directory(prospective_descriptor, runtime_uid, private=False)
        runner_descriptor = os.open(
            RUNNER_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=prospective_descriptor)
        descriptors.append(runner_descriptor)
        runner_info = _validate_lock(runner_descriptor, runtime_uid, private=False)
        if not _same_object(prospective_descriptor, RUNNER_LOCK, runner_info):
            raise BootstrapError("runner lock changed")
        try:
            fcntl.flock(runner_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BootstrapError("prospective runner is active") from None
        if not _same_object(prospective_descriptor, RUNNER_LOCK, runner_info):
            raise BootstrapError("runner lock changed")

        try:
            namespace_descriptor = os.open(
                NAMESPACE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=state_descriptor)
        except FileNotFoundError:
            if not apply:
                raise BootstrapRequired("empty namespace bootstrap required") from None
            os.mkdir(NAMESPACE, mode=0o700, dir_fd=state_descriptor)
            namespace_created = True
            namespace_descriptor = os.open(
                NAMESPACE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=state_descriptor)
        descriptors.append(namespace_descriptor)
        namespace_info = _validate_directory(namespace_descriptor, runtime_uid, private=True)
        if not _same_object(state_descriptor, NAMESPACE, namespace_info):
            raise BootstrapError("namespace changed")
        entries = os.listdir(namespace_descriptor)
        if entries not in ([], [BTTS_LOCK]):
            raise BootstrapError("namespace is not empty")

        try:
            lock_descriptor = os.open(
                BTTS_LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=namespace_descriptor)
        except FileNotFoundError:
            if not apply:
                raise BootstrapRequired("empty namespace lock bootstrap required") from None
            lock_descriptor = os.open(
                BTTS_LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600, dir_fd=namespace_descriptor)
            lock_created = True
        descriptors.append(lock_descriptor)
        lock_info = os.fstat(lock_descriptor)
        _validate_lock(lock_descriptor, runtime_uid, private=True)
        if not _same_object(namespace_descriptor, BTTS_LOCK, lock_info):
            raise BootstrapError("BTTS lock changed")
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BootstrapError("BTTS writer is active") from None
        if not _same_object(namespace_descriptor, BTTS_LOCK, lock_info):
            raise BootstrapError("BTTS lock changed")
        if os.listdir(namespace_descriptor) != [BTTS_LOCK]:
            raise BootstrapError("namespace changed")

        # An apply retry may be completing a prior attempt whose durability sync
        # failed after publication.  Repeat all three syncs even when the exact
        # namespace and lock already existed; check-only remains read-only.
        if apply:
            os.fsync(lock_descriptor)
            os.fsync(namespace_descriptor)
            os.fsync(state_descriptor)
        if (_acl(state_descriptor, ACL_ACCESS) != state_acl
                or _acl(state_descriptor, ACL_DEFAULT) is not None):
            raise BootstrapError("existing ACL boundary changed")
        if (not _same_object(prospective_descriptor, RUNNER_LOCK, runner_info)
                or not _same_object(state_descriptor, NAMESPACE, namespace_info)):
            raise BootstrapError("bootstrap boundary changed")
        return "CREATED" if namespace_created or lock_created else "READY"
    except BootstrapRequired:
        raise
    except (BootstrapError, OSError):
        # Publication is monotonic.  Another runtime process may already have
        # opened a newly named lock and be waiting for this flock; unlinking it
        # could split serialization across the old and a replacement inode.
        # A reviewed retry validates and reuses an exact private partial state.
        raise BootstrapError("BTTS bootstrap failed") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in (["--check"], ["--apply"]):
        print(json.dumps({"status": "FAIL", "reason": "INVALID_REQUEST"}, sort_keys=True))
        return 2
    try:
        account = pwd.getpwnam("modelfc-runtime")
        runtime_uid = account.pw_uid
        if (runtime_uid == 0 or account.pw_gid == 0 or os.geteuid() != runtime_uid
                or os.getegid() != account.pw_gid
                or set(os.getgroups()) - {account.pw_gid}):
            raise BootstrapError("invalid activation identity")
        os.umask(0o077)
        result = bootstrap_empty_namespace(STATE, runtime_uid=runtime_uid, apply=arguments == ["--apply"])
    except BootstrapRequired:
        print(json.dumps({"status": "REQUIRED", "reason": "EMPTY_NAMESPACE"}, sort_keys=True))
        return 3
    except (BootstrapError, KeyError, OSError):
        print(json.dumps({"status": "FAIL", "reason": "BOOTSTRAP_REJECTED"}, sort_keys=True))
        return 1
    print(json.dumps({"status": "PASS", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
