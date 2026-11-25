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
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = Path(__file__).resolve().parents[1]
SNAPSHOT_PREFIX = 'https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/'
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS_PER_URL = 4
BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def candidate_urls(row):
    """Official Ubuntu archive URL first, then pinned Ubuntu snapshot fallback."""
    official = row['url']
    path = urllib.parse.urlsplit(official).path.removeprefix('/ubuntu/')
    snapshot = SNAPSHOT_PREFIX + path
    return (official, snapshot) if official != snapshot else (snapshot,)


def download_archive(row, *, opener=urllib.request.urlopen, sleep=time.sleep,
                     max_attempts=MAX_ATTEMPTS_PER_URL):
    last_error = None
    for url in candidate_urls(row):
        for attempt in range(max_attempts):
            try:
                with opener(url, timeout=60) as response:
                    data = response.read(row['bytes'] + 1)
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code in RETRYABLE_HTTP_CODES and attempt + 1 < max_attempts:
                    sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                if error.code == 404 or error.code in RETRYABLE_HTTP_CODES:
                    break
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt + 1 < max_attempts:
                    sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                break
            if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('ARCHIVE_HASH')
            return data
    raise last_error


def prepare(cache, output, download=False, *, opener=urllib.request.urlopen,
            sleep=time.sleep):
    proof = json.loads((BASE/'stage/PYTHON_CLOSURE_PROVENANCE.json').read_bytes())
    deps = json.loads((BASE/'stage/runtime_dependencies.json').read_bytes())['dependencies']
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=False)
    for row in proof['packages']:
        package = cache/(row['name']+'.deb')
        if not package.exists() and download:
            data = download_archive(row, opener=opener, sleep=sleep)
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
