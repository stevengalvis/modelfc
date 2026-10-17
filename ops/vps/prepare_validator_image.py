"""Prepare verified development build inputs; never installs or runs validation.

Use an existing local wheelhouse or explicitly opt into official PyPI downloads.
Run Podman separately in a private development store, never the active store.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.parse
import urllib.request

SOURCE = Path(__file__).resolve().parent


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message='INVALID_BUILD_INPUT'):
    if not condition:
        raise ValueError(message)


def prepare(output, wheelhouse=None, download=False):
    manifest = json.loads((SOURCE / 'validator-image-inputs.json').read_bytes())
    require(manifest['platform'] == 'linux/amd64')
    require(len(manifest['packages']) == 5)
    require(digest((SOURCE / 'validate_capture.py').read_bytes()) == manifest['harness_sha256'])
    payload = {}
    for package in manifest['packages']:
        name = package['filename']
        require(Path(name).name == name and name.endswith('.whl'))
        if wheelhouse is not None:
            path = wheelhouse / name
            require(path.is_file() and not path.is_symlink())
            raw = path.read_bytes()
        else:
            require(download, 'provide --wheelhouse or explicitly allow --download')
            url = package['url']
            require(urllib.parse.urlparse(url).scheme == 'https')
            require(urllib.parse.urlparse(url).hostname == 'files.pythonhosted.org')
            require(package['metadata_url'] == 'https://pypi.org/pypi/'+package['name']+'/'+package['version']+'/json')
            with urllib.request.urlopen(package['metadata_url'], timeout=30) as response:
                metadata = json.load(response)
            require(any(item['filename'] == name and item['digests']['sha256'] == package['sha256']
                       and item['url'] == url for item in metadata['urls']))
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read(20_000_001)
        require(len(raw) <= 20_000_000 and digest(raw) == package['sha256'])
        payload[name] = raw
    output.mkdir(mode=0o755)  # refuse an existing context, including a symlink
    (output / 'wheels').mkdir(mode=0o755)
    for name, raw in payload.items():
        (output / 'wheels' / name).write_bytes(raw)
    for name in ('Containerfile', 'requirements-validator.lock', 'validate_capture.py'):
        shutil.copyfile(SOURCE / name, output / name)
    for path in output.rglob('*'):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
        os.utime(path, (manifest['source_date_epoch'],) * 2)
    os.utime(output, (manifest['source_date_epoch'],) * 2)
    inputs = {name: digest((output / name).read_bytes()) for name in
              ('Containerfile', 'requirements-validator.lock', 'validate_capture.py')}
    return {'platform': manifest['platform'], 'base': manifest['base'],
            'source_date_epoch': manifest['source_date_epoch'], 'input_hashes': inputs,
            'wheel_hashes': {p['filename']: p['sha256'] for p in manifest['packages']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument('--wheelhouse', type=Path)
    choices.add_argument('--download', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.wheelhouse, args.download), sort_keys=True))


if __name__ == '__main__':
    main()
