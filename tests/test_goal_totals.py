"""Synthetic-only totals evidence; no league qualification."""
from dataclasses import replace
from datetime import timedelta
import json
import math
import unittest
from unittest.mock import patch

from modelfc import goal_totals as t
from modelfc.acquisition_storage import fingerprint
from modelfc.five_league_readiness import SavedQuotes
from tests.oddspapi_inventory_fixtures import NOW, metadata, market
from tests.test_oddspapi_saved_response import saved_fixture
from tests.test_shared_odds import sources, known
from modelfc.offline_forecasts import prepare_forecast


def fixture(code='E1', identity='next'):
    return t.GoalFixture(code, 'oddspapi', identity, 'Home', 'Away', NOW+timedelta(days=1), 'Europe/London')


def history(code='E1'):
    rows = []
    for i in range(100):
        start = NOW-timedelta(days=101-i)
        f = t.GoalFixture(code, 'synthetic', str(i), 'Home', 'Away', start, 'Europe/London')
        rows.append(t.GoalObservation(f, i%3, i%2, start+timedelta(hours=2), start+timedelta(hours=3), 'a'*64))
    return tuple(rows)


def frozen(code='E1'):
    return t.freeze(fixture(code), history(code), frozen_at=NOW-timedelta(minutes=1), cutoff=NOW.date())


def quote_inputs(f):
    row = saved_fixture(); row['fixtureId']='quote'; row['startTime']=f.fixture.kickoff.isoformat()
    row['bookmakerOdds']['fanduel']['markets'] = {'6': market(6)}
    for index, outcome in enumerate(row['bookmakerOdds']['fanduel']['markets']['6']['outcomes'].values()):
        outcome['players']['0']['priceAmerican'] = '-111' if index == 0 else '+100'
    definitions = metadata(); definitions[5]['handicap']=4.5
    raw = json.dumps([row]).encode(); meta = json.dumps(definitions).encode()
    saved = SavedQuotes(raw, fingerprint(raw), meta, fingerprint(meta), NOW)
    binding = json.dumps(dict(forecast_id=f.record_id, competition='E1', quote_fixture_id='quote',
        home='Home', away='Away', kickoff=f.fixture.kickoff.isoformat(), bookmaker='fanduel')).encode()
    return saved, binding


