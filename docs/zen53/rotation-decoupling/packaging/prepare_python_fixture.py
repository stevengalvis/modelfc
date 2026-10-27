"""Acquire pinned Ubuntu archives into a disposable offline-test fixture.

No package installation, maintainer scripts, host metadata claim or diagnostic.
Network acquisition is separate from the offline tests. Only regular files
selected by runtime_dependencies.json are extracted, after archive/member hashes.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

BASE = Path(__file__).resolve().parents[1]


def prepare(cache, output, download=False):
    proof = json.loads((BASE/'stage/PYTHON_CLOSURE_PROVENANCE.json').read_bytes())
    deps = json.loads((BASE/'stage/runtime_dependencies.json').read_bytes())['dependencies']
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=False)
    for row in proof['packages']:
        package = cache/(row['name']+'.deb')
        if not package.exists() and download:
            with urllib.request.urlopen(row['url'], timeout=60) as response:
                data = response.read(row['bytes']+1)
            if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('ARCHIVE_HASH')
            with package.open('xb') as f:
                f.write(data)
        data = package.read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('ARCHIVE_HASH')
        # dpkg-deb only decodes the authenticated archive; it does not install.
        tar_bytes = subprocess.run(['/usr/bin/dpkg-deb', '--fsys-tarfile', str(package)],
                                   check=True, capture_output=True, timeout=30).stdout
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as archive:
            for member in archive:
                path = '/'+member.name.removeprefix('./')
                if path not in deps or not member.isfile():
                    continue
                content = archive.extractfile(member).read()
                if hashlib.sha256(content).hexdigest() != deps[path]['sha256']:
                    raise ValueError('MEMBER_HASH:'+path)
                target = output/path.lstrip('/')
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as f:
                    f.write(content)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    prepare(args.cache, args.output, args.download)
