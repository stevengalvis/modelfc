"""Protected local operator inputs; never sourced from a task or comment."""
import json
import os
import re
from pathlib import Path
import stat
from .bridge_envelope import _pairs


def protected_dir(path, mode=None):
    path = Path(path)
    if os.geteuid() != 0 or not path.is_absolute() or path.resolve() != path:
        raise ValueError('unprotected_path')
    for directory in (path, *path.parents):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022):
            raise ValueError('unprotected_directory')
    if mode is not None and stat.S_IMODE(path.lstat().st_mode) != mode:
        raise ValueError('invalid_directory_mode')
    return path


def protected_bytes(path, maximum=16384):
    path = Path(path)
    protected_dir(path.parent)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('unprotected_file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('unprotected_file')
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('oversized_file')
    return raw


def protected_json(path):
    return json.loads(protected_bytes(path), object_pairs_hook=_pairs)


def disjoint(file, *directories):
    file = Path(file)
    for item in directories:
        item = Path(item)
        if item == file or item in file.parents or file in item.parents:
            raise ValueError('overlapping_credential_path')


def protected_tree(path):
    path = protected_dir(path)
    if (path / 'objects/info/alternates').exists():
        raise ValueError('mirror_alternates_rejected')
    count = 0
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(path, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            info = (Path(directory) / name).lstat()
            count += 1
            if (count > 100000 or info.st_uid != 0 or info.st_mode & 0o022
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise ValueError('unprotected_tree')
    fd = os.open(path / 'config', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        raw = source.read(65537)
    if len(raw) > 65536 or re.search(rb'(?im)^\s*\[\s*include(?:if)?(?:\s|\])', raw):
        raise ValueError('mirror_includes_rejected')
    return path
