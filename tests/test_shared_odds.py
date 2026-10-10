"""Offline pre-known fixture -> freeze -> one snapshot -> independent consumers."""
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
import hashlib
import json
from unittest import TestCase
from unittest.mock import patch

from modelfc.acquisition_planner import Fixture, plan_acquisition
from modelfc.offline_forecasts import KnownFixture, prepare_forecast, prepare_plan, consume_forecast
from modelfc.shared_odds import snapshot_from_bytes, btts_observation, corner_selections, unique_snapshots, snapshot_from_files
from modelfc.btts_model import history_from_bytes, freeze_btts_forecast
from modelfc.providers.oddspapi_btts import normalize_btts
from tests.test_oddspapi_saved_response import saved_fixture
from tests.oddspapi_inventory_fixtures import NOW, metadata, market


def sources(extra=''):
    rows = ['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC']
    for index in range(100):
        day = date(2026, 6, 1) + timedelta(days=index)
        home, away = ('Home', 'Away') if index % 2 == 0 else ('Away', 'Home')
        rows.append(f'E1,{day:%d/%m/%Y},{home},{away},2,1,{4 + index % 3},{3 + index % 2}')
    return (('E1_2627.csv', ('\n'.join(rows) + '\n' + extra).encode()),)


def snapshot(rows=None, definitions=None, observed=NOW):
    rows = [saved_fixture()] if rows is None else rows
    # Valid American/decimal pairs for strict existing BTTS contracts.
    for row in rows:
        for m in row['bookmakerOdds'].get('fanduel', {}).get('markets', {}).values():
            for o in m['outcomes'].values():
                for p in o['players'].values():
                    p['priceAmerican'] = '-111' if p['price'] == 1.9 else '+100'
    return snapshot_from_bytes(json.dumps(rows).encode(), json.dumps(metadata() if definitions is None else definitions).encode(), retrieved_at=observed)


def known():
    return KnownFixture(Fixture('E1', 18, 'fixture-1', NOW + timedelta(hours=24)), 'Home', 'Away')


