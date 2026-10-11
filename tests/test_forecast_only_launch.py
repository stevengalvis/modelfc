"""Pinned, credential-minimal forecast-only host launcher checks."""
from contextlib import redirect_stderr
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "forecast_only_launch", ROOT / "ops/vps/forecast_only_launch.py")
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


class ForecastOnlyLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.releases = root / "srv/modelfc/releases"; self.releases.mkdir(parents=True)
        self.release = self.releases / ("a" * 40 + "-123456abcdef")
        for name in (".git", "src/modelfc", ".venv/bin"):
            (self.release / name).mkdir(parents=True)
        marker = self.release / ".git/modelfc-deployed-sha"
        marker.write_text("a" * 40, encoding="ascii"); marker.chmod(0o444)
        for name in ("forecast_only_runner.py", "supplemental_history.py",
                     "research_predictions.py"):
            (self.release / "src/modelfc" / name).touch()
        (self.release / ".venv/pyvenv.cfg").touch()
        (self.release / ".venv/bin/python").symlink_to(sys.executable)
        self.history = root / "history"; self.history.mkdir()
        self.state = root / "state"; self.state.mkdir()
        self.config = root / "etc/modelfc/corner_data.json"; self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"data_directory": str(self.history)}))
        for name, value in (("RELEASES", self.releases), ("CONFIG", self.config),
                            ("HISTORY", self.history), ("STATE", self.state)):
            mocked = patch.object(LAUNCHER, name, value); mocked.start(); self.addCleanup(mocked.stop)
        identity = type("Identity", (), {"pw_uid": os.getuid()})()
        mocked = patch.object(LAUNCHER.pwd, "getpwnam", return_value=identity)
        mocked.start(); self.addCleanup(mocked.stop)

    def test_executes_only_pinned_runner_and_scrubs_provider_environment(self):
        output = StringIO()
        with patch.object(Path, "cwd", return_value=self.release), \
                patch.object(os, "execve") as execute, \
                patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": "/run/credentials/unit",
                                        "ODDSPAPI_API_KEY": "must-not-pass",
                                        "SPORTMONKS_API_TOKEN": "must-not-pass"}, clear=True), \
                redirect_stderr(output):
            LAUNCHER.launch()
        executable, argv, env = execute.call_args.args
        self.assertEqual(executable, str(self.release / ".venv/bin/python"))
        self.assertEqual(argv[-4:], ["--data-config", str(self.config),
                                     "--state-dir", str(self.state)])
        self.assertEqual(env["CREDENTIALS_DIRECTORY"], "/run/credentials/unit")
        self.assertNotIn("ODDSPAPI_API_KEY", env)
        self.assertNotIn("SPORTMONKS_API_TOKEN", env)
        self.assertNotIn("must-not-pass", repr(env) + output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["sha"], "a" * 40)

    def test_bad_release_or_marker_fails_before_exec(self):
        for change in ("cwd", "marker"):
            marker = self.release / ".git/modelfc-deployed-sha"
            marker.chmod(0o644); marker.write_text("a" * 40); marker.chmod(0o444)
            cwd = self.release
            if change == "cwd": cwd = self.releases.parent
            else:
                marker.chmod(0o644); marker.write_text("b" * 40); marker.chmod(0o444)
            with self.subTest(change=change), patch.object(Path, "cwd", return_value=cwd), \
                    patch.object(os, "execve") as execute, \
                    patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": "/run/credentials/unit"}, clear=True):
                with self.assertRaises(ValueError): LAUNCHER.launch()
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
