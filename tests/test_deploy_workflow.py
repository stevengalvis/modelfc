"""Run the production Bash step offline with disposable keys and simulated SSH."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "SHA256:IpHgSvswGdBNFT6ETalX+2rGwbhdfItuXkDS4JSN1LE"
SHA = "a" * 40


@unittest.skipUnless(shutil.which("ssh-keygen") and shutil.which("bash"), "requires OpenSSH parser and Bash")
class DeployWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="deploy-workflow-fixture-")
        cls.addClassCleanup(cls.fixture.cleanup)
        key = Path(cls.fixture.name) / "disposable"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       capture_output=True, check=True)
        cls.private = key.read_bytes()
        cls.public = key.with_suffix(".pub").read_bytes()
        cls.encoded = base64.b64encode(cls.private).decode("ascii")
        cls.fingerprint = subprocess.run(["ssh-keygen", "-lf", str(key), "-E", "sha256"],
                                         capture_output=True, check=True).stdout.decode().split()[1]
        cls.workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        cls.script = textwrap.dedent(cls.workflow.split("        shell: bash\n        run: |\n", 1)[1])

    def report(self, reason="ALREADY_CURRENT"):
        value = dict(status="PASS", requested_sha=SHA, previous_sha=SHA, final_sha=SHA,
                     fetch_verified=False, release_created=False, dependency_sync="NOT_ATTEMPTED",
                     tests_status="NOT_RUN", tests_run=0, promotion_status="ALREADY_CURRENT",
                     state_boundary_enforced=True, reason=reason)
        if reason == "OK":
            value.update(fetch_verified=True, release_created=True, dependency_sync="INSTALLED",
                         tests_status="PASS", tests_run=2, promotion_status="PROMOTED")
        elif reason == "SUPERSEDED":
            value.update(status="SUPERSEDED", fetch_verified=True, final_sha="b" * 40,
                         promotion_status="SUPERSEDED")
        return value

    def exercise(self, encoded, *, accepted=False, report=None, ssh_status=0, fail_mktemp=0):
        with tempfile.TemporaryDirectory(prefix="deploy-workflow-run-") as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            files = root / "temporary"
            files.mkdir()
            ssh = binary / "ssh"
            ssh.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                import hashlib, json, os, pathlib, stat, sys
                args = sys.argv[1:]
                key = pathlib.Path(args[args.index('-i') + 1])
                hosts = pathlib.Path(next(a.split('=', 1)[1] for a in args if a.startswith('UserKnownHostsFile=')))
                event = dict(args=args, mode=stat.S_IMODE(key.stat().st_mode),
                             key_matches=hashlib.sha256(key.read_bytes()).hexdigest() == os.environ['TEST_KEY_DIGEST'],
                             secret_in_environment='DEPLOY_KEY_BASE64' in os.environ,
                             hosts_match=hosts.read_text() == 'fixture-host-pin\\n')
                with open(os.environ['TEST_SSH_EVENTS'], 'a') as log:
                    log.write(json.dumps(event) + '\\n')
                # Deliberate secret-like output must stay suppressed by production capture.
                sys.stderr.write(key.read_text() + os.environ['TEST_LEAK_CANARY'])
                sys.stdout.write(os.environ['TEST_REPORT'])
                sys.exit(int(os.environ['TEST_SSH_STATUS']))
            '''))
            ssh.chmod(0o700)
            mktemp = binary / "mktemp"
            mktemp.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                import os, pathlib, sys
                counter = pathlib.Path(os.environ['TEST_MKTEMP_COUNT'])
                count = int(counter.read_text()) + 1 if counter.exists() else 1
                counter.write_text(str(count))
                if count == int(os.environ['TEST_MKTEMP_FAIL']):
                    sys.exit(1)
                os.execv('/usr/bin/mktemp', ['mktemp'] + sys.argv[1:])
            '''))
            mktemp.chmod(0o700)
            source = self.script
            if accepted:
                # Exercise the exact fingerprint gate with an ephemeral key, never the VPS key.
                self.assertEqual(source.count(EXPECTED), 1)
                source = source.replace(EXPECTED, self.fingerprint)
            script = root / "step.sh"
            script.write_text(source)
            event_file = root / "ssh-events.jsonl"
            env = {"PATH": str(binary) + ":/usr/bin:/bin", "TMPDIR": str(files),
                   "DEPLOY_KEY_BASE64": encoded, "DEPLOY_HOST": "192.0.2.1",
                   "DEPLOY_HOST_KEY": "fixture-host-pin", "EXPECTED_SHA": SHA,
                   "TEST_KEY_DIGEST": hashlib.sha256(self.private).hexdigest(),
                   "TEST_SSH_EVENTS": str(event_file), "TEST_SSH_STATUS": str(ssh_status),
                   "TEST_MKTEMP_COUNT": str(root / "mktemp-count"), "TEST_MKTEMP_FAIL": str(fail_mktemp),
                   "TEST_REPORT": json.dumps(self.report() if report is None else report),
                   "TEST_LEAK_CANARY": self.encoded + self.public.decode() + "UNRELATED_SECRET_CANARY"}
            result = subprocess.run(["bash", "--noprofile", "--norc", str(script)],
                                    capture_output=True, text=True, env=env, cwd=root, timeout=30)
            events = [json.loads(line) for line in event_file.read_text().splitlines()] if event_file.exists() else []
            self.assertEqual(list(files.iterdir()), [], "temporary key/hosts/report must be removed")
            output = result.stdout + result.stderr
            for payload in (self.private.decode(), self.public.decode().strip(), self.encoded,
                            self.encoded[:32], self.encoded[-32:], "UNRELATED_SECRET_CANARY"):
                self.assertFalse(payload in output, "sensitive payload leaked")
            self.assertNotIn("PRIVATE KEY", output)
            self.assertNotIn("Traceback", output)
            for event in events:
                self.assertEqual(event["mode"], 0o600)
                self.assertTrue(event["key_matches"])
                self.assertTrue(event["hosts_match"])
                self.assertFalse(event["secret_in_environment"])
                args = event["args"]
                key = args[args.index("-i") + 1]
                hosts = next(a for a in args if a.startswith("UserKnownHostsFile="))
                self.assertEqual(args, ["-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                    "-o", "StrictHostKeyChecking=yes", "-o", hosts, "-o", "ForwardAgent=no",
                    "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
                    "-o", "ClearAllForwardings=yes", "-i", key, "modelfc-deploy@192.0.2.1",
                    "deploy stevengalvis/modelfc " + SHA])
            return result, events

    def assert_rejected(self, value):
        result, events = self.exercise(value)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '{"status":"FAIL","reason":"DEPLOY_KEY_INVALID"}\n')
        self.assertEqual(result.stderr, "")
        self.assertEqual(events, [])

    def test_invalid_base64_never_reaches_ssh(self):
        for value in ("", "é", "!bad!", self.encoded[:-1], self.encoded + "\n"):
            with self.subTest(length=len(value)):
                self.assert_rejected(value)

    def test_noncanonical_base64_never_reaches_ssh(self):
        for value in ("Zh==", "YWJj=", "YWJj===="):
            with self.subTest(length=len(value)):
                # These decode with validate=True; canonical equality must reject them.
                self.assertNotEqual(base64.b64encode(base64.b64decode(value, validate=True)).decode(), value)
                self.assert_rejected(value)

    def test_decoded_invalid_key_never_reaches_ssh(self):
        self.assert_rejected(base64.b64encode(b"invalid private key").decode())

    def test_wrong_fingerprint_never_reaches_ssh(self):
        self.assert_rejected(self.encoded)

    def test_accepted_key_reaches_one_ssh_and_preserves_success_semantics(self):
        for reason in ("OK", "ALREADY_CURRENT", "SUPERSEDED"):
            with self.subTest(reason=reason):
                value = self.report(reason)
                result, events = self.exercise(self.encoded, accepted=True, report=value)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), value)
                self.assertEqual(result.stderr, "")
                self.assertEqual(len(events), 1)

    def test_ssh_failure_is_sanitized_and_cleans_temporary_files(self):
        result, events = self.exercise(self.encoded, accepted=True, ssh_status=255)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), dict(status="FAIL", reason="SSH_TRANSPORT_FAILED"))
        self.assertEqual(len(events), 1)

    def test_report_rejection_is_sanitized_and_cleans_temporary_files(self):
        for changes in ({"extra": self.encoded}, {"requested_sha": "b" * 40},
                        {"tests_run": 0}, {"state_boundary_enforced": False}):
            with self.subTest(fields=list(changes)):
                result, events = self.exercise(self.encoded, accepted=True, report={**self.report("OK"), **changes})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), dict(status="FAIL", reason="INVALID_DEPLOYMENT_REPORT"))
                self.assertEqual(len(events), 1)

    def test_partial_temporary_file_creation_is_cleaned(self):
        for allocation in (2, 3):
            with self.subTest(allocation=allocation):
                result, events = self.exercise(self.encoded, accepted=True, fail_mktemp=allocation)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(events, [])

    def test_reviewed_report_validator_is_byte_for_byte_unchanged(self):
        validator = textwrap.dedent(self.workflow.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        self.assertEqual(hashlib.sha256(validator.encode()).hexdigest(),
                         "fadcf98154e70863f4a847ed0f798d9bbfa9392c9de4924bee5cc2310de46393")

    def test_production_settings_and_diagnostic_removal(self):
        for setting in ("${{ secrets.MODELFC_DEPLOY_SSH_KEY_BASE64 }}", "${{ github.sha }}",
                        "environment: production", "timeout-minutes: 75", "needs: test",
                        "group: modelfc-vps-deployment", "cancel-in-progress: false"):
            self.assertIn(setting, self.workflow)
        self.assertNotIn("${{ secrets.MODELFC_DEPLOY_SSH_KEY }}", self.workflow)
        self.assertFalse((ROOT / ".github/workflows/deployment-ssh-diagnostic.yml").exists())


if __name__ == "__main__":
    unittest.main()
