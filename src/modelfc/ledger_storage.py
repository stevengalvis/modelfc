"""Shared append-only JSON storage primitives for local forecast ledgers."""

from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterable, Iterator


class LedgerError(ValueError):
    """Raised when a ledger operation or saved record is invalid."""


class LedgerStorageUnavailable(LedgerError):
    """Raised when state storage cannot currently be accessed."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


DEPLOYMENT_RELEASES = Path("/srv/modelfc/releases")


def _deployed_commit_sha(repository: Path) -> str | None:
    """Read controller metadata only from protected permanent releases.

    No Git process or safe.directory exception is needed by the runtime user.
    The deployment account and host administrators remain trusted.
    """
    if (repository.parent != DEPLOYMENT_RELEASES
            or not re.fullmatch(r"[0-9a-f]{40}-[0-9a-f]{12}", repository.name)):
        return None
    descriptors = []
    try:
        owner = pwd.getpwnam("modelfc-deploy").pw_uid
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in (*repository.parts[1:], ".git"):
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
            info = os.fstat(fd)
            if info.st_uid not in (0, owner) or info.st_mode & 0o022:
                return None
        fd = os.open("modelfc-deployed-sha", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=fd)
        descriptors.append(fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o444
                or info.st_uid != owner or info.st_size != 40):
            return None
        value = os.read(fd, 41).decode("ascii")
        return value if value == repository.name[:40] else None
    except (OSError, KeyError, UnicodeError):
        return None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def git_commit_sha() -> str:
    repository = Path(__file__).resolve().parents[2]
    deployed = _deployed_commit_sha(repository)
    if deployed is not None:
        return deployed
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "HEAD"), check=True,
            capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    sha = result.stdout.strip()
    return sha if sha else "unknown"


def source_records(paths: Iterable[Path]) -> list[dict[str, str]]:
    records = []
    for path in paths:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise LedgerError(f"could not hash source CSV {path}: {error}") from error
        records.append({"filename": path.name, "sha256": digest})
    return records


def ensure_directory(path: Path, label: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LedgerStorageUnavailable(
            f"could not create {label} {path}: {error}"
        ) from error


def _link_open_file(descriptor: int, target: Path) -> None:
    """Link the open evidence inode, not a replaceable temporary pathname."""
    library = ctypes.CDLL(None, use_errno=True)
    linkat = library.linkat
    linkat.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_int)
    linkat.restype = ctypes.c_int
    # Linux AT_FDCWD and AT_SYMLINK_FOLLOW. Python's os.link does not reliably
    # follow /proc/self/fd here; linkat explicitly resolves the open inode.
    if linkat(-100, os.fsencode(f"/proc/self/fd/{descriptor}"),
              -100, os.fsencode(target), 0x400) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def write_new_record(
    path: Path, record: dict[str, Any], *, before_publish: Callable[[], None] | None = None,
    evidence_state: Path | None = None, evidence_lock_fd: int | None = None,
) -> None:
    """Atomically create a complete JSON record without replacing a file."""
    try:
        serialized = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as error:
        raise LedgerError(f"could not serialize ledger record {path}: {error}") from error
    temporary_path: Path | None = None
    try:
        if (evidence_state is not None
                and os.environ.get("MODELFC_EVIDENCE_ACL_USER") == "modelfc-api"):
            # An unnamed inode has no writable pathname alias after publication.
            descriptor = os.open(path.parent, os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
                _grant_evidence_read(path, evidence_state, evidence_lock_fd,
                                     output.fileno())
                if before_publish is not None:
                    before_publish()
                if os.fstat(output.fileno()).st_nlink != 0:
                    raise LedgerStorageUnavailable("public evidence inode changed; record not published")
                _link_open_file(output.fileno(), path)
            return
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=".record-",
            suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
            # Run after serialization/fsync, immediately before exclusive publication.
            if before_publish is not None:
                before_publish()
            os.link(temporary_path, path)
    except FileExistsError as error:
        raise LedgerError(f"refusing to overwrite existing record: {path}") from error
    except OSError as error:
        raise LedgerStorageUnavailable(
            f"could not write ledger record {path}: {error}"
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _grant_evidence_read(target: Path, state: Path, lock_descriptor: int | None,
                         temporary_descriptor: int) -> None:
    """Grant the dedicated API UID read access before immutable publication.

    The opt-in is supplied only by the root-installed prospective launcher.
    Other ledger records, especially budget/control state, never call this path.
    """
    if os.environ.get("MODELFC_EVIDENCE_ACL_USER") != "modelfc-api":
        return
    public_ledgers = {"analyses", "analysis-outcomes", "predictions", "prediction-targets",
                      "market-observations", "opportunities"}
    try:
        relative = target.relative_to(state)
        is_btts = (len(relative.parts) == 2 and relative.parts[0] == "btts-research"
                   and re.fullmatch(r"(?:forecast-)?[0-9a-f]{64}\.json",
                                    relative.parts[1]) is not None)
        is_public_ledger = (len(relative.parts) in (2, 3)
                            and relative.parts[0] in public_ledgers
                            and target.suffix == ".json")
        if not (is_btts or is_public_ledger):
            raise ValueError("invalid public evidence location")
        selected_lock = state / "btts-research" / ".lock" if is_btts else state / ".lock"
        uid = pwd.getpwnam("modelfc-api").pw_uid
        directories = [state, *(state.joinpath(*relative.parts[:index])
                               for index in range(1, len(relative.parts)))]
        for index, directory in enumerate(directories):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(descriptor)
                current = directory.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
                        or not stat.S_ISDIR(current.st_mode) or current.st_uid != os.getuid()):
                    raise ValueError("invalid public evidence directory")
                permission = "--x" if index == 0 else "r-x"
                subprocess.run(("/usr/bin/setfacl", "-m", f"u:{uid}:{permission}",
                                "--", f"/proc/self/fd/{descriptor}"),
                               check=True, timeout=10, env={"PATH": "/usr/bin:/bin"},
                               pass_fds=(descriptor,), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                current = directory.lstat()
                if ((info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
                        or not stat.S_ISDIR(current.st_mode) or current.st_uid != os.getuid()):
                    raise ValueError("public evidence directory changed during ACL setup")
            finally:
                os.close(descriptor)
        # The writer created this real lock on entering ledger_lock. The API
        # needs its read ACL before any new evidence is made visible.
        if lock_descriptor is None:
            raise ValueError("public evidence requires the held state lock")
        lock_info = os.fstat(lock_descriptor)
        current_lock = selected_lock.lstat()
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
                or lock_info.st_nlink != 1 or lock_info.st_size != 0
                or (current_lock.st_dev, current_lock.st_ino) != (lock_info.st_dev, lock_info.st_ino)
                or not stat.S_ISREG(current_lock.st_mode)
                or current_lock.st_uid != os.getuid() or current_lock.st_nlink != 1
                or current_lock.st_size != 0):
            raise ValueError("invalid public evidence lock")
        # The child inherits only the descriptor already exclusively locked by
        # the writer. A pathname replacement cannot redirect its ACL.
        subprocess.run(("/usr/bin/setfacl", "-m", f"u:{uid}:r--", "--",
                        f"/proc/self/fd/{lock_descriptor}"),
                       check=True, timeout=10, env={"PATH": "/usr/bin:/bin"},
                       pass_fds=(lock_descriptor,), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        current_lock = selected_lock.lstat()
        if ((current_lock.st_dev, current_lock.st_ino) != (lock_info.st_dev, lock_info.st_ino)
                or not stat.S_ISREG(current_lock.st_mode)
                or current_lock.st_uid != os.getuid() or current_lock.st_nlink != 1
                or current_lock.st_size != 0):
            raise ValueError("public evidence lock changed during ACL setup")
        # Persist the named-user ACL before any evidence depending on this lock
        # becomes durable or visible.
        os.fsync(lock_descriptor)
        info = os.fstat(temporary_descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 0):
            raise ValueError("invalid public evidence file")
        subprocess.run(("/usr/bin/setfacl", "-m", f"u:{uid}:r--", "--",
                        f"/proc/self/fd/{temporary_descriptor}"),
                       check=True, timeout=10, env={"PATH": "/usr/bin:/bin"},
                       pass_fds=(temporary_descriptor,), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        current = os.fstat(temporary_descriptor)
        if ((info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
                or not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid()
                or current.st_nlink != 0):
            raise ValueError("public evidence file changed during ACL setup")
        # setfacl changes inode metadata after the serialized bytes were synced.
        # Persist both together before linking the immutable inode into place.
        os.fsync(temporary_descriptor)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        raise LedgerStorageUnavailable("public evidence read ACL failed; record not published") from None


@contextmanager
def ledger_lock(ledger: Path) -> Iterator[int]:
    """Serialize operations whose correctness depends on ledger contents."""
    lock_path = ledger / ".lock"
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield lock_file.fileno()
    except OSError as error:
        raise LedgerStorageUnavailable(
            f"could not lock ledger {ledger}: {error}"
        ) from error


@contextmanager
def existing_read_lock(lock_path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    """Bounded shared lock on an existing regular file, without mutation."""
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    descriptor = None
    locked = False
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise LedgerStorageUnavailable("ledger read lock is not a regular file")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LedgerStorageUnavailable("ledger read lock timed out") from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    except FileNotFoundError as error:
        raise LedgerStorageUnavailable("ledger read lock is unavailable") from error
    except OSError as error:
        raise LedgerStorageUnavailable("ledger read lock is unavailable") from error
    try:
        yield
    finally:
        if descriptor is not None:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def ledger_read_lock(ledger: Path, *, timeout_seconds: float = 5.0):
    """Bounded shared lock matching the existing state publication lock."""
    return existing_read_lock(ledger / ".lock", timeout_seconds=timeout_seconds)


def read_json_record(path: Path, kind: str, unknown: str) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LedgerError(unknown) from error
    except OSError as error:
        raise LedgerStorageUnavailable(
            f"could not read {kind} record {path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise LedgerError(f"invalid {kind} record {path}: {error}") from error
    if not isinstance(record, dict):
        raise LedgerError(f"invalid {kind} record {path}: expected a JSON object")
    return record
