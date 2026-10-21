"""Offline saved-evidence integration and presentation bounds."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc import match_evidence as compact
from modelfc import corner_research
from modelfc.btts_market_data import BttsFixture
from modelfc.btts_research import record_btts_research
from tests import test_btts_research as btts
from tests import test_corner_research as research_tests
from tests.team_intelligence_data import fixture_bytes
from modelfc.team_intelligence import REGISTRY


class MatchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'absent'

    def read(self, **kwargs):
        return compact.match_evidence(self.state, btts.fixture(), as_of=btts.NOW, **kwargs)

    def test_missing_has_no_writes_and_no_claims(self):
        result = self.read()
        self.assertFalse(self.state.exists())
        for key in ('saved_btts', 'frozen_corners', 'descriptive_team_corners'):
            self.assertEqual(result[key]['state'], 'UNAVAILABLE')
        self.assertEqual(result['published_pick']['state'], 'NOT_READ')

    def test_btts_reuses_prices_probability_status_and_immutable_source(self):
        record = record_btts_research(self.state, btts.forecast(), btts.observation())
        before = {p: p.read_bytes() for p in self.state.rglob('*.json')}
        result = self.read()
        row = result['saved_btts']['comparisons'][0]
        source = next(c for c in record.comparisons if c.comparison_id == row['comparison_id'])
        self.assertEqual(row['yes'], source.yes.model_dump(mode='json'))
        self.assertEqual(row['current_status'], 'AVAILABLE')
        stale = compact.match_evidence(self.state, btts.fixture(), as_of=btts.NOW + timedelta(minutes=6))
        self.assertEqual(stale['saved_btts']['comparisons'][0]['current_status'], 'STALE')
        future = compact.match_evidence(self.state, btts.fixture(), as_of=btts.NOW - timedelta(minutes=1))
        self.assertEqual(future['saved_btts']['comparisons'][0]['current_status'], 'FUTURE_OBSERVATION')
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*.json')})

    def test_fixture_joins_all_identity_fields(self):
        record_btts_research(self.state, btts.forecast(), btts.observation())
        for changes in ({'home_team': 'B'}, {'away_team': 'C'}, {'kickoff_utc': '2026-12-30T15:00:00Z'}):
            with self.subTest(changes=changes):
                result = compact.match_evidence(self.state, btts.fixture(**changes), as_of=btts.NOW)
                self.assertEqual(result['saved_btts']['state'], 'CONFLICTING_FIXTURE')
                self.assertEqual(result['saved_btts']['comparisons'], [])
        for changes in ({'provider': 'another'}, {'provider_fixture_id': 'another'}):
            result = compact.match_evidence(self.state, btts.fixture(**changes), as_of=btts.NOW)
            self.assertEqual(result['saved_btts']['state'], 'UNAVAILABLE')

    def test_history_exact_mapping_season_denominators_and_reference(self):
        payload = fixture_bytes(n=3, missing=(0, 0))
        fixture = btts.fixture(home_team=REGISTRY[0].source_name, away_team=REGISTRY[12].source_name)
        result = compact.match_evidence(self.state, fixture, as_of=btts.NOW, history_bytes=payload)
        stats = result['descriptive_team_corners']
        self.assertEqual(stats['source_sha256'], hashlib.sha256(payload).hexdigest())
        home = stats['teams'][0]
        self.assertEqual(home['season']['completed'], 3)
        self.assertEqual(home['season']['n'], 2)
        self.assertEqual(home['missing_corner_rows'], 1)
        self.assertTrue(home['small_sample'])
        self.assertEqual(home['venue_sample']['completed'], 2)
        unmapped = self.read(history_bytes=payload)['descriptive_team_corners']['teams'][0]
        self.assertEqual(unmapped['state'], 'UNMAPPED_TEAM')
        old_fixture = fixture.model_copy(update={'kickoff_utc': '2025-12-31T15:00:00Z'})
        wrong_season = compact.match_evidence(self.state, old_fixture, as_of=btts.NOW, history_bytes=payload.replace(b'/2026', b'/2025'))
        self.assertEqual(wrong_season['descriptive_team_corners']['state'], 'UNSUPPORTED_SEASON')
        self.assertEqual(wrong_season['descriptive_team_corners']['teams'], [])

    def test_missing_corner_samples_and_unknown_source_freshness(self):
        fixture = btts.fixture(home_team=REGISTRY[0].source_name, away_team=REGISTRY[12].source_name)
        result = compact.match_evidence(self.state, fixture, as_of=btts.NOW,
                                        history_bytes=fixture_bytes(n=1, missing=(0, 0)))
        stats = result['descriptive_team_corners']
        self.assertIsNone(stats['source_observed_at_utc'])
        self.assertEqual(stats['source_freshness'], 'UNKNOWN')
        self.assertEqual(stats['teams'][0]['state'], 'UNAVAILABLE')
        self.assertIsNone(stats['teams'][0]['season']['won'])

    def test_invalid_season_competition_and_size_fail_closed(self):
        for payload in (fixture_bytes().replace(b'E1,', b'E0,'), fixture_bytes().replace(b'/2026', b'/2025')):
            with self.assertRaises(ValueError):
                self.read(history_bytes=payload)
        with self.assertRaises(ValueError):
            compact.match_evidence(self.state, btts.fixture().model_copy(update={'competition': 'E0'}), as_of=btts.NOW)
        with self.assertRaisesRegex(ValueError, 'MATCH_HISTORY_LIMIT'):
            self.read(history_bytes=b'x' * (compact.MAX_SOURCE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'MATCH_EVIDENCE_OUTPUT_LIMIT'):
            compact.match_evidence(self.state, btts.fixture(home_team='x' * compact.MAX_OUTPUT_BYTES), as_of=btts.NOW)
        with self.assertRaises(ValueError):
            self.read(snapshot_id='0' * 32)

    def test_bounded_rows_with_explicit_omissions(self):
        for minute in range(4):
            record_btts_research(self.state, btts.forecast(), btts.observation(retrieved=btts.NOW + timedelta(minutes=minute)))
        result = self.read()
        self.assertEqual(len(result['saved_btts']['comparisons']), 2)
        self.assertEqual(result['saved_btts']['comparisons_omitted'], 6)
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':')).encode()), compact.MAX_OUTPUT_BYTES)

    def test_storage_error_is_not_missing_evidence(self):
        with patch.object(compact, 'read_btts_research', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.read()


class FrozenCornerTests(unittest.TestCase):
    def test_existing_snapshot_is_immutable_and_exactly_joined(self):
        harness = research_tests.ResearchTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.run_pilot()
        snapshot = harness.snapshot()
        prediction = harness.manifest(snapshot)['entries'][0]['prediction']['id']
        detail = corner_research.inspect_fixture(harness.state, snapshot, prediction)
        saved = detail['fixture']
        fixture = BttsFixture(**{k: saved[k] for k in ('competition', 'provider', 'provider_fixture_id', 'home_team', 'away_team')},
                              kickoff_utc=saved['kickoff_at'])
        args = dict(as_of=harness.now, snapshot_id=snapshot, prediction_id=prediction)
        before_bytes = {p: p.read_bytes() for p in harness.state.rglob('*.json')}
        calls_before = len(harness.calls)
        before = compact.match_evidence(harness.state, fixture, **args)
        self.assertEqual(len(harness.calls), calls_before)
        self.assertEqual(before_bytes, {p: p.read_bytes() for p in harness.state.rglob('*.json')})
        self.assertEqual(before['frozen_corners']['prediction_hash'], detail['prediction_hash'])
        self.assertEqual(before['frozen_corners']['targets'], detail['champion']['targets'][:4])
        mismatch = compact.match_evidence(harness.state, fixture.model_copy(update={'home_team': 'Other'}), **args)
        self.assertEqual(mismatch['frozen_corners']['state'], 'CONFLICTING_FIXTURE')
        harness.now = harness.now.replace(hour=10)
        harness.run_pilot()
        self.assertEqual(compact.match_evidence(harness.state, fixture, **args), before)
