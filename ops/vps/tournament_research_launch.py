"""Root-installed launcher for the one-shot OddsPapi tournament experiment."""

import os
import json
from pathlib import Path
import pwd
import re
import stat
import sys


RELEASES = Path("/srv/modelfc/releases")
STATE = Path("/var/lib/modelfc/state")
AUTHORIZATION = Path("/etc/modelfc/oddspapi-tournament-research.json")
METADATA = Path("/etc/modelfc/oddspapi-market-metadata.json")
CREDENTIAL_NAME = "oddspapi.key"
AUTHORIZATION_CREDENTIAL_NAME = "tournament-authorization.json"


def protected(path, owner, *, directory=False, private=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022
            or private and info.st_mode & 0o077):
        raise ValueError("invalid tournament research path")


def credential(name=CREDENTIAL_NAME, *, json_document=False) -> str:
    directory_value = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory_value:
        raise ValueError("missing tournament research credential")
    directory = Path(directory_value)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("invalid tournament research credential")
    descriptor = os.open(directory / name,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info, raw = os.fstat(source.fileno()), source.read(4097)
    if not stat.S_ISREG(info.st_mode) or not 1 <= len(raw) <= 4096:
        raise ValueError("invalid tournament research credential")
    try:
        value = raw.decode("utf-8" if json_document else "ascii").strip()
    except UnicodeDecodeError:
        raise ValueError("invalid tournament research credential") from None
    if json_document:
        try:
            if not isinstance(json.loads(value), dict):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("invalid tournament research credential") from None
        return value
    if not value or any(character.isspace() or not character.isprintable() for character in value):
        raise ValueError("invalid tournament research credential")
    return value


def launch():
    release = Path.cwd()
    match = re.fullmatch(r"([0-9a-f]{40})-[0-9a-f]{12}", release.name)
    if release.parent != RELEASES or match is None:
        raise ValueError("invalid tournament research release")
    deploy_owner = pwd.getpwnam("modelfc-deploy").pw_uid
    runtime_owner = pwd.getpwnam("modelfc-runtime").pw_uid
    for path in (RELEASES.parent, RELEASES, release, release / ".git", release / "src",
                 release / "src/modelfc", release / "src/modelfc/providers",
                 release / ".venv", release / ".venv/bin"):
        protected(path, deploy_owner, directory=True)
    marker = release / ".git/modelfc-deployed-sha"
    descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != deploy_owner
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or source.read(41) != match[1].encode("ascii")):
            raise ValueError("invalid tournament research provenance")
    for path in (release / "src/modelfc/oddspapi_tournament_research.py",
                 release / "src/modelfc/providers/oddspapi.py",
                 release / "src/modelfc/corner_prospective.py",
                 release / "src/modelfc/corner_prospective_budget.py",
                 release / "src/modelfc/ledger_storage.py"):
        protected(path, deploy_owner)
    python = release / ".venv/bin/python"
    if python.lstat().st_uid != deploy_owner or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("invalid tournament research interpreter")
    protected(STATE, runtime_owner, directory=True)
    protected(AUTHORIZATION.parent, 0, directory=True)
    protected(AUTHORIZATION, 0, private=True)
    protected(METADATA, 0)
    key = credential()
    credential(AUTHORIZATION_CREDENTIAL_NAME, json_document=True)
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
                   "PYTHONPATH": str(release / "src"), "PYTHONNOUSERSITE": "1",
                   "PYTHONDONTWRITEBYTECODE": "1", "ODDSPAPI_API_KEY": key,
                   "CREDENTIALS_DIRECTORY": os.environ["CREDENTIALS_DIRECTORY"]}
    os.execve(str(python), [str(python), "-B", "-P", "-s", "-m",
              "modelfc.oddspapi_tournament_research"], environment)


def main():
    try:
        launch()
    except (OSError, ValueError, KeyError):
        print("TOURNAMENT_RESEARCH_LAUNCH_FAILED", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
