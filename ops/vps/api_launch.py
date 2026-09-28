"""Root-installed launcher for the read-only API on one physical release."""

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
ORIGINS = Path("/etc/modelfc/api-cors-origin")


def protected(path, owner, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError("invalid API path")


def launch():
    # systemd resolves WorkingDirectory before execution; cwd is physical.
    release = Path.cwd()
    match = re.fullmatch(r"([0-9a-f]{40})-[0-9a-f]{12}", release.name)
    if release.parent != RELEASES or match is None:
        raise ValueError("invalid API release")
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
            raise ValueError("invalid API provenance")
    for path in (release / ".venv/pyvenv.cfg", release / "src/modelfc/corner_api.py",
                 release / "src/modelfc/corner_prospective_read.py"):
        protected(path, deploy_owner)
    python = release / ".venv/bin/python"
    if python.lstat().st_uid != deploy_owner or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("invalid API interpreter")
    for path in (CONFIG.parent.parent, CONFIG.parent):
        protected(path, 0, directory=True)
    protected(CONFIG, 0)
    if json.loads(CONFIG.read_text(encoding="utf-8"))["data_directory"] != str(HISTORY):
        raise ValueError("invalid API history configuration")
    protected(STATE, runtime_owner, directory=True)
    protected(ORIGINS, 0)
    origin = ORIGINS.read_text(encoding="ascii").strip()
    if (not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", origin)
            or ".." in origin or origin.startswith("https://-")):
        raise ValueError("invalid API origin")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
           "PYTHONPATH": str(release / "src"), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "MODELFC_DATA_CONFIG": str(CONFIG),
           "MODELFC_STATE_DIR": str(STATE), "MODELFC_CORS_ORIGINS": origin}
    record = {"event": "modelfc_api_start", "release": str(release), "sha": match[1]}
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), file=sys.stderr, flush=True)
    os.execve(str(python), [str(python), "-B", "-P", "-s", "-m", "uvicorn",
                              "modelfc.corner_api:app", "--host", "127.0.0.1", "--port", "8000",
                              "--workers", "1", "--limit-concurrency", "16",
                              "--timeout-keep-alive", "5"], env)


def main():
    try:
        launch()
    except (OSError, ValueError, KeyError, UnicodeError):
        print("API_LAUNCH_FAILED", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
