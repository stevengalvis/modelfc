#!/usr/bin/python3 -I
"""Root-installed operator entrypoint; drop privilege before loading release code."""

import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys


CURRENT = Path("/srv/modelfc/current")
RELEASES = Path("/srv/modelfc/releases")
CONFIG = Path("/etc/modelfc/corner_data.json")
HISTORY = Path("/var/lib/modelfc/history")
STATE = Path("/var/lib/modelfc/state")


def protected(path: Path, owner: int, *, directory: bool = False) -> None:
    info = path.lstat()
    if ((not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or info.st_uid != owner or info.st_mode & 0o022):
        raise ValueError("invalid status path")


def launch(argv: list[str]) -> None:
    if argv not in ([], ["--json"]) or os.geteuid() != 0:
        raise ValueError("invalid status invocation")
    deploy_uid = pwd.getpwnam("modelfc-deploy").pw_uid
    account = pwd.getpwnam("modelfc-runtime")
    if (deploy_uid == 0 or account.pw_uid in (0, deploy_uid) or account.pw_gid == 0):
        raise ValueError("invalid status identities")
    target = os.readlink(CURRENT)
    release = Path(target)
    match = re.fullmatch(r"([0-9a-f]{40})-[0-9a-f]{12}", release.name)
    if (not CURRENT.is_symlink() or CURRENT.lstat().st_uid != deploy_uid
            or not release.is_absolute() or release.parent != RELEASES or match is None):
        raise ValueError("invalid selected release")
    for path in (RELEASES.parent, RELEASES, release, release / ".git",
                 release / "src", release / "src/modelfc", release / ".venv", release / ".venv/bin"):
        protected(path, deploy_uid, directory=True)
    marker = release / ".git/modelfc-deployed-sha"
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != deploy_uid
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or stream.read(41) != match[1].encode("ascii")):
            raise ValueError("invalid release marker")
    for path in (release / "src/modelfc/production_status.py",
                 release / "src/modelfc/production_status_host.py",
                 release / "src/modelfc/prospective_run_receipts.py",
                 release / ".venv/pyvenv.cfg"):
        protected(path, deploy_uid)
    python = release / ".venv/bin/python"
    if python.lstat().st_uid != deploy_uid or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("invalid status interpreter")
    for path in (CONFIG.parent.parent, CONFIG.parent):
        protected(path, 0, directory=True)
    protected(CONFIG, 0)
    if json.loads(CONFIG.read_text(encoding="utf-8"))["data_directory"] != str(HISTORY):
        raise ValueError("invalid status history configuration")
    protected(HISTORY, account.pw_uid, directory=True)
    protected(STATE, account.pw_uid, directory=True)
    os.chdir(release)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
           "PYTHONPATH": str(release / "src"), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    os.execve(str(python), [str(python), "-B", "-P", "-s", "-m",
              "modelfc.production_status", "--release", str(release),
              "--config", str(CONFIG), "--state-dir", str(STATE), *argv], env)


def main() -> None:
    try:
        launch(sys.argv[1:])
    except (OSError, ValueError, KeyError):
        print("STATUS_CONFIG_UNAVAILABLE", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
