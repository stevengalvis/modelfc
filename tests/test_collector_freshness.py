"""Read-only eligibility parity against the real offline collector."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
import unittest
from unittest.mock import patch

from modelfc.collector_freshness import read
from tests import test_friday_collection as friday
from tests import test_corner_prospective as pilot
from modelfc import corner_prospective as runner
from modelfc.ledger_storage import LedgerStorageUnavailable


class EmptyLedgerStatusTests(unittest.TestCase):
    def setUp(self):
        self.fx = pilot.PilotTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def snapshot(self):
        return {str(p.relative_to(self.fx.state)):
                (p.lstat().st_mode, p.lstat().st_ino,
                 os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                for p in self.fx.state.rglob('*')}

    def status(self):
        before = self.snapshot()
        try:
            with patch('modelfc.providers.oddspapi.OddsPapiMarketData.__init__',
                       side_effect=AssertionError('status must not create a provider client')):
                return read(self.fx.state, now=self.fx.now)
        finally:
            self.assertEqual(before, self.snapshot())

    def test_initialized_empty_state_and_cli_without_evidence_lock(self):
        self.assertFalse((self.fx.state / '.lock').exists())
        report = self.status()
        self.assertEqual(report['fixtures'], [])
        self.assertEqual(report['budget_remaining'], 180)
        self.assertIsNone(report['last_completed_run_at_utc'])
        before = self.snapshot()
        output = StringIO()
        with redirect_stdout(output), patch('modelfc.providers.oddspapi.OddsPapiMarketData.__init__',
                                            side_effect=AssertionError('no provider')):
            runner.main(['collector-status', '--state-dir', str(self.fx.state)])
        self.assertEqual(json.loads(output.getvalue())['fixtures'], [])
        self.assertEqual(before, self.snapshot())

    def test_empty_discovery_retains_receipt_and_budget_without_evidence_lock(self):
        self.fx.discovery_override = []
        # Discovery is durable before publication first creates the evidence lock.
        with patch('modelfc.pick_strategies.publish_locked',
                   side_effect=OSError('interrupted before evidence publication')):
            summary = runner.run_once(state_dir=self.fx.state, data_config_path=self.fx.config)
        self.assertEqual(summary['provider_requests'], 1)  # recorded HTTP only
        self.assertFalse((self.fx.state / '.lock').exists())
        report = self.status()
        self.assertEqual(report['fixtures'], [])
        self.assertEqual(report['budget_remaining'], 179)
        self.assertEqual(report['last_run_status'], summary['status'])
        self.assertIsNotNone(report['last_completed_run_at_utc'])

    def test_existing_regular_evidence_lock_is_still_used_without_mutation(self):
        (self.fx.state / '.lock').touch()
        self.assertEqual(self.status()['fixtures'], [])

    def test_unsafe_existing_lock_is_never_treated_as_absent(self):
        lock = self.fx.state / '.lock'
        for kind in ('directory', 'fifo', 'dangling_symlink', 'regular_symlink'):
            with self.subTest(kind=kind):
                if kind == 'directory':
                    lock.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(lock)
                else:
                    lock.symlink_to('absent' if kind == 'dangling_symlink' else 'prospective/runner.lock')
                try:
                    with self.assertRaises(LedgerStorageUnavailable):
                        self.status()
                finally:
                    lock.rmdir() if kind == 'directory' else lock.unlink()

    def test_orphaned_evidence_without_lock_is_rejected(self):
        directory = self.fx.state / 'market-observations'
        directory.mkdir()
        (directory / 'orphan.json').write_text('{}')
        with self.assertRaises(LedgerStorageUnavailable):
            self.status()


class FreshnessParityTests(unittest.TestCase):
    def setUp(self):
        self.case = friday.FridayTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fx = self.case.fx

    def report(self):
        before = {p:p.read_bytes() for p in self.fx.state.rglob('*') if p.is_file()}
        with patch('modelfc.providers.oddspapi.OddsPapiMarketData.__init__', side_effect=AssertionError('read must not create a client')):
            result = read(self.fx.state, now=self.fx.now)
        self.assertEqual(before, {p:p.read_bytes() for p in self.fx.state.rglob('*') if p.is_file()})
        return result['fixtures'][0]

    def test_unwatchlisted_capture_has_no_refresh_despite_one_unused_attempt(self):
        for book in self.fx.payload['bookmakerOdds'].values():
            for market in book['markets'].values():
                for outcome in market['outcomes'].values():
                    for quote in outcome['players'].values():
                        quote.update(price=1.1, priceAmerican='-1000')
        self.assertEqual(self.case.run_collect()['captures_created'],1)
        self.fx.now = datetime(2026,9,25,19,5,tzinfo=timezone.utc)
        row = self.report()
        self.assertEqual(row['collection_state'],'NO_QUALIFYING_VALUE')
        self.assertEqual(row['skip_reason'],'NOT_WATCHLISTED')
        self.assertIsNone(row['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now = datetime(2026,9,26,13,30,tzinfo=timezone.utc)
        self.assertIsNone(self.report()['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)

    def test_stale_watchlisted_capture_has_same_ninety_minute_boundary_as_runner(self):
        self.case.run_collect()
        self.fx.now = datetime(2026,9,26,13,0,tzinfo=timezone.utc)
        row = self.report()
        self.assertGreater(row['quote_age_seconds'],300)
        self.assertEqual(row['next_opportunity_at_utc'],'2026-09-26T13:30:00+00:00')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now = datetime.fromisoformat(row['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['market_observations_created'],1)
        self.assertIsNone(self.report()['next_opportunity_at_utc'])
        self.assertEqual(self.report()['skip_reason'],'ATTEMPTS_EXHAUSTED')

    def test_missing_market_waits_a_full_hour_inside_ninety_minute_window(self):
        original=deepcopy(self.fx.payload)
        self.fx.fixtures[0]['startTime']='2026-09-26T16:00:00Z'
        self.fx.no_team_totals()
        self.fx.now=datetime(2026,9,26,14,0,tzinfo=timezone.utc)
        self.case.run_collect()
        first=datetime.fromisoformat(self.report()['last_collection_at_utc'])
        self.fx.now=datetime(2026,9,26,14,30,tzinfo=timezone.utc)
        self.assertEqual(self.report()['next_opportunity_at_utc'],(first+timedelta(hours=1)).isoformat())
        self.fx.payload=original
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now=first+timedelta(hours=1)-timedelta(seconds=1)
        self.assertEqual(self.case.run_collect()['captures_created'],1)
        self.assertIsNone(self.report()['next_opportunity_at_utc'])

    def test_missing_market_delay_that_crosses_close_has_no_opportunity(self):
        self.fx.no_team_totals()
        self.fx.now=datetime(2026,9,26,14,0,tzinfo=timezone.utc)
        self.case.run_collect()
        self.fx.now=datetime(2026,9,26,14,30,tzinfo=timezone.utc)
        self.assertIsNone(self.report()['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)

    def test_missing_market_delay_cannot_escape_friday_afternoon_deadline(self):
        from modelfc import corner_prospective as runner
        from datetime import date
        self.fx.no_team_totals()
        self.fx.now=datetime(2026,9,25,20,30,tzinfo=timezone.utc)
        runner.run_once(state_dir=self.fx.state,data_config_path=self.fx.config,
                        early_scope=(date(2026,9,26),[self.fx.fixtures[0]['fixtureId']]))
        self.fx.now=datetime(2026,9,25,20,40,tzinfo=timezone.utc)
        self.assertEqual(self.report()['next_opportunity_at_utc'],'2026-09-26T13:30:00+00:00')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)

    def test_known_budget_and_cohort_caps_remove_next_opportunity(self):
        self.case.run_collect()
        self.fx.now=datetime(2026,9,26,13,30,tzinfo=timezone.utc)
        baseline=deepcopy(self.fx.control())
        for reserved, spent, reason in [(167,None,'MIDWEEK_BUDGET_RESERVED'),
                                        (180,None,'REQUEST_BUDGET'),(None,21,'WEEKEND_BUDGET')]:
            control=deepcopy(baseline)
            if reserved is not None:control['period']['reserved']=reserved
            if spent is not None:control['weekend_allocations']['2026-09-26']=spent
            from modelfc import corner_prospective as runner
            runner._save(self.fx.path,control)
            row=self.report()
            self.assertIsNone(row['next_opportunity_at_utc'])
            self.assertTrue(row['budget_blocked'])
            self.assertEqual(row['skip_reason'],reason)
            self.assertEqual(self.case.run_collect()['market_observations_created'],0)

    def test_friday_reserve_can_defer_initial_capture_until_saturday(self):
        self.fx.write_control(lambda c:c['period'].update(reserved=161))
        self.case.run_collect()
        row=self.report()
        self.assertEqual(row['collection_state'],'ODDS_NOT_COLLECTED')
        self.assertEqual(row['next_opportunity_at_utc'],'2026-09-26T09:00:00+00:00')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now=datetime.fromisoformat(row['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['captures_created'],1)

    def test_partial_friday_coverage_does_not_promise_saturday_catch_up(self):
        self.case.many(3)
        self.fx.write_control(lambda c:c['period'].update(reserved=161))
        self.case.run_collect()  # Discovery succeeds, but Friday quote budget does not.
        self.fx.write_control(lambda c:c['period'].update(reserved=100))
        self.fx.now=datetime(2026,9,26,10,0,tzinfo=timezone.utc)
        self.assertEqual(self.case.run_collect()['captures_created'],2)
        row=next(f for f in read(self.fx.state,now=self.fx.now)['fixtures'] if f['provider_fixture_id']=='friday-02')
        self.assertEqual(row['collection_state'],'ODDS_NOT_COLLECTED')
        self.assertEqual(row['skip_reason'],'SATURDAY_LIMIT')
        self.assertIsNone(row['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['captures_created'],0)

    def test_reserved_without_capture_remains_incomplete_not_retryable(self):
        with patch('modelfc.providers.oddspapi.OddsPapiMarketData.get_corner_markets',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.case.run_collect()
        # Create the otherwise absent ledger lock via a normal recovery invocation.
        self.case.run_collect()
        row=self.report()
        self.assertEqual(row['skip_reason'],'ATTEMPT_INCOMPLETE')
        self.assertIsNone(row['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)

    def test_afternoon_initial_capture_does_not_get_another_afternoon_attempt(self):
        self.fx.now=datetime(2026,9,25,19,5,tzinfo=timezone.utc)
        self.assertEqual(self.case.run_collect()['captures_created'],1)
        self.fx.now=datetime(2026,9,25,20,5,tzinfo=timezone.utc)
        self.assertEqual(self.report()['next_opportunity_at_utc'],'2026-09-26T13:30:00+00:00')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now=datetime(2026,9,26,13,30,tzinfo=timezone.utc)
        self.assertEqual(self.case.run_collect()['market_observations_created'],1)

    def test_missing_market_without_matching_completed_attempt_is_not_retryable(self):
        self.fx.fixtures[0]['startTime']='2026-09-26T16:00:00Z'
        self.fx.no_team_totals()
        self.fx.now=datetime(2026,9,26,14,0,tzinfo=timezone.utc)
        self.case.run_collect()
        self.fx.now=datetime(2026,9,26,15,5,tzinfo=timezone.utc)
        self.fx.write_control(lambda c:next(iter(c['attempts'].values())).update(at=self.fx.now.isoformat()))
        self.assertIsNone(self.report()['next_opportunity_at_utc'])
        self.assertEqual(self.report()['skip_reason'],'NO_RETRY_ELIGIBLE')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)

    def test_afternoon_global_cap_defers_unspent_fixture_until_saturday(self):
        self.case.many(3)
        self.case.run_collect()
        self.fx.now=datetime(2026,9,25,19,5,tzinfo=timezone.utc)
        self.assertEqual(self.case.run_collect()['market_observations_created'],2)
        self.fx.now=datetime(2026,9,25,20,5,tzinfo=timezone.utc)
        row=next(f for f in read(self.fx.state,now=self.fx.now)['fixtures'] if f['provider_fixture_id']=='friday-02')
        self.assertEqual(row['next_opportunity_at_utc'],'2026-09-26T13:32:00+00:00')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now=datetime.fromisoformat(row['next_opportunity_at_utc'])
        self.assertEqual(self.case.run_collect()['market_observations_created'],1)

    def test_required_discovery_cost_is_included_until_utc_cutoff(self):
        self.fx.fixtures[0]['startTime']='2026-09-25T19:00:00Z'
        self.fx.now=datetime(2026,9,25,7,5,tzinfo=timezone.utc)
        self.case.run_collect()  # Cached discovery, outside initial capture window.
        self.fx.now=self.fx.now.replace(hour=13,minute=1)
        self.assertEqual(self.case.run_collect()['captures_created'],1)
        self.fx.now=self.fx.now.replace(hour=17,minute=30)
        self.fx.write_control(lambda c:c['period'].update(reserved=178))
        baseline=deepcopy(self.fx.control())
        self.assertEqual(self.report()['next_opportunity_at_utc'],'2026-09-25T18:00:00+00:00')
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        # Restore the offline fixture snapshot to test the alternative of waiting
        # until 18:00, when the second discovery is no longer due.
        from modelfc import corner_prospective as runner
        runner._save(self.fx.path,baseline)
        self.fx.now=self.fx.now.replace(hour=18,minute=0)
        self.assertEqual(self.case.run_collect()['market_observations_created'],1)

    def test_expired_budget_does_not_advertise_an_unverified_reset(self):
        self.case.run_collect()
        self.fx.now=datetime(2026,9,26,13,30,tzinfo=timezone.utc)
        self.fx.write_control(lambda c:c['period'].update(end='2026-09-26'))
        row=self.report()
        self.assertIsNone(row['next_opportunity_at_utc'])
        self.assertEqual(row['skip_reason'],'PERIOD_EXPIRED')
        self.assertIn('PERIOD_EXPIRED',self.case.run_collect()['reasons'])

    def test_equivalent_timezone_clock_has_same_eligibility(self):
        from modelfc.friday_collection import NEW_YORK
        self.case.run_collect()
        self.fx.now=datetime(2026,9,26,0,5,tzinfo=timezone.utc)
        self.assertEqual(read(self.fx.state,now=self.fx.now),
                         read(self.fx.state,now=self.fx.now.astimezone(NEW_YORK)))
