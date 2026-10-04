"""Disposable evidence and fake SSH only; no host, root or provider operations."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/vps/team_baseline_inspect.py"
spec = importlib.util.spec_from_file_location("baseline_inspector", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
DIGEST = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
CSV = b"Date,Div,FTHG,FTAG,FTR\n20/09/2026,E1,2,1,H\n30/10/2026,E1,,,\n"


def baseline():
    return dict(release="/srv/modelfc/releases/" + m.RELEASE_SHA + "-012345abcdef",
                installed_hashes={str(p): "a" * 64 for p in m.INSTALLED},
                identities={str(p): [1, n + 1] for n, p in enumerate(m.IDENTITIES)},
                e1_sha256="b" * 64, data_cutoff="2026-09-20")


class BaselineTests(unittest.TestCase):
    def test_complete_disposable_collection_and_drift(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            release = root / "releases" / (m.RELEASE_SHA + "-012345abcdef")
            release.mkdir(parents=True)
            current = root / "current"
            current.symlink_to(release)
            (release / ".git").mkdir()
            (release / ".git/HEAD").write_text(m.RELEASE_SHA)
            (release / ".git/modelfc-deployed-sha").write_text(m.RELEASE_SHA)
            templates = {}
            for name in m.TEMPLATES:
                path = release / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"public fixture template")
                templates[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            installed = tuple(root / "installed" / str(i) for i in range(len(m.INSTALLED)))
            installed[0].parent.mkdir()
            for path in installed:
                path.write_bytes(b"private fixture installed file")
            history = root / "history"
            refresh = history / "data/corner-refresh"
            refresh.mkdir(parents=True)
            e1 = history / "E1_2627.csv"
            e1.write_bytes(CSV)
            deploy_lock = root / "deploy.lock"
            deploy_lock.touch()
            refresh_lock = refresh / "refresh.lock"
            refresh_lock.touch()
            identities = (deploy_lock, root, history, history / "data", refresh, refresh_lock, e1)
            for name, value in dict(CURRENT=current, INSTALLED=installed, IDENTITIES=identities,
                                    E1=e1, TEMPLATES=templates,
                                    RELEASE_PATTERN=re.compile(re.escape(str(release)) + r"\Z")).items():
                stack.enter_context(patch.object(m, name, value))
            observation = m.Observation()
            value = m.collect(observation)
            observation.finish()
            self.assertEqual(value["e1_sha256"], hashlib.sha256(CSV).hexdigest())
            self.assertEqual(value["data_cutoff"], "2026-09-20")
            self.assertEqual(value["identities"][str(e1)], [e1.stat().st_dev, e1.stat().st_ino])
            installed[0].write_bytes(b"changed")
            with self.assertRaises(m.Rejected) as raised:
                observation.finish()
            self.assertEqual(raised.exception.reason, "OBSERVATION_CHANGED")
            (release / next(iter(templates))).write_bytes(b"unexpected template")
            with self.assertRaises(m.Rejected) as raised:
                m.collect(m.Observation())
            self.assertEqual(raised.exception.reason, "TEMPLATE_MISMATCH")

    def test_schema_accepts_exact_contract(self):
        m.validate_baseline(baseline())

    def test_schema_rejects_candidate_envelope_and_bad_types(self):
        for change in ({"extra": 1}, {"release": "/tmp/anything"},
                       {"data_cutoff": "2026-9-20"}, {"data_cutoff": "2025-09-20"},
                       {"data_cutoff": "2026-02-30"}, {"e1_sha256": "x" * 64},
                       {"identities": {}}, {"installed_hashes": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                m.validate_baseline({**baseline(), **change})
        value = baseline()
        value["identities"][str(m.IDENTITIES[0])] = [True, 1]
        with self.assertRaises(ValueError):
            m.validate_baseline(value)
        with self.assertRaises(ValueError):
            m.validate_baseline({"kind": "UNREVIEWED_CANDIDATE", "baseline": baseline()})

    def test_duplicate_json_rejected_at_any_depth(self):
        with self.assertRaises(ValueError):
            m.decode_json(b'{"a":{"b":1,"b":2}}')

    def test_cutoff_excludes_scheduled_rows(self):
        self.assertEqual(m.candidate_cutoff(CSV), "2026-09-20")

    def test_cutoff_rejects_incomplete_or_wrong_result(self):
        for line in ("20/09/2026,E1,2,,H", "20/09/2026,E1,2,1,A",
                     "20/09/2025,E1,2,1,H", "20/09/2026,SP1,2,1,H",
                     "20/09/2026,E1,,,", "20/09/2026,E1,2,1,H,extra"):
            with self.subTest(line=line), self.assertRaises((m.Rejected, ValueError)):
                m.candidate_cutoff(("Date,Div,FTHG,FTAG,FTR\n" + line).encode())

    def test_bounded_regular_nofollow_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence"
            path.write_bytes(b"abcd")
            with self.assertRaises(m.Rejected):
                m.Observation().read(path, 3)
            link = path.with_name("link")
            link.symlink_to(path)
            with self.assertRaises(OSError):
                m.Observation().read(link)
            fifo = path.with_name("fifo")
            os.mkfifo(fifo)
            with self.assertRaises(m.Rejected):
                m.Observation().read(fifo)

    def test_change_after_read_and_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence"
            path.write_bytes(b"first")
            observation = m.Observation()
            observation.read(path)
            path.write_bytes(b"changed")
            with self.assertRaises(m.Rejected) as raised:
                observation.finish()
            self.assertEqual(raised.exception.reason, "OBSERVATION_CHANGED")
            link = path.with_name("dirlink")
            link.symlink_to(directory)
            with self.assertRaises(m.Rejected):
                m.Observation().read(link / "evidence")

    def test_private_metadata_rejected_without_root_or_trusted_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline"
            path.write_bytes(b"{}")
            path.chmod(0o644)
            with patch.object(m, "trusted_parents"), self.assertRaises(m.Rejected):
                m.Observation().read(path, private=True)
            with self.assertRaises(m.Rejected):
                m.trusted_parents(path)

    def test_baseline_outcomes_and_final_stability_check(self):
        for expected, payload in (("BASELINE_MATCH", baseline()),
                                  ("BASELINE_MISMATCH", {**baseline(), "e1_sha256": "c" * 64}),
                                  ("BASELINE_MISSING", None)):
            with self.subTest(expected=expected), patch.object(m, "collect", return_value=baseline()), \
                    patch.object(m, "Observation") as observation:
                obj = observation.return_value
                if payload is None:
                    obj.read.side_effect = FileNotFoundError()
                else:
                    obj.read.return_value = json.dumps(payload).encode()
                self.assertEqual(m.inspect(), expected)
                obj.finish.assert_called_once()

    def test_invalid_baseline_cannot_match(self):
        with patch.object(m, "collect", return_value=baseline()), patch.object(m, "Observation") as obs:
            obs.return_value.read.return_value = b'{"private":"must not leak"}'
            with self.assertRaises(m.Rejected) as raised:
                m.inspect()
            self.assertEqual(raised.exception.reason, "BASELINE_INVALID")

    def main(self, args, **patches):
        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            stack.enter_context(patch.object(m.os, "geteuid", return_value=0))
            for name, value in patches.items():
                stack.enter_context(patch.object(m, name, **value))
            with redirect_stdout(out):
                result = m.main(args)
        return result, json.loads(out.getvalue())

    def test_candidate_marked_unreviewed_not_installable(self):
        code, value = self.main(["--candidate"], collect={"return_value": baseline()},
                                Observation={"return_value": unittest.mock.Mock()})
        self.assertEqual(code, 0)
        self.assertEqual(value["kind"], "UNREVIEWED_CANDIDATE")
        with self.assertRaises(ValueError):
            m.validate_baseline(value)

    def test_inspection_errors_do_not_expose_private_values(self):
        code, value = self.main(["--inspect"], inspect={"side_effect": RuntimeError("private nonce credential")})
        self.assertEqual(code, 1)
        self.assertNotIn("private", json.dumps(value))
        self.assertEqual(value["production_acceptance"], "NOT_RUN")

    def test_invalid_requests_never_inspect(self):
        for args in ([], ["--apply"], ["--candidate", "/tmp/output"], ["--inspect", "extra"]):
            with self.subTest(args=args):
                code, value = self.main(args, inspect={"side_effect": AssertionError("called")})
                self.assertEqual(value["reason"], "INVALID_REQUEST")
                self.assertEqual(code, 1)

    def test_nonroot_and_ssh_cannot_collect(self):
        for uid, env, reason in ((1000, {}, "ROOT_REQUIRED"), (0, {"SSH_ORIGINAL_COMMAND": m.TASK}, "INVALID_REQUEST")):
            with patch.object(m.os, "geteuid", return_value=uid), patch.dict(os.environ, env, clear=True), \
                    redirect_stdout(io.StringIO()) as out, patch.object(m, "collect") as collect:
                self.assertEqual(m.main(["--candidate"]), 1)
                collect.assert_not_called()
                self.assertEqual(json.loads(out.getvalue())["reason"], reason)

    def test_dispatch_fixed_command_and_clean_environment(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(m.report("BASELINE_MATCH")).encode())
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": m.TASK, "PYTHONPATH": "/attacker"}), \
                patch.object(m.subprocess, "run", return_value=result) as run, \
                patch.object(m, "INSTALLED_SCRIPT", str(SCRIPT)), redirect_stdout(io.StringIO()):
            self.assertEqual(m.dispatch(), 0)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", str(SCRIPT), "--inspect"])
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_dispatch_rejects_all_other_tasks(self):
        for task in ("", m.TASK + " ", "--candidate", m.TASK + "; id", "inspect " + "a" * 40):
            with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": task}), \
                    patch.object(m.subprocess, "run") as run, self.assertRaises(m.Rejected):
                m.dispatch()
            run.assert_not_called()

    def test_report_blocks_extra_private_data_wrong_hash_stale_and_duplicates(self):
        valid = m.report("BASELINE_MATCH")
        self.assertEqual(m.validate_report(json.dumps(valid).encode(), DIGEST), valid)
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        for changes in ({"private": "secret"}, {"inspector_sha256": "0" * 64},
                        {"observed_at": old}, {"production_acceptance": "PASS"},
                        {"schema": True}, {"reason": ["BASELINE_MATCH"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                m.validate_report(json.dumps({**valid, **changes}).encode(), DIGEST)
        with self.assertRaises(ValueError):
            m.validate_report(b"x" * 4097, DIGEST)


class WorkflowTests(unittest.TestCase):
    def exercise(self, value, transport=0):
        workflow = (ROOT / ".github/workflows/team-baseline-inspection.yml").read_text()
        script = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in {
                "ssh-keygen": "import sys\nprint('256 SHA256:' + 'A'*43 + ' fixture' if '-lf' in sys.argv else 'fixture-public')",
                "ssh": "import os,sys\nassert 'INSPECT_KEY_BASE64' not in os.environ\nassert sys.argv[-1]=='inspect-team-baseline-v1'\nassert 'StrictHostKeyChecking=yes' in sys.argv\nsys.stderr.write('PRIVATE STDERR')\nsys.stdout.write(os.environ['FAKE_REPORT'])\nsys.exit(int(os.environ['FAKE_STATUS']))",
            }.items():
                path = root / name
                path.write_text(f"#!{sys.executable}\n" + body)
                path.chmod(0o700)
            summary = root / "summary"
            env = {"PATH": str(root) + ":/usr/bin:/bin", "INSPECT_KEY_BASE64": "Zml4dHVyZQ==",
                   "INSPECT_KEY_FINGERPRINT": "SHA256:" + "A" * 43,
                   "INSPECT_HOST": "192.0.2.1", "INSPECT_HOST_KEY": "fixture-pin",
                   "INSPECTOR_SHA256": DIGEST, "GITHUB_STEP_SUMMARY": str(summary),
                   "FAKE_REPORT": json.dumps(value), "FAKE_STATUS": str(transport), "TMPDIR": str(root)}
            result = subprocess.run(["/bin/bash", "-c", script], env=env, cwd=ROOT,
                                    capture_output=True, text=True, timeout=15)
            return result, summary.read_text() if summary.exists() else ""

    def test_match_and_blocked_reports_reach_summary(self):
        for reason, status in (("BASELINE_MATCH", 0), ("BASELINE_MISSING", 1)):
            result, summary = self.exercise(m.report(reason), status)
            self.assertEqual(result.returncode, status, result.stdout + result.stderr)
            self.assertEqual(json.loads(summary)["reason"], reason)
            self.assertNotIn("PRIVATE", result.stdout + result.stderr + summary)

    def test_untrusted_report_and_transport_never_leak(self):
        for value, status in (({"private": "SECRET"}, 0), (m.report("BASELINE_MATCH"), 255)):
            result, summary = self.exercise(value, status)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("SECRET", result.stdout + result.stderr + summary)
            self.assertNotIn("PRIVATE", result.stdout + result.stderr + summary)


if __name__ == "__main__":
    unittest.main()
