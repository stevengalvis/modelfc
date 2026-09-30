"""Synthetic offline checks for the production status read model and CLI."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modelfc import production_status as status
from modelfc import production_status_host as host
from modelfc.ledger_storage import LedgerError
from modelfc.prospective_run_receipts import COUNTERS, publish as publish_receipt


NOW = datetime(2026, 9, 28, 22, 5, tzinfo=timezone.utc)
SHA = "a" * 40
SERVICES = {name: {"active": True, "enabled": True} for name in host.UNITS}


class ProductionStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.release = self.root / (SHA + "-" + "b" * 12)
        (self.release / ".git").mkdir(parents=True)
        marker = self.release / ".git/modelfc-deployed-sha"
        marker.write_text(SHA, encoding="ascii")
        marker.chmod(0o444)
        self.history = self.root / "history"
        self.history.mkdir()
        (self.history / "E1_2627.csv").write_text(
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
            "E1,20/09/2026,Wolves,West Brom,2,1,H,6,3\n", encoding="utf-8")
        refresh = self.history / "data/corner-refresh"
        refresh.mkdir(parents=True)
        (refresh / "refresh.lock").touch()
        self.refresh = refresh / "status.json"
        self.refresh_report = {"checked_at": "2026-09-28T03:05:00+00:00", "season": "2627",
                               "max_age_days": 14, "results": [{"league": "E1", "status": "unchanged"}]}
        self.save(self.refresh, self.refresh_report)
        self.config = self.root / "corner_data.json"
        self.save(self.config, {"data_directory": str(self.history), "leagues": ["E1"],
                                "max_age_days": 14})
        self.state = self.root / "state"
        prospective = self.state / "prospective"
        prospective.mkdir(parents=True)
        (prospective / "runner.lock").touch()
        self.control_path = prospective / "control.json"
        before = {"start": "2026-09-23", "end": "2026-10-01", "allowance": 40, "reserved": 1}
        after = dict(before, allowance=180)
        self.control = {"version": 2, "period": dict(after, reserved=3),
                        "budget": {"kind": "UTC_CALENDAR_MONTH", "monthly_allowance": 180,
                                   "transitional_period": {"start": before["start"], "end": before["end"],
                                                           "enrolled_reserved": 1}},
                        "discovery": {"date": "2026-09-28", "status": "DONE", "fixtures": [],
                                      "queries": 1, "last_attempt_at": "2026-09-28T06:05:00+00:00"},
                        "attempts": {}, "last_request": None}
        self.save(self.control_path, self.control)
        budget_events = prospective / "budget-events"
        budget_events.mkdir()
        self.save(budget_events / "enrollment:2026-09.json", {
            "version": 1, "event_id": "enrollment:2026-09", "event_type": "ENROLLMENT",
            "recorded_at_utc": "2026-09-23T12:00:00+00:00", "before": before, "after": after})

    @staticmethod
    def save(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def report(self, *, now=NOW, services=None):
        return status.report_status(release=self.release, config_path=self.config,
                                    state_dir=self.state, now=now,
                                    service_signals=SERVICES if services is None else services)

    def receipt(self, *, at=NOW, state="OK", fixtures=0):
        summary = {"status": state, "reasons": ["REQUEST_BUDGET"] if state == "PARTIAL" else
                   ["STORAGE_OR_INTEGRITY_FAILURE"] if state == "FAIL" else [],
                   "prospective_budget_remaining": 177}
        summary.update({key: 0 for key in COUNTERS})
        summary["fixtures_discovered"] = fixtures
        summary["provider_requests"] = 2
        return publish_receipt(self.state, started=at - timedelta(milliseconds=2800), completed=at,
                               summary=summary, release_sha=SHA)

    def test_latest_completed_receipt_and_status_variants(self):
        for value in ("OK", "PARTIAL", "FAIL"):
            with self.subTest(value=value):
                at = NOW.replace(minute=5 + ("OK", "PARTIAL", "FAIL").index(value))
                self.receipt(at=at, state=value, fixtures=3)
                report = self.report(now=NOW.replace(minute=20))
                part = report["components"]["prospective"]
                self.assertEqual(part["runner_completion"], "VERIFIED")
                self.assertEqual(part["last_completed_run_at_utc"], at.isoformat())
                self.assertEqual(part["last_run_duration_ms"], 2800)
                self.assertEqual(part["last_run_status"], value)
                self.assertEqual(part["state"], "ERROR" if value == "FAIL" else
                                 "WARNING" if value == "PARTIAL" else "OK")
                self.assertEqual(part["last_run_reasons"],
                                 ["REQUEST_BUDGET"] if value == "PARTIAL" else
                                 ["STORAGE_OR_INTEGRITY_FAILURE"] if value == "FAIL" else [])
                self.assertEqual(part["last_run_release_sha"], SHA)
                self.assertEqual(part["last_run_summary"]["fixtures_discovered"], 3)
                self.assertEqual(part["last_run_summary"]["provider_requests"], 2)
                self.assertIn("last run status", status.format_status(report))
                self.assertNotIn(str(self.root), json.dumps(report))

    def test_corrupt_newest_receipt_fails_closed_and_ignores_temp(self):
        self.receipt()
        directory = self.state / "prospective/run-receipts" / NOW.date().isoformat()
        (directory / ".record-crashed.tmp").write_text("partial", encoding="utf-8")
        self.assertEqual(self.report()["components"]["prospective"]["runner_completion"], "VERIFIED")
        newest = next(directory.glob("*.json"))
        for contents in ('{', json.dumps({**json.loads(newest.read_text()), "release_sha": "invalid"})):
            with self.subTest(contents=contents):
                newest.write_text(contents, encoding="utf-8")
                prospective = self.report()["components"]["prospective"]
                self.assertEqual((prospective["state"], prospective["runner_completion"]), ("ERROR", "CORRUPT"))
                self.assertIsNone(prospective["last_completed_run_at_utc"])
                self.assertEqual(status.exit_code(self.report()), 1)

    def test_completed_fail_receipt_survives_invalid_control(self):
        self.receipt(state="FAIL")
        self.control_path.write_text("{", encoding="utf-8")
        prospective = self.report()["components"]["prospective"]
        self.assertEqual(prospective["state"], "ERROR")
        self.assertEqual(prospective["runner_completion"], "VERIFIED")
        self.assertEqual(prospective["last_run_status"], "FAIL")
        self.assertEqual(prospective["last_completed_run_at_utc"], NOW.isoformat())

    def test_receipt_release_is_frozen_across_current_release_marker_failure(self):
        self.receipt()
        marker = self.release / ".git/modelfc-deployed-sha"
        marker.chmod(0o644)
        marker.write_text("invalid", encoding="ascii")
        report = self.report()
        self.assertEqual(report["components"]["release"]["state"], "ERROR")
        self.assertEqual(report["components"]["prospective"]["last_run_release_sha"], SHA)

    def test_receipt_completed_during_status_reads_is_not_future(self):
        completed = NOW + timedelta(seconds=2)
        publish_receipt(self.state, started=NOW, completed=completed,
                        summary={"status": "OK", "reasons": [],
                                 "prospective_budget_remaining": 177,
                                 **{key: 0 for key in COUNTERS}}, release_sha=SHA)
        clock = iter((100.0, 103.0))
        with patch.object(status, "time", SimpleNamespace(monotonic=lambda: next(clock))):
            prospective = self.report(now=NOW)["components"]["prospective"]
        self.assertEqual(prospective["runner_completion"], "VERIFIED")
        self.assertEqual(prospective["last_completed_run_at_utc"], completed.isoformat())

    def test_healthy_empty_state_zero_fixtures_and_no_lazy_state_lock(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        report = self.report()
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.state / ".lock").exists())
        parts = report["components"]
        self.assertEqual(status.exit_code(report), 0)
        self.assertEqual(parts["release"], {"state": "OK", "sha": SHA})
        self.assertEqual(parts["history"]["e1_latest_result"], "2026-09-20")
        self.assertEqual(parts["prospective"]["fixtures_discovered"], 0)
        self.assertEqual(parts["prospective"]["runner_completion"], "UNVERIFIED")
        self.assertIsNone(parts["prospective"]["last_completed_run_at_utc"])
        self.assertEqual(parts["budget"]["remaining"], 177)
        self.assertEqual(parts["evidence"]["predictions"], 0)
        self.assertEqual(parts["evidence"]["settled_predictions"], 0)

    def test_populated_evidence_counts_are_backend_values(self):
        from modelfc.corner_prospective_read import read_performance
        example = read_performance(self.state)
        example["model_performance"].update(total_prediction_runs=3, settled_prediction_runs=2)
        example["opportunity_performance"].update(total_opportunity_events=4,
            settled_opportunities=2, unresolved_open_opportunities=2)
        with patch.object(status, "read_performance", return_value=example):
            result = self.report()["components"]["evidence"]
        self.assertEqual(result, {"state": "OK", "predictions": 3, "settled_predictions": 2,
                                  "opportunities": 4, "settled_opportunities": 2,
                                  "unresolved_opportunities": 2})

    def test_corrupt_evidence_preserves_validated_control_and_budget(self):
        with patch.object(status, "read_performance", side_effect=LedgerError("sensitive evidence path")):
            components = self.report()["components"]
        self.assertEqual(components["prospective"]["state"], "OK")
        self.assertEqual(components["budget"]["remaining"], 177)
        self.assertEqual(components["evidence"]["state"], "ERROR")
        self.assertIsNone(components["evidence"]["predictions"])
        self.assertNotIn("sensitive evidence path", json.dumps(components))

    def test_stale_history_and_failed_refresh_are_independent(self):
        later = datetime(2026, 10, 10, tzinfo=timezone.utc)
        parts = self.report(now=later)["components"]
        self.assertEqual(parts["history"]["state"], "WARNING")
        self.assertEqual(parts["history"]["age_days"], 20)
        self.refresh_report["results"][0]["status"] = "failed"
        self.save(self.refresh, self.refresh_report)
        parts = self.report()["components"]
        self.assertEqual(parts["history"]["state"], "OK")
        self.assertEqual(parts["refresh"]["state"], "ERROR")
        self.assertEqual(status.exit_code(self.report()), 1)

    def test_malformed_refresh_and_missing_history_fail_closed(self):
        self.refresh.write_text("{", encoding="utf-8")
        self.assertEqual(self.report()["components"]["refresh"]["state"], "ERROR")
        self.refresh_report["season"] = None
        self.save(self.refresh, self.refresh_report)
        self.assertEqual(self.report()["components"]["refresh"]["state"], "ERROR")
        self.refresh.unlink()
        self.assertEqual(self.report()["components"]["refresh"]["state"], "ERROR")
        (self.history / "E1_2627.csv").unlink()
        self.save(self.refresh, {**self.refresh_report, "season": "2627"})
        parts = self.report()["components"]
        self.assertEqual(parts["history"]["state"], "ERROR")
        self.assertEqual(parts["refresh"]["state"], "OK")
        self.assertEqual(parts["refresh"]["e1_result"], "UNCHANGED")

    def test_budget_exhausted_or_outside_period_is_warning(self):
        self.control["period"]["reserved"] = 180
        self.save(self.control_path, self.control)
        budget = self.report()["components"]["budget"]
        self.assertEqual((budget["state"], budget["remaining"]), ("WARNING", 0))
        self.assertEqual(status.exit_code(self.report()), 0)
        self.control["period"]["reserved"] = 3
        self.save(self.control_path, self.control)
        self.assertEqual(self.report(now=datetime(2026, 10, 1, tzinfo=timezone.utc))
                         ["components"]["budget"]["state"], "WARNING")

    def test_rollover_event_proves_current_period_without_rewriting_evidence(self):
        self.control["period"] = {"start": "2026-10-01", "end": "2026-11-01",
                                  "allowance": 180, "reserved": 4}
        del self.control["budget"]["transitional_period"]
        self.save(self.control_path, self.control)
        self.save(self.state / "prospective/budget-events/rollover:2026-09:2026-10.json", {
            "version": 1, "event_id": "rollover:2026-09:2026-10",
            "event_type": "MONTH_ROLLOVER", "recorded_at_utc": "2026-10-01T00:05:00+00:00",
            "before": {"start": "2026-09-23", "end": "2026-10-01", "allowance": 180,
                       "reserved": 3},
            "after": {"start": "2026-10-01", "end": "2026-11-01", "allowance": 180,
                      "reserved": 0},
        })
        october = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
        self.assertEqual(self.report(now=october)["components"]["budget"]["remaining"], 176)
        event = self.state / "prospective/budget-events/rollover:2026-09:2026-10.json"
        value = json.loads(event.read_text(encoding="utf-8"))
        value["after"]["reserved"] = 1
        self.save(event, value)
        self.assertEqual(self.report(now=october)["components"]["budget"]["state"], "ERROR")

    def test_incomplete_discovery_warning_and_zero_done_ok(self):
        self.assertEqual(self.report()["components"]["prospective"]["state"], "OK")
        for value in ("RESERVED", "FAILED"):
            with self.subTest(value=value):
                self.control["discovery"].update(status=value, fixtures=[])
                self.save(self.control_path, self.control)
                self.assertEqual(self.report()["components"]["prospective"]["state"], "WARNING")

    def test_malformed_control_budget_event_and_missing_lock(self):
        self.control_path.write_text("{", encoding="utf-8")
        self.assertEqual(self.report()["components"]["prospective"]["state"], "ERROR")
        self.assertEqual(self.report()["components"]["budget"]["state"], "ERROR")
        self.save(self.control_path, self.control)
        next((self.state / "prospective/budget-events").glob("*.json")).unlink()
        self.assertEqual(self.report()["components"]["budget"]["state"], "ERROR")
        (self.state / "prospective/runner.lock").unlink()
        self.assertEqual(self.report()["components"]["prospective"]["state"], "ERROR")

    def test_services_and_unverified_are_not_success_claims(self):
        inactive = {**SERVICES, "prospective_timer": {"active": False, "enabled": True}}
        self.assertEqual(self.report(services=inactive)["components"]["services"]["state"], "ERROR")
        unknown = {**SERVICES, "read_only_api": {"active": None, "enabled": None}}
        self.assertEqual(self.report(services=unknown)["components"]["services"]["state"], "UNVERIFIED")
        self.assertEqual(status.exit_code(self.report(services=unknown)), 0)

    def test_release_marker_corruption_does_not_invoke_git(self):
        marker = self.release / ".git/modelfc-deployed-sha"
        marker.chmod(0o644)
        marker.write_text("bad", encoding="ascii")
        with patch("subprocess.run", side_effect=AssertionError("git must never run")):
            report = self.report()
        self.assertEqual(report["components"]["release"], {"state": "ERROR", "sha": None})

    def test_human_json_redaction_and_exit_code(self):
        self.control["private_key"] = "secret-value"
        self.save(self.control_path, self.control)
        report = self.report()
        text = status.format_status(report)
        self.assertIn("MODEL FC PRODUCTION", text)
        self.assertIn("[ERROR]", text)
        self.assertNotIn("secret-value", text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("secret-value", json.dumps(report))
        self.assertEqual(status.exit_code(report), 1)
        with patch.object(host, "probe_services", return_value=SERVICES), patch.object(
            status, "report_status", return_value=report
        ) as builder:
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(status.main(["--json", "--release", str(self.release),
                                              "--config", str(self.config),
                                              "--state-dir", str(self.state)]), 1)
            self.assertEqual(json.loads(output.getvalue()), report)
            self.assertEqual(builder.call_count, 1)
        with self.assertRaises(status.StatusConfigurationError):
            status.report_status(release=self.release, config_path=self.root / "missing",
                                 state_dir=self.state, service_signals=SERVICES, now=NOW)


class SystemdProbeTests(unittest.TestCase):
    def test_fixed_read_only_units_and_no_shell(self):
        sample = "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n"
        with patch.object(host.subprocess, "run", return_value=SimpleNamespace(stdout=sample)) as run:
            signals = host.probe_services()
        self.assertEqual(signals, SERVICES)
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:2], ["/usr/bin/systemctl", "show"])
            self.assertIn(call.args[0][2], host.UNITS.values())
            self.assertNotIn("shell", call.kwargs)

    def test_probe_failure_is_unverified(self):
        with patch.object(host.subprocess, "run", side_effect=OSError("private path")):
            self.assertTrue(all(item == {"active": None, "enabled": None}
                                for item in host.probe_services().values()))
        duplicate = "LoadState=loaded\nActiveState=failed\nActiveState=active\nUnitFileState=enabled\n"
        with patch.object(host.subprocess, "run", return_value=SimpleNamespace(stdout=duplicate)):
            self.assertTrue(all(item == {"active": None, "enabled": None}
                                for item in host.probe_services().values()))


class TrustedLauncherTests(unittest.TestCase):
    @staticmethod
    def launcher():
        source = Path(__file__).resolve().parents[1] / "ops/vps/status_launch.py"
        spec = importlib.util.spec_from_file_location("status_launcher_test", source)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        return launcher

    def test_rejects_unapproved_arguments_before_any_release_or_host_access(self):
        launcher = self.launcher()
        with patch.object(launcher.os, "geteuid", return_value=0), patch.object(
            launcher.pwd, "getpwnam", side_effect=AssertionError("host access")
        ), patch.object(launcher.os, "execve", side_effect=AssertionError("execution")):
            with self.assertRaises(ValueError):
                launcher.launch(["--release", "/tmp/attacker"])

    def test_rejects_root_or_deployment_runtime_identity_before_selecting_release(self):
        launcher = self.launcher()
        for account in (SimpleNamespace(pw_uid=0, pw_gid=12),
                        SimpleNamespace(pw_uid=31, pw_gid=0),
                        SimpleNamespace(pw_uid=21, pw_gid=12)):
            with self.subTest(account=account), patch.object(launcher.os, "geteuid", return_value=0), patch.object(
                launcher.pwd, "getpwnam", side_effect=[SimpleNamespace(pw_uid=21), account]
            ), patch.object(launcher.os, "readlink", side_effect=AssertionError("release access")):
                with self.assertRaises(ValueError):
                    launcher.launch([])


if __name__ == "__main__":
    unittest.main()