class TotalsTests(unittest.TestCase):
    def test_probability_bounds_complements_monotonicity_and_tails(self):
        for rates in ((0,0), (1.2,1.8), (400,400), (0,1000)):
            values = [t.probabilities(*rates,line) for line in (.5,1.5,2.5,4.5,10.5,100.5,1000.5,10000.5)]
            self.assertTrue(all(0 <= o <= 1 and 0 <= u <= 1 and o+u == 1 for o,u in values))
            self.assertEqual([o for o,_ in values], sorted((o for o,_ in values), reverse=True))
        expected = math.exp(-3)*sum(3**i/math.factorial(i) for i in range(5))
        self.assertAlmostEqual(t.probabilities(1,2,4.5)[1], expected, places=14)
        self.assertGreater(t.probabilities(1,2,30.5)[0], 0)  # direct tail, no 1-CDF cancellation
        for rate in (1e-300, 1e-20, 1e-16, 1e-10):
            self.assertAlmostEqual(t.probabilities(rate, 0, .5)[0] / -math.expm1(-rate), 1, places=12)
        self.assertEqual(t.probabilities(1,2,1e12+.5), (0,1))

    def test_reject_bad_rates_lines_period(self):
        for line in (True, -0.5, 0, 2, 2.25, 2.75, float('nan'), float('inf'), '4.5'):
            with self.assertRaises(ValueError): t.probabilities(1,1,line)
        for rate in (True, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError): t.probabilities(rate,1,4.5)
        with self.assertRaises(ValueError): replace(history()[0],period='EXTRA_TIME')

    def test_single_calculation_path_for_five_leagues(self):
        for code in ('E1','E0','SP1','I1','MLS'):
            f = frozen(code)
            self.assertEqual(f.home_rate, frozen().home_rate)
            self.assertEqual(len(f.record_id),64)
        r = t.readiness({},as_of=NOW)
        self.assertEqual(len(r['leagues']),5)
        for league in r['leagues']:
            self.assertEqual(league['markets']['goal_totals']['model'],'RESEARCH_ONLY')
            self.assertFalse(league['markets']['goal_totals']['validated'])

    def test_prior_only_and_receipt_leakage(self):
        original = frozen()
        later = t.GoalObservation(replace(fixture(), fixture_id='future', kickoff=NOW), 9,9,
            NOW+timedelta(hours=2), NOW+timedelta(hours=3),'b'*64)
        rows = history()+(later,)
        result=t.freeze(fixture(),rows,frozen_at=original.frozen_at,cutoff=NOW.date())
        self.assertEqual(original,result)
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT'):
            t.freeze(fixture(),[replace(r,retrieved_at=NOW+timedelta(hours=1)) for r in history()],
                frozen_at=original.frozen_at,cutoff=NOW.date())
        with self.assertRaises(ValueError): t.freeze(fixture(),history('E0'),frozen_at=NOW,cutoff=NOW.date())
        with self.assertRaises(ValueError): t.freeze(fixture(),history()+history()[:1],frozen_at=NOW,cutoff=NOW.date())
        with self.assertRaises(ValueError): t.freeze(fixture(),(),frozen_at=NOW,cutoff=NOW.date())

    def test_frozen_btts_adapter_reuses_rates(self):
        old=prepare_forecast(known(),sources(),frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date()).btts
        value=t.from_btts(old)
        self.assertEqual(value.home_rate,old.home_expected_goals)
        self.assertEqual(value.away_rate,old.away_expected_goals)
        self.assertEqual(value.origin,'E1_FROZEN_BTTS')

    def test_exact_pair_no_refit_no_network_no_recommendation(self):
        f=frozen(); saved,binding=quote_inputs(f)
        with patch('socket.socket',side_effect=AssertionError('network')), patch.object(t,'freeze',side_effect=AssertionError('refit')):
            r=t.consume(f,saved,binding_raw=binding,binding_sha256=fingerprint(binding),line=4.5,as_of=NOW)
        self.assertEqual(r['state'],'RESEARCH_ONLY')
        self.assertIsNone(r['value']); self.assertIsNone(r['recommendation'])
        self.assertFalse(r['validated']); self.assertFalse(r['live'])

    def test_wrong_join_market_or_time_rejected(self):
        f=frozen(); saved,binding=quote_inputs(f)
        for field,value in (('competition','E0'),('home','Away'),('bookmaker','draftkings'),('quote_fixture_id','other')):
            b=json.loads(binding);b[field]=value;b=json.dumps(b).encode()
            with self.assertRaises(ValueError): t.consume(f,saved,binding_raw=b,binding_sha256=fingerprint(b),line=4.5,as_of=NOW)
        for change in ('period','team','inactive','missing','duplicate','stale', 'undated', 'unknown', 'price', 'future'):
            s,b=quote_inputs(f); rows=json.loads(s.payload); meta=json.loads(s.metadata)
            if change=='period':meta[5]['period']='p1'
            if change=='team':meta[5]['marketType']='teamtotals-team1'
            m=rows[0]['bookmakerOdds']['fanduel']['markets']['6']
            if change=='inactive':m['marketActive']=False
            if change=='missing':del m['outcomes']['61']
            if change=='undated':m['outcomes']['60']['players']['0'].pop('changedAt')
            if change=='unknown':m['outcomes']['999'] = m['outcomes']['60']
            if change=='price':m['outcomes']['60']['players']['0']['priceAmerican'] = '+1000'
            if change=='future':m['outcomes']['60']['players']['0']['changedAt'] = (NOW+timedelta(seconds=1)).isoformat()
            if change=='duplicate':
                meta.append({**meta[5],'marketId':9})
                rows[0]['bookmakerOdds']['fanduel']['markets']['9']=m
            if change=='stale':
                for outcome in m['outcomes'].values():outcome['players']['0']['staleOdds']=True
            raw=json.dumps(rows).encode(); definitions=json.dumps(meta).encode()
            s=replace(s,payload=raw,payload_sha256=fingerprint(raw),metadata=definitions,metadata_sha256=fingerprint(definitions))
            with self.assertRaises(ValueError,msg=change):t.consume(f,s,binding_raw=b,binding_sha256=fingerprint(b),line=4.5,as_of=NOW)
        with self.assertRaises(ValueError): t.consume(f,saved,binding_raw=binding,binding_sha256=fingerprint(binding),line=4.5,as_of=NOW+timedelta(minutes=16))

    def test_immutable_regulation_settlement_separate_from_prices(self):
        f=frozen(); result=t.GoalObservation(f.fixture,3,2,f.fixture.kickoff+timedelta(hours=2),
                f.fixture.kickoff+timedelta(hours=3),'d'*64)
        record=t.settle(f,result,line=4.5)
        self.assertTrue(json.loads(record)['over_won']);self.assertIsNone(json.loads(record)['priced_return'])
        self.assertEqual(record,t.settle(f,result,line=4.5))
        with self.assertRaises(ValueError):t.settle(f,replace(result,fixture=replace(result.fixture,competition='E0')),line=4.5)

    def test_zero_scores_stale_history_and_unfinished_statuses(self):
        rows=tuple(replace(r,home_goals=0,away_goals=0) for r in history())
        f=t.freeze(fixture(),rows,frozen_at=NOW,cutoff=NOW.date())
        self.assertLess(f.home_rate,1e-8)
        for status in ('POSTPONED','ABANDONED','AET','PEN','IN_PLAY'):
            with self.assertRaises(ValueError):replace(rows[0],status=status)
        with self.assertRaisesRegex(ValueError,'STALE'):
            t.freeze(replace(fixture(),kickoff=NOW+timedelta(days=31)), rows,
                     frozen_at=NOW+timedelta(days=30),cutoff=(NOW+timedelta(days=30)).date())
        with self.assertRaisesRegex(ValueError,'VENUE'):
            t.freeze(replace(fixture(),home='Unseen'),rows,frozen_at=NOW,cutoff=NOW.date())

    def test_frozen_rates_and_input_hash_cannot_be_tampered(self):
        f=frozen()
        for updates in ({'home_rate':500.}, {'inputs_sha256':'b'*64},
                        {'aggregates':replace(f.aggregates,home_goals_for=f.aggregates.home_goals_for-1)}):
            with self.assertRaises(ValueError): replace(f,**updates)

    def test_reordered_provider_ties_are_deterministic(self):
        rows=history()
        other=replace(rows[0],fixture=replace(rows[0].fixture,provider='second',home='Other',away='Another'))
        kwargs=dict(frozen_at=NOW,cutoff=NOW.date())
        self.assertEqual(t.freeze(fixture(), rows+(other,),**kwargs),
                         t.freeze(fixture(), tuple(reversed(rows+(other,))),**kwargs))

    def test_reuses_verified_future_matchweek_cutoff(self):
        from tests.test_history_completeness import bundle,raw,histories
        from modelfc.history_completeness import parse_calendar
        from modelfc.offline_forecasts import KnownFixture
        from modelfc.acquisition_planner import Fixture
        calendar=parse_calendar(raw(bundle()),as_of=NOW)
        known_fixture=KnownFixture(Fixture('E1',18,'future',NOW+timedelta(days=1)),'West Ham','QPR')
        old=prepare_forecast(known_fixture,histories(),frozen_at=NOW,cutoff=(NOW+timedelta(days=1)).date(),history_calendar=calendar).btts
        f=t.from_btts(old)
        self.assertEqual(f.cutoff,old.inputs.cutoff_date)
        self.assertIsNotNone(old.history_calendar_proof)
        with self.assertRaises(ValueError):replace(f,origin_record=None)
