"""Read-only offline integrity check for the compact ZEN-53 review package.

This never downloads an external input or prepares/launches a sandbox stage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile

ROOT = Path(__file__).resolve().parent


def digest_file(path, expected_size):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise ValueError('FILE_TYPE_OR_SIZE: ' + str(path))
        h = hashlib.sha256()
        count = 0
        while count < expected_size:
            chunk = os.read(fd, min(65536, expected_size - count))
            if not chunk:
                raise ValueError('SHORT_FILE: ' + str(path))
            h.update(chunk)
            count += len(chunk)
        if os.read(fd, 1):
            raise ValueError('LONG_FILE: ' + str(path))
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
                  'st_mode', 'st_uid', 'st_gid', 'st_nlink')
        if tuple(getattr(before, key) for key in fields) != \
                tuple(getattr(after, key) for key in fields):
            raise ValueError('CHANGED_FILE: ' + str(path))
        return h.hexdigest()
    finally:
        os.close(fd)


def check_package():
    inventory = json.loads((ROOT / 'INVENTORY.json').read_text())
    if inventory.get('format') != 'zen53-compact-bridge-v1':
        raise ValueError('INVENTORY_FORMAT')
    expected = inventory['files']
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and
              p != ROOT / 'INVENTORY.json'}
    if actual != set(expected):
        raise ValueError('INVENTORY_SET')
    for name, record in expected.items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('INVENTORY_PATH')
        if digest_file(ROOT / name, record['size']) != record['sha256']:
            raise ValueError('INVENTORY_HASH: ' + name)
    return len(expected)


def check_external(supplied):
    records = json.loads((ROOT / 'EXTERNAL_INPUTS.json').read_text())['external_inputs']
    if set(supplied) != set(records):
        raise ValueError('EXTERNAL_INPUT_SET')
    for name, record in records.items():
        if digest_file(supplied[name], record['bytes']) != record['sha256']:
            raise ValueError('EXTERNAL_HASH: ' + name)
    origin = 'vendor/systemd_255.4.orig.tar.gz'
    if origin in records:
        dsc = (ROOT / 'vendor/systemd_255.4-1ubuntu8.18.dsc').read_text()
        for name in (origin, 'vendor/systemd_255.4-1ubuntu8.18.debian.tar.xz'):
            record = records[name]
            if f" {record['sha256']} {record['bytes']} {Path(name).name}" not in dsc:
                raise ValueError('VENDOR_DSC_PIN: ' + name)
        with tarfile.open(supplied[origin], 'r:gz') as archive:
            for member, local in (
                    ('systemd-stable-255.4/src/shared/base-filesystem.c',
                     'vendor/base-filesystem.c'),
                    ('systemd-stable-255.4/src/core/namespace.c',
                     'vendor/namespace.c')):
                entry = archive.getmember(member)
                if not entry.isfile() or archive.extractfile(entry).read() != \
                        (ROOT / local).read_bytes():
                    raise ValueError('VENDOR_EXCERPT: ' + local)
    return len(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', action='append', default=[], metavar='NAME=PATH')
    args = parser.parse_args()
    supplied = {}
    for value in args.input:
        name, sep, path = value.partition('=')
        if not sep or name in supplied:
            raise ValueError('INPUT_FORMAT')
        supplied[name] = path
    local = check_package()
    external = check_external(supplied) if supplied else 0
    print(f'COMPACT_PACKAGE_PASS files={local} external_verified={external} '
          f'external_required=4')


if __name__ == '__main__':
    main()