class SharedOddsTests(TestCase):
    def test_complete_offline_sequence_no_io_deterministic(self):
        f = known()
        row = saved_fixture(); row['startTime'] = f.identity.kickoff_utc.isoformat()
        plan = plan_acquisition({'E1': [f.identity]}, as_of=NOW)
        with patch('socket.socket', side_effect=AssertionError('network')), patch('modelfc.ledger_storage.write_new_record', side_effect=AssertionError('write')):
            prepared = prepare_plan(plan, (f,), {'E1': sources()}, frozen_at=NOW - timedelta(seconds=1), cutoff=NOW.date())[0]
            snap = snapshot([row])
            result = consume_forecast(prepared, snap, snap.fixtures[0])
            self.assertEqual(result, consume_forecast(prepared, snap, snap.fixtures[0]))
            self.assertEqual(len(result.corner), 4)
            self.assertEqual(len(result.btts), 1)
            self.assertEqual(result.btts[0].bookmaker, 'fanduel')
            self.assertEqual(result.btts[0].forecast_id, hashlib.sha256(json.dumps(prepared.btts.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(prepared.corner.model, 'venue-opponent-negative-binomial')
        self.assertEqual(prepared.btts, freeze_btts_forecast(prepared.btts.fixture, history_from_bytes('E1', sources()), frozen_at=NOW-timedelta(seconds=1)))

    def test_five_league_identity_and_unsupported_models(self):
        snap = snapshot([saved_fixture(tid, str(tid)) for tid in (18,17,8,23,242)])
        self.assertEqual({f.identity.competition for f in snap.fixtures}, {'E1','E0','SP1','I1','MLS'})
        for f in snap.fixtures:
            if f.identity.competition != 'E1':
                p = prepare_forecast(KnownFixture(f.identity, f.home_team, f.away_team), (), frozen_at=NOW, cutoff=NOW.date())
                self.assertEqual(p.status, 'UNSUPPORTED_MODEL')
                with self.assertRaises(ValueError): consume_forecast(p, snap, f)

    def test_cutoff_and_no_future_leakage(self):
        f = known()
        base = prepare_forecast(f, sources(), frozen_at=NOW, cutoff=NOW.date())
        extra = f'E1,{NOW:%d/%m/%Y},Home,Away,99,99,99,99\nE1,09/10/2026,Home,Away,99,99,99,99\n'
        future = prepare_forecast(f, sources(extra), frozen_at=NOW, cutoff=NOW.date())
        self.assertEqual(base.corner, future.corner)
        self.assertEqual(base.btts.yes_probability, future.btts.yes_probability)
        self.assertNotEqual(base.sources, future.sources)
        with self.assertRaises(ValueError): prepare_forecast(f, sources(), frozen_at=NOW, cutoff=NOW.date()-timedelta(days=1))
        with self.assertRaises(ValueError): prepare_forecast(f, sources(), frozen_at=f.identity.kickoff_utc, cutoff=f.identity.kickoff_utc.date())

    def test_incidental_fixture_and_temporal_boundary_rejected(self):
        f = known(); row = saved_fixture(); row['startTime'] = f.identity.kickoff_utc.isoformat()
        snap = snapshot([row])
        same_time = prepare_forecast(f, sources(), frozen_at=NOW, cutoff=NOW.date())
        with self.assertRaises(ValueError): consume_forecast(same_time, snap, snap.fixtures[0])
        different = KnownFixture(Fixture('E1',18,'other',f.identity.kickoff_utc), 'Home','Away')
        p = prepare_forecast(different,sources(),frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date())
        with self.assertRaises(ValueError): consume_forecast(p,snap,snap.fixtures[0])
        plan = plan_acquisition({'E1':[f.identity]},as_of=NOW)
        with self.assertRaises(ValueError): prepare_plan(plan, (), {'E1':sources()}, frozen_at=NOW,cutoff=NOW.date())

    def test_immutable_snapshot_and_forecast_hash_provenance(self):
        snap = snapshot(); p = prepare_forecast(known(),sources(),frozen_at=NOW,cutoff=NOW.date())
        self.assertEqual(len(snap.payload_sha256),64)
        self.assertEqual(len(p.forecast_id),64)
        for obj, attr in ((snap,'provider'),(snap.fixtures[0],'home_team'),(snap.fixtures[0].quotes[0],'status'),(p,'status')):
            with self.assertRaises(FrozenInstanceError): setattr(obj,attr,'changed')
        self.assertEqual(snap, snapshot())
        self.assertNotEqual(snap.observation_id, snapshot(observed=NOW+timedelta(seconds=1)).observation_id)

    def test_metadata_104_legacy_and_null_period(self):
        for modern in (False,True):
            definitions=metadata(); row=saved_fixture()
            if modern:
                definitions[4].update(marketId=104,marketType='bothteamsscore')
                definitions[4]['outcomes']=[{'outcomeId':1040,'outcomeName':'Yes'},{'outcomeId':1041,'outcomeName':'No'}]
                del row['bookmakerOdds']['fanduel']['markets']['5'];row['bookmakerOdds']['fanduel']['markets']['104']=market(104)
            snap=snapshot([row],definitions)
            obs=btts_observation(snap,snap.fixtures[0],historical_names={'Home','Away'})
            self.assertEqual({s.side for s in obs.selections},{'YES','NO'})
            raw=row|{'participant1Id':1000001,'participant2Id':1000002,'categorySlug':'england','tournamentSlug':'championship'}
            normal=normalize_btts(raw,definitions,raw,competition='E1',historical_names={'Home','Away'},retrieved_at=NOW.isoformat(),as_of=NOW)
            self.assertEqual(normal.selections[0].decimal_odds,obs.selections[0].decimal_odds)
            definitions[4]['period']=None
            snap=snapshot([row],definitions)
            self.assertEqual(btts_observation(snap,snap.fixtures[0],historical_names={'Home','Away'}).selections,())

    def test_incomplete_stale_inactive_and_alternate_markets(self):
        for change in ('missing','stale','inactive','future'):
            row=saved_fixture();m=row['bookmakerOdds']['fanduel']['markets']['5']
            if change=='missing': del m['outcomes']['51']
            if change=='stale':m['staleOdds']=True
            if change=='inactive':m['marketActive']=False
            if change=='future':m['outcomes']['50']['players']['0']['changedAt']=(NOW+timedelta(seconds=1)).isoformat()
            snap=snapshot([row]);obs=btts_observation(snap,snap.fixtures[0],historical_names={'Home','Away'})
            self.assertNotEqual(obs.availability[1].status,'AVAILABLE')
            self.assertEqual(len(obs.selections),1 if change=='missing' else 0)
        row=saved_fixture();row['bookmakerOdds']['fanduel']['markets']['6']=market(6,main=False)
        snap=snapshot([row])
        self.assertIn('ALTERNATE_GOAL_TOTALS',{q.family for q in snap.fixtures[0].quotes})
        self.assertNotIn('FIRST_HALF_CORNERS',{s.request.market_type for s in corner_selections(snap,snap.fixtures[0])})

    def test_duplicate_and_malformed_metadata_rejected(self):
        row=saved_fixture()
        with self.assertRaises(ValueError):snapshot([row,row])
        with self.assertRaises(ValueError):snapshot(definitions=metadata()+[metadata()[0]])
        with self.assertRaises(ValueError):snapshot_from_bytes(b'[{"a":1,"a":2}]',json.dumps(metadata()).encode(),retrieved_at=NOW)

    def test_corner_can_freeze_without_goals_or_odds(self):
        only_corners = sources()[0][1].decode().splitlines()
        only_corners = [",".join(row.split(",")[:4]+row.split(",")[-2:]) for row in only_corners]
        p = prepare_forecast(known(), (("E1_2627.csv",("\n".join(only_corners)+"\n").encode()),), frozen_at=NOW,cutoff=NOW.date(),models=("corner",))
        self.assertIsNotNone(p.corner)
        self.assertIsNone(p.btts)
        row=saved_fixture();row['startTime']=known().identity.kickoff_utc.isoformat()
        snap=snapshot([row],observed=NOW+timedelta(seconds=1))
        result=consume_forecast(p,snap,snap.fixtures[0])
        self.assertEqual(len(result.corner),4)
        self.assertEqual(result.btts,())

    def test_duplicate_observation_replay_and_outcome_integrity(self):
        snap=snapshot()
        self.assertEqual(unique_snapshots((snap,snap)),(snap,))
        row=saved_fixture()
        row['bookmakerOdds']['fanduel']['markets']['5']['outcomes']['999']={'players':{'0':{'price':2,'active':True}}}
        snap=snapshot([row])
        with self.assertRaisesRegex(ValueError,'INVALID_BTTS_OUTCOME_MAPPING'):
            btts_observation(snap,snap.fixtures[0],historical_names={'Home','Away'})
        self.assertEqual(len(corner_selections(snap,snap.fixtures[0])),6)

    def test_hash_pinned_shared_file_boundary(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as d:
            r,m=Path(d)/'r.json',Path(d)/'m.json'
            r.write_text(json.dumps([saved_fixture()]));m.write_text(json.dumps(metadata()))
            kwargs=dict(response_sha256=hashlib.sha256(r.read_bytes()).hexdigest(),metadata_sha256=hashlib.sha256(m.read_bytes()).hexdigest(),retrieved_at=NOW)
            with patch('socket.socket',side_effect=AssertionError('network')):
                self.assertEqual(snapshot_from_files(r,m,**kwargs),snapshot_from_bytes(r.read_bytes(),m.read_bytes(),retrieved_at=NOW))
            with self.assertRaises(ValueError):snapshot_from_files(r,m,**(kwargs|{'response_sha256':'0'*64}))

    def test_history_reader_existing_shared_lock_no_writes(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from modelfc.offline_forecasts import load_history_bytes
        with TemporaryDirectory() as d:
            root=Path(d);state=root/'data'/'corner-refresh';state.mkdir(parents=True)
            (state/'refresh.lock').write_text('')
            (root/'E1_2627.csv').write_bytes(sources()[0][1])
            config=root/'config.json';config.write_text(json.dumps({'data_directory':'.','leagues':['E1'],'max_age_days':14}))
            before={p.relative_to(root): (p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
            self.assertEqual(load_history_bytes(config,'E1'),sources())
            self.assertEqual(before,{p.relative_to(root): (p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})
            with self.assertRaises(ValueError):load_history_bytes(config,'MLS')
            (state/'refresh.lock').unlink()
            with self.assertRaises(ValueError):load_history_bytes(config,'E1')
            self.assertFalse((state/'refresh.lock').exists())

    def test_standalone_btts_104_rejects_ambiguous_or_malformed_mappings(self):
        definitions=metadata();definitions[4].update(marketType='bothteamsscore',marketId=104)
        row=saved_fixture();row['bookmakerOdds']['fanduel']['markets']={'104':market(104)}
        row.update(participant1Id=1000001,participant2Id=1000002,categorySlug='england',tournamentSlug='championship')
        for variant in ('malformed','ambiguous'):
            bad=json.loads(json.dumps(definitions))
            if variant=='malformed':bad[4]['outcomes'][1]['outcomeName']='Maybe'
            else:
                second=json.loads(json.dumps(bad[4]));second['marketId']=105;bad.append(second)
                row['bookmakerOdds']['fanduel']['markets']['105']=market(105)
            with self.assertRaises(ValueError):normalize_btts(row,bad,row,competition='E1',historical_names={'Home','Away'},retrieved_at=NOW.isoformat(),as_of=NOW)

    def test_corner_normalization_parity_with_production(self):
        from modelfc.providers.oddspapi import normalize_odds
        row=saved_fixture();row.update(participant1Id=1000001,participant2Id=1000002,categorySlug='england',tournamentSlug='championship')
        snap=snapshot([row])
        production=normalize_odds(row,metadata(),row,retrieved_at=NOW.isoformat(),now=NOW,competition='E1')
        self.assertEqual(corner_selections(snap,snap.fixtures[0]),production.selections)

    def test_combined_consumption_isolates_btts_and_corner_failures(self):
        f=known();prepared=prepare_forecast(f,sources(),frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date())
        for broken in ('btts','corner','both'):
            definitions=metadata();row=saved_fixture();row['startTime']=f.identity.kickoff_utc.isoformat()
            if broken in ('btts','both'):
                row['bookmakerOdds']['fanduel']['markets']['5']['outcomes']['999']={'players':{'0':{'price':2,'active':True}}}
            if broken in ('corner','both'):definitions[1]['handicap']=4.25
            snap=snapshot([row],definitions)
            result=consume_forecast(prepared,snap,snap.fixtures[0])
            self.assertEqual(result.corner_status,'REVIEW' if broken in ('corner','both') else 'PASS')
            self.assertEqual(result.btts_status,'REVIEW' if broken in ('btts','both') else 'PASS')
            self.assertEqual(len(result.corner),0 if broken in ('corner','both') else 4)
            self.assertEqual(len(result.btts),0 if broken in ('btts','both') else 1)
            self.assertEqual(set(result.review_reasons),({'CORNER_CONSUMPTION_REVIEW'} if broken=='corner' else
                {'BTTS_CONSUMPTION_REVIEW'} if broken=='btts' else {'CORNER_CONSUMPTION_REVIEW','BTTS_CONSUMPTION_REVIEW'}))

    def test_planned_single_model_preparation_preserves_eligibility(self):
        f=known();plan=plan_acquisition({'E1':[f.identity]},as_of=NOW)
        corner_only=sources()[0][1].decode().splitlines()
        corner_only=[','.join(row.split(',')[:4]+row.split(',')[-2:]) for row in corner_only]
        prepared=prepare_plan(plan,(f,),{'E1':(('E1_2627.csv', ('\n'.join(corner_only)+'\n').encode()),)},
            frozen_at=NOW,cutoff=NOW.date(),models=('corner',))[0]
        self.assertIsNotNone(prepared.corner);self.assertIsNone(prepared.btts)
        # BTTS can freeze from 100 prior results while corner venue-history gates fail.
        rows=sources()[0][1].decode().splitlines()
        for i in range(1,len(rows)):
            cols=rows[i].split(',')
            if i%2 and i>7:cols[2]='Other home'
            rows[i]=','.join(cols)
        prepared=prepare_plan(plan,(f,),{'E1':(('E1_2627.csv', ('\n'.join(rows)+'\n').encode()),)},
            frozen_at=NOW,cutoff=NOW.date(),models=('btts',))[0]
        self.assertIsNone(prepared.corner);self.assertIsNotNone(prepared.btts)
        with self.assertRaises(ValueError):prepare_plan(plan,(),{'E1':sources()},frozen_at=NOW,cutoff=NOW.date(),models=('btts',))
        with self.assertRaises(ValueError):prepare_plan(plan,(f,),{'E1':sources()},frozen_at=NOW,cutoff=NOW.date(),models=('unknown',))

    def test_btts_ambiguity_includes_markets_without_retained_player_zero(self):
        f=known();prepared=prepare_forecast(f,sources(),frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date())
        for shape in ('empty','no_players','other_player','unknown_outcome','inactive'):
            definitions=metadata();second=json.loads(json.dumps(definitions[4]));second.update(marketId=104,marketType='bothteamsscore')
            second['outcomes']=[{'outcomeId':1040,'outcomeName':'Yes'},{'outcomeId':1041,'outcomeName':'No'}];definitions.append(second)
            row=saved_fixture();row['startTime']=f.identity.kickoff_utc.isoformat();m=market(104)
            if shape=='empty':m['outcomes']={}
            if shape=='no_players':
                for o in m['outcomes'].values():o['players']={}
            if shape=='other_player':
                for o in m['outcomes'].values():o['players']={'1':o['players']['0']}
            if shape=='unknown_outcome':m['outcomes']={'999':{'players':{'0':{'price':2,'active':True}}}}
            if shape=='inactive':m['marketActive']=False
            row['bookmakerOdds']['fanduel']['markets']['104']=m
            snap=snapshot([row],definitions)
            with self.assertRaisesRegex(ValueError,'AMBIGUOUS_BTTS_MARKET'):
                btts_observation(snap,snap.fixtures[0],historical_names={'Home','Away'})
            result=consume_forecast(prepared,snap,snap.fixtures[0])
            self.assertEqual(result.btts_status,'REVIEW');self.assertEqual(result.btts,())
            self.assertEqual(result.corner_status,'PASS');self.assertEqual(len(result.corner),4)
            raw=row|{'participant1Id':1000001,'participant2Id':1000002,'categorySlug':'england','tournamentSlug':'championship'}
            with self.assertRaises(ValueError):normalize_btts(raw,definitions,raw,competition='E1',historical_names={'Home','Away'},retrieved_at=NOW.isoformat(),as_of=NOW)

    def test_zero_quote_unmapped_btts_is_review_without_poisoning_corners(self):
        f=known();prepared=prepare_forecast(f,sources(),frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date())
        row=saved_fixture();row['startTime']=f.identity.kickoff_utc.isoformat()
        row['bookmakerOdds']['fanduel']['markets']['5']['outcomes']={'999':{'players':{'0':{'price':2,'active':True}}}}
        snap=snapshot([row])
        self.assertIn(('5','BTTS'),{(m.market_id,m.family) for m in snap.fixtures[0].present_markets})
        self.assertEqual([q for q in snap.fixtures[0].quotes if q.family=='BTTS'],[])
        result=consume_forecast(prepared,snap,snap.fixtures[0])
        self.assertEqual((result.btts_status,result.btts,result.review_reasons),('REVIEW',(),('BTTS_CONSUMPTION_REVIEW',)))
        self.assertEqual((result.corner_status,len(result.corner)),('PASS',4))
        # A malformed corner mapping does not falsely cause BTTS review.
        row=saved_fixture();row['startTime']=f.identity.kickoff_utc.isoformat()
        row['bookmakerOdds']['fanduel']['markets']['2']['outcomes']={'999':{'players':{'0':{'price':2,'active':True}}}}
        snap=snapshot([row]);result=consume_forecast(prepared,snap,snap.fixtures[0])
        self.assertEqual((result.btts_status,len(result.btts)),('PASS',1))
