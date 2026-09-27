"""Offline prospective launcher and unit/timer contract tests."""

from contextlib import redirect_stderr
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prospective_launch", ROOT / "ops/vps/prospective_launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ProspectiveLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.config = self.root / "etc/modelfc/corner_data.json"
        self.config.parent.mkdir(parents=True)
        self.history = self.root / "history"
        self.state = self.root / "state"
        self.history.mkdir(); self.state.mkdir()
        self.config.write_text(json.dumps({"data_directory": str(self.history)}))
        self.credentials = self.root / "credentials"
        self.credentials.mkdir()
        (self.credentials / "oddspapi.key").write_text("offline-secret\n")
        self.a = self.make_release("a")
        self.b = self.make_release("b")
        owner = type("Account", (), {"pw_uid": os.getuid()})()
        patches = (patch.object(launcher, "RELEASES", self.releases),
                   patch.object(launcher, "CONFIG", self.config),
                   patch.object(launcher, "HISTORY", self.history),
                   patch.object(launcher, "STATE", self.state),
                   patch.object(launcher.pwd, "getpwnam", return_value=owner))
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        original = launcher.protected
        def fixture_protected(path, owner, **kwargs):
            return original(path, os.getuid(), **kwargs)
        item = patch.object(launcher, "protected", side_effect=fixture_protected)
        item.start(); self.addCleanup(item.stop)

    def make_release(self, letter):
        release = self.releases / (letter * 40 + "-123456abcdef")
        for name in (".git", "src/modelfc", ".venv/bin"):
            (release / name).mkdir(parents=True)
        (release / ".git/modelfc-deployed-sha").write_text(letter * 40)
        (release / ".git/modelfc-deployed-sha").chmod(0o444)
        (release / "src/modelfc/corner_prospective.py").touch()
        (release / "src/modelfc/corner_prospective_budget.py").touch()
        (release / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
        (release / ".venv/bin/python").symlink_to(sys.executable)
        return release

    def test_physical_release_pin_clean_environment_and_secret_not_logged(self):
        current = self.root / "current"
        current.symlink_to(self.a)
        def entered_a():
            current.unlink(); current.symlink_to(self.b)
            return self.a
        output = StringIO()
        hostile = {"CREDENTIALS_DIRECTORY": str(self.credentials), "PYTHONPATH": "/evil",
                   "GITHUB_TOKEN": "bad", "ODDSPAPI_API_KEY": "inherited-bad",
                   "LD_PRELOAD": "/evil", "PWD": str(current)}
        with patch.object(Path, "cwd", side_effect=entered_a), patch.object(os, "execve") as execute, \
                patch.dict(os.environ, hostile, clear=True), redirect_stderr(output):
            launcher.launch()
        executable, argv, env = execute.call_args.args
        self.assertEqual(executable, str(self.a / ".venv/bin/python"))
        self.assertEqual(argv, [executable, "-B", "-P", "-s", "-m", "modelfc.corner_prospective",
                                "run-once", "--data-config", str(self.config),
                                "--state-dir", str(self.state), "--require-calendar-budget"])
        self.assertEqual(set(env), {"PATH", "HOME", "LANG", "PYTHONPATH", "PYTHONNOUSERSITE",
                                    "PYTHONDONTWRITEBYTECODE", "ODDSPAPI_API_KEY"})
        self.assertEqual(env["ODDSPAPI_API_KEY"], "offline-secret")
        record = json.loads(output.getvalue())
        self.assertEqual(record["release"], str(self.a))
        self.assertEqual(record["sha"], "a" * 40)
        self.assertNotIn("offline-secret", output.getvalue())
        self.assertNotIn("inherited-bad", output.getvalue())

    def test_missing_bad_credential_or_incompatible_release_fails_before_exec(self):
        cases = (None, "", "contains whitespace", "x" * 4097)
        for value in cases:
            with self.subTest(value=value), patch.object(Path, "cwd", return_value=self.a), \
                    patch.object(os, "execve") as execute:
                if value is None:
                    env = {}
                else:
                    (self.credentials / "oddspapi.key").write_text(value)
                    env = {"CREDENTIALS_DIRECTORY": str(self.credentials)}
                with patch.dict(os.environ, env, clear=True), self.assertRaises((OSError, ValueError)):
                    launcher.launch()
                execute.assert_not_called()
        (self.credentials / "oddspapi.key").write_text("offline-secret")
        (self.a / "src/modelfc/corner_prospective_budget.py").unlink()
        with patch.object(Path, "cwd", return_value=self.a), patch.object(os, "execve") as execute, \
                patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}, clear=True):
            with self.assertRaises(OSError): launcher.launch()
            execute.assert_not_called()

    def test_real_subprocess_prefers_pinned_src(self):
        intended = self.a / "src/modelfc"
        (intended / "__init__.py").write_text("")
        (intended / "corner_prospective.py").write_text(
            "import json,os,sys; print(json.dumps({'module':'pinned','argv':sys.argv[1:],'key':bool(os.environ.get('ODDSPAPI_API_KEY'))}))\n")
        conflict = self.a / "modelfc"
        conflict.mkdir()
        (conflict / "__init__.py").write_text('raise RuntimeError("release-root-imported")\n')
        script = r'''
import importlib.util, os, pathlib
spec=importlib.util.spec_from_file_location('fixture_launcher', os.environ['LAUNCHER_SOURCE'])
launcher=importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
for name in ('RELEASES','CONFIG','HISTORY','STATE'):
    setattr(launcher,name,pathlib.Path(os.environ['FIXTURE_'+name]))
launcher.pwd.getpwnam=lambda name:type('Account',(),{'pw_uid':os.getuid()})()
original=launcher.protected
launcher.protected=lambda path,owner,**kwargs:original(path,os.getuid(),**kwargs)
launcher.launch()
'''
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CREDENTIALS_DIRECTORY": str(self.credentials),
               "LAUNCHER_SOURCE": str(ROOT / "ops/vps/prospective_launch.py"),
               "FIXTURE_RELEASES": str(self.releases), "FIXTURE_CONFIG": str(self.config),
               "FIXTURE_HISTORY": str(self.history), "FIXTURE_STATE": str(self.state)}
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script], cwd=self.a,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["module"], "pinned")
        self.assertTrue(json.loads(result.stdout)["key"])
        self.assertNotIn("offline-secret", result.stderr)

    def test_service_timer_and_refresh_unchanged_contract(self):
        service = (ROOT / "deploy/modelfc-corner-prospective.service").read_text()
        required = ("User=modelfc-runtime", "Group=modelfc-runtime",
                    "WorkingDirectory=/srv/modelfc/current",
                    "ExecStart=/usr/bin/python3 -I -B /usr/local/libexec/modelfc-prospective-launch.py",
                    "LoadCredential=oddspapi.key:/etc/modelfc/credentials/oddspapi.key",
                    "ReadOnlyPaths=/srv/modelfc /etc/modelfc /var/lib/modelfc/history",
                    "ReadWritePaths=/var/lib/modelfc/state /var/lib/modelfc/history/data/corner-refresh/refresh.lock",
                    "InaccessiblePaths=-/etc/modelfc/credentials", "InaccessiblePaths=-/root/modelfc-state -/root")
        for value in required:
            self.assertIn(value, service)
        for hook in ("ExecStartPre=", "ExecStartPost=", "ExecStop=", "ExecStopPost=", "EnvironmentFile="):
            self.assertFalse(any(line.startswith(hook) for line in service.splitlines()))
        timer = (ROOT / "deploy/modelfc-corner-prospective.timer").read_text()
        self.assertIn("OnCalendar=*-*-* *:05:00 UTC", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("RandomizedDelaySec=0", timer)
        self.assertNotIn("OnBootSec=", timer)
        self.assertNotIn("Restart=", service)
        refresh_service = (ROOT / "deploy/modelfc-corner-refresh.service").read_bytes()
        refresh_timer = (ROOT / "deploy/modelfc-corner-refresh.timer").read_bytes()
        import subprocess
        self.assertEqual(subprocess.check_output(["git", "show", "HEAD:deploy/modelfc-corner-refresh.service"]), refresh_service)
        self.assertEqual(subprocess.check_output(["git", "show", "HEAD:deploy/modelfc-corner-refresh.timer"]), refresh_timer)


if __name__ == "__main__":
    unittest.main()
