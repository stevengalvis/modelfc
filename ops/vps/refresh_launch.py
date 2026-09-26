"""Root-installed launcher for one physically pinned refresh release."""

import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

RELEASES = Path('/srv/modelfc/releases')
CONFIG = Path('/etc/modelfc/corner_data.json')
HISTORY = Path('/var/lib/modelfc/history')


def protected(path, owner, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError('invalid refresh path')


def launch():
    # getcwd uses the physical directory selected by systemd, not $PWD/current.
    release = Path.cwd()
    match = re.fullmatch(r'([0-9a-f]{40})-[0-9a-f]{12}', release.name)
    if release.parent != RELEASES or match is None:
        raise ValueError('invalid refresh release')
    owner = pwd.getpwnam('modelfc-deploy').pw_uid
    for path in (RELEASES.parent, RELEASES, release, release / '.git',
                 release / 'src', release / 'src/modelfc', release / '.venv', release / '.venv/bin'):
        protected(path, owner, directory=True)
    marker = release / '.git/modelfc-deployed-sha'
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_size != 40
                or source.read(41) != match[1].encode('ascii')):
            raise ValueError('invalid refresh provenance')
    for path in (release / '.venv/pyvenv.cfg', release / 'src/modelfc/corner_refresh.py'):
        protected(path, owner)
    python = release / '.venv/bin/python'
    # A normal venv interpreter is a symlink. Validate, but execute its venv path.
    if python.lstat().st_uid != owner or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('invalid refresh interpreter')
    for path in (CONFIG.parent.parent, CONFIG.parent):
        protected(path, 0, directory=True)
    protected(CONFIG, 0)
    if json.loads(CONFIG.read_text())['data_directory'] != str(HISTORY):
        raise ValueError('invalid refresh history configuration')
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
           'PYTHONPATH': str(release / 'src'), 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1'}
    os.execve(str(python), [str(python), '-B', '-s', '-m', 'modelfc.corner_refresh',
              '--config', str(CONFIG), '--validator-read-user', 'modelfc-validator'], env)


def main():
    try:
        launch()
    except (OSError, ValueError, KeyError):
        print('REFRESH_LAUNCH_FAILED', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
