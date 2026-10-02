"""Root-installed tunnel entrypoint; the MCP child separately scrubs this key."""

import os
from pathlib import Path
import stat
import sys


TUNNEL = '/opt/modelfc-research/bin/tunnel-client'
PROFILE = 'zeno-private-research'


def launch() -> None:
    directory = os.environ['CREDENTIALS_DIRECTORY']
    path = Path(directory) / 'tunnel.key'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        raw = source.read(4097)
    if not stat.S_ISREG(info.st_mode) or not 1 <= len(raw) <= 4096:
        raise ValueError('invalid tunnel key')
    key = raw.decode('ascii').strip()
    if not key or any(c.isspace() or not c.isprintable() for c in key):
        raise ValueError('invalid tunnel key')
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/var/lib/modelfc-research',
           'LANG': 'C.UTF-8', 'CONTROL_PLANE_API_KEY': key}
    os.execve(TUNNEL, [TUNNEL, 'run', '--profile', PROFILE], env)


if __name__ == '__main__':
    try:
        launch()
    except (OSError, ValueError, KeyError, UnicodeError):
        print('RESEARCH_TUNNEL_LAUNCH_FAILED', file=sys.stderr)
        raise SystemExit(1) from None
