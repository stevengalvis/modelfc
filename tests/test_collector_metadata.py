"""Pinned collector mode with mocked transport; never live provider calls."""
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
import unittest
from unittest.mock import patch

from modelfc import corner_prospective as runner
from modelfc.metadata_cache import import_metadata
from modelfc.acquisition_storage import private_store
from tests import test_btts_prospective as btts_tests


class CollectorMetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = btts_tests.BttsProspectiveTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pilot = self.fixture.pilot
        self.cache = self.pilot.setup.root / 'metadata-cache'
        self.cache.mkdir(mode=0o700)
        self.retrieved = self.pilot.now - timedelta(days=1)
        self.raw = json.dumps(self.pilot.metadata).encode()
        self.pin = hashlib.sha256(self.raw).hexdigest()
        self.entry = self.put(self.retrieved)

    def put(self, retrieved):
        return import_metadata(self.cache, self.raw, source_sha256=self.pin,
                               retrieved_at=retrieved, observed_at=self.pilot.now)

    def run_cached(self, **kwargs):
        return self.fixture.run_once(metadata_cache_dir=self.cache,
                                     metadata_entry_id=self.entry, **kwargs)

    def test_one_odds_request_shared_by_corners_and_btts_with_private_provenance(self):
        report = self.run_cached()
        self.assertEqual(report['status'], 'OK', report)
        self.assertEqual(report['provider_requests'], 2)
        self.assertEqual(report['captures_created'], 1)
        self.assertEqual(report['btts_research']['comparisons_recorded'], 2)
        self.assertEqual([c[0] for c in self.pilot.calls], ['fixtures', 'odds'])
        self.assertEqual(self.pilot.control()['period']['reserved'], 2)
        self.assertAlmostEqual(self.pilot.sleeps[0], 2.1, places=5)
        files = list((self.pilot.state / 'collector-metadata-uses').glob('use-*.json'))
        self.assertEqual(len(files), 1)
        with private_store(files[0].parent) as store:
            record = store.get(files[0].name)
        self.assertEqual(record['metadata_retrieved_at_utc'], self.retrieved.isoformat())
        self.assertEqual(record['entry_id'], self.entry)
        self.assertEqual(record['source_sha256'], self.pin)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        research = self.fixture.records()[0]
        # Same response/dictionary hashes bind the private sidecar to BTTS evidence.
        text = research.model_dump_json()
        self.assertIn(record['provider_snapshot_sha256'], text)
        self.assertIn(record['provider_metadata_sha256'], text)
        observations = list((self.pilot.state / 'market-observations').rglob('*.json'))
        self.assertTrue(any(record['observation_id'] in p.read_text() for p in observations))
        self.assertNotIn('offline-secret', files[0].read_text())
        before = files[0].read_bytes()
        again = self.run_cached()
        self.assertEqual(again['provider_requests'], 0)
        self.assertEqual(files[0].read_bytes(), before)

    def test_missing_stale_tampered_and_unpaired_inputs_reserve_nothing(self):
        original = self.entry
        for entry in ('0'*64, self.put(self.pilot.now-timedelta(days=8)), 'not-a-pin'):
            self.entry = entry
            report = self.run_cached()
            self.assertIn('MARKET_METADATA_INVALID', report['reasons'])
            self.assertEqual(report['provider_requests'], 0)
            self.assertEqual(self.pilot.control()['period']['reserved'], 0)
        self.entry = original
        path = self.cache / f'metadata-{original}.json'
        path.write_bytes(path.read_bytes().replace(b'oddspapi', b'changed!'))
        self.assertIn('MARKET_METADATA_INVALID', self.run_cached()['reasons'])
        report = self.fixture.run_once(metadata_cache_dir=self.cache)
        self.assertIn('MARKET_METADATA_INVALID', report['reasons'])
        self.assertEqual(self.pilot.calls, [])

    def test_expiry_during_cooldown_keeps_precharge_without_dispatch_or_fallback(self):
        self.entry = self.put(self.pilot.now-timedelta(days=7)+timedelta(seconds=1))
        report = self.run_cached()
        self.assertEqual([c[0] for c in self.pilot.calls], ['fixtures'])
        self.assertEqual(report['provider_requests'], 1)
        self.assertIn('MARKET_METADATA_INVALID', report['reasons'])
        self.assertEqual(self.pilot.control()['period']['reserved'], 2)
        self.assertFalse(list((self.pilot.state/'collector-metadata-uses').glob('use-*.json')))

    def test_cap_remains_180_and_cache_cannot_bypass_it(self):
        self.pilot.write_control(lambda c: c['period'].update(reserved=178))
        report = self.run_cached()
        self.assertEqual(report['captures_created'], 1, report)
        self.assertEqual(report['prospective_budget_remaining'], 0)
        self.assertEqual(self.pilot.control()['period']['reserved'], 180)
        self.assertEqual(report['provider_requests'], 2)

    def test_exhausted_budget_makes_no_request(self):
        self.pilot.write_control(lambda c: c['period'].update(reserved=180))
        report = self.run_cached()
        self.assertEqual(report['provider_requests'], 0)
        self.assertIn('REQUEST_BUDGET', report['reasons'])
        self.assertEqual(self.pilot.calls, [])

    def test_fresh_dictionary_does_not_make_september_e0_history_eligible_in_october(self):
        from modelfc.metadata_cache import load_metadata
        from modelfc.acquisition_planner import Fixture
        from modelfc.offline_forecasts import KnownFixture, prepare_forecast
        now = datetime(2026, 10, 12, 17, tzinfo=timezone.utc)
        entry = import_metadata(self.cache, self.raw, source_sha256=self.pin,
                                retrieved_at=now, observed_at=now)
        load_metadata(self.cache, entry, as_of=now)
        rows = ['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC']
        for index in range(120):
            day = date(2026, 9, 20) - timedelta(days=index)
            home, away = ('Home', 'Away') if index % 2 else ('Away', 'Home')
            rows.append(f'E0,{day:%d/%m/%Y},{home},{away},2,1,5,4')
        fixture = KnownFixture(Fixture('E0', 17, 'synthetic-e0', now+timedelta(days=1)), 'Home', 'Away')
        with self.assertRaisesRegex(ValueError, 'E0_HISTORY_STALE'):
            prepare_forecast(fixture, (('E0_2627.csv', ('\n'.join(rows)+'\n').encode()),),
                             frozen_at=now, cutoff=now.date(), models=('corner',))
        self.assertEqual(self.pilot.calls, [])

    def test_defaults_still_fetch_metadata_and_e0_remains_disabled(self):
        report = self.fixture.run_once()
        self.fixture.assert_corner_success(report)
        self.assertFalse((self.pilot.state/'collector-metadata-uses').exists())
        from modelfc.providers.oddspapi import OddsPapiMarketData
        from modelfc.corner_market_data import MarketDataError
        with self.assertRaisesRegex(MarketDataError, 'PROVIDER_CONFIGURATION'):
            OddsPapiMarketData('E0')


    def test_filtered_excluded_ids_preserve_default_availability_and_unknown_failures(self):
        from copy import deepcopy
        from modelfc.providers.oddspapi import normalize_odds, OddsPapiMarketData
        from modelfc.oddspapi_research_metadata import filter_metadata
        from modelfc.collector_metadata import PinnedCollectorMetadata
        unsupported = {'marketId': 999001, 'marketName': 'Unsupported', 'marketType': 'unsupported',
                       'sportId': 10, 'period': 'fulltime', 'playerProp': False, 'outcomes': []}
        metadata = deepcopy(self.pilot.metadata)+[unsupported]
        raw = json.dumps(metadata).encode()
        filtered = json.loads(filter_metadata(raw)[0])
        payload = dict(deepcopy(self.pilot.payload), **self.pilot.fixtures[0])
        for book in payload['bookmakerOdds'].values():
            book['markets'] = {'999001': {}}
        kwargs = dict(retrieved_at=self.pilot.now.isoformat(), now=self.pilot.now)
        baseline = normalize_odds(payload, metadata, self.pilot.fixtures[0], **kwargs)
        cached = normalize_odds(payload, filtered, self.pilot.fixtures[0], **kwargs)
        self.assertEqual(cached.availability, baseline.availability)
        self.assertFalse(any(state['issues'] for state in cached.availability.values()))
        for book in payload['bookmakerOdds'].values(): book['markets']['999002'] = {}
        cached = normalize_odds(payload, filtered, self.pilot.fixtures[0], **kwargs)
        self.assertEqual(cached.availability, normalize_odds(payload, metadata, self.pilot.fixtures[0], **kwargs).availability)
        self.assertTrue(any({'market_id':'999002','reason':'MISSING_MARKET_METADATA'} in state['issues']
                            for state in cached.availability.values()))
        entry = import_metadata(self.cache, raw, source_sha256=hashlib.sha256(raw).hexdigest(),
                                retrieved_at=self.retrieved, observed_at=self.pilot.now)
        pinned = PinnedCollectorMetadata(self.cache, entry, self.pilot.state, lambda: self.pilot.now)
        client = OddsPapiMarketData('E1'); pinned.install(client)
        self.assertEqual(client._market_metadata, filtered)
        self.assertEqual(client.corner_market_request_count(), 1)
        invalid = deepcopy(filtered)
        invalid['excluded_ids']['UNSUPPORTED_FAMILY'].append(invalid['markets'][0]['marketId'])
        invalid['source_entries'] += 1
        with self.assertRaises(ValueError): normalize_odds(payload, invalid, self.pilot.fixtures[0], **kwargs)


if __name__ == '__main__':
    unittest.main()
