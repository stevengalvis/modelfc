"""Independent data-only patch validation in a clean trusted checkout."""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess


def _git(work, *args, data=None):
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
    result = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c',
                             'core.fsmonitor=false', '-c', 'credential.helper=', *args],
                            cwd=work, env=env, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 1048576:
        raise ValueError('git_validation_failed')
    return result.stdout


def _file(work, path):
    current = Path(work)
    parts = path.split('/')
    for component in parts[:-1]:
        current = current / component
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('unsafe_parent')
    current = current / parts[-1]
    fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 33554432:
            raise ValueError('unsafe_file')
        raw = source.read(33554433)
        if len(raw) != info.st_size:
            raise ValueError('changed_file')
    text = raw.decode('utf-8')
    if '\x00' in text or (text and not text.endswith('\n')):
        raise ValueError('not_text')
    return stat.S_IMODE(info.st_mode), raw


def validate_and_apply(work, task, patch, expected_sha256):
    task.validate()
    if (type(patch) is not bytes or not patch or len(patch) > task.diff_bytes
            or type(expected_sha256) is not str or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)
            or hashlib.sha256(patch).hexdigest() != expected_sha256):
        raise ValueError('patch_digest_or_size')
    if _git(work, 'rev-parse', 'HEAD').strip().decode() != task.base_revision:
        raise ValueError('base_mismatch')
    if _git(work, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('unclean_checkout')
    try:
        lines = patch.decode('utf-8').splitlines(keepends=True)
    except UnicodeDecodeError:
        raise ValueError('not_text') from None
    names = []
    index = 0
    while index < len(lines):
        header = lines[index]
        if not header.startswith('--- a/') or not header.endswith('\n'):
            raise ValueError('patch_header')
        name = header[6:-1]
        if name not in task.allowed_paths or index + 1 >= len(lines) or lines[index + 1] != '+++ b/' + name + '\n':
            raise ValueError('patch_scope')
        names.append(name)
        index += 2
        hunks = 0
        while index < len(lines) and lines[index].startswith('@@ '):
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\n]*)?\n', lines[index])
            if not match:
                raise ValueError('patch_hunk')
            old = int(match.group(2) or '1')
            new = int(match.group(4) or '1')
            if old > 33554432 or new > 33554432:
                raise ValueError('patch_hunk')
            index += 1
            hunks += 1
            while old or new:
                if index >= len(lines) or not lines[index].endswith('\n') or '\x00' in lines[index]:
                    raise ValueError('patch_hunk')
                prefix = lines[index][:1]
                if prefix == ' ':
                    old -= 1
                    new -= 1
                elif prefix == '-':
                    old -= 1
                elif prefix == '+':
                    new -= 1
                else:
                    raise ValueError('patch_hunk')
                if old < 0 or new < 0:
                    raise ValueError('patch_hunk')
                index += 1
        if not hunks:
            raise ValueError('patch_hunk')
    if not names or len(names) != len(set(names)):
        raise ValueError('patch_headers')
    before = {}
    for name in names:
        entry = _git(work, 'ls-tree', '-z', task.base_revision, '--', name)
        if not entry.startswith(b'100644 blob '):
            raise ValueError('not_existing_regular_file')
        if not entry.endswith(b'\t' + name.encode() + b'\0'):
            raise ValueError('tree_entry_mismatch')
        before[name] = _file(work, name)
    _git(work, 'apply', '--check', '--', data=patch)
    _git(work, 'apply', '--', data=patch)
    changed = _git(work, 'diff', '--name-only', '-z', '--no-renames', '--').split(b'\0')
    changed = {item.decode() for item in changed if item}
    if changed != set(names):
        raise ValueError('changed_paths_mismatch')
    result = {}
    for name in names:
        mode, contents = _file(work, name)
        if mode != before[name][0] or contents == before[name][1]:
            raise ValueError('mode_or_content_mismatch')
        result[name] = contents
    return result
