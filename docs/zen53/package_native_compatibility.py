"""Deterministic local source archive; never acquires sources or runs a stage."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile

PACKAGE = Path(__file__).with_name('native-compatibility')


def build(package=PACKAGE):
    inventory_bytes = (package / 'INVENTORY.json').read_bytes()
    inventory = json.loads(inventory_bytes)
    expected = inventory['files']
    actual = {p.relative_to(package).as_posix() for p in package.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p != package / 'INVENTORY.json'}
    if inventory['format'] != 'zen53-compact-bridge-v1' or actual != set(expected):
        raise ValueError('PACKAGE_MEMBERS')
    files = {}
    for name, record in expected.items():
        if not name or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
            raise ValueError('PACKAGE_PATH')
        path = package / name
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('PACKAGE_SYMLINK')
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('PACKAGE_REGULAR')
        data = path.read_bytes()
        if len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('PACKAGE_PIN')
        files[name] = data
    files['INVENTORY.json'] = inventory_bytes
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(expected) + ['INVENTORY.json']:
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(files[name]))
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', mtime=0, compresslevel=9) as compressor:
        compressor.write(stream.getvalue())
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = build()
    if args.output:
        # No overwrite and no partial output ever used as success.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    print(json.dumps({'state': 'OFFLINE_SOURCE_ONLY', 'bytes': len(data),
                      'sha256': hashlib.sha256(data).hexdigest()}))


if __name__ == '__main__':
    main()
