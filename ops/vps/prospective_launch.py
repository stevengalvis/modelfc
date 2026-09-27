"""Root-installed launcher for one physically pinned prospective release."""

import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys


RELEASES = Path("/srv/modelfc/releases")
CONFIG = Path("/etc/modelfc/corner_data.json")
HISTORY = Path("/var/lib/modelfc/history")
STATE = Path("/var/lib/modelfc/state")
CREDENTIAL_NAME = "oddspapi.key"


def protected(path, owner, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError("invalid prospective path")


def _credential() -> str:
    directory_value = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory_value:
        raise ValueError("missing prospective credential")
    directory = Path(directory_value)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("invalid prospective credential directory")
    path = directory / CREDENTIAL_NAME
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        raw = source.read(4097)
    if not stat.S_ISREG(info.st_mode) or not 1 <= len(raw) <= 4096:
        raise ValueError("invalid prospective credential")
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise ValueError("invalid prospective credential") from None
    if not value or any(character.isspace() or not character.isprintable() for character in value):
        raise ValueError("invalid prospective credential")
    return value


def launch():
    # getcwd returns the physical directory selected by systemd, not $PWD/current.
    release = Path.cwd()
    match = re.fullmatch(r"([0-9a-f]{40})-[0-9a-f]{12}", release.name)
    if release.parent != RELEASES or match is None:
        raise ValueError("invalid prospective release")
    deploy_owner = pwd.getpwnam("modelfc-deploy").pw_uid
    runtime_owner = pwd.getpwnam("modelfc-runtime").pw_uid
    for path in (RELEASES.parent, RELEASES, release, release / ".git", release / "src",
                 release / "src/modelfc", release / ".venv", release / ".venv/bin"):
        protected(path, deploy_owner, directory=True)
    marker = release / ".git/modelfc-deployed-sha"
    descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != deploy_owner
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or source.read(41) != match[1].encode("ascii")):
            raise ValueError("invalid prospective provenance")
    for path in (release / ".venv/pyvenv.cfg",
                 release / "src/modelfc/corner_prospective.py",
                 release / "src/modelfc/corner_prospective_budget.py"):
        protected(path, deploy_owner)
    python = release / ".venv/bin/python"
    if python.lstat().st_uid != deploy_owner or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("invalid prospective interpreter")
    for path in (CONFIG.parent.parent, CONFIG.parent):
        protected(path, 0, directory=True)
    protected(CONFIG, 0)
    if json.loads(CONFIG.read_text(encoding="utf-8"))["data_directory"] != str(HISTORY):
        raise ValueError("invalid prospective history configuration")
    protected(HISTORY, runtime_owner, directory=True)
    protected(STATE, runtime_owner, directory=True)
    key = _credential()
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
           "PYTHONPATH": str(release / "src"), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "ODDSPAPI_API_KEY": key}
    record = {"event": "modelfc_prospective_start", "release": str(release),
              "sha": match[1], "interpreter": str(python),
              "import_path": str(release / "src"), "uid": os.getuid(), "gid": os.getgid(),
              "config": str(CONFIG), "state": str(STATE)}
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), file=sys.stderr, flush=True)
    os.execve(str(python), [str(python), "-B", "-P", "-s", "-m",
              "modelfc.corner_prospective", "run-once", "--data-config", str(CONFIG),
              "--state-dir", str(STATE), "--require-calendar-budget"], env)


def main():
    try:
        launch()
    except (OSError, ValueError, KeyError):
        print("PROSPECTIVE_LAUNCH_FAILED", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
