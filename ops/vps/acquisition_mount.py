"""Root-only, fixed-target tmpfs lifecycle for the reviewed deploy controller."""

import fcntl
import grp
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys


TARGET = Path("/run/modelfc-acquisition")
LOCK = Path("/run/modelfc-acquisition-control.lock")
RELEASE_ID = re.compile(r"[0-9a-f]{40}-[0-9a-f]{12}\Z")
CAPACITY = 256 * 1024**2
INODES = 32768


def mount_rows():
    rows = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        left, right = before.split(), after.split()
        if left[4] == str(TARGET) or left[4].startswith(str(TARGET) + "/"):
            rows.append((left[4], set(left[5].split(",")), right[0], right[1]))
    return rows


def identity():
    account = pwd.getpwnam("modelfc-deploy")
    group = grp.getgrnam("modelfc-deploy")
    if account.pw_uid == 0 or account.pw_gid != group.gr_gid:
        raise ValueError("boundary")
    return account.pw_uid, group.gr_gid


def verify_mount(release_id):
    uid, gid = identity()
    rows = mount_rows()
    if (len(rows) != 1 or rows[0][0] != str(TARGET)
            or not {"rw", "nodev", "nosuid", "noexec"} <= rows[0][1]
            or rows[0][2:] != ("tmpfs", "modelfc-acquire-" + release_id)):
        raise ValueError("boundary")
    info = TARGET.lstat()
    usage = os.statvfs(TARGET)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != 0o700
            or usage.f_blocks * usage.f_frsize != CAPACITY or usage.f_files != INODES):
        raise ValueError("boundary")


def run(action, release_id):
    if (os.geteuid() != 0 or action not in ("mount", "unmount", "verify")
            or not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id)):
        raise ValueError("boundary")
    parent = TARGET.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0
            or parent.st_mode & 0o022):
        raise ValueError("boundary")
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError("boundary")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = {"PATH": "/usr/bin:/bin", "HOME": "/", "LANG": "C"}
        if action in ("unmount", "verify"):
            verify_mount(release_id)
            if action == "unmount":
                subprocess.run(["/usr/bin/umount", "--", str(TARGET)], cwd="/", env=env,
                               check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                TARGET.rmdir()
            return
        # Never adopt or tear down stale/foreign acquisition state automatically.
        if mount_rows() or TARGET.exists() or TARGET.is_symlink():
            raise ValueError("boundary")
        uid, gid = identity()
        TARGET.mkdir(mode=0o700)
        try:
            subprocess.run(["/usr/bin/mount", "-t", "tmpfs", "-o",
                            f"rw,nosuid,nodev,noexec,size={CAPACITY},nr_inodes={INODES},mode=0700,uid={uid},gid={gid}",
                            "modelfc-acquire-" + release_id, str(TARGET)], cwd="/", env=env,
                           check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            verify_mount(release_id)
        except BaseException:
            # Only an exact mount owned by this attempt may be removed.
            rows = mount_rows()
            if len(rows) == 1 and rows[0][2:] == ("tmpfs", "modelfc-acquire-" + release_id):
                subprocess.run(["/usr/bin/umount", "--", str(TARGET)], cwd="/", env=env,
                               check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not mount_rows():
                TARGET.rmdir()
            raise
    finally:
        os.close(fd)


def main(argv):
    try:
        if len(argv) != 2:
            raise ValueError("boundary")
        run(*argv)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
