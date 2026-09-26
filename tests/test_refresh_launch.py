"""Offline launcher pinning and repository unit checks, not host acceptance."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr
from io import StringIO

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('refresh_launch', ROOT / 'ops/vps/refresh_launch.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class RefreshLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.releases = self.root / 'releases'
        self.releases.mkdir()
        self.config = self.root / 'etc/modelfc/corner_data.json'
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({'data_directory': str(launcher.HISTORY)}))
        self.a = self.make_release('a')
        self.b = self.make_release('b')
        for stub in (patch.object(launcher, 'RELEASES', self.releases),
                     patch.object(launcher, 'CONFIG', self.config),
                     patch.object(launcher.pwd, 'getpwnam', return_value=type('Account', (), {'pw_uid': os.getuid()})())):
            stub.start()
            self.addCleanup(stub.stop)
        # Production config checks root ownership; no production account required.
        original = launcher.protected
        def fixture_protected(path, owner, **kwargs):
            return original(path, os.getuid() if path in (self.config, self.config.parent, self.config.parent.parent) else owner, **kwargs)
        stub = patch.object(launcher, 'protected', side_effect=fixture_protected)
        stub.start()
        self.addCleanup(stub.stop)

    def make_release(self, letter):
        release = self.releases / (letter * 40 + '-123456abcdef')
        for name in ('.git', 'src/modelfc', '.venv/bin'):
            (release / name).mkdir(parents=True)
        (release / '.git/modelfc-deployed-sha').write_text(letter * 40)
        (release / '.git/modelfc-deployed-sha').chmod(0o444)
        (release / 'src/modelfc/corner_refresh.py').touch()
        (release / '.venv/pyvenv.cfg').write_text('version = 3.12\n')
        (release / '.venv/bin/python').symlink_to(sys.executable)
        return release

    def test_pins_physical_release_across_promotion_and_cleans_environment(self):
        current = self.root / 'current'
        current.symlink_to(self.a)
        def entered_a():
            current.unlink()
            current.symlink_to(self.b)
            return self.a
        output = StringIO()
        with patch.object(Path, 'cwd', side_effect=entered_a) as cwd, patch.object(os, 'execve') as execute, patch.dict(
                os.environ, {'PYTHONPATH': '/evil', 'ODDSPAPI_API_KEY': 'fake', 'LD_PRELOAD': '/evil', 'PWD': str(current)}), redirect_stderr(output):
            launcher.launch()
            cwd.assert_called_once()
        executable, argv, env = execute.call_args.args
        self.assertEqual(executable, str(self.a / '.venv/bin/python'))
        self.assertEqual(argv, [executable, '-B', '-P', '-s', '-m', 'modelfc.corner_refresh',
                               '--config', str(self.config), '--validator-read-user', 'modelfc-validator'])
        self.assertEqual(env, {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
                              'PYTHONPATH': str(self.a / 'src'), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertNotEqual(executable, str((self.a / '.venv/bin/python').resolve()))
        record = json.loads(output.getvalue())
        self.assertEqual(record, {'event': 'modelfc_refresh_start', 'release': str(self.a),
            'sha': 'a' * 40, 'interpreter': executable, 'import_path': str(self.a / 'src'),
            'uid': os.getuid(), 'gid': os.getgid(), 'config': str(self.config)})
        self.assertNotIn('fake', output.getvalue())
        with patch.object(Path, 'cwd', return_value=self.b), patch.object(os, 'execve') as next_run, redirect_stderr(StringIO()):
            launcher.launch()
        self.assertEqual(next_run.call_args.args[0], str(self.b / '.venv/bin/python'))

    def test_real_subprocess_prefers_pinned_src_over_release_root(self):
        intended = self.a / 'src/modelfc'
        (intended / '__init__.py').write_text('')
        (intended / 'corner_refresh.py').write_text(
            "import json,sys; print(json.dumps({'module':'pinned-src','argv':sys.argv[1:]}))\n")
        conflict = self.a / 'modelfc'
        conflict.mkdir()
        (conflict / '__init__.py').write_text('raise RuntimeError("release-root-imported")\n')
        (conflict / 'corner_refresh.py').write_text('raise RuntimeError("release-root-imported")\n')
        script = r"""
