"""Synthetic complete calendars and recorded OddsPapi transport; no live calls."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from modelfc import odds_calendar as bridge
from modelfc import corner_prospective as runner
from modelfc.prospective_run_receipts import latest
from tests import test_corner_prospective as pilot_tests
from tests.test_history_completeness import bundle, raw
from tests import test_forecast_only_runner as forecast_tests


class CalendarGateTests(unittest.TestCase):
    setUp = pilot_tests.PilotTests.setUp
    sleep = pilot_tests.PilotTests.sleep
    http = pilot_tests.PilotTests.http
    control = pilot_tests.PilotTests.control
    write_control = pilot_tests.PilotTests.write_control

    def calendar(self, *, match=True, status='TIMED', kickoff=None):
        value = bundle()
        value['retrieved_at'] = self.now.isoformat()
        payload = json.loads(value['payload'])
        for row in payload['matches']:
            if row['status'] == 'FINISHED':
                row['utcDate'] = '2026-09-19T14:00:00Z'
            if (row['homeTeam']['name'], row['awayTeam']['name']) == ('Source Wolves', 'Source West Brom') and match:
                row['utcDate'] = kickoff or self.fixtures[0]['startTime']
                row['status'] = status
        value['payload'] = json.dumps(payload)
        return raw(value)

    def publish(self, **kwargs):
        content = self.calendar(**kwargs)
        bridge.publish(self.state, content, as_of=self.now)
        return content

    def run_gate(self):
        return runner.run_once(state_dir=self.state, data_config_path=self.config, calendar_gate=True)

    def test_empty_day_avoids_both_discoveries_and_preserves_control(self):
        self.publish(match=False)
        before = self.path.read_bytes()
        report = self.run_gate()
        self.assertEqual(report['status'], 'OK')
        self.assertEqual(report['reasons'], [])
        self.assertEqual(report['calendar_gate']['skip_reason'], 'CALENDAR_NO_RELEVANT_FIXTURES')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.control()['discovery'])
        self.now += timedelta(hours=6)
        self.publish(match=False)
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(latest(self.state, now=self.now)['summary']['calendar_gate'], self.run_gate()['calendar_gate'])

    def test_old_partial_receipt_semantics_preserved_and_skip_cannot_hide_errors(self):
        from copy import deepcopy
        from modelfc.prospective_run_receipts import validate, _hash, ReceiptError
        self.publish(match=False)
        self.run_gate()
        record = latest(self.state, now=self.now)
        old = deepcopy(record)
        old['summary']['calendar_gate'].pop('skip_reason')
        old['summary'].update(status='PARTIAL', reasons=['CALENDAR_NO_RELEVANT_FIXTURES'])
        old['record_hash'] = _hash(old)
        original = json.dumps(old, sort_keys=True)
        self.assertEqual(validate(old), old)
        self.assertEqual(json.dumps(old, sort_keys=True), original)
        invalid = deepcopy(record)
        invalid['summary']['reasons'] = ['CALENDAR_EVIDENCE_UNAVAILABLE']
        invalid['record_hash'] = _hash(invalid)
        with self.assertRaises(ReceiptError): validate(invalid)
        invalid = deepcopy(record)
        invalid['summary']['provider_requests'] = 1
        invalid['record_hash'] = _hash(invalid)
        with self.assertRaises(ReceiptError): validate(invalid)
        # Existing inventory errors must remain PARTIAL even on a valid empty day.
        def inventory(state, config, summary, source):
            runner._reason(summary, 'SETTLEMENT_REVIEW')
            return {}
        with patch.object(runner, '_inventory', side_effect=inventory):
            report = self.run_gate()
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual(report['reasons'], ['SETTLEMENT_REVIEW'])
        self.assertEqual(report['calendar_gate']['skip_reason'], 'CALENDAR_NO_RELEVANT_FIXTURES')

    def test_match_day_discovery_metadata_odds_then_idempotent_reuse(self):
        content = self.publish()
        report = self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures', 'markets', 'odds'])
        self.assertEqual(report['captures_created'], 1)
        self.assertEqual(report['calendar_gate']['bundle_sha256'], hashlib.sha256(content).hexdigest())
        evidence = {p:p.read_bytes() for folder in ('analyses','predictions','market-observations','odds-calendar')
                    for p in (self.state/folder).glob('*.json')}
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.assertTrue(all(p.read_bytes() == b for p,b in evidence.items()))

    def test_missing_stale_future_partial_corrupt_calendar_never_reserves(self):
        before = self.control()['period']['reserved']
        self.assertEqual(self.run_gate()['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])
        self.publish()
        self.now += timedelta(hours=6, seconds=1)
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.now -= timedelta(hours=7)
        self.assertEqual(self.run_gate()['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])
        self.now += timedelta(hours=1)
        value = json.loads(self.calendar()); payload = json.loads(value['payload'])
        payload['matches'].pop(); payload['resultSet']['count'] -= 1
        value['payload'] = json.dumps(payload)
        with self.assertRaises(ValueError): bridge.publish(self.state, raw(value), as_of=self.now)
        (self.state/'odds-calendar/latest.json').write_text('{}')
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.control()['period']['reserved'], before)

    def test_cancellation_skips_and_reschedule_blocks_old_mapping(self):
        self.publish(status='CANCELLED')
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.now += timedelta(seconds=1)
        self.publish(kickoff='2026-09-20T16:00:00Z')
        result = self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures'])
        self.assertIn('CALENDAR_FIXTURE_UNMAPPED', result['reasons'])
        self.assertIn('CALENDAR_FIXTURE_MISMATCH', result['reasons'])

    def test_new_match_after_skipped_empty_day_can_discover(self):
        self.publish(match=False); self.run_gate()
        self.now += timedelta(seconds=1); self.publish()
        self.assertEqual(self.run_gate()['captures_created'], 1)
        self.assertEqual([c[0] for c in self.calls], ['fixtures','markets','odds'])

    def test_late_listing_waits_for_existing_rediscovery_window(self):
        self.fixtures[0]["startTime"] = "2026-09-20T17:00:00Z"
        self.publish(); self.discovery_override = []
        self.assertIn('CALENDAR_FIXTURE_UNMAPPED', self.run_gate()['reasons'])
        self.discovery_override = None
        self.now += timedelta(hours=1); self.publish()
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.assertEqual([c[0] for c in self.calls], ['fixtures'])
        self.now += timedelta(hours=5); self.publish()
        self.run_gate()
        self.assertEqual([c[0] for c in self.calls].count('fixtures'), 2)

    def test_cached_provider_mapping_quotes_without_rediscovery(self):
        self.now -= timedelta(hours=5)
        self.publish(); self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures'])
        self.now += timedelta(hours=5); self.publish()
        self.assertEqual(self.run_gate()['captures_created'], 1)
        self.assertEqual([c[0] for c in self.calls], ['fixtures','markets','odds'])
        self.assertNotEqual(self.control()['discovery']['fixtures'][0]['fixtureId'], '2000')

    def test_cancelled_cached_event_has_no_quote(self):
        self.now -= timedelta(hours=5)
        self.publish(); self.run_gate()
        self.now += timedelta(hours=5); self.publish(status='POSTPONED')
        self.assertEqual(self.run_gate()['calendar_gate']['skip_reason'], 'CALENDAR_NO_RELEVANT_FIXTURES')
        self.assertEqual([c[0] for c in self.calls], ['fixtures'])

    def test_cli_empty_day_retained_evidence_zero_requests(self):
        from datetime import datetime, timezone
        self.now = datetime.now(timezone.utc)
        self.publish(match=False)
        env = {k:v for k,v in os.environ.items() if 'TOKEN' not in k and 'KEY' not in k}
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'src')
        result = subprocess.run([sys.executable,'-m','modelfc.corner_prospective','run-once',
            '--state-dir',str(self.state),'--data-config',str(self.config),'--calendar-gate'],
            env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['provider_requests'], 0)
        self.assertEqual(report['status'], 'OK')
        self.assertEqual(report['reasons'], [])
        self.assertEqual(report['calendar_gate']['skip_reason'], 'CALENDAR_NO_RELEVANT_FIXTURES')
        self.assertIn('calendar_gate', report)

    def test_duplicate_mapping_isolated_while_unrelated_fixture_is_quoted(self):
        from copy import deepcopy
        from tests.test_oddspapi import recorded
        other = deepcopy(recorded('odds-fixtures')[1])
        duplicate = dict(other, fixtureId='duplicate-provider-id')
        self.fixtures.extend([other, duplicate])
        value = json.loads(self.calendar()); payload = json.loads(value['payload'])
        for row in payload['matches']:
            if (row['homeTeam']['name'], row['awayTeam']['name']) == ('Source Norwich', 'Source Bolton'):
                row['utcDate'] = other['startTime']
        value['payload'] = json.dumps(payload)
        bridge.publish(self.state, raw(value), as_of=self.now)
        result = self.run_gate()
        self.assertEqual(result['reasons'], ['CALENDAR_FIXTURE_MISMATCH'])
        self.assertEqual(result['captures_created'], 1)
        self.assertEqual([c[0] for c in self.calls], ['fixtures', 'markets', 'odds'])
        self.assertEqual(set(self.control()['attempts']), {self.fixtures[0]['fixtureId']})

    def test_six_hour_window_unchanged(self):
        self.fixtures[0]['startTime'] = '2026-09-20T16:00:00Z'
        self.publish()
        self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures'])

    def test_expiry_during_spacing_keeps_precharge_without_http(self):
        self.fixtures[0]["startTime"] = "2026-09-20T17:00:00Z"
        self.publish()
        self.now += timedelta(hours=6)
        self.write_control(lambda c: c.update(last_request=self.now.timestamp()))
        result = self.run_gate()
        self.assertEqual(result['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.control()['period']['reserved'], 1)

    def test_budget_exhaustion_has_no_http(self):
        self.publish()
        self.write_control(lambda c: c['period'].update(reserved=c['period']['allowance']))
        self.assertIn('REQUEST_BUDGET', self.run_gate()['reasons'])
        self.assertEqual(self.calls, [])

    def test_immutable_replay_conflict_symlink_and_tamper(self):
        original = self.publish()
        root = self.state/'odds-calendar'
        before = {p:p.read_bytes() for p in root.glob('*.json')}
        bridge.publish(self.state, original, as_of=self.now)
        self.assertTrue(all(p.read_bytes() == b for p,b in before.items()))
        with self.assertRaises(ValueError): self.publish(match=False)
        pin = root/'latest.json'; pin.unlink(); pin.symlink_to(self.path)
        self.assertEqual(self.run_gate()['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])
        pin.unlink(); bridge.publish(self.state, original, as_of=self.now)
        record = root/(hashlib.sha256(original).hexdigest()+'.json')
        record.write_text('{"raw":"tampered"}')
        self.assertEqual(self.run_gate()['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])

    def test_actual_cli_missing_evidence_requires_no_provider_credential(self):
        env = {k:v for k,v in os.environ.items() if 'TOKEN' not in k and 'KEY' not in k}
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'src')
        result = subprocess.run([sys.executable,'-m','modelfc.corner_prospective','run-once',
            '--state-dir',str(self.state),'--data-config',str(self.config),'--calendar-gate'],
            env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('CALENDAR_EVIDENCE_UNAVAILABLE', json.loads(result.stdout)['reasons'])
        self.assertEqual(json.loads(result.stdout)['provider_requests'], 0)


class PublisherTests(unittest.TestCase):
    def test_existing_forecast_fetch_publishes_before_no_eligible_return(self):
        fixture = forecast_tests.ForecastOnlyRunnerTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        from tests.test_history_completeness import NOW
        result = fixture.execute(when=NOW + timedelta(hours=6))
        self.assertEqual(result['status'], 'NO_ELIGIBLE_FIXTURES')
        saved, calendar = bridge.load(fixture.state, as_of=NOW + timedelta(hours=6))
        self.assertEqual(calendar.retrieved_at, NOW)
        self.assertEqual(result['provider_requests'], {'football_data_org':1,'sportmonks':0,'oddspapi':0})
        self.assertEqual(saved, raw(bundle()))
