#!/usr/bin/env python3
"""Reconstruct the two pinned ZEN-53 archives from the exact PR #212 checkout.

This is an offline acquisition recipe. It creates files only below a new output
directory; it does not install, authorize, or execute sandbox components.
"""

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys
import tarfile
import tempfile


COMMIT = "f79866666a8b464854929875825498b8fd8ccdf0"
BASE = Path("docs/zen53/rotation-decoupling")
STAGE_ARCHIVE = "zen53-api-rotation-decoupling-stage-offline.tar.gz"
STAGE_SHA = "a527085d5fb968fed1577a9afdea4d8173fef1b931ec06012cc49c3d909a4d33"
STAGE_INVENTORY_SHA = "c74f259e3a94fc31c1792d66c13fff4f2149430688dc18db932137db157a985c"
OPERATOR_ARCHIVE = "zen53-api-rotation-operator-v4-source-reduced-offline.tar.gz"
OPERATOR_SHA = "692386f512bdea0dab54426c5a0336f4f9d0ae9fbcfce712eb89143275b705a1"
OPERATOR_INVENTORY_SHA = "b8edd449ae8ef62b8596ba4c52c38c0f84dc7a82fd80636be9d529cc922ebd80"
LAUNCH_INVENTORY_SHA = "842069099abebbedd4bcc80f0a31ccc7c2ff01f7f5418e14d0df5b5e4fe6ebfe"
SOURCE_MANIFEST_SHA = "bc144806d69095fb4ae56375c62043f97a23292b77f90d6d447ce853f0750ab7"
SOURCE_README_SHA = "b1f44e9a0ec15f7c29e499518766b41ac6f5a1bdeaa7f1558591345f7a9f475c"
STAGE_MODE_0600 = frozenset({
    "CONTRACT_COVERAGE.md", "INVENTORY.json", "README.md", "SOURCE_PINS.json",
    "admission.py", "test_admission.py",
})
OPERATOR_DIRS = (
    "zen53-api-rotation-operator-v4-offline",
    "zen53-api-rotation-launch-tests-offline",
)
GZIP = Path("/usr/bin/gzip")
MAX_TAR_BYTES = 1_048_576
MAX_GZIP_BYTES = 1_048_576


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read_regular(path, maximum=1_048_576):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_size <= maximum, "SOURCE_METADATA")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        first = os.fstat(fd)
        require((first.st_dev, first.st_ino, first.st_size) ==
                (before.st_dev, before.st_ino, before.st_size), "SOURCE_IDENTITY")
        chunks = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        last = os.fstat(fd)
        require(len(data) == before.st_size and
                (first.st_dev, first.st_ino, first.st_size,
                 first.st_mtime_ns, first.st_ctime_ns) ==
                (last.st_dev, last.st_ino, last.st_size,
                 last.st_mtime_ns, last.st_ctime_ns), "SOURCE_CHANGED")
        return data
    finally:
        os.close(fd)


def checked_group(directory, inventory_sha):
    require(directory.is_dir() and not directory.is_symlink(), "SOURCE_DIRECTORY")
    raw = read_regular(directory / "INVENTORY.json")
    require(sha(raw) == inventory_sha, "INVENTORY_HASH")
    files = json.loads(raw)["files"]
    require(type(files) is dict and all(type(k) is str and k and "/" not in k
              and k not in (".", "..") for k in files), "INVENTORY_SCHEMA")
    require({p.name for p in directory.iterdir()} == set(files) | {"INVENTORY.json"},
            "SOURCE_MEMBER_SET")
    result = {"INVENTORY.json": raw}
    for name, pin in files.items():
        data = read_regular(directory / name)
        require(len(data) == pin["bytes"] and sha(data) == pin["sha256"],
                "SOURCE_HASH")
        result[name] = data
    return result


def verified_gzip(expected_sha, owner_uid=0, owner_gid=0):
    require(type(expected_sha) is str and len(expected_sha) == 64 and
            all(c in "0123456789abcdef" for c in expected_sha), "GZIP_PIN_FORMAT")
    before = GZIP.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == owner_uid and
            before.st_gid == owner_gid and before.st_nlink == 1 and
            before.st_size <= MAX_TAR_BYTES and
            not (before.st_mode & 0o022), "GZIP_METADATA")
    require(sha(read_regular(GZIP)) == expected_sha, "GZIP_HASH")
    return (before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns)


def gzip_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_GZIP_BYTES, MAX_GZIP_BYTES))
    resource.setrlimit(resource.RLIMIT_AS, (268_435_456, 268_435_456))


