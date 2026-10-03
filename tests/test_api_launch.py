"""Offline release pinning and ingress configuration checks."""

from contextlib import redirect_stderr
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("api_launch", ROOT / "ops/vps/api_launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ApiLaunchTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.releases = root / "releases"
        self.releases.mkdir()
        self.config = root / "etc/modelfc/corner_data.json"
        self.config.parent.mkdir(parents=True)
        self.origin = self.config.parent / "api-cors-origin"
        self.origin.write_text("https://modelfc.vercel.app\n")
        self.state = root / "state"
        self.state.mkdir()
        self.history = root / "history"
        self.config.write_text(json.dumps({"data_directory": str(self.history)}))
        self.first = self.make_release("a")
        self.second = self.make_release("b")
        for name, value in (("RELEASES", self.releases), ("CONFIG", self.config),
                            ("ORIGINS", self.origin), ("STATE", self.state),
                            ("HISTORY", self.history)):
            item = patch.object(launcher, name, value)
            item.start(); self.addCleanup(item.stop)
        item = patch.object(launcher.pwd, "getpwnam", side_effect=lambda name: type(
            "UID", (), {"pw_uid": os.getuid() + 10001 if name == "modelfc-runtime" else os.getuid()})())
        item.start(); self.addCleanup(item.stop)
        original = launcher.protected
        item = patch.object(launcher, "protected", side_effect=lambda path, _uid, **kw: original(path, os.getuid(), **kw))
        item.start(); self.addCleanup(item.stop)

    def make_release(self, letter):
        path = self.releases / (letter * 40 + "-123456abcdef")
        for name in (".git", "src/modelfc", ".venv/bin"):
            (path / name).mkdir(parents=True)
        marker = path / ".git/modelfc-deployed-sha"
        marker.write_text(letter * 40)
        marker.chmod(0o444)
        for name in ("corner_api.py", "corner_prospective_read.py", "team_intelligence.py"):
            (path / "src/modelfc" / name).touch()
        (path / ".venv/pyvenv.cfg").touch()
        (path / ".venv/bin/python").symlink_to(sys.executable)
        return path

    def test_pins_physical_release_and_scrubs_inherited_credentials(self):
        current = self.releases.parent / "current"
        current.symlink_to(self.first)
        def enter_first():
            current.unlink(); current.symlink_to(self.second)
            return self.first
        output = StringIO()
        with patch.object(Path, "cwd", side_effect=enter_first), patch.object(os, "execve") as execute, \
                patch.dict(os.environ, {"ODDSPAPI_API_KEY": "private", "PYTHONPATH": "/bad",
                                             "GITHUB_TOKEN": "private"}, clear=True), redirect_stderr(output):
            launcher.launch()
        executable, argv, env = execute.call_args.args
        self.assertEqual(executable, str(self.first / ".venv/bin/python"))
        self.assertEqual(argv[:6], [executable, "-B", "-P", "-s", "-m", "uvicorn"])
        self.assertEqual(argv[6:11], ["modelfc.corner_api:app", "--host", "127.0.0.1", "--port", "8000"])
        self.assertNotIn("private", repr(env) + output.getvalue())
        self.assertEqual(env["PYTHONPATH"], str(self.first / "src"))
        self.assertEqual(env["MODELFC_STATE_DIR"], str(self.state))
        self.assertEqual(env["MODELFC_CORS_ORIGINS"], "https://modelfc.vercel.app")
        self.assertEqual(json.loads(output.getvalue())["sha"], "a" * 40)

    def test_bad_origin_or_provenance_fails_closed(self):
        for origin in ("*", "http://modelfc.vercel.app", "https://a/b", "https://a,https://b"):
            with self.subTest(origin=origin), patch.object(Path, "cwd", return_value=self.first), \
                    patch.object(os, "execve") as execute:
                self.origin.write_text(origin)
                with self.assertRaises(ValueError):
                    launcher.launch()
                execute.assert_not_called()
        self.origin.write_text("https://modelfc.vercel.app")
        (self.first / ".git/modelfc-deployed-sha").chmod(0o644)
        (self.first / ".git/modelfc-deployed-sha").write_text("b" * 40)
        with patch.object(Path, "cwd", return_value=self.first), patch.object(os, "execve") as execute:
            with self.assertRaises(ValueError):
                launcher.launch()
            execute.assert_not_called()

    def test_api_must_have_distinct_uid_from_writer(self):
        with patch.object(Path, "cwd", return_value=self.first), \
                patch.object(launcher.pwd, "getpwnam", return_value=type("UID", (), {"pw_uid": os.getuid()})()), \
                patch.object(os, "execve") as execute:
            with self.assertRaisesRegex(ValueError, "separate identity"):
                launcher.launch()
            execute.assert_not_called()

    def test_installed_unit_and_exact_public_routes(self):
        unit = (ROOT / "deploy/modelfc-corner-api.service").read_text()
        caddy = (ROOT / "deploy/modelfc-api.Caddyfile").read_text()
        for required in ("User=modelfc-api", "Group=modelfc-api",
                         "WorkingDirectory=/srv/modelfc/current",
                         "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes",
                         "NoNewPrivileges=yes", "RestrictSUIDSGID=yes",
                         "ReadOnlyPaths=/srv/modelfc /etc/modelfc /var/lib/modelfc/state",
                         "IPAddressDeny=any", "IPAddressAllow=localhost", "RuntimeMaxSec=30min",
                         "Restart=always", "InaccessiblePaths=-/etc/modelfc/credentials"):
            self.assertIn(required, unit)
        self.assertIn("InaccessiblePaths=-/etc/modelfc/credentials -/run/credentials", unit)
        self.assertNotIn("ReadWritePaths=", unit)
        self.assertNotIn("LoadCredential=", unit)
        self.assertIn("{$MODELFC_API_HOST}", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8000", caddy)
        self.assertIn("method GET", caddy)
        self.assertIn("respond 404", caddy)
        match = re.search(r"^\s*path (.+)$", caddy, re.MULTILINE)
        self.assertIsNotNone(match)
        allowed = set(match.group(1).split())
        self.assertEqual(allowed, {"/api/v1/predictions", "/api/v1/opportunities",
                                   "/api/v1/prospective/performance"})
        for blocked in ("/api/v1/capabilities", "/api/v1/opportunities/id", "/docs",
                        "/redoc", "/openapi.json", "/api/v1/analyses", "/anything"):
            self.assertNotIn(blocked, allowed)
        detail = re.search(r"@opportunity_detail\s*\{\s*path_regexp (.+)$", caddy, re.MULTILINE)
        self.assertIsNotNone(detail)
        matcher = re.compile(detail.group(1))
        self.assertTrue(matcher.fullmatch("/api/v1/opportunities/" + "a" * 32))
        for denied in ("/api/v1/opportunities/id", "/api/v1/opportunities/" + "A" * 32,
                       "/api/v1/opportunities/" + "a" * 32 + "/", "/api/v1/analyses/" + "a" * 32):
            self.assertIsNone(matcher.fullmatch(denied))
        self.assertRegex(caddy, r"@opportunity_detail\s*\{\s*path_regexp [^\n]+\s*method GET")
        self.assertRegex(caddy, r"handle @opportunity_detail\s*\{\s*header Cache-Control \"no-store\"\s*reverse_proxy 127.0.0.1:8000")


    def test_team_routes_are_bounded_get_only_and_preserve_cache_headers(self):
        from modelfc.team_intelligence import REGISTRY
        caddy = (ROOT / "deploy/modelfc-api.Caddyfile").read_text()
        self.assertRegex(caddy, r"@team_read\s*\{\s*path /api/v1/teams /api/v1/team-insights\s*method GET")
        detail = re.search(r"@team_detail\s*\{\s*path_regexp (.+)$", caddy, re.MULTILINE)
        matcher = re.compile(detail.group(1))
        for team in REGISTRY:
            self.assertTrue(matcher.fullmatch("/api/v1/teams/" + team.team_id))
        for path in ("/api/v1/teams/", "/api/v1/teams/Cardiff", "/api/v1/teams/cardiff/",
                     "/api/v1/teams/../docs", "/api/v1/teams/" + "a"*40, "/api/v1/capabilities"):
            self.assertIsNone(matcher.fullmatch(path))
        self.assertRegex(caddy, r"@team_detail\s*\{\s*path_regexp [^\n]+\s*method GET")
        for name in ("team_read", "team_detail"):
            handle = re.search(r"handle @" + name + r"\s*\{([^}]+)\}", caddy).group(1)
            self.assertNotIn("header Cache-Control", handle)
            self.assertIn("reverse_proxy 127.0.0.1:8000", handle)


if __name__ == "__main__":
    unittest.main()
