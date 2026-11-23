"""Entirely synthetic normalized calendars, names, provider IDs and history."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.five_league_readiness import Evidence, SavedQuotes, report as shared_report
from modelfc.spain_italy_readiness import report
from tests.test_five_league_readiness import e1, e0

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def sample(code='SP1'):
    teams = [{'source_team_id': f'synthetic-{i}', 'source_name': f'Synthetic Team {i}',
              'history_name': f'Team {i}'} for i in range(20)]
    contract = {'version': 1, 'competition': code, 'source': 'synthetic-only',
        'source_competition_id': 'synthetic-league', 'source_season_id': 'synthetic-season',
        'season_year': 2026, 'timezone': 'Europe/Madrid' if code == 'SP1' else 'Europe/Rome', 'team_bindings': teams}
    ring, rounds = list(teams), []
    for _ in range(19):
        rounds.append([(ring[i], ring[-1-i]) for i in range(10)])
        ring = [ring[0], ring[-1], *ring[1:-1]]
    rounds += [[(a, h) for h, a in pairs] for pairs in rounds]
    rows = []
    for number, pairs in enumerate(rounds, 1):
        for h, a in pairs:
            rows.append({'fixture_id': f'synthetic-fixture-{len(rows)}', 'kickoff': '2027-05-01T14:00:00Z',
                'matchday': number, 'home_id': h['source_team_id'], 'home_name': h['source_name'],
                'away_id': a['source_team_id'], 'away_name': a['source_name'], 'status': 'SCHEDULED', 'goals': None})
    rows[0].update(kickoff='2026-10-20T18:00:00Z', status='FT', goals=[0, 0])
    source = 'SYNTHETIC ORIGINAL SOURCE - NOT GENUINE EVIDENCE'
    bundle = {k: contract[k] for k in ('version', 'competition', 'source', 'source_competition_id', 'source_season_id')}
    bundle.update(season_start='2026-08-01', season_end='2027-05-31', stage='REGULAR_SEASON',
        retrieved_at=NOW.isoformat(), source_payload=source, source_sha256=fingerprint(source.encode()), matches=rows)
    history = f'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC,HS,HST\n{code},20/10/2026,Team 0,Team 19,0,0,D,0,0,2,7\n'.encode()
    return contract, bundle, history


def evidence(code='SP1', contract=None, bundle=None, history=None, retrieved=NOW, provenance='SYNTHETIC'):
    c, b, h = sample(code)
    c, b, h = contract or c, bundle or b, h if history is None else history
    cb, bb = encoded(c), encoded(b)
    return Evidence(code, 8 if code == 'SP1' else 23, 2026, date(2026, 10, 22),
        calendar=bb, calendar_sha256=fingerprint(bb), mapping=cb, mapping_sha256=fingerprint(cb),
        sources=((code+'_2627.csv', h, fingerprint(h), retrieved),), provenance=provenance)


def league(e, **kwargs):
    return next(r for r in report({e.competition:e}, as_of=NOW, **kwargs)['leagues'] if r['competition']==e.competition)


class SpainItalyTests(unittest.TestCase):
    def test_both_leagues_complete_zero_values_unqualified(self):
        for code in ('SP1', 'I1'):
            with self.subTest(code=code), patch('socket.socket', side_effect=AssertionError('network')):
                r=league(evidence(code))
            self.assertEqual(set(r['markets']), {'corners','btts','goal_totals'})
            for m in r['markets'].values():
                self.assertEqual(m['gates']['calendar']['state'],'VALID')
                self.assertEqual(m['gates']['coverage']['prior_matches'],1)
                self.assertEqual(m['gates']['history']['state'],'VALID')
                self.assertEqual(m['input_state'],'UNSUPPORTED')
                self.assertFalse(m['validated']);self.assertFalse(m['live'])

    def test_partial_or_mislabelled_round_never_complete(self):
        for change in ('partial','round','duplicate','pair'):
            c,b,h=sample()
            if change=='partial':b['matches'].pop()
            if change=='round':b['matches'][0]['matchday']=2
            if change=='duplicate':b['matches'][1]['fixture_id']=b['matches'][0]['fixture_id']
            if change=='pair':b['matches'][1].update({k:b['matches'][0][k] for k in ('home_id','home_name','away_id','away_name')})
            r=league(evidence(bundle=b))
            self.assertEqual(r['markets']['btts']['gates']['calendar']['state'],'INVALID',change)

    def test_mapping_ambiguity_and_cross_league_scope(self):
        for key in ('source_team_id','source_name','history_name'):
            c,b,h=sample();c['team_bindings'][1][key]=c['team_bindings'][0][key].upper()
            self.assertEqual(league(evidence(contract=c))['markets']['btts']['gates']['calendar']['state'],'INVALID')
        e=evidence()
        for changed in (replace(e,tournament_id=23),replace(e,season_year=2025),replace(e,stage='PLAYOFF')):
            self.assertEqual(league(changed)['markets']['btts']['gates']['calendar']['state'],'INVALID')

    def test_corner_missing_or_invalid_does_not_block_goals(self):
        c,b,h=sample()
        for replacement in (b'D,,,2,7', b'D,-1,0,2,7'):
            r=league(evidence(history=h.replace(b'D,0,0,2,7',replacement)))
            self.assertNotEqual(r['markets']['corners']['gates']['coverage']['state'],'VALID')
            for m in ('btts','goal_totals'):
                self.assertEqual(r['markets'][m]['gates']['coverage']['state'],'VALID')
                self.assertEqual(r['markets'][m]['gates']['history']['validation_scope'],'GOALS')

    def test_invalid_missing_goals_and_ftr_rejected(self):
        c,b,h=sample()
        for values in (b',,0,D,',b',-1,0,D,',b',0,0,H,'):
            r=league(evidence(history=h.replace(b',0,0,D,',values)))
            for m in r['markets'].values():self.assertEqual(m['gates']['history']['state'],'INVALID')

    def test_calendar_scores_and_history_reconcile_both_ways(self):
        c,b,h=sample();b['matches'][0]['goals']=[1,0]
        self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['coverage']['reason'],'HISTORY_CALENDAR_SCORE_CONFLICT')
        for hist in (h.splitlines(keepends=True)[0], h+h.splitlines(keepends=True)[1].replace(b'20/10',b'19/10')):
            self.assertNotEqual(league(evidence(history=hist))['markets']['btts']['gates']['coverage']['state'],'VALID')

    def test_provenance_stale_unknown_future_and_pins(self):
        for timestamp,state in ((None,'UNKNOWN'),((NOW-timedelta(hours=7)).isoformat(),'STALE'),
                                 ((NOW+timedelta(seconds=1)).isoformat(),'INVALID')):
            c,b,h=sample();b['retrieved_at']=timestamp
            self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['calendar']['state'],state)
        for e in (replace(evidence(),calendar_sha256='0'*64),replace(evidence(),mapping_sha256='0'*64)):
            self.assertEqual(league(e)['markets']['btts']['gates']['calendar']['state'],'INVALID')
        for e in (evidence(provenance='UNKNOWN'),evidence(retrieved=None)):
            self.assertEqual(league(e)['markets']['btts']['gates']['history']['state'],'UNKNOWN')
        _,_,old_history=sample()
        old_history=old_history.replace(b'20/10/2026',b'01/10/2026')
        self.assertEqual(league(evidence(history=old_history,retrieved=NOW-timedelta(days=15)))['markets']['btts']['gates']['history']['state'],'STALE')

    def test_bad_source_hash_names_stage_and_result_types(self):
        for change in ('hash','name','stage','bool_score','aet','scope'):
            c,b,h=sample()
            if change=='hash':b['source_sha256']='0'*64
            if change=='name':b['matches'][0]['home_name']='wrong'
            if change=='stage':b['stage']='PLAYOFF'
            if change=='bool_score':b['matches'][0]['goals']=[True,0]
            if change=='aet':b['matches'][0]['status']='AET'
            if change=='scope':b['source_competition_id']='other'
            self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['calendar']['state'],'INVALID',change)

    def test_prior_unresolved_is_blocked_not_empty(self):
        c,b,h=sample();b['matches'][0].update(status='POSTPONED',goals=None)
        self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['calendar']['reason'],'CALENDAR_PRIOR_UNRESOLVED')

    def test_local_day_and_same_day_results_not_history(self):
        c,b,h=sample();b['matches'][0]['kickoff']='2026-10-19T23:30:00Z'
        self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['coverage']['state'],'VALID')
        b['matches'][0]['kickoff']='2026-10-22T08:00:00Z'
        e=evidence(bundle=b,history=h.splitlines(keepends=True)[0])
        self.assertEqual(league(e)['markets']['btts']['gates']['coverage']['prior_matches'],0)

    def test_unknown_input_and_malformed_json_fail_closed(self):
        for data in (b'{}',b'{"x":1,"x":2}',b'invalid'):
            e=replace(evidence(),calendar=data,calendar_sha256=fingerprint(data))
            self.assertEqual(league(e)['markets']['btts']['gates']['calendar']['state'],'INVALID')
        result=report({},as_of=NOW)
        self.assertEqual(len(result['leagues']),5)
        for changed in (replace(evidence(),mapping=None),replace(evidence(),calendar=None)):
            self.assertEqual(league(changed)['markets']['btts']['gates']['calendar']['state'],'MISSING')

    def test_history_duplicates_wrong_div_and_future_receipt(self):
        c,b,h=sample()
        for data in (h+h.splitlines(keepends=True)[1],h.replace(b'SP1,',b'I1,')):
            self.assertEqual(league(evidence(history=data))['markets']['btts']['gates']['history']['state'],'INVALID')
        self.assertEqual(league(evidence(retrieved=NOW+timedelta(seconds=1)))['markets']['btts']['gates']['history']['state'],'INVALID')

    def test_season_bounds_timezone_and_future_finished_fixture(self):
        c,b,h=sample();c['timezone']='Europe/London'
        self.assertEqual(league(evidence(contract=c))['markets']['btts']['gates']['calendar']['state'],'INVALID')
        for field,value in (('season_end','2028-05-31'),('season_start','2026-11-01')):
            c,b,h=sample();b[field]=value
            self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['calendar']['state'],'INVALID')
        c,b,h=sample();b['matches'][0]['kickoff']='2026-10-23T18:00:00Z'
        self.assertEqual(league(evidence(bundle=b))['markets']['btts']['gates']['calendar']['state'],'INVALID')

    def test_quotes_use_shared_normalization_without_qualification(self):
        from tests.oddspapi_inventory_fixtures import NOW as at, metadata
        from tests.test_oddspapi_saved_response import saved_fixture
        payload=encoded([saved_fixture(8,'synthetic-sp1'),saved_fixture(23,'synthetic-i1')])
        meta=encoded(metadata())
        quotes=SavedQuotes(payload,fingerprint(payload),meta,fingerprint(meta),at)
        for q,state in ((quotes,'AVAILABLE'),(replace(quotes,payload_sha256='0'*64),'INVALID'),
                        (replace(quotes,retrieved_at=at-timedelta(hours=7)),'STALE')):
            result=report({},as_of=at,quotes=q)
            for r in result['leagues']:
                if r['competition'] not in ('SP1','I1'):continue
                for m in r['markets'].values():
                    self.assertEqual(m['gates']['quotes']['state'],state)
                    self.assertEqual(m['input_state'],'UNSUPPORTED')
                    self.assertFalse(m['validated'])

    def test_e1_e0_and_mls_rows_unchanged(self):
        e,now=e0();inputs={'E0':e,'E1':e1()}
        base=shared_report(inputs,as_of=now);extended=report(inputs,as_of=now)
        for code in ('E1','E0','MLS'):
            self.assertEqual(next(r for r in base['leagues'] if r['competition']==code),
                             next(r for r in extended['leagues'] if r['competition']==code))

    def test_deterministic_reordered_calendar_and_no_self_attestation(self):
        c,b,h=sample();first=league(evidence(bundle=b,provenance='SUPPLIED'))
        b['matches'].reverse();second=league(evidence(bundle=b,provenance='SUPPLIED'))
        self.assertEqual(first['markets']['corners']['gates']['coverage'],second['markets']['corners']['gates']['coverage'])
        e=evidence();self.assertEqual(encoded(league(e)),encoded(league(e)))
        self.assertFalse(first['markets']['btts']['gates']['calendar']['normalization_authenticated'])


if __name__=='__main__':unittest.main()
