"""Offline collector startup against authenticated, projected Python bytes.

Set ZEN53_PYTHON_FIXTURE to an unpacked authenticated Ubuntu fixture. Tests
copy only the pinned interpreter/Python/ELF members, never a host library tree.
This is a relocated import test, not Bubblewrap or live isolation acceptance.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import controller
import restricted_launcher as restricted
import stage_builder as builder

ROOT = Path(__file__).resolve().parent


def raw(name):
    return (ROOT / name).read_bytes()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def document(name):
    return json.loads(raw(name))


class ClosureContractTests(unittest.TestCase):
    def test_authenticated_closure_is_in_every_plan_and_pin(self):
        proof = document('PYTHON_CLOSURE_PROVENANCE.json')
        deps = document('runtime_dependencies.json')
        manifest = document('runtime_source_manifest.json')
        recovery = document('recovery_contract.json')
        pins = document('stage_pins_source.json')['files']
        self.assertEqual(proof['collector_sha256'], sha(raw('collector_v3.py')))
        self.assertEqual(proof['interpreter_sha256'], deps['interpreter_sha256'])
        self.assertEqual(len(proof['added_files']), 7)
        plan = builder.make_plan(str(ROOT), '/unused/recovery', raw('INVENTORY.json'),
                                 raw('recovery_contract.json'),
                                 raw('runtime_source_manifest.json'),
                                 raw('runtime_dependencies.json'),
                                 expected_inventory_sha=sha(raw('INVENTORY.json')))
        preflight_path = ROOT.parent/'operator-review/zen53-api-rotation-operator-v4-offline/zen53-preparation-preflight.py'
        spec = importlib.util.spec_from_file_location('closure_preflight', preflight_path)
        preflight = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preflight)
        self.assertEqual(len(preflight.routes(plan)), 62)
        for path, row in proof['added_files'].items():
            with self.subTest(path=path):
                self.assertEqual({deps['dependencies'][path]['sha256'],
                                  manifest['rootfs'+path], pins[path],
                                  recovery['dependencies'][path]['sha256'],
                                  recovery['manifest']['rootfs'+path]['sha256'],
                                  plan['regular'][path]['sha256']}, {row['sha256']})
                self.assertEqual(plan['regular'][path]['source'], path)
                self.assertEqual(plan['regular'][path]['source_uid'], 0)
                self.assertNotIn('historical_source_metadata', recovery['dependencies'][path])
                self.assertEqual(deps['dependencies'][path]['current_host_validation'],
                                 'REQUIRED_NOT_OBSERVED')
        for path, target in proof['added_symlinks'].items():
            self.assertEqual(deps['symlinks'][path], target)
            self.assertEqual(recovery['symlinks'][path], target)
        self.assertIn('libffi.so.8', deps['elf_closure'][
            '/usr/lib/python3.12/lib-dynload/_ctypes.cpython-312-x86_64-linux-gnu.so']['needed'])
        self.assertEqual(manifest['rootfs/diagnostic/pins.json'], sha(raw('stage_pins_source.json')))

    def test_manifest_dependency_hash_disagreement_is_rejected(self):
        deps = document('runtime_dependencies.json')
        deps['dependencies']['/usr/lib/python3.12/datetime.py']['sha256'] = '0'*64
        changed = json.dumps(deps).encode()
        # Even after authenticating a different outer document, members must agree.
        with mock.patch.object(restricted, 'RUNTIME_DEPENDENCIES_SHA256', sha(changed)):
            with self.assertRaisesRegex(ValueError, 'PROJECTION_RUNTIME_SET'):
                restricted.projection_contract(raw('runtime_source_manifest.json'), changed)


class ProjectedStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = os.environ.get('ZEN53_PYTHON_FIXTURE')
        if not fixture:
            raise unittest.SkipTest('requires authenticated offline ZEN53_PYTHON_FIXTURE')
        cls.fixture = Path(fixture).resolve()
        cls.deps = document('runtime_dependencies.json')
        cls.proof = document('PYTHON_CLOSURE_PROVENANCE.json')
        # Parse the real collector; stop immediately before its first observation.
        source = raw('collector_v3.py').decode()
        tree = ast.parse(source)
        boundary = next(n.lineno for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'label' for t in n.targets))
        assert all(n.lineno < boundary for n in ast.walk(tree)
                   if isinstance(n, (ast.Import, ast.ImportFrom))), 'unexercised late import'
        cls.prefix = '\n'.join(source.splitlines()[:boundary-1]) + '\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projection = Path(self.tmp.name) / 'projection'
        self.projection.mkdir()
        required = {p for p in self.deps['dependencies'] if
                    p.startswith('/usr/lib/python3.12/') or
                    p in {'/usr/bin/python3.12', '/usr/lib/x86_64-linux-gnu/libc.so.6',
                          '/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
                          '/usr/lib/x86_64-linux-gnu/libm.so.6',
                          '/usr/lib/x86_64-linux-gnu/libz.so.1.3',
                          '/usr/lib/x86_64-linux-gnu/libexpat.so.1.9.1',
                          '/usr/lib/x86_64-linux-gnu/libcrypto.so.3',
                          '/usr/lib/x86_64-linux-gnu/libffi.so.8.1.4'}}
        for path in sorted(required):
            data = (self.fixture / path.lstrip('/')).read_bytes()
            self.assertEqual(sha(data), self.deps['dependencies'][path]['sha256'], path)
            destination = self.projection / path.lstrip('/')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o555 if path.endswith('python3.12') or 'ld-linux' in path else 0o444)
        for path, target in self.deps['symlinks'].items():
            if path.startswith('/usr/lib/x86_64-linux-gnu/'):
                if target.startswith('/'):
                    target = os.path.relpath(target, os.path.dirname(path))
                if (self.projection / path.lstrip('/')).with_name(target).exists():
                    (self.projection / path.lstrip('/')).symlink_to(target)
        self.env = {'LC_ALL': 'C', 'PATH': '/nonexistent',
                    'LOCPATH': str(self.projection / 'absent'),
                    'GCONV_PATH': str(self.projection / 'absent')}

    def argv(self):
        root = str(self.projection)
        # Exact pinned loader/interpreter; -I ignores PYTHONPATH/PYTHONHOME.
        # Verify every imported module and mapped ELF below; fallback is failure.
        check = '''
assert {'_datetime', '_struct', 'select'} <= set(sys.builtin_module_names)
assert now().endswith('+00:00')
assert struct.unpack('<I', struct.pack('<I', 123))[0] == 123
assert libc.getpid() == os.getpid()
assert select.select([], [], [], 0) == ([], [], [])
assert digest(b'x') == hashlib.sha256(b'x').hexdigest()
root = sys.argv[1]
assert all(p.startswith(root + '/') for p in sys.path), sys.path
for name, module in tuple(sys.modules.items()):
    spec = getattr(module, '__spec__', None)
    origin = getattr(spec, 'origin', None)
    assert origin in (None, 'built-in', 'frozen') or origin.startswith(root + '/'), (name, origin)
with open('/proc/self/maps') as maps:
    for line in maps:
        fields = line.split()
        if len(fields) >= 6 and fields[5].startswith('/'):
            assert fields[5].startswith(root + '/'), ('HOST_MAPPING', fields[5])
print('PROJECTED_COLLECTOR_STARTUP_OK')
'''
        return [root+'/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
                '--inhibit-cache', '--library-path', root+'/usr/lib/x86_64-linux-gnu',
                root+'/usr/bin/python3.12', '-I', '-S', '-B', '-c', self.prefix+check, root]

    def run_startup(self):
        return subprocess.run(self.argv(), env=self.env, capture_output=True, timeout=10)

    def test_real_collector_startup_has_no_host_library_fallback(self):
        result = self.run_startup()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b'PROJECTED_COLLECTOR_STARTUP_OK\n')

    def test_each_added_file_and_link_is_required(self):
        for path in list(self.proof['added_files']) + list(self.proof['added_symlinks']):
            with self.subTest(path=path):
                target = self.projection / path.lstrip('/')
                saved = target.with_name(target.name+'.held')
                target.rename(saved)
                try:
                    result = self.run_startup()
                    self.assertNotEqual(result.returncode, 0, path)
                finally:
                    saved.rename(target)

    def test_original_import_failure_aborts_host_and_preserves_unknown_cleanup(self):
        for path in self.proof['added_files']:
            (self.projection / path.lstrip('/')).unlink()
        result = self.run_startup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No module named 'datetime'", result.stderr)
        children = []
        def spawn(argv, **kwargs):
            child = subprocess.Popen(argv, env=self.env, **kwargs)
            children.append(child)
            return child
        record = controller.run(
            [sys.executable, '-I', '-S', '-B', str(ROOT/'fake_child.py'), 'host', 'ok'],
            self.argv(), spawn_host=spawn, spawn_restricted=spawn,
            startup_seconds=2, hold_seconds=3)
        self.assertEqual(record['outcome'], 'BLOCKED')
        self.assertEqual(record['reason'], 'RESTRICTED_FAILED')
        self.assertEqual(record['host_exit'], -9)
        self.assertEqual(record['restricted_exit'], 1)
        self.assertIn(b"No module named 'datetime'", record['streams']['restricted_stderr']['raw'])
        self.assertNotIn('FINISH_WRITTEN', [e['event'] for e in record['events']])
        self.assertTrue(all(child.poll() is not None for child in children))
        # Child reaping alone is deliberately not complete scope/lock acceptance.
        self.assertNotIn('cleanup_state', record)
        real_stop = controller._stop
        def uncertain(child):
            real_stop(child)
            return None
        with mock.patch.object(controller, '_stop', side_effect=uncertain):
            record = controller.run(
                [sys.executable, '-I', '-S', '-B', str(ROOT/'fake_child.py'), 'host', 'ok'],
                self.argv(), spawn_host=spawn, spawn_restricted=spawn,
                startup_seconds=2, hold_seconds=3)
        self.assertEqual(record['outcome'], 'BLOCKED')
        self.assertEqual(record['reason'], 'CLEANUP_UNCERTAIN')


if __name__ == '__main__':
    unittest.main()
