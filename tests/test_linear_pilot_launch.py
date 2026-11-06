"""Offline launcher boundaries; no root, systemd, credentials or HTTP required."""
import contextlib
import io
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker.linear_pilot import launch


class LauncherTests(unittest.TestCase):
    def test_minimal_package_imports_with_isolated_standard_library(self):
        source = Path(launch.__file__).parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'bounded_worker'
            package.mkdir()
            for name in launch.MODULES:
                (package / name).write_bytes((source / name).read_bytes())
            result = subprocess.run([
                sys.executable, '-I', '-B', '-S', '-c',
                'import sys; sys.path.insert(0, sys.argv[1]); '
                'from bounded_worker.linear_poll import run; assert callable(run)', tmp,
            ], capture_output=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b'')

    def test_release_rejects_unexpected_importable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ('a' * 40)
            package = root / 'bounded_worker'
            package.mkdir(parents=True)
            (root / 'launch.py').touch()
            for name in launch.MODULES:
                (package / name).touch()
            with patch.object(launch, 'BASE', Path(tmp)), patch.object(launch, 'protected'):
                launch.validate_release(root)
                (package / 'sitecustomize.py').touch()
                with self.assertRaises(ValueError):
                    launch.validate_release(root)
                (package / 'sitecustomize.py').unlink()
                (package / 'linear_poll.py').unlink()
                with self.assertRaises(ValueError):
                    launch.validate_release(root)

    def test_protection_rejects_owner_write_links_and_ancestor(self):
        path = Path('/opt/pilot/file.py')
        good = SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=0, st_nlink=1)
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_nlink=1)
        for bad in (
            SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=1000, st_nlink=1),
            SimpleNamespace(st_mode=stat.S_IFREG | 0o620, st_uid=0, st_nlink=1),
            SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=0, st_nlink=2),
            SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_nlink=1),
        ):
            with patch.object(Path, 'resolve', return_value=path), patch.object(Path, 'lstat', return_value=bad):
                with self.assertRaises(ValueError):
                    launch.protected(path)
        with patch.object(Path, 'resolve', return_value=path), patch.object(
                Path, 'lstat', side_effect=[good, directory, directory, directory]):
            launch.protected(path)
        with patch.object(Path, 'resolve', return_value=path), patch.object(
                Path, 'lstat', side_effect=[good, SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)]):
            with self.assertRaises(ValueError):
                launch.protected(path)

    def test_settings_cannot_redirect_credentials_or_state(self):
        config = dict(state_root=launch.STATE, host_policy=str(launch.CONFIG / 'host.json'),
                      grant=str(launch.CONFIG / 'grant.json'),
                      reader_token_file=str(launch.CONFIG / 'credentials/linear.key'),
                      sink_token_file=str(launch.CONFIG / 'credentials/linear.key'),
                      reader_oauth=False, sink_oauth=False)
        launch.validate_settings(config)
        for key in config:
            with self.assertRaises(ValueError):
                launch.validate_settings({**config, key: '/tmp/untrusted'})

    def test_unprivileged_or_nonisolated_invocation_blocks_before_import(self):
        for uid, isolated, no_site in ((1000, 1, 1), (0, 0, 1), (0, 1, 0)):
            fake_sys = SimpleNamespace(argv=['launch.py'], flags=SimpleNamespace(
                isolated=isolated, no_site=no_site), dont_write_bytecode=True)
            with patch.object(launch, 'sys', fake_sys), patch.object(launch.os, 'geteuid', return_value=uid), \
                    patch.object(launch, 'validate_release') as validate, contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(launch.main(), 1)
                self.assertEqual(out.getvalue(), '{"status":"blocked"}\n')
                validate.assert_not_called()

    def test_unit_has_deadline_and_no_activation(self):
        unit = (Path(launch.__file__).parent / 'modelfc-linear-status.service.in').read_text()
        for setting in ('-I -B -S', 'Restart=no', 'TimeoutStartSec=90s',
                        'KillMode=control-group', 'CapabilityBoundingSet=\n',
                        'ReadWritePaths=/var/lib/modelfc-linear-status'):
            self.assertIn(setting, unit)
        self.assertNotIn('[Install]', unit)

    def test_run_has_clean_environment_and_sanitized_outcomes(self):
        def run(path):
            self.assertEqual(path, launch.CONFIG / 'settings.json')
            self.assertEqual(dict(launch.os.environ), {'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
            return outcome

        modules = {'bounded_worker': SimpleNamespace(),
                   'bounded_worker.bridge_security': SimpleNamespace(protected_json=lambda _: {}),
                   'bounded_worker.linear_poll': SimpleNamespace(run=run)}
        for outcome, expected, code in (('reported', 'reported', 0),
                                        ('failed_reported', 'failed_reported', 1),
                                        ('private exception text', 'blocked', 1)):
            fake_sys = SimpleNamespace(argv=['launch.py'], path=[], flags=SimpleNamespace(
                isolated=1, no_site=1), dont_write_bytecode=True)
            with patch.object(launch, 'sys', fake_sys), patch.dict(sys.modules, modules), \
                    patch.object(launch.os, 'geteuid', return_value=0), \
                    patch.object(launch.os, 'chdir'), patch.object(launch.os, 'umask'), \
                    patch.dict(launch.os.environ, {'SECRET': 'fixture'}), \
                    patch.object(launch, 'validate_release'), patch.object(launch, 'validate_settings'), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(launch.main(), code)
                self.assertEqual(out.getvalue(), '{"status":"' + expected + '"}\n')
