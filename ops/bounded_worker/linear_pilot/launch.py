"""Protected one-shot launcher. Install only from a reviewed, pinned release."""
import json
import os
from pathlib import Path
import re
import stat
import sys

BASE = Path('/opt/modelfc-linear-status/releases')
CONFIG = Path('/etc/modelfc-linear-status')
STATE = '/var/lib/modelfc-linear-status'
MODULES = frozenset({
    '__init__.py', 'linear_poll.py', 'linear_status.py', 'linear_transport.py',
    'service_status.py', 'bridge_security.py', 'bridge_envelope.py',
    'bridge_transport.py', 'task.py',
})
OUTCOMES = frozenset({'reported', 'failed_reported', 'idle', 'duplicate',
                      'collision_rejected', 'rejected', 'backoff', 'busy', 'blocked'})


def protected(path, *, directory=False):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('path')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or (not directory and info.st_nlink != 1)):
        raise ValueError('protection')
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('ancestor')


def validate_release(root):
    if root.parent != BASE or not re.fullmatch(r'[0-9a-f]{40}', root.name):
        raise ValueError('release')
    protected(root, directory=True)
    if {p.name for p in root.iterdir()} != {'launch.py', 'bounded_worker'}:
        raise ValueError('release_files')
    protected(root / 'launch.py')
    package = root / 'bounded_worker'
    protected(package, directory=True)
    if {p.name for p in package.iterdir()} != MODULES:
        raise ValueError('package_files')
    for name in MODULES:
        protected(package / name)


def validate_settings(config):
    expected = {
        'state_root': STATE, 'host_policy': str(CONFIG / 'host.json'),
        'grant': str(CONFIG / 'grant.json'),
        'reader_token_file': str(CONFIG / 'credentials/linear.key'),
        'sink_token_file': str(CONFIG / 'credentials/linear.key'),
        'reader_oauth': False, 'sink_oauth': False,
    }
    if any(type(config.get(k)) is not type(v) or config.get(k) != v
           for k, v in expected.items()):
        raise ValueError('settings_paths')


def main():
    try:
        if (os.geteuid() != 0 or len(sys.argv) != 1 or not sys.flags.isolated
                or not sys.flags.no_site or not sys.dont_write_bytecode):
            raise ValueError('invocation')
        os.umask(0o077)
        os.chdir('/')
        os.environ.clear()
        os.environ.update({'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
        root = Path(__file__).absolute().parent
        validate_release(root)
        # No application imports until the entire dedicated source tree is checked.
        sys.path.insert(0, str(root))
        from bounded_worker.bridge_security import protected_json
        from bounded_worker.linear_poll import run
        settings = CONFIG / 'settings.json'
        validate_settings(protected_json(settings))
        result = run(settings)
        if result not in OUTCOMES:
            result = 'blocked'
    except Exception:
        result = 'blocked'
    print(json.dumps({'status': result}, separators=(',', ':')))
    return 0 if result in {'reported', 'idle', 'duplicate', 'backoff'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