import importlib.util, os, pathlib
spec = importlib.util.spec_from_file_location('fixture_launcher', os.environ['LAUNCHER_SOURCE'])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.RELEASES = pathlib.Path(os.environ['FIXTURE_RELEASES'])
launcher.CONFIG = pathlib.Path(os.environ['FIXTURE_CONFIG'])
launcher.pwd.getpwnam = lambda name: type('Account', (), {'pw_uid': os.getuid()})()
original = launcher.protected
def fixture_protected(path, owner, **kwargs):
    config = launcher.CONFIG
    if path in (config, config.parent, config.parent.parent):
        owner = os.getuid()
    return original(path, owner, **kwargs)
launcher.protected = fixture_protected
launcher.launch()
"""
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'LAUNCHER_SOURCE': str(ROOT / 'ops/vps/refresh_launch.py'),
               'FIXTURE_RELEASES': str(self.releases), 'FIXTURE_CONFIG': str(self.config)}
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script], cwd=self.a,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, {'module': 'pinned-src', 'argv': [
            '--config', str(self.config), '--validator-read-user', 'modelfc-validator']})
        record = json.loads(result.stderr)
        self.assertEqual(record['release'], str(self.a))
        self.assertEqual(record['sha'], 'a' * 40)
        self.assertEqual(record['interpreter'], str(self.a / '.venv/bin/python'))
        self.assertEqual(record['import_path'], str(self.a / 'src'))
        self.assertNotIn('release-root-imported', result.stdout + result.stderr)

    def test_invalid_release_marker_or_required_path_never_executes(self):
        with patch.object(Path, 'cwd', return_value=self.root), patch.object(os, 'execve') as execute:
            with self.assertRaises(ValueError): launcher.launch()
            execute.assert_not_called()
        marker = self.a / '.git/modelfc-deployed-sha'
        for value in ('bad', 'b' * 40, 'a' * 40 + '\n'):
            marker.chmod(0o644); marker.write_text(value); marker.chmod(0o444)
            with self.subTest(value=value), patch.object(Path, 'cwd', return_value=self.a), patch.object(os, 'execve') as execute:
                with self.assertRaises(ValueError): launcher.launch()
                execute.assert_not_called()
        marker.unlink()
        marker.symlink_to(self.b / '.git/modelfc-deployed-sha')
        with patch.object(Path, 'cwd', return_value=self.a), patch.object(os, 'execve') as execute:
            with self.assertRaises(OSError): launcher.launch()
            execute.assert_not_called()
        (self.b / '.venv/pyvenv.cfg').unlink()
        with patch.object(Path, 'cwd', return_value=self.b), patch.object(os, 'execve') as execute:
            with self.assertRaises(OSError): launcher.launch()
            execute.assert_not_called()

    def test_service_and_timer_contract(self):
        unit = (ROOT / 'deploy/modelfc-corner-refresh.service').read_text()
        for setting in ('User=modelfc-runtime', 'Group=modelfc-runtime', 'WorkingDirectory=/srv/modelfc/current',
                        'UMask=0077', 'ProtectHome=yes', 'ProtectSystem=strict', 'StandardInput=null',
                        'ReadOnlyPaths=/srv/modelfc /etc/modelfc', 'ReadWritePaths=/var/lib/modelfc/history',
                        'ExecStart=/usr/bin/python3 -I -B /usr/local/libexec/modelfc-refresh-launch.py',
                        'UnsetEnvironment=ODDSPAPI_API_KEY GITHUB_TOKEN GH_TOKEN SSH_AUTH_SOCK PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT'):
            self.assertIn(setting, unit)
        for prefix in ('Environment=', 'EnvironmentFile=', 'LoadCredential=', 'ExecStartPre=', 'ExecStartPost=', 'ExecStopPost='):
            self.assertFalse(any(line.startswith(prefix) for line in unit.splitlines()))
        self.assertEqual([line for line in unit.splitlines() if line.startswith('UnsetEnvironment=')],
                         ['UnsetEnvironment=ODDSPAPI_API_KEY GITHUB_TOKEN GH_TOKEN SSH_AUTH_SOCK PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT'])
        timer = (ROOT / 'deploy/modelfc-corner-refresh.timer').read_text()
        self.assertIn('OnCalendar=Mon,Thu *-*-* 06:00:00 UTC', timer)
        self.assertIn('Persistent=true', timer)
