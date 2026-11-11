"""Bounded SYNTHETIC EPL replay; no authentic, prospective or live acceptance.

One calendar/five-season history cohort feeds existing market consumers. The
totals conversion retains the original frozen EPL BTTS record without invented
historical timestamps. No production module imports this harness.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc import goal_totals, pl_btts, pl_corner_capture, pl_corner_odds
from modelfc.pl_goal_totals import from_pl_btts
from modelfc.acquisition_storage import encoded, fingerprint, private_store
from modelfc.five_league_readiness import Evidence, SavedQuotes, report
from modelfc.shared_odds import snapshot_from_bytes, corner_selections
from tests import test_pl_history_readiness as history_fixture
from tests.test_pl_calendar import NOW
from tests.test_market_scoped_history import shots
from tests.test_oddspapi_saved_response import saved_fixture
from tests.oddspapi_inventory_fixtures import metadata

QUOTE_TIME = NOW + timedelta(seconds=1)


def saved_quotes(forecasts):
    rows = []
    for forecast in forecasts:
        e = json.loads(forecast.evidence)
        row = saved_fixture(17, 'synthetic-odds-'+e['fixture_id'])
        row.update(participant1Name=e['home'], participant2Name=e['away'], startTime=e['kickoff'])
        for market in row['bookmakerOdds']['fanduel']['markets'].values():
            for outcome in market['outcomes'].values():
                for player in outcome['players'].values():
                    player.update(changedAt=QUOTE_TIME.isoformat(),
                        priceAmerican='-111' if player['price'] < 2 else '+100')
        rows.append(row)
    return rows


def saved(rows, definitions=None):
    payload, meta = encoded(rows), encoded(metadata() if definitions is None else definitions)
    return SavedQuotes(payload, fingerprint(payload), meta, fingerprint(meta), QUOTE_TIME)


def btts_join(forecast, quotes, as_of=QUOTE_TIME):
    e = json.loads(forecast.evidence)
    binding = encoded(dict(forecast_id=forecast.identity,
        fixture_id='synthetic-odds-'+e['fixture_id'], bookmaker='fanduel'))
    return json.loads(pl_btts.consume(forecast, quotes.payload, quotes.metadata,
        retrieved_at=quotes.retrieved_at, as_of=as_of,
        binding=binding, binding_sha256=fingerprint(binding)))


def totals_join(forecast, quotes, as_of=QUOTE_TIME, line=2.5):
    f = forecast.fixture
    binding = encoded(dict(forecast_id=forecast.record_id, competition='E0',
        quote_fixture_id='synthetic-odds-'+f.fixture_id, home=f.home, away=f.away,
        kickoff=f.kickoff.isoformat(), bookmaker='fanduel'))
    return goal_totals.consume(forecast, quotes, binding_raw=binding,
        binding_sha256=fingerprint(binding), line=line, as_of=as_of)


def corner_join(forecast, quotes, as_of=QUOTE_TIME, team_side='HOME', line=4.5):
    i = forecast['identity']
    raw = encoded(forecast)
    binding = encoded(dict(forecast_id=forecast['forecast_sha256'], competition='E0',
        source_fixture_id=str(i['fixture_id']), quote_fixture_id='synthetic-odds-'+str(i['fixture_id']),
        home=i['home']['history_name'], away=i['away']['history_name'], kickoff=i['kickoff'], bookmaker='fanduel'))
    return json.loads(pl_corner_odds.consume(raw, quotes, forecast_sha256=fingerprint(raw),
        binding_raw=binding, binding_sha256=fingerprint(binding), team_side=team_side,
        line=line, as_of=as_of, enabled=True))


class EPLOfflineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data = history_fixture.PLHistoryTests()
        self.data.setUp()
        # Exercise PR199 scopes with deliberately invalid, unassessed shots.
        self.data.sources = [(n, shots(b), fingerprint(shots(b)), at)
                             for n, b, _, at in self.data.sources]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Resolve the real local revision before forbidding subprocess execution.
        revision = pl_corner_capture.git_commit_sha()
        revision_guard = patch.object(pl_corner_capture, "git_commit_sha", return_value=revision)
        revision_guard.start()
        self.addCleanup(revision_guard.stop)
        # Applied to every scenario, including rejection paths.
        for target in ('socket.socket', 'socket.getaddrinfo',
                       'modelfc.ledger_storage.write_new_record',
                       'subprocess.Popen'):
            guard = patch(target, side_effect=AssertionError('offline boundary: '+target))
            guard.start()
            self.addCleanup(guard.stop)

    def prepare(self):
        raw, opts = self.data.options()
        return (pl_corner_capture.prepare(raw, sources=self.data.sources, enabled=True, **opts),
                pl_btts.prepare(raw, sources=self.data.sources, enabled=True, **opts))

    def readiness(self, quotes=None):
        raw, opts = self.data.options()
        evidence = Evidence('E0', 17, 2026, NOW.date(), calendar=raw,
            calendar_sha256=fingerprint(raw), mapping=opts['contract_raw'],
            mapping_sha256=opts['reviewed_contract_sha256'], sources=tuple(self.data.sources),
            provenance='SYNTHETIC')
        return report({'E0': evidence}, as_of=QUOTE_TIME, quotes=quotes)

    def test_positive_whole_day_to_quotes_and_immutable_private_output(self):
        before = deepcopy((self.data.options(), self.data.sources))
        corners, btts = self.prepare()
        self.assertEqual((corners['state'], btts['state']), ('CAPTURED', 'RESEARCH_READY'))
        self.assertEqual(len(corners['forecasts']), 8)
        self.assertEqual(len(btts['forecasts']), 8)
        capture_root = self.root/'corner-capture'
        capture_root.mkdir(mode=0o700)
        raw, options = self.data.options()
        capture_args = dict(sources=self.data.sources, enabled=True, **options)
        captured = pl_corner_capture.capture(capture_root, raw, **capture_args)
        self.assertEqual(captured['write_state'], 'CREATED')
        self.assertFalse(captured['prospective_capture'])
        self.assertEqual(captured['payload']['forecasts'], corners['forecasts'])
        replay = pl_corner_capture.capture(capture_root, raw, **capture_args)
        self.assertEqual(replay, {**captured, 'write_state': 'EXISTING'})
        capture_bytes = (capture_root/pl_corner_capture.RECORD_NAME).read_bytes()
        with self.assertRaisesRegex(ValueError, 'CAPTURE_STORE_CONFLICT'):
            pl_corner_capture.capture(capture_root, raw, **{**capture_args, 'as_of': QUOTE_TIME})
        self.assertEqual((capture_root/pl_corner_capture.RECORD_NAME).read_bytes(), capture_bytes)
        quotes = saved(saved_quotes(btts['forecasts']))
        snapshot = snapshot_from_bytes(quotes.payload, quotes.metadata, retrieved_at=QUOTE_TIME)
        joined = []
        for forecast in btts['forecasts']:
            e = json.loads(forecast.evidence)
            corner = next(c for c in corners['forecasts'] if str(c['identity']['fixture_id']) == e['fixture_id'])
            self.assertEqual(corner['identity']['kickoff'], e['kickoff'])
            self.assertEqual(corner['calendar']['bundle_sha256'], e['calendar_sha256'])
            self.assertEqual([s['sha256'] for s in corner['sources']], [s['sha256'] for s in e['sources']])
            self.assertTrue(all(s['validation_scope'] == 'GOALS_CORNERS' for s in corner['sources']))
            self.assertTrue(all(s['validation_scope'] == 'GOALS' for s in e['sources']))
            totals = from_pl_btts(forecast, enabled=True)
            self.assertEqual(totals.aggregates, forecast.aggregates)
            with patch.object(pl_btts, 'prepare', side_effect=AssertionError('refit')), patch.object(
                    goal_totals, 'freeze', side_effect=AssertionError('refit')):
                b, t = btts_join(forecast, quotes), totals_join(totals, quotes)
            self.assertEqual(b['snapshot_id'], t['snapshot_id'])
            self.assertEqual(b['snapshot_id'], snapshot.observation_id)
            self.assertIsNone(b['recommendation']); self.assertIsNone(t['recommendation'])
            self.assertFalse(b['live_activation']); self.assertFalse(t['live'])
            joined.append({'btts': b, 'totals': t, 'totals_frozen': goal_totals._wire(asdict(totals)),
                           'corner_forecast_sha256': corner['forecast_sha256'],
                           'corner_quotes': corner_join(corner, quotes)})
        # Existing E1-only shared consumer remains unchanged; EPL uses explicit opt-in.
        for fixture in snapshot.fixtures:
            self.assertTrue(any(q.family == 'MATCH_CORNER_TOTALS' for q in fixture.quotes))
            with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_OR_UNBOUND'):
                corner_selections(snapshot, fixture)
        record = json.loads(encoded(dict(provenance='SYNTHETIC', mode='OFFLINE_REPLAY',
            prospective_capture=False, live=False, genuine_data_validated=False,
            readiness=self.readiness(quotes), corners=corners, corner_capture=captured, joined=joined)))
        name = 'integration-'+fingerprint(b'EPL synthetic integration v1')+'.json'
        with private_store(self.root) as store:
            self.assertTrue(store.put(name, record))
            original = (self.root/name).read_bytes()
            self.assertFalse(store.put(name, record))
            self.assertEqual(store.get(name), record)
            with self.assertRaisesRegex(ValueError, 'RECORD_CONFLICT'):
                store.put(name, {**record, 'live': True})
            self.assertEqual((self.root/name).read_bytes(), original)
        self.assertEqual({p.name for p in self.root.iterdir()}, {name, 'corner-capture'})
        self.assertEqual(before, (self.data.options(), self.data.sources))
        repeated_corners, repeated_btts = self.prepare()
        repeated_quotes = saved(saved_quotes(repeated_btts['forecasts']))
        repeated_joined = []
        for forecast in repeated_btts['forecasts']:
            totals = from_pl_btts(forecast, enabled=True)
            e = json.loads(forecast.evidence)
            corner = next(c for c in repeated_corners['forecasts']
                          if str(c['identity']['fixture_id']) == e['fixture_id'])
            repeated_joined.append({'btts': btts_join(forecast, repeated_quotes),
                'totals': totals_join(totals, repeated_quotes),
                'totals_frozen': goal_totals._wire(asdict(totals)),
                'corner_forecast_sha256': corner['forecast_sha256'],
                'corner_quotes': corner_join(corner, repeated_quotes)})
        # Retain the original write receipt; a replay must never invent one.
        repeated = {**record, 'corners': repeated_corners, 'joined': repeated_joined,
                    'readiness': self.readiness(repeated_quotes)}
        self.assertEqual(encoded(record), encoded(repeated))
        self.data.sources.reverse()
        reordered_corners, reordered_btts = self.prepare()
        self.assertEqual([f.identity for f in btts['forecasts']],
                         [f.identity for f in reordered_btts['forecasts']])
        self.assertEqual([f['prediction'] for f in corners['forecasts']],
                         [f['prediction'] for f in reordered_corners['forecasts']])
        # Corner evidence intentionally preserves supplied source order.
        self.assertNotEqual(corners['forecasts'][0]['forecast_sha256'],
                            reordered_corners['forecasts'][0]['forecast_sha256'])

    def test_quote_rejections_never_change_frozen_models(self):
        corners, btts = self.prepare()
        forecast = btts['forecasts'][0]
        totals = from_pl_btts(forecast, enabled=True)
        original = encoded([corners, forecast.wire(), totals.record_id])
        for case in ('missing', 'stale', 'identity', 'tournament', 'kickoff', 'partial', 'period', 'future'):
            with self.subTest(case=case):
                rows, definitions = saved_quotes([forecast]), metadata()
                as_of = QUOTE_TIME
                if case == 'missing': rows = []
                elif case == 'stale': as_of += timedelta(minutes=16)
                elif case == 'identity': rows[0]['participant1Name'] = 'Wrong synthetic home'
                elif case == 'tournament': rows[0]['tournamentId'] = 18
                elif case == 'kickoff': rows[0]['startTime'] = (NOW+timedelta(days=1)).isoformat()
                elif case == 'period':
                    definitions[4]['period'] = definitions[5]['period'] = 'p1'
                else:
                    for mid in ('5', '6'):
                        outcomes = rows[0]['bookmakerOdds']['fanduel']['markets'][mid]['outcomes']
                        if case == 'partial': outcomes.pop(str(int(mid)*10+1))
                        else: outcomes[str(int(mid)*10)]['players']['0']['changedAt'] = (QUOTE_TIME+timedelta(seconds=1)).isoformat()
                quotes = saved(rows, definitions)
                for consume, f in ((btts_join, forecast), (totals_join, totals)):
                    with self.assertRaises(ValueError): consume(f, quotes, as_of=as_of)
                self.assertEqual(encoded([corners, forecast.wire(), totals.record_id]), original)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_empty_and_identity_calendar_fail_closed(self):
        original = deepcopy(self.data.payload)
        for case in ('partial', 'empty', 'identity'):
            with self.subTest(case=case):
                self.data.payload = deepcopy(original)
                if case == 'partial': self.data.payload['matches'].pop()
                elif case == 'empty':
                    for m in self.data.payload['matches'][:8]: m['utcDate'] = '2026-10-18T14:00:00Z'
                else: self.data.payload['matches'][0]['competition']['id'] = 999
                corners, btts = self.prepare()
                expected = 'EMPTY' if case == 'empty' else 'BLOCKED'
                self.assertEqual((corners['state'], btts['state']), (expected, expected))
                self.assertFalse(corners['forecasts']); self.assertFalse(btts['forecasts'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_scoped_history_and_unsupported_readiness_remain_independent(self):
        baseline = self.prepare()[1]
        self.data.replace_current(lambda b: b.replace(b',5,3,', b',bad,3,'))
        corners, btts = self.prepare()
        self.assertEqual(corners['state'], 'BLOCKED')
        self.assertEqual(btts['state'], 'RESEARCH_READY')
        self.assertEqual({json.loads(f.evidence)['fixture_id']: f.probabilities for f in btts['forecasts']},
                         {json.loads(f.evidence)['fixture_id']: f.probabilities for f in baseline['forecasts']})
        quotes = saved(saved_quotes(btts['forecasts']))
        league = next(l for l in self.readiness(quotes)['leagues'] if l['competition'] == 'E0')
        self.assertEqual(league['markets']['btts']['input_state'], 'UNSUPPORTED')
        self.assertEqual(league['markets']['btts']['gates']['history']['state'], 'VALID')
        for m in league['markets'].values():
            self.assertFalse(m['validated']); self.assertFalse(m['live'])
        f = btts['forecasts'][0]
        t = from_pl_btts(f, enabled=True)
        with self.assertRaises(ValueError): totals_join(t, quotes, line=2.25)
        self.data.replace_current(lambda b: b.replace(b',1,0,H,', b',bad,0,H,'))
        corners, btts = self.prepare()
        self.assertEqual((corners['state'], btts['state']), ('BLOCKED', 'BLOCKED'))

    def test_missing_stale_history_and_calendar_block_both_consumers(self):
        sources = self.data.sources[:]
        for case in ('missing', 'stale', 'pin', 'calendar_stale', 'calendar_missing'):
            with self.subTest(case=case):
                self.data.sources = sources[:]
                raw, options = self.data.options()
                if case == 'missing': self.data.sources.pop()
                elif case in ('stale', 'pin'):
                    n, b, p, at = self.data.sources[-1]
                    self.data.sources[-1] = n, b, '0'*64 if case == 'pin' else p, NOW-timedelta(days=15) if case == 'stale' else at
                elif case == 'calendar_stale': options['as_of'] = NOW+timedelta(hours=7)
                else: raw = None
                for prepare in (pl_corner_capture.prepare, pl_btts.prepare):
                    result = prepare(raw, sources=self.data.sources, enabled=True, **options)
                    self.assertEqual(result['state'], 'BLOCKED')
                    self.assertFalse(result['forecasts'])
        self.assertEqual(list(self.root.iterdir()), [])
