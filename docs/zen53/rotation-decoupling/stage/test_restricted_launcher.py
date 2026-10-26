"""Pure argv tests. Bubblewrap and the collector are never executed."""
import importlib.util
import hashlib
import os
import pathlib
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'zen53_restricted_launcher', ROOT / 'restricted_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def valid(**changes):
    args = {'observed_bwrap_sha256': launcher.BWRAP_SHA256,
            'observed_collector_sha256': launcher.COLLECTOR_SHA256,
            'runtime_manifest_sha256': launcher.RUNTIME_MANIFEST_SHA256,
            'source_sha256': launcher.SOURCE_SHA256}
    args.update(changes)
    return launcher.build_argv('/tmp/zen53-owner-projection-new/rootfs', **args)


class LauncherTests(unittest.TestCase):
    def test_two_file_binds_and_isolation_flags(self):
        argv = valid()
        self.assertEqual(argv[0], '/usr/bin/bwrap')
        self.assertEqual(argv.count('--ro-bind'), 3)
        pairs = [argv[i+1:i+3] for i, item in enumerate(argv)
                 if item == '--ro-bind']
        self.assertEqual(pairs, [
            ('/tmp/zen53-owner-projection-new/rootfs', '/'),
            (launcher.SOURCES[0], launcher.SOURCES[0]),
            (launcher.SOURCES[1], launcher.SOURCES[1])])
        for flag in ('--unshare-user', '--unshare-pid', '--unshare-net',
                     '--unshare-ipc', '--die-with-parent', '--clearenv',
                     '--proc', '--remount-ro', '--cap-drop'):
            self.assertIn(flag, argv)
        self.assertNotIn('--bind', argv)
        self.assertNotIn('--ro-bind-try', argv)
        self.assertEqual(argv[-6:], ('/usr/bin/python3', '-I', '-S', '-B',
                                     launcher.COLLECTOR, 'restricted'))
        self.assertEqual(set(launcher.expected_projection_additions()), {
            launcher.COLLECTOR, *launcher.SOURCES})

    def test_bad_pins_and_paths_reject(self):
        for path in ('relative', '/', '/tmp/../bad', '/tmp//bad',
                     '/tmp/with\x00nul'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                launcher.build_argv(path,
                    observed_bwrap_sha256=launcher.BWRAP_SHA256,
                    observed_collector_sha256=launcher.COLLECTOR_SHA256,
                    runtime_manifest_sha256=launcher.RUNTIME_MANIFEST_SHA256,
                    source_sha256=launcher.SOURCE_SHA256)
        for override in ({'observed_bwrap_sha256': '0'*64},
                         {'observed_collector_sha256': '0'*64},
                         {'runtime_manifest_sha256': '0'*64},
                         {'source_sha256': ('0'*64,)*2}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                valid(**override)

    def test_projection_exact_reviewed_source_set(self):
        manifest = (ROOT/'runtime_source_manifest.json').read_bytes()
        dependencies = (ROOT/'runtime_dependencies.json').read_bytes()
        contract = launcher.projection_contract(manifest, dependencies)
        self.assertEqual((len(contract['regular']),
                          len(contract['symlinks']),
                          len(contract['additions'])), (44, 9, 3))
        self.assertIn('/usr/bin/python3.12', contract['regular'])
        self.assertEqual(contract['symlinks']['/usr/bin/python3'],
                         'python3.12')
        with self.assertRaises(ValueError):
            launcher.projection_contract(manifest+b'\n', dependencies)
        with self.assertRaises(ValueError):
            launcher.projection_contract(manifest, dependencies+b'\n')

    def test_exact_projection_tree_and_tampering(self):
        data = b'pure-test-file'
        contract = {
            'regular': {'/alpha/file': {'sha256': hashlib.sha256(data).hexdigest(),
                                        'mode': '0o444'}},
            'symlinks': {'/alias': 'alpha'},
            'additions': {'/diagnostic/capture_ownership_v3.py':
                          {'kind': 'file', 'sha256': hashlib.sha256(b'collector').hexdigest(),
                           'mode': '0444'},
                          launcher.SOURCES[0]:
                          {'kind': 'bind_target_file', 'sha256': launcher.EMPTY_SHA256,
                           'mode': '0444'}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)/'rootfs'
            root.mkdir()
            for directory in sorted(launcher.expected_directories(contract),
                                    key=lambda x: x.count('/')):
                if directory != '/':
                    (root/directory.lstrip('/')).mkdir(exist_ok=True)
            (root/'alpha/file').write_bytes(data)
            (root/'diagnostic/capture_ownership_v3.py').write_bytes(b'collector')
            target = root/launcher.SOURCES[0].lstrip('/')
            target.write_bytes(b'')
            (root/'alias').symlink_to('alpha')
            for file in (root/'alpha/file',
                         root/'diagnostic/capture_ownership_v3.py', target):
                file.chmod(0o444)
            for directory in sorted(root.rglob('*'), reverse=True):
                if directory.is_dir():
                    directory.chmod(0o555)
            root.chmod(0o555)
            kwargs = {'owner_uid': os.geteuid(), 'owner_gid': os.getegid(),
                      'root_uid': os.stat('/').st_uid}
            result = launcher.verify_projection_tree(str(root), contract, **kwargs)
            self.assertEqual((result['regular_files'], result['symlinks']), (3, 1))
            target.chmod(0o644)
            with self.assertRaises(ValueError):
                launcher.verify_projection_tree(str(root), contract, **kwargs)
            target.write_bytes(b'not-empty')
            target.chmod(0o444)
            with self.assertRaises(ValueError):
                launcher.verify_projection_tree(str(root), contract, **kwargs)
            target.chmod(0o644)
            target.write_bytes(b'')
            target.chmod(0o444)
            real_stat = launcher.os.stat
            def wrong_link_group(path, *args, **kw):
                value = real_stat(path, *args, **kw)
                if path == 'alias' and kw.get('follow_symlinks') is False:
                    return types.SimpleNamespace(st_mode=value.st_mode,
                                                 st_uid=value.st_uid,
                                                 st_gid=value.st_gid+1)
                return value
            with mock.patch.object(launcher.os, 'stat',
                                   side_effect=wrong_link_group):
                with self.assertRaises(ValueError):
                    launcher.verify_projection_tree(str(root), contract,
                                                    **kwargs)
            (root/'alpha/file').chmod(0o644)
            with self.assertRaises(ValueError):
                launcher.verify_projection_tree(str(root), contract, **kwargs)
            with self.assertRaisesRegex(ValueError, 'PROJECTION_DEADLINE'):
                launcher.verify_projection_tree(
                    str(root), contract, clock_ns=iter(
                        (0, 90_000_000_000)).__next__, **kwargs)

    def test_release_file_sources_ignore_unexposed_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = pathlib.Path(tmp)/'release'
            (release/'bin').mkdir(parents=True)
            binary = release/'bin/codex'
            package = release/'codex-package.json'
            binary.write_bytes(b'codex')
            package.write_bytes(b'{}')
            binary.chmod(0o755)
            package.chmod(0o644)
            kwargs = {'owner_uid': os.geteuid(), 'owner_gid': os.getegid(),
                      'root_uid': os.stat('/').st_uid,
                      'expected_size': (5, 2),
                      'expected_inode': (binary.stat().st_ino, package.stat().st_ino),
                      'expected_device': binary.stat().st_dev}
            with mock.patch.object(launcher, 'SOURCE_SHA256',
                    (hashlib.sha256(b'codex').hexdigest(),
                     hashlib.sha256(b'{}').hexdigest())):
                self.assertEqual(launcher.verify_release_files(
                    str(release), **kwargs)['verified_files'], 2)
                (release/'unreviewed-token').write_bytes(b'x')
                (release/'bin/codex-code-mode-host').write_bytes(b'other')
                real_open = launcher.os.open
                seen = []
                def noatime_open(name, flags, *args, **options):
                    if name in ('codex', 'codex-package.json') and not flags & os.O_DIRECTORY:
                        seen.append(name)
                        self.assertTrue(flags & os.O_NOATIME)
                    return real_open(name, flags, *args, **options)
                with mock.patch.object(launcher.os, 'open', side_effect=noatime_open):
                    self.assertEqual(launcher.verify_release_files(
                        str(release), **kwargs)['verified_files'], 2)
                self.assertEqual(seen, ['codex', 'codex-package.json'])
                def reject_noatime(name, flags, *args, **options):
                    if name == 'codex' and not flags & os.O_DIRECTORY:
                        self.assertTrue(flags & os.O_NOATIME)
                        raise PermissionError('synthetic O_NOATIME denial')
                    return real_open(name, flags, *args, **options)
                with mock.patch.object(launcher.os, 'open', side_effect=reject_noatime):
                    with self.assertRaises(PermissionError):
                        launcher.verify_release_files(str(release), **kwargs)
                binary.write_bytes(b'other')
                with self.assertRaises(ValueError):
                    launcher.verify_release_files(str(release), **kwargs)
                binary.write_bytes(b'codex')
                binary.unlink()
                binary.symlink_to('codex-code-mode-host')
                with self.assertRaises(ValueError):
                    launcher.verify_release_files(str(release), **kwargs)

    def test_release_nested_mount_rejected(self):
        base = '30 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n'
        self.assertTrue(launcher.verify_release_mount_topology(base))
        nested = (f'31 30 8:1 /bin {launcher.RELEASE}/bin ro - ext4 '
                  '/dev/sda1 ro\n')
        with self.assertRaises(ValueError):
            launcher.verify_release_mount_topology(base + nested)
        for bad in ('', 'garbage\n',
                    '31 30 8:1 / /other rw - ext4 /dev/sda1 rw\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                launcher.verify_release_mount_topology(bad)


if __name__ == '__main__':
    unittest.main()
