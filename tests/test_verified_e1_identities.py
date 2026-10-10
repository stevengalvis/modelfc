"""Posted E1 identity evidence, synthetic history/markets, unchanged model gates."""
from datetime import date, datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from modelfc.acquisition_planner import Fixture, plan_acquisition
from modelfc.offline_forecasts import KnownFixture, prepare_forecast, prepare_plan, consume_forecast
from modelfc.providers.oddspapi import (E1_VERIFIED_TEAM_IDENTITIES, TEAM_ALIASES,
                                       _verified_e1_aliases, normalize_team, OddsPapiError)
from modelfc.providers.oddspapi_btts import normalize_btts
from tests.verified_e1_identity_fixtures import VERIFIED_IDENTITIES, VERIFIED_FIXTURES
from tests.test_offline_identity_audit import fixture, history
from tests.test_shared_odds import snapshot
from tests.oddspapi_inventory_fixtures import NOW, metadata


class VerifiedE1IdentityTests(TestCase):
    def test_all_18_added_aliases_and_existing_verified_mappings(self):
        names = {r[2] for r in VERIFIED_IDENTITIES}
        self.assertEqual(len([r for r in VERIFIED_IDENTITIES if r[3]]), 18)
        self.assertEqual(set(E1_VERIFIED_TEAM_IDENTITIES), {tuple(r[:3]) for r in VERIFIED_IDENTITIES})
        for pid, source, canonical, added in VERIFIED_IDENTITIES:
            with self.subTest(provider_id=pid):
                self.assertEqual(normalize_team(source, names, 'E1'), canonical)
                self.assertEqual(normalize_team(canonical, names, 'E1'), canonical)
                if added: self.assertEqual(TEAM_ALIASES[source], canonical)
        self.assertEqual(len(TEAM_ALIASES), 22)  # 18 new + four preserved aliases.

    def test_registry_conflicting_ids_names_and_canonical_collisions_rejected(self):
        valid = ((1, 'Source One', 'One'), (2, 'Source Two', 'Two'))
        self.assertEqual(_verified_e1_aliases(valid), {'Source One':'One', 'Source Two':'Two'})
        for bad in (((1,'A','X'),(1,'B','Y')), ((1,'A','X'),(2,'A','Y')),
                    ((1,'A','X'),(2,'B','X')), ((1,'A','X'),(2,'X','Y')),
                    ((True,'A','X'),), ((0,'A','X'),), ((1,' A','X'),)):
            with self.assertRaisesRegex(ValueError,'INVALID_VERIFIED_TEAM_REGISTRY'):
                _verified_e1_aliases(bad)

    def test_unknown_ambiguous_cross_competition_and_nonfuzzy_names_rejected(self):
        for unknown in ('West Ham united', 'Queens Park Rangers FC', 'Wrexham', 'Unknown FC'):
            with self.assertRaises(OddsPapiError): normalize_team(unknown, {'West Ham','QPR'}, 'E1')
        for _, source, canonical, _ in VERIFIED_IDENTITIES:
            if source == canonical: continue
            with self.assertRaises(OddsPapiError): normalize_team(source, {canonical}, 'SP1')
            with self.assertRaisesRegex(OddsPapiError, 'Ambiguous'):
                normalize_team(source, {source,canonical}, 'E1')
        self.assertEqual(normalize_team('Valencia CF', {'Valencia'}, 'SP1'), 'Valencia')

    def test_all_12_fixture_joins_use_one_corner_btts_resolver(self):
        with patch('socket.socket', side_effect=AssertionError('network')), \
             patch('modelfc.ledger_storage.write_new_record', side_effect=AssertionError('evidence write')):
            for fid, kickoff, hid, home, hcanon, aid, away, acanon in VERIFIED_FIXTURES:
                with self.subTest(fixture_id=fid):
                    identity = Fixture('E1',18,fid,datetime.fromisoformat(kickoff.replace('Z','+00:00')))
                    known = KnownFixture(identity,hcanon,acanon)
                    prepared = prepare_forecast(known,history(hcanon,acanon),
                        frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date())
                    row = fixture(home,away,hid,aid,fid)
                    row.update(startTime=kickoff, categorySlug='england', tournamentSlug='championship')
                    snap = snapshot([row])
                    result = consume_forecast(prepared,snap,snap.fixtures[0])
                    self.assertEqual((result.corner_status,result.btts_status),('PASS','PASS'))
                    self.assertEqual(len(result.corner),4)
                    self.assertEqual(len(result.btts),1)
                    # Standalone BTTS adapter uses the same exact E1 resolution.
                    observation = normalize_btts(row,metadata(),row,competition='E1',
                        historical_names={hcanon,acanon},retrieved_at=NOW.isoformat(),as_of=NOW)
                    self.assertEqual((observation.fixture.home_team,observation.fixture.away_team),(hcanon,acanon))

    def test_reported_four_venue_samples_reject_corners_but_allow_planned_btts(self):
        for home, away, scarce, venue in (('Bolton','Stoke','Bolton','HOME'),
                                        ('Middlesbrough','Wolves','Wolves','AWAY'),
                                        ('Sheffield United','Lincoln','Lincoln','AWAY')):
            with self.subTest(team=scarce):
                rows = ['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC']
                # Sufficient league history and counterpart venue history.
                counterpart = away if scarce == home else home
                for i in range(120):
                    day = date(2026,6,1)+timedelta(days=i)
                    h,a = (counterpart,'Other') if i%2==0 else ('Other',counterpart)
                    rows.append(f'E1,{day:%d/%m/%Y},{h},{a},2,1,4,3')
                for i in range(7 if scarce == "Wolves" else 8):
                    day = date(2026,9,1)+timedelta(days=i)
                    required_pair = (scarce,'Other') if venue == 'HOME' else ('Other',scarce)
                    h,a = required_pair if i%2==0 else tuple(reversed(required_pair))
                    rows.append(f'E1,{day:%d/%m/%Y},{h},{a},2,1,4,3')
                data = (('E1_2627.csv',('\n'.join(rows)+'\n').encode()),)
                identity = Fixture('E1',18,scarce,NOW+timedelta(hours=24))
                f = KnownFixture(identity,home,away)
                plan = plan_acquisition({'E1':[identity]},as_of=NOW)
                with self.assertRaisesRegex(ValueError,'4 matches; need at least 5'):
                    prepare_plan(plan,(f,),{'E1':data},frozen_at=NOW-timedelta(seconds=1),
                                 cutoff=NOW.date(),models=('corner',))
                prepared = prepare_plan(plan,(f,),{'E1':data},frozen_at=NOW-timedelta(seconds=1),
                                        cutoff=NOW.date(),models=('btts',))[0]
                self.assertIsNone(prepared.corner)
                self.assertIsNotNone(prepared.btts)
                canonical_sources = {r[2]:(r[0],r[1]) for r in VERIFIED_IDENTITIES}
                hid,hsource = canonical_sources[home]; aid,asource = canonical_sources[away]
                snap = snapshot([fixture(hsource,asource,hid,aid,scarce)])
                result = consume_forecast(prepared,snap,snap.fixtures[0])
                self.assertEqual(result.corner_status,'NOT_REQUESTED')
                self.assertEqual(result.btts_status,'PASS')
                self.assertEqual(len(result.btts),1)

    def test_alias_joins_do_not_admit_same_day_future_results_or_new_models(self):
        f = KnownFixture(Fixture('E1',18,'verified',NOW+timedelta(hours=24)),'West Ham','QPR')
        base = prepare_forecast(f,history('West Ham','QPR'),frozen_at=NOW,cutoff=NOW.date())
        future = f'E1,{NOW:%d/%m/%Y},West Ham,QPR,99,99,99,99\nE1,09/10/2026,West Ham,QPR,99,99,99,99\n'
        after = prepare_forecast(f,history('West Ham','QPR',future),frozen_at=NOW,cutoff=NOW.date())
        self.assertEqual(base.corner,after.corner)
        self.assertEqual(base.btts.yes_probability,after.btts.yes_probability)
        for league,tid in (('E0',17),('SP1',8),('I1',23),('MLS',242)):
            unsupported = KnownFixture(Fixture(league,tid,'other',f.identity.kickoff_utc),'West Ham','QPR')
            p = prepare_forecast(unsupported,(),frozen_at=NOW,cutoff=NOW.date())
            self.assertEqual(p.status,'UNSUPPORTED_MODEL')
            self.assertIsNone(p.corner); self.assertIsNone(p.btts)

    def test_conflicting_missing_unknown_provider_id_pairs_reject_all_consumers(self):
        from dataclasses import replace
        from modelfc.providers.oddspapi import validate_team_identity
        from modelfc.providers.oddspapi_saved_response import ReplayError
        from tests.test_offline_identity_audit import audit
        for _, source, canonical, _ in VERIFIED_IDENTITIES:
            pid = next(i for i,s,c in E1_VERIFIED_TEAM_IDENTITIES if s == source)
            validate_team_identity(source,pid,'E1')
            validate_team_identity(canonical,pid,'E1')
            for wrong in (None,9999999):
                with self.assertRaises(OddsPapiError): validate_team_identity(source,wrong,'E1')
        row = fixture('West Ham United','Queens Park Rangers',37,1)
        snap = snapshot([row]); f = snap.fixtures[0]
        self.assertEqual((f.home_provider_team_id,f.away_provider_team_id),(37,1))
        with self.assertRaises(OddsPapiError): replace(f,home_provider_team_id=1)
        for name, bad_id in (('West Ham United',1),('Queens Park Rangers',37),('Unknown',37)):
            bad = row.copy(); bad['participant1Name']=name; bad['participant1Id']=bad_id
            # Maintain distinct participant IDs so the mismatch test is independent.
            bad['participant2Id']=2; bad['participant2Name']='Portsmouth FC'
            with patch('socket.socket',side_effect=AssertionError('network')), \
                 patch('modelfc.ledger_storage.write_new_record',side_effect=AssertionError('write')):
                with self.assertRaisesRegex(ReplayError,'INVALID_TEAM_IDENTITY'): snapshot([bad])
                raw=bad|{'categorySlug':'england','tournamentSlug':'championship'}
                with self.assertRaises(ValueError):
                    normalize_btts(raw,metadata(),raw,competition='E1',historical_names={'West Ham','QPR','Portsmouth'},
                                   retrieved_at=NOW.isoformat(),as_of=NOW)
                report=audit([bad],history('West Ham','Portsmouth'))
                self.assertEqual(report['eligible_fixtures'][0]['status'],'REVIEW')
                self.assertIn('PROVIDER_IDENTITY_MISMATCH',{i['status'] for i in report['unmatched_identities']})
        for key in ('participant1Id','participant2Id'):
            bad=row.copy(); del bad[key]
            with self.assertRaisesRegex(ReplayError,'INVALID_TEAM_IDENTITY'): snapshot([bad])
