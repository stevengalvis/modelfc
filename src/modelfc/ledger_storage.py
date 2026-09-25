"""Shared append-only JSON storage primitives for local forecast ledgers."""

from contextlib import contextmanager
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


def write_new_record(
    path: Path, record: dict[str, Any], *, before_publish: Callable[[], None] | None = None,
) -> None:
    """Atomically create a complete JSON record without replacing a file."""
    try:
        serialized = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as error:
        raise LedgerError(f"could not serialize ledger record {path}: {error}") from error
    temporary_path: Path | None = None
    try:
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


@contextmanager
def ledger_lock(ledger: Path) -> Iterator[None]:
    """Serialize operations whose correctness depends on ledger contents."""
    lock_path = ledger / ".lock"
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield
    except OSError as error:
        raise LedgerStorageUnavailable(
            f"could not lock ledger {ledger}: {error}"
        ) from error


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
