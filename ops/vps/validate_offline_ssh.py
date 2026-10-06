"""Root-owned forced command, run as modelfc-validator via one exact sudo rule."""
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

REQUEST = re.compile(r'validate-offline stevengalvis/modelfc ([1-9][0-9]{0,9}) ([0-9a-f]{40})')
CONTROLLER = '/opt/modelfc-validator/validate_pr.py'


def controller_arguments(original):
    match = REQUEST.fullmatch(original)
    if not match:
        raise ValueError('INVALID_REQUEST')
    pr, sha = match.groups()
    return ['/usr/bin/python3', '-I', CONTROLLER, '--repository', 'stevengalvis/modelfc',
            '--pr', pr, '--sha', sha, '--mode', 'offline']


def main():
    # No caller argv, stdin, shell evaluation, inherited credentials or module paths.
    try:
        if len(sys.argv) != 1:
            raise ValueError
        args = controller_arguments(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
        account = pwd.getpwuid(os.geteuid())
        if account.pw_name != 'modelfc-validator':
            raise ValueError
        # Controller checks its own trusted installation and configuration again.
        for path in (Path(__file__).absolute(), Path(CONTROLLER)):
            for part in (path, *path.parents):
                info = part.lstat()
                if part.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': account.pw_dir,
               'XDG_RUNTIME_DIR': f'/run/user/{account.pw_uid}',
               'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{account.pw_uid}/bus'}
        # Fixed controller already bounds/sanitizes output. Never forward stderr.
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, timeout=390, check=True)
        if len(result.stdout) > 16384:
            raise ValueError
        sys.stdout.buffer.write(result.stdout)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError):
        # No raw command, error, or candidate output on rejection; runner marks error.
        return 1


if __name__ == '__main__':
    sys.exit(main())
