"""Offline validation of private immutable completion records."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc import prospective_run_receipts as receipts


NOW = datetime(2026, 9, 28, 12, 5, tzinfo=timezone.utc)
SHA = "a" * 40


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.summary = {"status": "OK", "reasons": [], "prospective_budget_remaining": 177,
                        **{key: 0 for key in receipts.COUNTERS}}

    def publish(self, *, start=NOW, end=NOW):
        return receipts.publish(self.state, started=start, completed=end,
                                summary=self.summary, release_sha=SHA)

    def test_no_receipt_and_atomic_exclusive_publication(self):
        self.assertIsNone(receipts.latest(self.state, now=NOW))
        observed = []
        original = receipts.write_new_record

        def checked(path, record):
            self.assertFalse(path.exists())
            observed.append(path)
            return original(path, record)

        with patch.object(receipts, "write_new_record", side_effect=checked):
            self.publish()
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0].exists())
        saved = receipts.latest(self.state, now=NOW)
        self.assertEqual(saved["summary"], self.summary)
        self.assertEqual(saved["completion"], "COMPLETED")
        self.assertEqual(saved["duration_ms"], 0)
        self.assertEqual(saved["record_hash"], receipts._hash(saved))

    def test_duration_is_derived_from_frozen_utc_timestamps(self):
        started = NOW - timedelta(seconds=2, milliseconds=812, microseconds=900)
        saved = self.publish(start=started)
        self.assertEqual(saved["duration_ms"], 2812)
        self.assertEqual(receipts.latest(self.state, now=NOW), saved)

    def test_intervals_schema_and_identity_fail_closed(self):
        record = self.publish()
        path = next(self.state.rglob("*.json"))
        for change in ({"schema_version": 2}, {"release_sha": "not-a-sha"},
                       {"completion": "STARTED"}, {"duration_ms": True},
                       {"duration_ms": 1},
                       {"started_at_utc": (NOW + timedelta(hours=1)).isoformat()},
                       {"run_id": "b" * 32},
                       {"summary": {**self.summary, "provider_requests": -1}},
                       {"summary": {**self.summary, "reasons": ["secret/path"]}}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**record, **change}), encoding="utf-8")
                with self.assertRaises(receipts.ReceiptError):
                    receipts.latest(self.state, now=NOW)

    def test_hash_detects_well_formed_counter_tampering(self):
        self.publish()
        path = next(self.state.rglob("*.json"))
        record = json.loads(path.read_text(encoding="utf-8"))
        record["summary"]["provider_requests"] = 1
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(receipts.ReceiptError, "hash mismatch"):
            receipts.latest(self.state, now=NOW)

    def test_duplicate_identity_and_future_timestamp(self):
        self.publish()
        path = next(self.state.rglob("*.json"))
        duplicate = path.with_name("20260928T120501000000Z-" + path.name.split("-")[1])
        duplicate.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(receipts.ReceiptError, "duplicate"):
            receipts.latest(self.state, now=NOW)
        duplicate.unlink()
        with self.assertRaisesRegex(receipts.ReceiptError, "interval"):
            receipts.latest(self.state, now=NOW - timedelta(seconds=1))

    def test_publication_failure_does_not_leave_partial_json(self):
        with patch.object(receipts, "write_new_record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(list(self.state.rglob("*.json")), [])

    def test_private_even_when_public_evidence_acl_opt_in_is_set(self):
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), patch(
            "modelfc.ledger_storage.subprocess.run", side_effect=AssertionError("no ACL command")
        ):
            self.publish()
        root = self.state / "prospective/run-receipts"
        day = root / NOW.date().isoformat()
        self.assertEqual(root.stat().st_mode & 0o077, 0)
        self.assertEqual(day.stat().st_mode & 0o077, 0)
        self.assertEqual(next(day.glob("*.json")).stat().st_mode & 0o077, 0)

    def test_incomplete_new_day_does_not_hide_last_published_receipt(self):
        earlier = NOW - timedelta(days=1)
        prior = self.publish(start=earlier, end=earlier)
        new_day = self.state / "prospective/run-receipts" / NOW.date().isoformat()
        new_day.mkdir(mode=0o700)
        (new_day / ".record-interrupted.tmp").write_text("unfinished", encoding="utf-8")
        self.assertEqual(receipts.latest(self.state, now=NOW), prior)
        (new_day / ("20260928T120500000000Z-" + "b" * 32 + ".json")).write_text("{", encoding="utf-8")
        with self.assertRaises(receipts.ReceiptError):
            receipts.latest(self.state, now=NOW)


    def test_fixture_report_old_receipts_and_absence_are_unknown(self):
        from modelfc.fixture_diagnostics import report
        missing = report(self.state, day=NOW.date(), now=NOW)
        self.assertEqual(missing['evidence_state'], 'UNKNOWN')
        self.assertIsNone(missing['counts']['distinct_fixtures'])
        original = self.publish()
        result = report(self.state, day=NOW.date(), fixture_id='123', now=NOW)
        self.assertEqual(result['count_scope'], 'UNKNOWN')
        self.assertEqual(result['runs'][0]['diagnostics'], 'UNKNOWN')
        self.assertEqual(receipts.latest(self.state, now=NOW), original)

    def test_fixture_diagnostic_bounds_and_validation(self):
        from types import SimpleNamespace
        from modelfc.fixture_diagnostics import Recorder, MAX_EVENTS, report
        recorder = Recorder()
        fixture = SimpleNamespace(provider_fixture_id='123', provider='oddspapi', competition='E1')
        for _ in range(MAX_EVENTS+1):
            recorder.add(fixture, 'DISCOVERED', NOW)
        self.assertEqual(len(recorder.events), MAX_EVENTS)
        self.assertEqual(recorder.snapshot()['status'], 'INCOMPLETE')
        record = receipts.publish(self.state, started=NOW, completed=NOW, summary=self.summary,
                                  release_sha=SHA, fixture_diagnostics=recorder.snapshot())
        self.assertEqual(receipts.latest(self.state, now=NOW), record)
        self.assertEqual(report(self.state, day=NOW.date(), now=NOW)['counts']['distinct_fixtures'], 1)
        from copy import deepcopy
        for change in [{'reason':'secret/path'}, {'fixture_id':'../private'},
                       {'observed_at_utc':(NOW+timedelta(seconds=1)).isoformat()},
                       {'category':'NO_QUALIFYING_VALUE'}, {'evidence_hash':'secret'}]:
            invalid = deepcopy(record)
            invalid['fixture_diagnostics']['events'][0].update(change)
            invalid['record_hash'] = receipts._hash(invalid)
            with self.assertRaises(receipts.ReceiptError):
                receipts.validate(invalid)

    def test_shared_namespaces_filters_and_distinct_reference_counts(self):
        from types import SimpleNamespace
        from modelfc.fixture_diagnostics import Recorder, report
        recorder = Recorder()
        for provider, competition in (('oddspapi', 'E1'), ('football-data.org', 'E1'), ('oddspapi', 'E0')):
            fixture = SimpleNamespace(provider_fixture_id='123', provider=provider, competition=competition)
            for market in ('BTTS', 'CORNER_TEAM_TOTAL'):
                recorder.add(fixture, 'DISCOVERED', NOW, market=market)
                for reason in ('FORECAST_RECORDED', 'FORECAST_REUSED'):
                    recorder.add(fixture, reason, NOW, market=market, reference_id='a'*64)
                for strategy in ('best_value', 'higher_probability'):
                    for reason in ('PUBLICATION_RECORDED', 'PUBLICATION_EXISTING'):
                        recorder.add(fixture, reason, NOW, market=market, strategy=strategy, reference_id='b'*64)
        receipts.publish(self.state, started=NOW, completed=NOW, summary=self.summary,
                         release_sha=SHA, fixture_diagnostics=recorder.snapshot())
        result = report(self.state, day=NOW.date(), now=NOW)
        self.assertEqual(result['counts']['distinct_fixtures'], 3)
        self.assertEqual(result['counts']['fixture_market_groups'], 6)
        self.assertEqual(result['counts']['forecast_cohorts'], 6)
        self.assertEqual(result['counts']['publication_records'], 12)
        filtered = report(self.state, day=NOW.date(), now=NOW,
                          competition='E1', provider='oddspapi', market='BTTS')
        self.assertEqual(filtered['counts']['distinct_fixtures'], 1)
        self.assertEqual(filtered['counts']['publication_records'], 2)
        self.assertEqual(filtered, report(self.state, day=NOW.date(), now=NOW,
                          competition='E1', provider='oddspapi', market='BTTS'))
        self.assertTrue(all(r['instrumentation'] == 'UNSUPPORTED_BY_IMPLEMENTATION'
                            for r in result['support'] if r['competition'] == 'E0'))

    def test_legacy_forecast_unknown_and_new_forecast_hash_checked(self):
        from modelfc.forecast_only_runner import _publish_receipt
        from modelfc.fixture_diagnostics import Recorder, report
        legacy = dict(run_at_utc=NOW.isoformat(), deployed_sha=SHA, status='RECORDED')
        _publish_receipt(self.state, legacy)
        result = report(self.state, day=NOW.date(), now=NOW)
        self.assertEqual(result['runs'][0]['diagnostics'], 'UNKNOWN')
        self.assertEqual(result['runs'][0]['time_basis'], 'LEGACY_RUN_START')
        self.assertIsNone(result['counts']['forecast_cohorts'])
        later = NOW + timedelta(seconds=1)
        _publish_receipt(self.state, dict(legacy, run_at_utc=later.isoformat()),
                         diagnostics=Recorder(), completed=later)
        self.assertEqual(len(report(self.state, day=NOW.date(), now=later)['runs']), 2)
        path = next(p for p in (self.state/'forecast-only-receipts').glob('*.json')
                    if 'record_hash' in json.loads(p.read_bytes()))
        record = json.loads(path.read_bytes())
        record['status'] = 'REJECTED'
        path.write_text(json.dumps(record))
        with self.assertRaises(receipts.ReceiptError):
            report(self.state, day=NOW.date(), now=later)

    def test_forecast_blocked_run_reasons_remain_run_wide_and_sanitized(self):
        from modelfc.forecast_only_runner import _publish_receipt
        from modelfc.fixture_diagnostics import Recorder, report
        for index, reason in enumerate(('SPORTMONKS_RETRY_WAIT', 'HISTORY_RESULTS_MISSING',
                                        'private/path/token', ['SPORTMONKS_RETRY_WAIT'])):
            when = NOW + timedelta(seconds=index)
            _publish_receipt(self.state, dict(run_at_utc=when.isoformat(), deployed_sha=SHA,
                             status='BLOCKED', reason=reason), diagnostics=Recorder(), completed=when)
        result = report(self.state, day=NOW.date(), now=when, fixture_id='123')
        self.assertEqual([run['run_reasons'] for run in result['runs']],
                         [['SPORTMONKS_RETRY_WAIT'], ['HISTORY_RESULTS_MISSING'], ['UNKNOWN'], ['UNKNOWN']])
        self.assertTrue(all(not run['events'] for run in result['runs']))
        self.assertEqual(result['stage_evidence'], [])
        self.assertNotIn('private/path/token', json.dumps(result))
        self.assertIsNone(result['counts']['distinct_fixtures'])

    def test_invalid_diagnostic_clock_degrades_without_losing_receipt(self):
        from types import SimpleNamespace
        from modelfc.fixture_diagnostics import Recorder
        recorder = Recorder()
        recorder.add(SimpleNamespace(provider_fixture_id='123', provider='oddspapi', competition='E1'),
                     'DISCOVERED', NOW + timedelta(seconds=1))
        record = receipts.publish(self.state, started=NOW, completed=NOW, summary=self.summary,
                                  release_sha=SHA, fixture_diagnostics=recorder.snapshot())
        self.assertEqual(record['fixture_diagnostics']['status'], 'INCOMPLETE')
        self.assertEqual(record['fixture_diagnostics']['events'], [])
        self.assertEqual(record['summary'], self.summary)

    def test_fixture_report_rejects_symlinks_and_tampered_records(self):
        from modelfc.fixture_diagnostics import report
        self.publish()
        path = next(self.state.rglob('*.json'))
        before = path.read_bytes()
        path.unlink()
        target = self.state/'private'
        target.write_bytes(before)
        path.symlink_to(target)
        with self.assertRaises(receipts.ReceiptError):
            report(self.state, day=NOW.date(), now=NOW)
        path.unlink()
        data = json.loads(before)
        data['summary']['provider_requests'] += 1
        path.write_text(json.dumps(data))
        with self.assertRaises(receipts.ReceiptError):
            report(self.state, day=NOW.date(), now=NOW)


if __name__ == "__main__":
    unittest.main()
