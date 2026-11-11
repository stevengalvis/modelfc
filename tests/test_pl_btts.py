"""Labeled synthetic EPL research; no genuine-input acceptance evidence."""
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.pl_btts import prepare, consume
from tests import test_pl_history_readiness as history_fixture
from tests.test_oddspapi_saved_response import saved_fixture
from tests.oddspapi_inventory_fixtures import metadata
from tests.test_pl_calendar import NOW


class PLBttsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = history_fixture.PLHistoryTests()
        self.fixture.setUp()

    def prepare(self):
        raw, options = self.fixture.options()
        return prepare(raw, sources=self.fixture.sources, enabled=True, **options)

    def quotes(self, forecast):
        e = json.loads(forecast.evidence)
        row = saved_fixture(17, 'odds-epl')
        row.update(participant1Name=e['home'], participant2Name=e['away'], startTime=e['kickoff'])
        for m in row['bookmakerOdds']['fanduel']['markets'].values():
            for outcome in m['outcomes'].values():
                for player in outcome['players'].values():
                    player['changedAt'] = NOW.isoformat()
                    player['priceAmerican'] = '-111' if player['price'] < 2 else '+100'
        binding = encoded(dict(forecast_id=forecast.identity, fixture_id='odds-epl', bookmaker='fanduel'))
        return row, dict(retrieved_at=NOW, as_of=NOW, binding=binding, binding_sha256=fingerprint(binding))

    def test_synthetic_positive_goals_only_determinism_no_network(self):
        with patch('socket.socket', side_effect=AssertionError('network')):
            first = self.prepare()
            self.assertEqual(first['state'], 'RESEARCH_READY')
            self.assertEqual(len(first['forecasts']), 8)
            original = [f.identity for f in first['forecasts']]
            self.fixture.sources.reverse()
            self.assertEqual(original, [f.identity for f in self.prepare()['forecasts']])
            self.fixture.sources = [(n, d.replace(b',5,3', b',,'), fingerprint(d.replace(b',5,3', b',,')), t)
                                    for n, d, _, t in self.fixture.sources]
            report = self.prepare()
            self.assertEqual(report['state'], 'RESEARCH_READY')
            for f in report['forecasts']:
                self.assertEqual(sum(f.probabilities), 1)
                self.assertFalse(f.wire()['live_activation'])
                self.assertFalse(f.wire()['genuine_data_validated'])

    def test_missing_stale_bad_hash_score_and_zero(self):
        sources = self.fixture.sources[:]
        for mutate in (
            lambda: self.fixture.sources.pop(),
            lambda: self.fixture.sources.__setitem__(0, (*sources[0][:3], None)),
            lambda: self.fixture.sources.__setitem__(0, (sources[0][0], sources[0][1], '0'*64, NOW)),
            lambda: self.fixture.replace_current(lambda b: b.replace(b',1,0,H', b',0,0,D')),
            lambda: self.fixture.replace_current(lambda b: b.replace(b',1,0,H', b',,0,H')),
        ):
            self.fixture.sources = sources[:]
            mutate()
            report = self.prepare()
            self.assertEqual(report['state'], 'BLOCKED')
            self.assertFalse(report['forecasts'])
        self.fixture.sources = sources[:]
        self.fixture.replace_current(lambda b: b.replace(b',1,0,H', b',0,0,D'))
        for m in self.fixture.payload['matches'][10:20]:
            m['score']['fullTime'] = {'home': 0, 'away': 0}
        self.assertEqual(self.prepare()['state'], 'RESEARCH_READY')
        raw, opts = self.fixture.options()
        opts['as_of'] = NOW + timedelta(hours=7)
        self.assertEqual(prepare(raw, sources=self.fixture.sources, enabled=True, **opts)['state'], 'BLOCKED')

    def test_exact_pair_capture_and_rejections(self):
        forecast = self.prepare()['forecasts'][0]
        row, opts = self.quotes(forecast)
        capture = consume(forecast, encoded([row]), encoded(metadata()), **opts)
        self.assertEqual(capture, consume(forecast, encoded([row]), encoded(metadata()), **opts))
        result = json.loads(capture)
        self.assertFalse(result['prospective_capture'])
        self.assertIsNone(result['recommendation'])
        for modify in (
            lambda r: r.update(participant1Name='wrong'),
            lambda r: r.update(tournamentId=18),
            lambda r: r.update(startTime=(NOW + timedelta(days=1)).isoformat()),
            lambda r: r['bookmakerOdds']['fanduel']['markets']['5']['outcomes'].pop('51'),
            lambda r: r['bookmakerOdds']['fanduel']['markets']['5'].update(staleOdds=True),
        ):
            bad = json.loads(json.dumps(row)); modify(bad)
            with self.assertRaises(ValueError):
                consume(forecast, encoded([bad]), encoded(metadata()), **opts)
        for changes in ({'as_of': NOW+timedelta(minutes=16)}, {'binding_sha256': '0'*64},
                        {'retrieved_at': NOW-timedelta(seconds=1)}):
            with self.assertRaises(ValueError):
                consume(forecast, encoded([row]), encoded(metadata()), **(opts | changes))

    def test_cutoff_excludes_same_day_and_atomic_fixture_gate(self):
        baseline = {json.loads(f.evidence)['fixture_id']: f.aggregates for f in self.prepare()['forecasts']}
        self.fixture.replace_current(lambda b: b + b'E0,17/10/2026,Team0,Team1,9,9,D,,\n')
        report = self.prepare()
        self.assertEqual(report['state'], 'RESEARCH_READY')
        self.assertEqual({json.loads(f.evidence)['fixture_id']: f.aggregates for f in report['forecasts']}, baseline)
        self.fixture.payload['matches'][0]['utcDate'] = NOW.isoformat()
        self.assertEqual(self.prepare()['state'], 'BLOCKED')

    def test_quote_period_and_duplicate_pairs(self):
        forecast = self.prepare()['forecasts'][0]
        row, opts = self.quotes(forecast)
        definitions = metadata()
        definitions[4]['period'] = 'p1'
        with self.assertRaises(ValueError):
            consume(forecast, encoded([row]), encoded(definitions), **opts)
        with self.assertRaises(ValueError):
            consume(forecast, encoded([row, row]), encoded(metadata()), **opts)

    def test_constructor_rejects_missing_provenance_and_identity(self):
        f = self.prepare()['forecasts'][0]
        for mutate in (
            lambda e: e.update(sources=[]),
            lambda e: e.update(fixture_id=''),
            lambda e: e.update(home=' '),
            lambda e: e.update(implementation_sha256='bad'),
            lambda e: e['sources'][0].update(sha256='bad'),
            lambda e: e['sources'][0].update(retrieved_at_utc=(NOW+timedelta(seconds=1)).isoformat()),
            lambda e: e['sources'].__setitem__(0, e['sources'][1]),
        ):
            evidence = json.loads(f.evidence); mutate(evidence)
            with self.assertRaises(ValueError):
                replace(f, evidence=encoded(evidence))

    def test_unknown_quote_outcome_and_disagreeing_prices(self):
        f = self.prepare()['forecasts'][0]
        row, opts = self.quotes(f)
        outcomes = row['bookmakerOdds']['fanduel']['markets']['5']['outcomes']
        outcomes['99999'] = outcomes['50']
        with self.assertRaises(ValueError):
            consume(f, encoded([row]), encoded(metadata()), **opts)
        del outcomes['99999']
        outcomes['50']['players']['0']['priceAmerican'] = '+500'
        with self.assertRaises(ValueError):
            consume(f, encoded([row]), encoded(metadata()), **opts)

    def test_stale_bookmaker_timestamp_rejected(self):
        f = self.prepare()['forecasts'][0]
        row, opts = self.quotes(f)
        outcomes = row['bookmakerOdds']['fanduel']['markets']['5']['outcomes']
        for outcome in outcomes.values():
            outcome['players']['0']['bookmakerChangedAt'] = (NOW-timedelta(minutes=16)).isoformat()
        with self.assertRaisesRegex(ValueError, 'QUOTE_STALE_OR_UNDATED'):
            consume(f, encoded([row]), encoded(metadata()), **opts)

    def test_optin_and_frozen_inputs(self):
        self.assertEqual(prepare(None)['state'], 'DISABLED')
        f = self.prepare()['forecasts'][0]
        with self.assertRaises(ValueError):
            replace(f, aggregates=replace(f.aggregates, history_matches=0))
        e = json.loads(f.evidence); e['competition'] = 'E1'
        with self.assertRaises(ValueError):
            replace(f, evidence=encoded(e))
