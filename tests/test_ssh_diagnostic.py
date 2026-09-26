"""Offline checks of the temporary workflow; never use production keys or SSH."""

import ast
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deployment-ssh-diagnostic.yml"
EXPECTED = "SHA256:IpHgSvswGdBNFT6ETalX+2rGwbhdfItuXkDS4JSN1LE"
EXPECTED_SECRET_SHA256 = "4bb1e4fc7970cfe754415b7a97f0b2661a2c8389795473af3d982aa25e8c8640"


@unittest.skipUnless(shutil.which("ssh-keygen"), "requires local OpenSSH key parser")
class SSHDiagnosticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ssh-diagnostic-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        key = Path(cls.directory.name) / "disposable"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True, capture_output=True)
        cls.private = key.read_bytes()
        cls.public = key.with_suffix(".pub").read_bytes()
        cls.encoded = base64.b64encode(cls.private).decode("ascii")
        cls.fingerprint = subprocess.run(
            ["ssh-keygen", "-lf", str(key), "-E", "sha256"],
            check=True, capture_output=True).stdout.decode("ascii").split()[1]
        cls.workflow = WORKFLOW.read_text()
        # This single-step workflow has no YAML dependency in the application lock.
        marker = "        run: |\n"
        if cls.workflow.count(marker) != 1:
            raise AssertionError("expected one inline diagnostic script")
        cls.source = textwrap.dedent(cls.workflow.split(marker, 1)[1])
        ast.parse(cls.source)

    def exercise(self, encoded, *, matching=False, matching_digest=True, rc=0, response=None, failure=None):
        calls, paths = [], []
        real_run = subprocess.run
        secrets = [self.encoded, self.private.decode(), self.public.decode().strip(),
                   "UNRELATED_SECRET_CANARY"]
        stderr = (f"debug1: Offering public key: identity ED25519 {self.fingerprint}\n"
                  f"debug1: Server accepts key: identity ED25519 {self.fingerprint}\n"
                  "debug1: Host hidden is known and matches the ED25519 host key.\n"
                  "debug1: Authentications that can continue: publickey,password\n"
                  "debug1: Next authentication method: publickey\n"
                  'Authenticated to hidden using "publickey".\n' + "\n".join(secrets)).encode()
        if response is None:
            response = json.dumps(dict(reason="INVALID_REQUEST", requested_sha="",
                                       promotion_status="NOT_ATTEMPTED")).encode()

        def invoke(args, **kwargs):
            calls.append(args)
            self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "LANG"})
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            if args[0] == "ssh-keygen":
                if "-y" in args:
                    path = Path(args[-1])
                    paths.append(path)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertTrue(path.read_bytes() == base64.b64decode(encoded, validate=True))
                return real_run(args, **kwargs)
            # Any unexpected subprocess (including any network tool) fails the test.
            self.assertEqual(args[0], "ssh")
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(args, [
                "ssh", "-vv", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes", "-o",
                "UserKnownHostsFile=" + str(paths[0].with_name("known_hosts")),
                "-o", "ForwardAgent=no", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
                "-o", "ClearAllForwardings=yes", "-i", str(paths[0]), "modelfc-deploy@192.0.2.1"])
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 60, stderr=stderr)
            if failure == "exception":
                raise ValueError("\n".join(secrets))
            return subprocess.CompletedProcess(args, rc, response, stderr)

        source = self.source
        if matching_digest:
            # Override only the expected digest to exercise later gates with offline fixtures.
            self.assertEqual(source.count(EXPECTED_SECRET_SHA256), 1)
            digest = hashlib.sha256(encoded.encode("utf-8", errors="surrogateescape")).hexdigest()
            source = source.replace(EXPECTED_SECRET_SHA256, digest)
        if matching:
            # Substitute only the expected fingerprint with our disposable key's value.
            self.assertEqual(source.count(EXPECTED), 1)
            source = source.replace(EXPECTED, self.fingerprint)
        output = io.StringIO()
        mask = os.umask(0o077)
        os.umask(mask)
        try:
            with patch.dict(os.environ, {
                    "HOME": self.directory.name, "DEPLOY_KEY_BASE64": encoded,
                    "DEPLOY_HOST": "192.0.2.1", "DEPLOY_HOST_KEY": "test-host-key",
                    "UNRELATED_SECRET": "UNRELATED_SECRET_CANARY"}, clear=True), patch(
                    "subprocess.run", side_effect=invoke), contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as stopped:
                    exec(compile(source, str(WORKFLOW), "exec"), {})
        finally:
            os.umask(mask)
        text = output.getvalue()
        for secret in secrets:
            self.assertFalse(secret in text, "sensitive payload leaked")
        self.assertNotIn("PRIVATE KEY", text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(not path.parent.exists() for path in paths))
        return stopped.exception.code, text, calls

    def test_workflow_keeps_manual_production_configuration(self):
        self.assertIn("on:\n  workflow_dispatch:\n", self.workflow)
        self.assertIn("environment: production", self.workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", self.workflow)
        self.assertIn("${{ secrets.MODELFC_DEPLOY_SSH_KEY_BASE64 }}", self.workflow)
        self.assertNotIn("${{ secrets.MODELFC_DEPLOY_SSH_KEY }}", self.workflow)
        self.assertIn("${{ vars.MODELFC_DEPLOY_HOST }}", self.workflow)
        self.assertIn("${{ vars.MODELFC_DEPLOY_HOST_KEY }}", self.workflow)
        self.assertIn(EXPECTED, self.source)
        self.assertIn(EXPECTED_SECRET_SHA256, self.source)

    def test_secret_bytes_mismatch_stops_before_decode_parser_and_ssh(self):
        for value in (self.encoded, "", self.encoded + "\n", " " + self.encoded, "é", "\udcff"):
            with self.subTest(case_length=len(value)), patch("base64.b64decode") as decode:
                status, output, calls = self.exercise(value, matching_digest=False)
                self.assertEqual((status, output, calls), (1, "SECRET_BYTES_MISMATCH\n", []))
                decode.assert_not_called()

    def test_valid_single_line_decodes_exact_bytes_and_attempts_ssh_once(self):
        self.assertNotIn("\n", self.encoded)
        status, output, calls = self.exercise(self.encoded, matching=True)
        self.assertEqual(status, 0)
        self.assertEqual(output.splitlines()[:2], ["SECRET_BYTES_MATCH", "KEY_FINGERPRINT_MATCH"])
        self.assertEqual(sum(call[0] == "ssh" for call in calls), 1)
        for label in ("EXPECTED_KEY_OFFERED", "EXPECTED_KEY_ACCEPTED", "INVALID_REQUEST",
                      "HOST_KEY_VERIFIED_ED25519", "AUTHENTICATED_PUBLICKEY"):
            self.assertIn(label, output)

    def test_invalid_base64_stops_before_key_parser_or_ssh(self):
        for value in ("", "!bad!", "é", self.encoded + "\n", " " + self.encoded,
                      self.encoded[:-1], self.encoded + "=", "Zh==", "MODELFC_DEPLOY_SSH_KEY_BASE64=" + self.encoded):
            with self.subTest(case_length=len(value)):
                status, output, calls = self.exercise(value)
                self.assertEqual((status, output, calls),
                                 (1, "SECRET_BYTES_MATCH\nBASE64_DECODE_FAILED\n", []))

    def test_decoded_invalid_key_stops_before_ssh(self):
        status, output, calls = self.exercise(base64.b64encode(b"not a private key").decode())
        self.assertEqual((status, output), (1, "SECRET_BYTES_MATCH\nKEY_PARSE_FAILED\n"))
        self.assertEqual([call[0] for call in calls], ["ssh-keygen"])

    def test_decoded_wrong_key_stops_before_ssh(self):
        status, output, calls = self.exercise(self.encoded)
        self.assertEqual((status, output), (1, "SECRET_BYTES_MATCH\nKEY_FINGERPRINT_MISMATCH\n"))
        self.assertEqual([call[0] for call in calls], ["ssh-keygen", "ssh-keygen"])

    def test_ssh_failure_has_one_attempt_and_safe_output(self):
        status, output, calls = self.exercise(self.encoded, matching=True, rc=255)
        self.assertEqual(status, 1)
        self.assertIn('"ssh_exit_status": 255', output)
        self.assertEqual(sum(call[0] == "ssh" for call in calls), 1)

    def test_remote_stdout_cannot_leak_payloads(self):
        status, output, _ = self.exercise(self.encoded, matching=True,
                                         response=self.private + self.public + self.encoded.encode())
        self.assertEqual(status, 1)
        self.assertIn("UNEXPECTED_OR_MISSING", output)

    def test_timeout_output_is_sanitized_and_key_is_removed(self):
        status, output, _ = self.exercise(self.encoded, matching=True, failure="timeout")
        self.assertEqual(status, 1)
        self.assertIn("SSH_DIAGNOSTIC_TIMEOUT", output)

    def test_exception_output_is_sanitized_and_key_is_removed(self):
        status, output, _ = self.exercise(self.encoded, matching=True, failure="exception")
        self.assertEqual(status, 1)
        self.assertIn("SSH_DIAGNOSTIC_INTERNAL_ERROR", output)


if __name__ == "__main__":
    unittest.main()
