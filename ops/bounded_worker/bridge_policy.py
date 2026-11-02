"""Protected operator policy loading; no policy is installed by this module."""
import json
import os
from pathlib import Path
import stat

from .bridge_envelope import Policy, _pairs


def load(path):
    path = Path(path)
    if os.geteuid() != 0 or not path.is_absolute() or path.resolve() != path:
        raise ValueError('unprotected_policy')
    for directory in path.parents:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('unprotected_policy_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('unprotected_policy_file')
        raw = source.read(16385)
    if len(raw) > 16384:
        raise ValueError('oversized_policy')
    value = json.loads(raw, object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != {'version', 'enabled', 'repository', 'thread', 'actor_id',
            'allowed_paths', 'timeout', 'output', 'diff', 'publish'}
            or value['version'] != 1 or type(value['version']) is not int or value['enabled'] is not True
            or value['repository'] != 'stevengalvis/modelfc' or value['publish'] is not True
            or type(value['actor_id']) is not int or value['actor_id'] <= 0
            or type(value['thread']) is not int or value['thread'] <= 0
            or type(value['allowed_paths']) is not list or not 1 <= len(value['allowed_paths']) <= 32
            or any(type(p) is not str for p in value['allowed_paths'])
            or len(set(value['allowed_paths'])) != len(value['allowed_paths'])
            or type(value['timeout']) is not int or not 1 <= value['timeout'] <= 90
            or type(value['output']) is not int or not 1024 <= value['output'] <= 262144
            or type(value['diff']) is not int or not 1 <= value['diff'] <= 4096):
        raise ValueError('invalid_or_disabled_policy')
    return Policy(value['actor_id'], value['thread'], tuple(value['allowed_paths']), True,
                  value['timeout'], value['output'], value['diff'])
