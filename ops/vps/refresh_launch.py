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
STATE = Path('/var/lib/modelfc/state')
SETTLEMENT_CONFIG = Path('/etc/modelfc/research-settlement.json')


def protected(path, owner, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError('invalid refresh path')


def settlement_enabled():
    if not os.path.lexists(SETTLEMENT_CONFIG):
        return False
    for path in (SETTLEMENT_CONFIG.parent.parent, SETTLEMENT_CONFIG.parent):
        protected(path, 0, directory=True)
    protected(SETTLEMENT_CONFIG, 0)
    value = json.loads(SETTLEMENT_CONFIG.read_text())
    if not isinstance(value, dict) or set(value) != {'enabled'} or type(value['enabled']) is not bool:
        raise ValueError('invalid settlement control')
    return value['enabled']


def launch(*, settlement=False):
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
    enabled = settlement_enabled()
    if settlement and not enabled:
        raise ValueError('research settlement disabled')
    if settlement:
        protected(release / 'src/modelfc/research_settlement.py', owner)
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
           'PYTHONPATH': str(release / 'src'), 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1'}
    if settlement:
        env['MODELFC_EVIDENCE_ACL_USER'] = 'modelfc-api'
    record = {'event': 'modelfc_research_settlement_start' if settlement else 'modelfc_refresh_start', 'release': str(release), 'sha': match[1],
              'interpreter': str(python), 'import_path': str(release / 'src'),
              'uid': os.getuid(), 'gid': os.getgid(), 'config': str(CONFIG)}
    print(json.dumps(record, sort_keys=True, separators=(',', ':')), file=sys.stderr, flush=True)
    arguments = (['--config', str(CONFIG), '--state-dir', str(STATE), '--enabled'] if settlement else
        ['--config', str(CONFIG), '--validator-read-user', 'modelfc-validator',
         '--public-history-read-user', 'modelfc-api'] + (['--retain-result-evidence'] if enabled else []))
    module = 'modelfc.research_settlement' if settlement else 'modelfc.corner_refresh'
    os.execve(str(python), [str(python), '-B', '-P', '-s', '-m', module, *arguments], env)


def main():
    try:
        if sys.argv[1:] not in ([], ['--research-settlement']):
            raise ValueError('invalid launcher arguments')
        launch(settlement=bool(sys.argv[1:]))
    except (OSError, ValueError, KeyError):
        print('REFRESH_LAUNCH_FAILED', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
