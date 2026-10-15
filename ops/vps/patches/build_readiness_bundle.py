"""Reproduce the proposal archive locally; never execute or install its payload."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[3]
PINS = {
    'tests/fixtures/vps/deploy_main_5c1b.py.txt': '5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5',
    'ops/vps/patches/deploy-main-5c1b-readiness.patch': 'eb894741459501bd57d5a29cd9eebde44bc8f23b33d6c48825df52aa8539b16a',
    'ops/vps/api_readiness.py': '2c462f7b33bc605a9e45a2497d31522a4d332272f776e7d8ac51145d4dd1c5d0',
}
CANDIDATE = '2a82ca7502724c207d4d4bd6af25f03f72ba687c868667366e6dee23f26b2db8'
RULE = b'modelfc-deploy ALL=(root) NOPASSWD: NOSETENV: /usr/bin/python3 -I -B /opt/modelfc-deploy/api_readiness.py\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def build(root=ROOT):
    source = {}
    for name, expected in PINS.items():
        raw = (root / name).read_bytes()
        if sha(raw) != expected:
            raise ValueError('source hash mismatch: ' + name)
        source[name] = raw
    with tempfile.TemporaryDirectory(prefix='modelfc-readiness-bundle-') as temporary:
        target = Path(temporary) / 'deploy_main.py'
        target.write_bytes(source['tests/fixtures/vps/deploy_main_5c1b.py.txt'])
        result = subprocess.run(['patch', '--batch', '--fuzz=0', str(target)],
            input=source['ops/vps/patches/deploy-main-5c1b-readiness.patch'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
        candidate = target.read_bytes()
        if sha(candidate) != CANDIDATE:
            raise ValueError('candidate hash mismatch')
    files = {
        'INSTALLATION.md': (root / 'ops/vps/patches/READINESS-INSTALLATION.md').read_bytes(),
        'deploy_main.original.py': source['tests/fixtures/vps/deploy_main_5c1b.py.txt'],
        'deploy-main-5c1b-readiness.patch': source['ops/vps/patches/deploy-main-5c1b-readiness.patch'],
        'deploy_main.candidate.py': candidate,
        'api_readiness.py': source['ops/vps/api_readiness.py'],
        'modelfc-api-readiness.sudoers': RULE,
    }
    files['SHA256SUMS'] = ''.join(sha(files[name]) + '  ' + name + '\n'
                                 for name in sorted(files)).encode('ascii')
    output = io.BytesIO()
    # Stored entries avoid compressor/version differences; fixed order/time/mode.
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 -I -B build_readiness_bundle.py NEW_ARCHIVE.zip')
    payload = build()
    # Never overwrite an earlier reviewed artifact.
    with Path(sys.argv[1]).open('xb') as destination:
        destination.write(payload)
    print('bundle_sha256=' + sha(payload))


if __name__ == '__main__':
    main()
