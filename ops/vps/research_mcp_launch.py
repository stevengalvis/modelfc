"""Root-installed, trusted launch boundary for the private stdio research adapter."""

import os
from pathlib import Path
import pwd
import re
import stat
import sys


# systemd resolves current once at service startup and pins the physical release
# through a read-only bind mount. The tunnel caller's cwd cannot choose a release.
RELEASE = Path('/srv/modelfc-research-release')
STATE = Path('/var/lib/modelfc/state')
PYTHON = Path('/opt/modelfc-research/.venv/bin/python')


def _protected(path: Path, owner: int, directory: bool = False) -> None:
    info = path.lstat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != owner or info.st_mode & 0o022):
        raise ValueError('invalid research launcher input')


def launch() -> None:
    release = RELEASE
    if not os.path.ismount(release):
        raise ValueError('invalid research release')
    deploy = pwd.getpwnam('modelfc-deploy').pw_uid
    runtime = pwd.getpwnam('modelfc-runtime').pw_uid
    if os.geteuid() != runtime:
        raise ValueError('invalid research identity')
    for path in (release, release / '.git', release / 'src', release / 'src/modelfc'):
        _protected(path, deploy, directory=True)
    marker = release / '.git/modelfc-deployed-sha'
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != deploy
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or not re.fullmatch(rb'[0-9a-f]{40}', source.read(41))):
            raise ValueError('invalid research provenance')
    for path in (release / 'src/modelfc/private_research_mcp.py',
                 release / 'src/modelfc/corner_research.py',
                 release / 'src/modelfc/ledger_storage.py'):
        _protected(path, deploy)
    for path in (PYTHON.parent.parent.parent, PYTHON.parent.parent, PYTHON.parent):
        _protected(path, 0, directory=True)
    if not PYTHON.is_file() or not os.access(PYTHON, os.X_OK):
        raise ValueError('invalid research interpreter')
    _protected(STATE, runtime, directory=True)
    _protected(STATE / 'research-snapshots', runtime, directory=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
           'PYTHONPATH': str(release / 'src'), 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'MODELFC_STATE_DIR': str(STATE)}
    os.execve(str(PYTHON), [str(PYTHON), '-B', '-P', '-s', '-m',
                            'modelfc.private_research_mcp'], env)


if __name__ == '__main__':
    try:
        launch()
    except (OSError, ValueError, KeyError):
        print('RESEARCH_MCP_LAUNCH_FAILED', file=sys.stderr)
        raise SystemExit(1) from None
