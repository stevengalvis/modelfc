"""Offline lock policy, local pip rejection tests, and CI inventory check."""
import hashlib
import importlib.metadata
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = dict(item.split('==') for item in (
    'fastapi==0.141.1 uvicorn==0.53.0 httpx==0.28.1 pydantic==2.13.5 '
    'starlette==1.6.0 annotated-doc==0.0.5 typing-inspection==0.4.4 '
    'typing-extensions==4.16.0 annotated-types==0.8.0 pydantic-core==2.46.5 '
    'anyio==4.15.1 idna==3.20 click==8.5.0 h11==0.16.0 '
    'httpcore==1.0.9 certifi==2026.7.22'
).split())
BOOTSTRAP = {'pip', 'setuptools'}
OPTIONS = {'--require-hashes', '--only-binary=:all:'}
ENTRY = re.compile(r'([a-z0-9]+(?:-[a-z0-9]+)*)==([0-9]+(?:\.[0-9]+)+) --hash=sha256:([0-9a-f]{64})')


def read_lock(text):
    packages, options = {}, set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line in OPTIONS:
            if line in options:
                raise ValueError('duplicate option')
            options.add(line)
            continue
        match = ENTRY.fullmatch(line)
        if not match or match[1] in packages:
            raise ValueError('invalid or duplicate requirement')
        packages[match[1]] = match[2]
    if options != OPTIONS:
        raise ValueError('required installation policy missing')
    return packages


def check_installed():
    expected = read_lock((ROOT / 'requirements-deploy.lock').read_text())
    actual = {}
    for dist in importlib.metadata.distributions():
        name = re.sub(r'[-_.]+', '-', dist.metadata['Name']).lower()
        if name not in BOOTSTRAP:
            if name in actual:
                raise ValueError('duplicate installed distribution')
            actual[name] = dist.version
    if actual != expected:
        raise ValueError('installed distributions differ from deployment lock')
    print(f'Installed inventory matches {len(expected)} locked distributions')


class DeploymentLockTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / 'requirements-deploy.lock').read_text()

    def test_exact_reviewed_graph_and_direct_pydantic(self):
        self.assertEqual(read_lock(self.text), EXPECTED)
        self.assertEqual(read_lock(self.text)['pydantic'], '2.13.5')

    def test_rejects_missing_policy_hash_unpinned_and_duplicates(self):
        entry = next(line for line in self.text.splitlines() if ENTRY.fullmatch(line))
        for bad in (self.text.replace('--require-hashes\n', ''),
                    self.text.replace('--only-binary=:all:\n', ''),
                    self.text.replace(entry, entry.split(' --hash')[0]),
                    self.text.replace(entry, entry.replace('==', '>=')),
                    self.text + entry + '\n'):
            with self.subTest(bad=bad[-100:]), self.assertRaises(ValueError):
                read_lock(bad)

    def test_rejects_additional_configuration_and_non_package_sources(self):
        for line in ('-e .', 'git+https://example.invalid/repo', './local.whl',
                     '-r other.txt', '-c constraints.txt', '--index-url https://example.invalid',
                     '--extra-index-url https://example.invalid', '--find-links .',
                     '--no-deps', 'fastapi[standard]==0.141.1', 'name @ https://example.invalid/a.whl'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                read_lock(self.text + line + '\n')

    def test_developer_ranges_are_satisfied(self):
        locked = read_lock(self.text)
        for line in (ROOT / 'requirements.txt').read_text().splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            match = re.fullmatch(r'([a-z0-9-]+)>=(\d+(?:\.\d+)*),<(\d+(?:\.\d+)*)', line)
            self.assertIsNotNone(match, 'new developer requirement syntax needs explicit validation')
            parts = lambda value: tuple(int(x) for x in value.split('.')) + (0,) * (4 - len(value.split('.')))
            self.assertGreaterEqual(parts(locked[match[1]]), parts(match[2]))
            self.assertLess(parts(locked[match[1]]), parts(match[3]))


class OfflinePipTests(unittest.TestCase):
    """Synthetic local archives only. Never import or install fixture packages."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def wheel(self, name, requires=''):
        path = self.path / f'{name}-1.0-py3-none-any.whl'
        info = f'{name}-1.0.dist-info'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(info + '/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n' + requires + '\n')
            archive.writestr(info + '/WHEEL', 'Wheel-Version: 1.0\nGenerator: offline-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr(info + '/RECORD', '')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def pip(self, text):
        lock = self.path / 'lock.txt'
        lock.write_text(text)
        env = {key: value for key, value in os.environ.items() if not key.startswith(('PIP_', 'PYTHON'))}
        env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'pip', 'install', '--dry-run', '--ignore-installed',
             '--no-index', '--find-links', str(self.path), '--no-cache-dir',
             '--require-hashes', '--only-binary=:all:', '-r', str(lock)],
            env=env, capture_output=True, text=True, timeout=30)

    def test_valid_local_wheel_control(self):
        digest = self.wheel('lock_probe')
        result = self.pip(f'lock-probe==1.0 --hash=sha256:{digest}\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_hash_rejected(self):
        self.wheel('lock_probe')
        result = self.pip('lock-probe==1.0 --hash=sha256:' + '0' * 64 + '\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DO NOT MATCH THE HASHES', result.stderr)

    def test_missing_transitive_requirement_rejected(self):
        digest = self.wheel('lock_probe', 'Requires-Dist: lock-child==1.0\n')
        child = self.wheel('lock_child')
        result = self.pip(f'lock-probe==1.0 --hash=sha256:{digest}\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Hashes are required', result.stderr)
        self.assertIn('lock-child', result.stderr)
        complete = self.pip(f'lock-probe==1.0 --hash=sha256:{digest}\nlock-child==1.0 --hash=sha256:{child}\n')
        self.assertEqual(complete.returncode, 0, complete.stderr)

    def test_sdist_only_never_executes_build(self):
        marker = self.path / 'BUILD_EXECUTED'
        source = self.path / 'lock_probe-1.0.tar.gz'
        code = f'from pathlib import Path\nPath({str(marker)!r}).write_text("unsafe")\n'.encode()
        with tarfile.open(source, 'w:gz') as archive:
            info = tarfile.TarInfo('lock_probe-1.0/setup.py')
            info.size = len(code)
            archive.addfile(info, io.BytesIO(code))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.pip(f'lock-probe==1.0 --hash=sha256:{digest}\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No matching distribution found', result.stderr)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    if sys.argv[1:] == ['--check-installed']:
        check_installed()
    else:
        unittest.main()
