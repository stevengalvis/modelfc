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
        self.assertEqual(report['calendar_cohorts'][0]['decision'], 'EMPTY')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.control()['discovery'])
        self.now += timedelta(hours=6)
        self.publish(match=False)
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(latest(self.state, now=self.now)['summary']['calendar_cohorts'][0]['decision'], 'EMPTY')

    def test_match_day_discovery_metadata_odds_then_idempotent_reuse(self):
        content = self.publish()
        report = self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures', 'markets', 'odds'])
        self.assertEqual(report['captures_created'], 1)
        self.assertEqual(report['calendar_cohorts'][0]['bundle_sha256'], hashlib.sha256(content).hexdigest())
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
        self.assertEqual(self.run_gate()['calendar_cohorts'][0]['decision'], 'EMPTY')
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
        self.assertEqual(report['calendar_cohorts'][0]['decision'], 'EMPTY')
        self.assertIn('calendar_cohorts', report)

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

    def test_first_publication_syncs_parent_and_retries_failed_directory_sync(self):
        original_sync = os.fsync
        seen = []
        def sync(fd):
            path = Path(os.readlink(f'/proc/self/fd/{fd}'))
            seen.append(path)
            if path == self.state:
                raise OSError('simulated parent sync failure')
            original_sync(fd)
        with patch.object(bridge.os, 'fsync', side_effect=sync):
            with self.assertRaises(OSError): self.publish()
        self.assertIn(self.state, seen)
        self.assertFalse((self.state/'odds-calendar/latest.json').exists())
        seen.clear()
        def successful_sync(fd):
            seen.append(Path(os.readlink(f'/proc/self/fd/{fd}')))
            original_sync(fd)
        with patch.object(bridge.os, 'fsync', side_effect=successful_sync):
            content = self.publish()
        self.assertIn(self.state, seen)
        self.assertIn(self.state/'odds-calendar', seen)
        self.assertEqual(bridge.load(self.state, as_of=self.now)[0], content)

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

    def test_gate_off_ignores_missing_calendar(self):
        report = runner.run_once(state_dir=self.state, data_config_path=self.config)
        self.assertEqual(report['captures_created'], 1)
        self.assertNotIn('calendar_cohorts', report)
        from modelfc.fixture_diagnostics import report as diagnostics
        result = diagnostics(self.state, day=self.now.date(), now=self.now)
        self.assertTrue(all(r['calendar_cohorts'] == 'UNKNOWN' for r in result['runs']))

    def install_untrusted(self, content):
        root = bridge.directory(self.state, create=True)
        digest = hashlib.sha256(content).hexdigest()
        from modelfc.ledger_storage import write_new_record
        write_new_record(root / (digest + '.json'), {'raw': content.decode()})
        pin = root / 'latest.json'
        pin.write_text(json.dumps({'bundle_sha256': digest}))
        pin.chmod(0o600)

    def test_partial_and_duplicate_calendar_fail_closed_at_consumer(self):
        for kind in ('partial', 'duplicate', 'corrupt'):
            with self.subTest(kind=kind):
                value = json.loads(self.calendar())
                payload = json.loads(value['payload'])
                if kind == 'partial':
                    payload['matches'].pop(); payload['resultSet']['count'] -= 1
                elif kind == 'duplicate':
                    payload['matches'][-1] = payload['matches'][0]
                value['payload'] = json.dumps(payload)
                content = raw(value) if kind != 'corrupt' else b'{'
                self.install_untrusted(content)
                before = self.path.read_bytes()
                self.assertEqual(self.run_gate()['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.calls, [])

    def test_partial_publication_has_no_paid_fallback(self):
        with patch.object(bridge.os, 'replace', side_effect=OSError('offline interruption')):
            with self.assertRaises(OSError): self.publish()
        self.assertTrue(list((self.state / 'odds-calendar').glob('*.json')))
        self.assertFalse((self.state / 'odds-calendar/latest.json').exists())
        before = self.path.read_bytes()
        self.assertEqual(self.run_gate()['reasons'], ['CALENDAR_EVIDENCE_UNAVAILABLE'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_reversed_teams_and_provider_id_mismatch(self):
        from copy import deepcopy
        self.publish()
        original = deepcopy(self.fixtures[0])
        self.fixtures[0]['participant1Id'], self.fixtures[0]['participant2Id'] = (
            original['participant2Id'], original['participant1Id'])
        result = self.run_gate()
        self.assertEqual(result['provider_requests'], 1)
        self.assertEqual(self.control()['attempts'], {})
        self.assertTrue(set(result['reasons']) & {'DISCOVERY_INVALID', 'CALENDAR_FIXTURE_MISMATCH'})

    def test_expiry_between_metadata_and_quote_keeps_reservation(self):
        self.publish()
        original = self.http
        retrieved = self.now
        def http(request, **kwargs):
            result = original(request, **kwargs)
            if self.calls[-1][0] == 'markets':
                self.now = retrieved + timedelta(hours=6)
            return result
        with patch('urllib.request.OpenerDirector.open', side_effect=http):
            result = self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures', 'markets'])
        self.assertEqual(self.control()['period']['reserved'], 3)
        self.assertIn('CALENDAR_EVIDENCE_UNAVAILABLE', result['reasons'])
        self.assertEqual(next(iter(self.control()['attempts'].values()))['state'], 'RESERVED')
        before = self.control()['period']['reserved']
        self.run_gate()
        self.assertEqual(self.control()['period']['reserved'], before)

    def test_cancellation_during_quote_pacing_blocks_http_after_precharge(self):
        self.publish()
        original = self.sleep
        def sleep(seconds):
            original(seconds)
            if self.calls and self.calls[-1][0] == 'markets':
                self.publish(status='CANCELLED')
        with patch.object(runner.time, 'sleep', side_effect=sleep):
            result = self.run_gate()
        self.assertEqual([c[0] for c in self.calls], ['fixtures', 'markets'])
        self.assertIn('CALENDAR_FIXTURE_MISMATCH', result['reasons'])
        self.assertEqual(self.control()['period']['reserved'], 3)

    def test_revalidation_before_reservation_avoids_mutations(self):
        self.publish()
        from modelfc.providers.oddspapi import OddsPapiMarketData
        class ExpiringClient(OddsPapiMarketData):
            def enable_run_metadata_reuse(client):
                super().enable_run_metadata_reuse()
                self.now += timedelta(hours=6, seconds=1)
        before = self.path.read_bytes()
        result = runner.run_once(state_dir=self.state, data_config_path=self.config,
                                calendar_gate=True, market_data_type=ExpiringClient)
        self.assertIn('CALENDAR_EVIDENCE_UNAVAILABLE', result['reasons'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_receipt_decisions_are_immutable_and_validated(self):
        from copy import deepcopy
        from modelfc.prospective_run_receipts import validate, _hash, ReceiptError
        self.publish(match=False); self.run_gate()
        record = latest(self.state, now=self.now)
        original = json.dumps(record, sort_keys=True)
        validate(record)
        self.assertEqual(original, json.dumps(record, sort_keys=True))
        for change in ({'decision': 'SKIPPED'}, {'target_date': 'invalid'},
                       {'bundle_sha256': None}, {'retrieved_at_utc': '2099-01-01T00:00:00Z'}):
            altered = deepcopy(record)
            altered['summary']['calendar_cohorts'][0].update(change)
            altered['record_hash'] = _hash(altered)
            with self.assertRaises(ReceiptError): validate(altered)


class FridayCalendarTests(unittest.TestCase):
    def setUp(self):
        from tests.test_friday_collection import FridayTests
        self.friday = FridayTests()
        self.friday.setUp()
        self.addCleanup(self.friday.doCleanups)
        self.fx = self.friday.fx

    def publish(self):
        value = bundle(); value['retrieved_at'] = self.fx.now.isoformat()
        payload = json.loads(value['payload'])
        from modelfc.providers.oddspapi import OddsPapiMarketData
        fixtures = [OddsPapiMarketData.fixture_from_provenance(f, self.fx.now, 'E1') for f in self.fx.fixtures]
        for row in payload['matches']:
            for f in fixtures:
                if (row['homeTeam']['name'], row['awayTeam']['name']) == ('Source '+f.home_team, 'Source '+f.away_team):
                    row['utcDate'] = f.kickoff_utc.isoformat()
        value['payload'] = json.dumps(payload)
        bridge.publish(self.fx.state, raw(value), as_of=self.fx.now)

    def run_gate(self):
        return runner.run_once(state_dir=self.fx.state, data_config_path=self.fx.config,
                               calendar_gate=True, friday_review=True)

    def test_empty_friday_does_not_suppress_saturday_and_retry_is_idempotent(self):
        self.publish()
        result = self.run_gate()
        self.assertEqual(result['captures_created'], 1, result)
        self.assertEqual([d['decision'] for d in result['calendar_cohorts']], ['EMPTY', 'ELIGIBLE'])
        self.assertEqual([d['target_date'] for d in result['calendar_cohorts']], ['2026-09-25', '2026-09-26'])
        self.assertIsNone(self.fx.control()['discovery'])
        self.assertEqual([c[0] for c in self.fx.calls], ['fixtures', 'markets', 'odds'])
        before = self.fx.path.read_bytes()
        self.assertEqual(self.run_gate()['provider_requests'], 0)
        self.assertEqual(self.fx.path.read_bytes(), before)
        from modelfc.fixture_diagnostics import report
        diagnostics = report(self.fx.state, day=self.fx.now.date(), now=self.fx.now)
        self.assertIn(result['calendar_cohorts'], [r['calendar_cohorts'] for r in diagnostics['runs']])

    def test_missing_calendar_records_both_cohorts_without_mutation(self):
        before = self.fx.path.read_bytes()
        result = self.run_gate()
        self.assertEqual([d['decision'] for d in result['calendar_cohorts']], ['UNAVAILABLE', 'UNAVAILABLE'])
        self.assertEqual(self.fx.calls, [])
        self.assertEqual(self.fx.path.read_bytes(), before)

    def both_days(self, count=2):
        from tests.test_oddspapi import recorded
        from copy import deepcopy
        from modelfc.providers.oddspapi import E1_TEAMS_BY_ID
        # Real recorded provider ID/name bindings, paired without ambiguity.
        templates = recorded('odds-fixtures')
        self.fx.fixtures = [deepcopy(templates[0]), deepcopy(templates[1])]
        self.fx.fixtures[0]['startTime'] = '2026-09-25T12:30:00Z'
        self.fx.fixtures[1]['startTime'] = '2026-09-26T15:00:00Z'
        if count > 2:
            ids = list(E1_TEAMS_BY_ID)
            for i in range(count-2):
                f = deepcopy(templates[0])
                f.update(fixtureId=f'extra-{i}', participant1Id=ids[2*i], participant2Id=ids[2*i+1],
                         participant1Name=E1_TEAMS_BY_ID[ids[2*i]][0],
                         participant2Name=E1_TEAMS_BY_ID[ids[2*i+1]][0], startTime='2026-09-26T16:00:00Z')
                self.fx.fixtures.append(f)

    def test_mixed_cohorts_share_metadata_and_budget(self):
        self.both_days(); self.publish()
        result = self.run_gate()
        self.assertEqual(result['provider_requests'], 5, result)
        self.assertEqual([c[0] for c in self.fx.calls].count('markets'), 1)
        self.assertEqual(self.fx.control()['period']['reserved'], 5)
        self.assertEqual([d['decision'] for d in result['calendar_cohorts']], ['ELIGIBLE', 'ELIGIBLE'])

    def test_empty_saturday_preserves_ordinary_work(self):
        self.both_days(); self.fx.fixtures.pop(); self.publish()
        result = self.run_gate()
        self.assertEqual(result['captures_created'], 1, result)
        self.assertEqual([d['decision'] for d in result['calendar_cohorts']], ['ELIGIBLE', 'EMPTY'])
        self.assertNotIn('advance_discovery', self.fx.control())

    def test_shared_eight_request_exhaustion_keeps_partial_publication(self):
        self.both_days(8); self.publish()
        result = self.run_gate()
        self.assertEqual(result['provider_requests'], 8, result)
        self.assertIn('REQUEST_BUDGET', result['reasons'])
        self.assertEqual(self.fx.control()['period']['reserved'], 8)
        self.assertEqual([c[0] for c in self.fx.calls].count('markets'), 1)
        self.assertTrue(list((self.fx.state / 'strategy-picks').glob('*.json')))

    def test_utc_cohort_boundary_across_dst(self):
        from datetime import datetime
        for stamp in ('2026-10-24T23:30:00+00:00', '2026-10-25T23:30:00+00:00'):
            with self.subTest(stamp=stamp):
                self.fx.now = datetime.fromisoformat(stamp) - timedelta(hours=1)
                self.fx.fixtures[0]['startTime'] = stamp
                self.publish()
                _, calendar = bridge.load(self.fx.state, as_of=self.fx.now)
                day = datetime.fromisoformat(stamp).date()
                self.assertEqual(len(bridge.active(calendar, self.fx.now, day)), 1)
                self.assertFalse(bridge.active(calendar, self.fx.now, day + timedelta(days=1)))
