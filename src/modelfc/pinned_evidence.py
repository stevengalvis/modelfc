"""Offline hash-pinned regular-file evidence reader; no provider dependencies."""
import hashlib
import os
from pathlib import Path
import re
import stat

from modelfc.corner_analysis_outcomes import OutcomeError, _require


def read_pinned(path, digest, limit):
    """Read exactly the reviewed regular inode; no links, mutation or enumeration."""
    _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
             "SAVED_RESULT_PROVENANCE_REJECTED")
    path = Path(path).absolute()
    descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            _require(part != "..", "SAVED_RESULT_PROVENANCE_REJECTED")
            following = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW |
                                os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor); descriptor = following
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                     os.O_CLOEXEC, dir_fd=descriptor)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                     and 0 < before.st_size <= limit, "SAVED_RESULT_PROVENANCE_REJECTED")
            raw = source.read(limit + 1)
            after = os.fstat(source.fileno())
            final = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                      before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                      after.st_mtime_ns, after.st_ctime_ns)
                     and (before.st_dev, before.st_ino) == (final.st_dev, final.st_ino)
                     and len(raw) <= limit and hashlib.sha256(raw).hexdigest() == digest,
                     "SAVED_RESULT_PROVENANCE_REJECTED")
            return raw
    except OSError:
        raise OutcomeError("SAVED_RESULT_PROVENANCE_REJECTED") from None
    finally:
        os.close(descriptor)

