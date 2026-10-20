"""Offline recorded HTTP, real capture/publication, deterministic local clocks."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from modelfc import corner_prospective as runner
from modelfc.collector_freshness import read
from modelfc.friday_collection import friday_slot, review_deadlines, project_month
from tests import test_corner_prospective as pilot


class PolicyTests(unittest.TestCase):
    def test_dst_deadlines_and_utc_transition(self):
        for saturday, morning, afternoon in [(date(2026, 7, 4), 13, 21),
                                             (date(2026, 1, 3), 14, 22),
                                             (date(2026, 3, 7), 14, 22),
                                             (date(2026, 3, 14), 13, 21),
                                             (date(2026, 11, 7), 14, 22)]:
            deadlines = review_deadlines(saturday)
            self.assertEqual([d.hour for d in deadlines], [morning, afternoon])
            for deadline, phase in zip(deadlines, ('morning', 'afternoon')):
                self.assertIsNone(friday_slot(deadline))
                self.assertEqual(friday_slot(deadline - timedelta(minutes=55)), (saturday, phase))
                self.assertEqual(friday_slot(deadline - timedelta(minutes=115)), (saturday, phase))
                self.assertIsNone(friday_slot(deadline - timedelta(hours=2, seconds=1)))
        self.assertIsNone(friday_slot(datetime(2026, 9, 26, 0, 5, tzinfo=timezone.utc)))

    def test_four_and_five_weekends_with_midweek_and_retries(self):
        self.assertEqual(project_month(weekends=4)['projected_requests'], 88)
        self.assertEqual(project_month(weekends=5)['projected_requests'], 110)
        self.assertEqual(project_month(weekends=4, midweek=20, other_discovery=44,
                                       wasted_reservations=4)['headroom'], 24)
        self.assertEqual(project_month(weekends=5, midweek=20, other_discovery=42,
                                       wasted_reservations=4)['headroom'], 4)
        self.assertFalse(project_month(weekends=5, other_discovery=42)['coverage_budget_blocked'])
        self.assertTrue(project_month(weekends=5, saturday=10)['coverage_policy_limited'])
        self.assertFalse(project_month(weekends=5, saturday=2, midweek=20,
                                       other_discovery=42, wasted_reservations=4)['coverage_budget_blocked'])


class FridayTests(unittest.TestCase):
    def setUp(self):
        self.fx = pilot.PilotTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.now = datetime(2026, 9, 25, 11, 5, tzinfo=timezone.utc)
        self.fx.fixtures[0]['startTime'] = '2026-09-26T15:00:00Z'
        self.enterContext(patch('modelfc.published_recommendations._now', side_effect=lambda: self.fx.now))
        self.enterContext(patch('urllib.request.OpenerDirector.open', side_effect=self.http))

    def http(self, request, **kwargs):
        endpoint = urlsplit(request.full_url).path.rsplit('/', 1)[-1]
        params = parse_qs(urlsplit(request.full_url).query)
        control = self.fx.control()
        self.assertGreater(control['period']['reserved'], len(self.fx.calls))
        self.fx.calls.append((endpoint, self.fx.now))
        if endpoint == 'fixtures':
            # Provider requests a date interval; record exact query for assertions.
            self.discovery_params = params
            value = deepcopy([f for f in self.fx.fixtures if f["startTime"][:10] == params["from"][0][:10]])
        elif endpoint == 'markets':
            value = deepcopy(self.fx.metadata)
        else:
            fixture = next(f for f in self.fx.fixtures if f['fixtureId'] == params['fixtureId'][0])
            value = dict(deepcopy(self.fx.payload), **fixture)
        response = BytesIO(json.dumps(value).encode()); response.headers = {}
        return response

    def run_collect(self):
        self.fx.now += timedelta(seconds=1)
        result = runner.run_once(state_dir=self.fx.state, data_config_path=self.fx.config, friday_review=True)
        self.assertNotEqual(result['status'], 'FAIL', result)
        return result

    def many(self, count):
        original = self.fx.fixtures[0]
        self.fx.fixtures = [dict(original, fixtureId=f'friday-{i:02}',
                                startTime=f'2026-09-26T15:{i:02}:00Z') for i in range(count)]

    def test_two_batches_deadline_and_partial_publication(self):
        self.many(8)
        first = self.run_collect()
        self.assertEqual(first['provider_requests'], 8)
        self.assertEqual(first['captures_created'], 5)
        self.assertIn('REQUEST_BUDGET', first['reasons'])
        publications = list((self.fx.state / 'strategy-picks').glob('*.json'))
        self.assertTrue(publications)
        frozen = {p: p.read_bytes() for p in publications}
        self.fx.now = self.fx.now.replace(hour=12, minute=5, second=0, microsecond=0)
        second = self.run_collect()
        self.assertEqual((second['provider_requests'], second['captures_created']), (4, 3))
        self.assertEqual(len(self.fx.capture_paths()), 8)
        self.assertTrue(all(p.read_bytes() == b for p,b in frozen.items()))
        self.assertLess(self.fx.now, review_deadlines(date(2026, 9, 26))[0])
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        before = {p: p.read_bytes() for p in self.fx.state.rglob('*.json')}
        report = read(self.fx.state, now=self.fx.now)
        self.assertEqual(len(report['fixtures']), 8)
        self.assertTrue(all(row['quote_age_seconds'] is not None for row in report['fixtures']))
        self.assertEqual(before, {p:p.read_bytes() for p in self.fx.state.rglob('*.json')})

    def test_missing_market_afternoon_two_and_saturday_fallback(self):
        self.many(3)
        original = deepcopy(self.fx.payload)
        self.fx.no_team_totals()
        self.assertEqual(self.run_collect()['captures_skipped_no_team_totals'], 3)
        self.assertTrue(all(f['collection_state'] == 'MARKET_UNAVAILABLE'
                            for f in read(self.fx.state, now=self.fx.now)['fixtures']))
        self.fx.now = self.fx.now.replace(hour=19, minute=5, second=0, microsecond=0)
        self.run_collect()
        self.assertEqual(sorted(a['count'] for a in self.fx.control()['attempts'].values()), [1,2,2])
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        self.fx.payload = original
        self.fx.now = datetime(2026, 9, 26, 14, 5, tzinfo=timezone.utc)
        result = self.run_collect()
        self.assertEqual((result['provider_requests'], result['captures_created']), (2,1))
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        self.assertTrue(all(a['count'] == 2 for a in self.fx.control()['attempts'].values()))
        self.assertEqual(sum(e == 'fixtures' for e,t in self.fx.calls), 3)

    def test_budget_reserve_and_immutable_history_refresh(self):
        self.run_collect()
        first = {p:p.read_bytes() for name in ('analyses','predictions','strategy-picks')
                 for p in (self.fx.state / name).glob('*.json')}
        self.assertTrue(first)
        self.fx.now = self.fx.now.replace(hour=19, minute=5)
        self.run_collect()
        self.assertTrue(all(p.read_bytes() == b for p,b in first.items()))
        self.assertEqual(len(self.fx.capture_paths()), 1)
        self.fx.now = datetime(2026,9,26,14,5,tzinfo=timezone.utc)
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        self.assertTrue(all(p.read_bytes() == b for p,b in first.items()))

    def test_friday_budget_leaves_four_then_saturday_can_use_them(self):
        self.fx.write_control(lambda c: c['period'].update(reserved=161))
        result = self.run_collect()
        self.assertIn('MIDWEEK_BUDGET_RESERVED', result['reasons'])
        self.assertEqual(result['prospective_budget_remaining'], 17)
        self.assertEqual(read(self.fx.state, now=self.fx.now)['fixtures'][0]['collection_state'], 'ODDS_NOT_COLLECTED')
        self.fx.now = datetime(2026,9,26,14,5,tzinfo=timezone.utc)
        result = self.run_collect()
        self.assertEqual(result['captures_created'], 1)
        self.assertEqual(result['prospective_budget_remaining'], 15)

    def test_crash_after_first_checkpoint_and_repeat_never_third_attempt(self):
        self.many(2)
        from modelfc import pick_strategies
        actual = pick_strategies.publish_locked
        def crash(*a, **kw):
            actual(*a, **kw)
            raise KeyboardInterrupt
        with patch.object(pick_strategies, 'publish_locked', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt): self.run_collect()
        saved = {p:p.read_bytes() for p in (self.fx.state/'strategy-picks').glob('*.json')}
        self.assertTrue(saved)
        self.assertEqual(self.run_collect()['captures_created'], 1)
        self.assertTrue(all(p.read_bytes() == b for p,b in saved.items()))
        self.assertEqual(self.run_collect()['provider_requests'], 0)

    def test_interrupted_reservation_is_not_retried_or_refunded(self):
        with patch('modelfc.providers.oddspapi.OddsPapiMarketData.get_corner_markets', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_collect()
        used = self.fx.control()['period']['reserved']
        result = self.run_collect()
        self.assertIn('ATTEMPT_INCOMPLETE', result['reasons'])
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(self.fx.control()['period']['reserved'], used)

    def test_midweek_and_friday_same_day_cache_coexist(self):
        # Normal pre-window Friday processing does not destroy the advance cache.
        self.run_collect()
        advance = deepcopy(self.fx.control()['advance_discovery'])
        self.fx.now = self.fx.now.replace(hour=14)
        self.fx.fixtures = []
        self.run_collect()
        self.assertEqual(self.fx.control()['advance_discovery'], advance)
        self.assertEqual(self.fx.control()['discovery']['date'], '2026-09-25')
        self.fx.now = datetime(2026,9,28,11,5,tzinfo=timezone.utc)
        self.assertEqual(self.run_collect()['provider_requests'], 1)
        self.assertEqual(self.fx.control()['advance_discovery'], advance)

    def test_interrupted_advance_discovery_has_only_one_afternoon_retry(self):
        from modelfc.providers.oddspapi import OddsPapiMarketData
        actual = OddsPapiMarketData.discover_fixtures
        def interrupt_advance(client, competition, day):
            if day.weekday() == 5:
                raise KeyboardInterrupt
            return actual(client, competition, day)
        with patch.object(OddsPapiMarketData, 'discover_fixtures', interrupt_advance):
            with self.assertRaises(KeyboardInterrupt): self.run_collect()
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        self.fx.now = self.fx.now.replace(hour=19, minute=5)
        result = self.run_collect()
        self.assertEqual(result['captures_created'], 1)
        self.assertEqual(self.fx.control()['advance_discovery']['queries'], 2)
        self.fx.now = datetime(2026,9,26,0,5,tzinfo=timezone.utc)
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        self.assertEqual(self.fx.control()['advance_discovery']['queries'], 2)

    def test_empty_friday_and_new_afternoon_fixture(self):
        fixtures = self.fx.fixtures
        self.fx.fixtures = []
        self.assertEqual(self.run_collect()['fixtures_discovered'], 0)
        self.assertEqual(self.run_collect()['provider_requests'], 0)
        self.fx.fixtures = fixtures
        self.fx.now = self.fx.now.replace(hour=19, minute=5)
        self.assertEqual(self.run_collect()['captures_created'], 1)
        self.assertEqual(self.fx.control()['advance_discovery']['queries'], 2)
        self.fx.now = self.fx.now.replace(hour=20, minute=5)
        self.assertEqual(self.run_collect()['provider_requests'], 0)

    def test_saturday_hard_budget_exhaustion_publishes_successful_subset(self):
        self.many(3)
        self.fx.no_team_totals()
        self.run_collect()
        from tests.test_oddspapi import recorded
        self.fx.payload = recorded('wolves-west-brom-odds')
        self.fx.now = datetime(2026,9,26,14,5,tzinfo=timezone.utc)
        self.fx.write_control(lambda c: c['period'].update(reserved=166))
        result = self.run_collect()
        self.assertEqual((result['captures_created'], result['prospective_budget_remaining']), (1,12))
        self.assertIn('MIDWEEK_BUDGET_RESERVED', result['reasons'])
        self.assertTrue(list((self.fx.state/'strategy-picks').glob('*.json')))
        report = read(self.fx.state, now=self.fx.now)
        self.assertTrue(all(f['budget_blocked'] for f in report['fixtures']))
        self.assertEqual(self.run_collect()['provider_requests'], 0)

    def test_no_value_is_distinct_from_missing_and_stale_saved_value(self):
        for book in self.fx.payload['bookmakerOdds'].values():
            for market in book['markets'].values():
                for outcome in market['outcomes'].values():
                    for quote in outcome['players'].values():
                        quote.update(price=1.1, priceAmerican='-1000')
        self.assertEqual(self.run_collect()['captures_created'], 1)
        report = read(self.fx.state, now=self.fx.now)
        self.assertEqual(report['fixtures'][0]['collection_state'], 'NO_QUALIFYING_VALUE')
        self.assertEqual(len(list((self.fx.state/'strategy-picks').glob('*.json'))), 0)
        self.fx.now += timedelta(minutes=10)
        self.assertGreater(read(self.fx.state, now=self.fx.now)['fixtures'][0]['quote_age_seconds'], 300)

    def test_refresh_changed_kickoff_is_review_not_duplicate_capture(self):
        self.run_collect()
        original = {p:p.read_bytes() for p in self.fx.capture_paths()}
        self.fx.fixtures[0]['startTime'] = '2026-09-26T16:00:00Z'
        self.fx.now = self.fx.now.replace(hour=19, minute=5)
        self.assertIn('DISCOVERY_INVALID', self.run_collect()['reasons'])
        self.assertEqual({p:p.read_bytes() for p in self.fx.capture_paths()}, original)
        retained = deepcopy(self.fx.control()['advance_discovery'])
        self.assertEqual(retained['status'], 'FAILED')
        self.fx.now = datetime(2026, 9, 26, 0, 5, tzinfo=timezone.utc)
        result = self.run_collect()
        self.assertIn('DISCOVERY_INCOMPLETE', result['reasons'])
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(self.fx.control()['advance_discovery'], retained)
        from modelfc.prospective_run_receipts import latest
        receipt = latest(self.fx.state, now=self.fx.now)
        diagnostics = receipt['fixture_diagnostics']
        self.assertEqual(diagnostics['discovery'], 'OBSERVED')
        discovered = [e for e in diagnostics['events'] if e['reason'] == 'DISCOVERED']
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]['fixture_id'], self.fx.fixtures[0]['fixtureId'])
        self.assertIn('DISCOVERY_INCOMPLETE', receipt['summary']['reasons'])
        self.assertEqual({p:p.read_bytes() for p in self.fx.capture_paths()}, original)