def fixed_gzip(raw, expected_sha, owner_uid=0, owner_gid=0):
    require(len(raw) <= MAX_TAR_BYTES, "TAR_SIZE")
    identity = verified_gzip(expected_sha, owner_uid, owner_gid)
    with tempfile.TemporaryFile() as out:
        child = subprocess.Popen(
            [str(GZIP), "-n", "-6", "-c"], stdin=subprocess.PIPE,
            stdout=out, stderr=subprocess.DEVNULL, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
            preexec_fn=gzip_limits,
        )
        try:
            child.communicate(input=raw, timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            raise ValueError("GZIP_TIMEOUT") from None
        require(child.returncode == 0, "GZIP_EXIT")
        out.seek(0, os.SEEK_END)
        size = out.tell()
        require(size <= MAX_GZIP_BYTES, "GZIP_SIZE")
        out.seek(0)
        result = out.read(MAX_GZIP_BYTES + 1)
    after = GZIP.lstat()
    require(identity == (after.st_dev, after.st_ino, after.st_size,
                         after.st_mtime_ns, after.st_ctime_ns), "GZIP_CHANGED")
    return result


def stage_bytes(files, expected_gzip_sha, owner_uid=0, owner_gid=0):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        root.uid = root.gid = root.mtime = 0
        tar.addfile(root)
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo("./" + name)
            member.size = len(data)
            member.mode = 0o600 if name in STAGE_MODE_0600 else 0o400
            member.uid = member.gid = member.mtime = 0
            tar.addfile(member, io.BytesIO(data))
    return fixed_gzip(out.getvalue(), expected_gzip_sha, owner_uid, owner_gid)


def operator_bytes(groups):
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0,
                       compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for name in OPERATOR_DIRS:
                directory = tarfile.TarInfo(name)
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                directory.uid = directory.gid = directory.mtime = 0
                tar.addfile(directory)
                for child, data in sorted(groups[name].items()):
                    member = tarfile.TarInfo(name + "/" + child)
                    member.size = len(data)
                    member.mode = 0o600
                    member.uid = member.gid = member.mtime = 0
                    tar.addfile(member, io.BytesIO(data))
    return out.getvalue()


def reconstruct(checkout, output, expected_gzip_sha, owner_uid=0, owner_gid=0):
    root = checkout / BASE
    require(root.is_dir() and not root.is_symlink() and
            {p.name for p in root.iterdir()} ==
            {"stage", "operator-review", "README.md", "SOURCE_TREE_MANIFEST.json"},
            "SOURCE_TREE_SET")
    require(sha(read_regular(root / "README.md")) == SOURCE_README_SHA and
            sha(read_regular(root / "SOURCE_TREE_MANIFEST.json")) ==
            SOURCE_MANIFEST_SHA, "SOURCE_TREE_PINS")
    stage = checked_group(root / "stage", STAGE_INVENTORY_SHA)
    require(set(stage).issuperset(STAGE_MODE_0600) and len(stage) == 37,
            "STAGE_MEMBER_SET")
    operator_root = root / "operator-review"
    require(operator_root.is_dir() and not operator_root.is_symlink() and
            {p.name for p in operator_root.iterdir()} == set(OPERATOR_DIRS),
            "OPERATOR_MEMBER_SET")
    groups = {
        OPERATOR_DIRS[0]: checked_group(operator_root / OPERATOR_DIRS[0],
                                         OPERATOR_INVENTORY_SHA),
        OPERATOR_DIRS[1]: checked_group(operator_root / OPERATOR_DIRS[1],
                                         LAUNCH_INVENTORY_SHA),
    }
    require(len(groups[OPERATOR_DIRS[0]]) == 13 and
            len(groups[OPERATOR_DIRS[1]]) == 4, "OPERATOR_MEMBER_COUNT")
    stage_archive = stage_bytes(stage, expected_gzip_sha, owner_uid, owner_gid)
    operator_archive = operator_bytes(groups)
    require(len(stage_archive) == 126_618 and sha(stage_archive) == STAGE_SHA and
            len(operator_archive) == 67_338 and sha(operator_archive) ==
            OPERATOR_SHA, "ARCHIVE_RECONSTRUCTION")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in ((STAGE_ARCHIVE, stage_archive),
                       (OPERATOR_ARCHIVE, operator_archive)):
        path = output / name
        with path.open("xb") as dest:
            dest.write(data)
            dest.flush()
            os.fsync(dest.fileno())
        path.chmod(0o444)
    output_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(output_fd)
    finally:
        os.close(output_fd)
    return output


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: zen53_reconstruct_pr212_archives.py CHECKOUT NEW_OUTPUT_DIR VERIFIED_GZIP_SHA256")
    try:
        result = reconstruct(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
        print(json.dumps({"stage_sha256": STAGE_SHA,
                          "operator_sha256": OPERATOR_SHA,
                          "output": str(result)}, sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError, KeyError,
            json.JSONDecodeError) as exc:
        raise SystemExit("ZEN53_RECONSTRUCTION_BLOCKED:" +
                         type(exc).__name__) from None
