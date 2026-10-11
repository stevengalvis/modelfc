"""Root-installed launcher for the pinned forecast-only production runner."""
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


def protected(path, owner, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError("invalid forecast-only path")


def protected_tree(root, owner):
    """Validate every importable release inode without following links."""
    def failed(error):
        raise error
    for directory, names, files in os.walk(root, topdown=True, followlinks=False,
                                            onerror=failed):
        parent = Path(directory)
        protected(parent, owner, directory=True)
        for name in sorted(names):
            protected(parent/name, owner, directory=True)
        for name in sorted(files):
            protected(parent/name, owner)


def protected_interpreter(path, deploy):
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        if info.st_uid != deploy or info.st_mode & 0o022 or not os.access(path, os.X_OK):
            raise ValueError("invalid forecast-only interpreter")
        return path
    if not stat.S_ISLNK(info.st_mode) or info.st_uid != deploy:
        raise ValueError("invalid forecast-only interpreter")
    target = path.resolve(strict=True)
    target_info = target.lstat()
    if (not stat.S_ISREG(target_info.st_mode) or target_info.st_uid != 0
            or target_info.st_mode & 0o022 or not os.access(target, os.X_OK)):
        raise ValueError("invalid forecast-only interpreter")
    for ancestor in (target.parent, *target.parents):
        ancestor_info = ancestor.lstat()
        if (not stat.S_ISDIR(ancestor_info.st_mode) or ancestor_info.st_uid != 0
                or ancestor_info.st_mode & 0o022):
            raise ValueError("invalid forecast-only interpreter")
    return target


def launch():
    release = Path.cwd()
    match = re.fullmatch(r"([0-9a-f]{40})-[0-9a-f]{12}", release.name)
    if release.parent != RELEASES or match is None:
        raise ValueError("invalid forecast-only release")
    deploy = pwd.getpwnam("modelfc-deploy").pw_uid
    runtime = pwd.getpwnam("modelfc-runtime").pw_uid
    api = pwd.getpwnam("modelfc-api").pw_uid
    if len({deploy, runtime, api}) != 3 or os.getuid() != runtime:
        raise ValueError("invalid forecast-only identity")
    for path in (RELEASES.parent, RELEASES, release, release/".git", release/"src",
                 release/"src/modelfc", release/".venv", release/".venv/bin"):
        protected(path, deploy, directory=True)
    marker = release/".git/modelfc-deployed-sha"
    descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != deploy
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or source.read(41) != match[1].encode("ascii")):
            raise ValueError("invalid forecast-only provenance")
    for path in (release/".venv/pyvenv.cfg", release/"src/modelfc/forecast_only_runner.py",
                 release/"src/modelfc/supplemental_history.py",
                 release/"src/modelfc/research_predictions.py"):
        protected(path, deploy)
    protected_tree(release/"src/modelfc", deploy)
    protected_tree(release/".venv/lib", deploy)
    python = release/".venv/bin/python"
    executable = protected_interpreter(python, deploy)
    for path in (CONFIG.parent.parent, CONFIG.parent):
        protected(path, 0, directory=True)
    protected(CONFIG, 0)
    if json.loads(CONFIG.read_text(encoding="utf-8"))["data_directory"] != str(HISTORY):
        raise ValueError("invalid forecast-only history configuration")
    protected(HISTORY, runtime, directory=True)
    protected(STATE, runtime, directory=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
           "PYTHONPATH": str(release/"src"), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "MODELFC_EVIDENCE_ACL_USER": "modelfc-api",
           "CREDENTIALS_DIRECTORY": os.environ["CREDENTIALS_DIRECTORY"]}
    print(json.dumps({"event": "modelfc_forecast_only_start", "sha": match[1],
        "uid": os.getuid(), "gid": os.getgid()}, sort_keys=True, separators=(",", ":")),
        file=sys.stderr, flush=True)
    os.execve(str(executable), [str(python), "-B", "-P", "-s", "-m",
        "modelfc.forecast_only_runner", "--data-config", str(CONFIG),
        "--state-dir", str(STATE)], env)


def main():
    try:
        launch()
    except (OSError, ValueError, KeyError):
        print("FORECAST_ONLY_LAUNCH_FAILED", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
