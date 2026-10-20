"""Independent policy regression using the existing validated corner/BTTS fixtures."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from tests import test_published_recommendations as legacy_tests
from modelfc import published_recommendations as published, pick_strategies as strategies
from modelfc.corner_prospective import _lock
from modelfc.ledger_storage import LedgerError


class StrategyTests(unittest.TestCase):
    setUp = legacy_tests.PublishedRecommendationTests.setUp
    publish = legacy_tests.PublishedRecommendationTests.publish
    def independent(self):
        with _lock(self.state):
            return strategies.publish_locked(self.state)

    def test_filter_before_choice_preserves_ev_order(self):
        base = next(iter(published.select(published._locked_inventory(self.state,
            now=self.now, include_recorded=True, upcoming_only=True), self.now).values()))
        high_ev = dict(base, model_probability=.60, expected_profit=.5, line=4.5)
        high_prob = dict(base, model_probability=.66, expected_profit=.3, line=5.5)
        highest_prob = dict(base, model_probability=.85, expected_profit=.2, line=6.5)
        with patch.object(published, 'project_shortlist', return_value={'offers':[high_ev,high_prob,highest_prob]}):
            self.assertEqual(next(iter(published.select({}, self.now).values())), high_ev)
            self.assertEqual(next(iter(published.select({}, self.now, strategy='higher_probability').values())), high_prob)

    def test_diagnostics_distinguish_base_and_strategy_gates_with_source_reference(self):
        from modelfc.fixture_diagnostics import Recorder
        original = published._locked_inventory(self.state, now=self.now,
                                               include_recorded=True, upcoming_only=True)
        for probability in (.60, .65):
            inventory = deepcopy(original)
            for view in inventory['recorded'].values():
                for target in view['targets']:
                    for offer in target['offers']:
                        offer['model_probability'] = probability
            for strategy in ('best_value', 'higher_probability'):
                with self.subTest(probability=probability, strategy=strategy):
                    expected = published.select(inventory, self.now, strategy=strategy)
                    recorder = Recorder()
                    actual = published.select(inventory, self.now, strategy=strategy, diagnostics=recorder)
                    self.assertEqual(actual, expected)
                    self.assertFalse(recorder.incomplete)
                    base = [e for e in recorder.events if e['reason'] == 'BASE_VALUE_QUALIFIED']
                    self.assertTrue(base)
                    self.assertTrue(all(e['strategy'] is None for e in base))
                    decisions = [e for e in recorder.events if e['strategy'] == strategy]
                    self.assertTrue(decisions)
                    self.assertTrue(all(e['reference_id'] for e in decisions))
                    self.assertTrue({e['reference_id'] for e in decisions} <= {e['reference_id'] for e in base})
                    blocked = strategy == 'higher_probability' and probability == .60
                    self.assertEqual({e['reason'] for e in decisions},
                                     {'STRATEGY_PROBABILITY_THRESHOLD' if blocked else 'VALUE_QUALIFIED'})
                    self.assertEqual(bool(actual), not blocked)
                    self.assertTrue(all('observation_id' not in value for value in actual.values()))

    def test_legacy_immutable_new_epoch_and_independent_slots(self):
        self.publish()
        original = {p:p.read_bytes() for p in (self.state/published.DIRECTORY).glob('*.json')}
        legacy = published.read(self.state)
        self.now += timedelta(seconds=1)
        self.assertGreater(self.independent(), 0)
        self.assertEqual(original, {p:p.read_bytes() for p in (self.state/published.DIRECTORY).glob('*.json')})
        self.assertEqual(legacy, published.read(self.state))
        first = strategies.read(self.state)
        self.assertEqual(first['strategy'], 'best_value')
        self.assertTrue(first['simulated']); self.assertEqual(first['stake_dollars'], 2)
        self.assertEqual(first['publications'][0]['policy_effective_at_utc'], self.now.isoformat())
        self.now += timedelta(seconds=20)
        self.assertEqual(self.independent(), 0)
        self.assertEqual(first, strategies.read(self.state))

    def test_canonical_selection_ignores_price_time_but_not_line(self):
        base = next(iter(published.select(published._locked_inventory(self.state,
            now=self.now, include_recorded=True, upcoming_only=True), self.now).values()))
        self.assertEqual(strategies.selection_identity(base), strategies.selection_identity(dict(base, american_odds=200, decimal_odds=3., bookmaker='other',
            retrieved_at_utc=(self.now+timedelta(minutes=3)).isoformat())))
        self.assertNotEqual(strategies.selection_identity(base), strategies.selection_identity(dict(base,line=base['line']+1)))
        self.assertNotEqual(strategies.publication_identity(base,'best_value'), strategies.publication_identity(base,'higher_probability'))

    def test_readonly_page_filters_and_stake_math(self):
        self.independent()
        before = self.fx.state_bytes()
        response=self.fx.client.get('/api/v1/prospective/performance?view=strategies&scope=history&limit=1')
        self.assertEqual(response.status_code,200,response.text)
        self.assertLessEqual(len(response.json()['publications']),1)
        self.assertEqual(before,self.fx.state_bytes())
        self.assertEqual(strategies.read(self.state,market='BTTS')['total'],0)
        self.assertEqual(strategies.read(self.state,offset=2000)['publications'],[])
        self.fx.write_result(away=3); self.now+=timedelta(days=1)
        result=strategies.read(self.state,scope='history')
        self.assertEqual(result['profit_dollars'],2*result['summary']['profit_units'])
        for row in result['publications']:
            self.assertEqual(row['simulated_stake_dollars'],2)
            self.assertEqual(row['profit_dollars'],2*row['profit_units'])
        self.assertEqual(strategies.read(self.state)['total'],0)

    def test_activation_and_invalid_query_fail_closed(self):
        self.independent()
        path=next((self.state/strategies.ACTIVATIONS).glob('*.json'))
        record=json.loads(path.read_text()); record['record_hash']='0'*64; path.write_text(json.dumps(record))
        with self.assertRaises(LedgerError): self.independent()
        with self.assertRaises(ValueError): strategies.read(self.state,limit=21)

    def test_two_pending_legacy_sentinels_are_never_opened_or_rewritten(self):
        # Opaque sentinels prove namespace isolation; the separate legacy test above
        # verifies a fully validated historical publication through the legacy API.
        directory=self.state/published.DIRECTORY; directory.mkdir()
        for number in (1,2):
            (directory/f"{number:064x}.json").write_text(json.dumps({
                "historical_pending_corner_pick":number,"original_price":-200,
                "result":"PENDING","original_timestamp":"2026-10-16T13:00:00Z"}))
        original={p:p.read_bytes() for p in directory.iterdir()}
        reader=published.read_json_record
        def guarded(path,*args,**kwargs):
            self.assertNotEqual(path.parent,directory,"new policy must not read or migrate legacy picks")
            return reader(path,*args,**kwargs)
        with patch.object(published,'read_json_record',side_effect=guarded):
            self.independent(); strategies.read(self.state)
        self.assertEqual(original,{p:p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(len(original),2)

    def test_currency_and_roi_contract_for_favorite_underdog_push_loss_pending(self):
        from modelfc.corner_prospective_read import _profit
        self.independent()
        template=deepcopy(strategies.read(self.state)['publications'][0])
        # Expected dollars are explicit, not derived from the implementation's stake.
        for odds,result,expected_units,expected_dollars,expected_roi in (
            (-200,'WIN',.5,1.,.5),(150,'WIN',1.5,3.,1.5),
            (-200,'LOSS',-1.,-2.,-1.),(150,'LOSS',-1.,-2.,-1.),
            (-200,'PUSH',0.,0.,0.),(150,'PUSH',0.,0.,0.),
            (-200,'PENDING',None,None,None)):
            with self.subTest(odds=odds,result=result):
                row=deepcopy(template); row['offer']['american_odds']=odds
                row.update(result=result,profit_units=_profit(None if result=='PENDING' else result,odds))
                with patch.object(published,'read',return_value=published._report([row])):
                    report=strategies.read(self.state,scope='history')
                pick=report['publications'][0]
                self.assertEqual(pick['simulated_stake_dollars'],2)
                self.assertEqual(pick['profit_units'],expected_units)
                self.assertEqual(pick['profit_dollars'],expected_dollars)
                self.assertEqual(report['totals']['roi'],expected_roi)
                if expected_dollars is not None:
                    self.assertEqual(report['total_profit_dollars']/2,expected_roi)
                else:
                    self.assertEqual(report['totals']['pending'],1)
                    self.assertEqual(report['totals']['settled'],0)

class BttsStrategyTests(unittest.TestCase):
    def setUp(self):
        from tests.test_published_btts import PublishedBttsTests
        self.fx=PublishedBttsTests(); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)

    def test_different_selection_filter_before_selection_and_shared_identity(self):
        self.fx.capture(prices={'draftkings':(2.2,1.75),'fanduel':(1.2,6.)})
        with _lock(self.fx.state): strategies.publish_locked(self.fx.state)
        best=strategies.read(self.fx.state)['publications'][0]
        high=strategies.read(self.fx.state,strategy='higher_probability')['publications'][0]
        self.assertEqual(best['offer']['side'],'NO')
        self.assertEqual(high['offer']['side'],'YES')
        self.assertNotEqual(best['selection_id'],high['selection_id'])
        self.assertEqual(high['ranking_views'],['higher_probability'])
        self.assertEqual(best['ranking_views'],['best_value'])

    def test_shared_pick_two_strategy_records_not_two_unique_selections(self):
        self.fx.capture()
        with _lock(self.fx.state): strategies.publish_locked(self.fx.state)
        records=published._records(self.fx.state,directory_name=strategies.DIRECTORY)
        self.assertEqual(len(records),2)
        self.assertEqual(len({r['selection_id'] for r in records}),1)
        self.assertEqual(len({r['publication_id'] for r in records}),2)
        first={p:p.read_bytes() for p in (self.fx.state/strategies.DIRECTORY).glob('*.json')}
        self.fx.now+=timedelta(seconds=20)
        with _lock(self.fx.state): self.assertEqual(strategies.publish_locked(self.fx.state),0)
        self.assertEqual(first,{p:p.read_bytes() for p in (self.fx.state/strategies.DIRECTORY).glob('*.json')})

    def test_pages_preserve_full_filtered_totals(self):
        from tests.test_btts_research import history, observation
        from modelfc.btts_model import freeze_btts_forecast
        from modelfc.btts_research import record_btts_research
        from modelfc.btts_market_data import utc
        for i in range(6):
            fixture=self.fx.forecast.fixture.model_copy(update={'provider_fixture_id':f'fixture-{i}',
                'kickoff_utc':utc(published._time(self.fx.forecast.fixture.kickoff_utc)+timedelta(days=i))})
            forecast=freeze_btts_forecast(fixture,history(),frozen_at=published._time(self.fx.forecast.frozen_at_utc))
            original=observation()
            observed=original.model_copy(update={'fixture':fixture,'selections':tuple(
                s.model_copy(update={'provider_fixture_id':fixture.provider_fixture_id}) for s in original.selections)})
            record_btts_research(self.fx.state,forecast,observed)
        with _lock(self.fx.state): strategies.publish_locked(self.fx.state)
        first=strategies.read(self.fx.state,limit=5)
        last=strategies.read(self.fx.state,limit=5,offset=5)
        self.assertEqual(first['total'],6); self.assertEqual(last['total'],6)
        self.assertEqual(first['totals']['picks'],6); self.assertEqual(last['totals']['picks'],6)
        self.assertEqual(first['summary']['picks'],5); self.assertEqual(last['summary']['picks'],1)
        self.assertEqual(first['next_offset'],5); self.assertIsNone(last['next_offset'])
        self.assertFalse({r['publication_id'] for r in first['publications']} & {r['publication_id'] for r in last['publications']})
