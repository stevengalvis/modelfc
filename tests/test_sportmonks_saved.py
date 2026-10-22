"""Entirely synthetic seasons, stages, teams and statistics; no entitlement evidence."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import unittest

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.providers.sportmonks_saved import LEAGUES, SavedPage, assess, normalize

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def contract(competition='DK_SUPERLIGA'):
    league, zone = LEAGUES[competition]
    return dict(version=1, provider='sportmonks', competition=competition, league_id=league,
        season_id=999001, season_start='2026-07-01', season_end='2027-06-30', timezone=zone,
        stages=[dict(id=99001, name='Synthetic regular', phase='REGULAR_SEASON', group_ids=[None]),
                dict(id=99002, name='Synthetic split', phase='SPLIT_GROUP', group_ids=[99003]),
                dict(id=99004, name='Synthetic playoff', phase='PLAYOFF', group_ids=[None])],
        team_bindings=[dict(provider_team_id=i, provider_name=n, history_name=n)
                       for i, n in [(99101, 'Synthetic Alpha'), (99102, 'Synthetic Beta')]],
        statistics_scope='FT_90_REGULATION', provenance='SYNTHETIC')


def fixture(competition='DK_SUPERLIGA'):
    return dict(id=999101, league_id=LEAGUES[competition][0], season_id=999001,
        stage_id=99001, group_id=None, starting_at='2026-10-20 18:00:00',
        starting_at_timestamp=int(datetime(2026,10,20,18,tzinfo=timezone.utc).timestamp()),
        state={'short_name':'FT'}, length=90,
        participants=[dict(id=i, name=n, meta={'location':side}) for i,n,side in
                      [(99101,'Synthetic Alpha','home'), (99102,'Synthetic Beta','away')]],
        scores=[dict(participant_id=i, description='CURRENT', score={'goals':0,'participant':side})
                for i,side in [(99101,'home'),(99102,'away')]],
        statistics=[dict(participant_id=i,type_id=34,location=side,data={'value':0})
                    for i,side in [(99101,'home'),(99102,'away')]])


def page(rows, number=1, more=False, retrieved=NOW):
    raw=encoded({'data':rows,'pagination':{'current_page':number,'has_more':more}})
    return SavedPage(raw,fingerprint(raw),retrieved)


def run(c=None, rows=None, pages=None, **kwargs):
    c=contract() if c is None else c
    raw=encoded(c)
    return assess(c['competition'],contract_raw=raw,contract_sha256=fingerprint(raw),
        pages=(page([fixture(c['competition'])] if rows is None else rows),) if pages is None else pages,
        as_of=NOW, **kwargs)


class SavedSportmonksTests(unittest.TestCase):
    def test_both_leagues_zero_and_independent_markets(self):
        for competition in LEAGUES:
            with self.subTest(competition=competition):
                c=contract(competition); row=fixture(competition)
                report=run(c,[row]); self.assertEqual(report['state'],'OBSERVED_ONLY')
                self.assertEqual(report['fixtures'][0]['goals'],(0,0))
                self.assertEqual(report['fixtures'][0]['corners'],(0,0))
                for market in report['markets'].values():
                    self.assertEqual(market['history']['complete_fixtures'],1)
                    self.assertEqual(market['model'],'UNQUALIFIED')
                    self.assertFalse(market['live']); self.assertFalse(market['validated'])
                row['statistics']=[]
                report=run(c,[row])
                self.assertEqual(report['markets']['corners']['history']['state'],'MISSING')
                self.assertEqual(report['markets']['btts']['history']['complete_fixtures'],1)
                row['statistics']=fixture(competition)['statistics']; row['scores']=None
                report=run(c,[row])
                self.assertEqual(report['markets']['corners']['history']['complete_fixtures'],1)
                self.assertEqual(report['markets']['goal_totals']['history']['state'],'MISSING')

    def test_explicit_phases(self):
        for stage,group,phase in [(99001,None,'REGULAR_SEASON'),(99002,99003,'SPLIT_GROUP'),(99004,None,'PLAYOFF')]:
            row=fixture(); row.update(stage_id=stage,group_id=group)
            self.assertEqual(run(rows=[row])['fixtures'][0]['phase'],phase)
        row['group_id']=99003
        self.assertEqual(run(rows=[row])['reason'],'FIXTURE_PHASE_REJECTED')

    def test_identity_and_malformed_rejections(self):
        mutations=[lambda r:r.update(league_id=9),lambda r:r.update(season_id=True),
            lambda r:r.update(stage_id=1), lambda r:r.update(group_id=False),
            lambda r:r.update(starting_at_timestamp=1),lambda r:r.update(length=120),
            lambda r:r['participants'][0].update(name='Other'),
            lambda r:r['participants'][0].update(id=True),
            lambda r:r.update(season={'id':999001,'league_id':9}),
            lambda r:r.update(stage={'id':99001,'name':'Other'}),
            lambda r:r.update(scores='invalid'),lambda r:r.update(statistics={}),
            lambda r:r['scores'][0].update(description='EXTRA_TIME'),
            lambda r:r['statistics'][0].update(period_id=1),
            lambda r:r['scores'][0]['score'].update(goals=True),
            lambda r:r['scores'][0]['score'].update(participant='away'),
            lambda r:r['statistics'][0]['data'].update(value=-1)]
        for mutate in mutations:
            row=fixture(); mutate(row)
            with self.subTest(row=row): self.assertEqual(run(rows=[row])['state'],'BLOCKED')
        self.assertEqual(run(rows=[fixture(),fixture()])['reason'],'FIXTURE_DUPLICATE_OR_INVALID')
        second=fixture(); second['id']+=1
        self.assertEqual(run(rows=[fixture(),second])['reason'],'FIXTURE_IDENTITY_DUPLICATE')

    def test_null_partial_duplicate(self):
        for field,valuekey in [('scores','score'),('statistics','data')]:
            row=fixture(); row[field][0][valuekey]['goals' if field=='scores' else 'value']=None
            self.assertEqual(run(rows=[row])['state'],'BLOCKED')
            row[field][1][valuekey]['goals' if field=='scores' else 'value']=None
            self.assertEqual(run(rows=[row])['state'],'OBSERVED_ONLY')
            row[field].append(deepcopy(row[field][0]))
            self.assertEqual(run(rows=[row])['state'],'BLOCKED')

    def test_nonregulation_and_same_day_never_history(self):
        for status in ('AET','FT_PEN','ABAN','SUSP','NS'):
            row=fixture(); row['state']['short_name']=status
            report=run(rows=[row])
            self.assertIsNone(report['fixtures'][0]['goals'])
            self.assertEqual(report['markets']['btts']['history']['complete_fixtures'],0)
        row=fixture(); row.update(starting_at='2026-10-22 08:00:00',starting_at_timestamp=int(NOW.replace(hour=8).timestamp()))
        self.assertEqual(run(rows=[row])['markets']['btts']['history']['complete_fixtures'],0)

    def test_unknown_stale_and_pin(self):
        row=fixture()
        self.assertEqual(run(pages=(page([row],retrieved=None),))['calendar_freshness'],'UNKNOWN')
        c=contract(); c['provenance']='UNKNOWN'
        self.assertEqual(run(c)['markets']['btts']['history']['state'],'UNKNOWN')
        row.update(starting_at='2026-09-01 18:00:00',starting_at_timestamp=int(datetime(2026,9,1,18,tzinfo=timezone.utc).timestamp()))
        self.assertEqual(run(rows=[row])['markets']['btts']['history']['state'],'STALE')
        self.assertEqual(run(pages=(page([row],retrieved=NOW-timedelta(days=16)),))['markets']['btts']['history']['reason'],'HISTORY_CAPTURE_STALE')
        p=page([fixture()])
        self.assertEqual(run(pages=(SavedPage(p.raw,'0'*64,NOW),))['reason'],'RESPONSE_PIN_REJECTED')
        self.assertEqual(run(pages=(page([fixture()],retrieved=NOW+timedelta(seconds=1)),))['reason'],'RETRIEVAL_IN_FUTURE')

    def test_local_date_freshness_boundary_and_unknown_statistic(self):
        row=fixture(); row.update(starting_at='2026-10-07 18:00:00',
            starting_at_timestamp=int(datetime(2026,10,7,18,tzinfo=timezone.utc).timestamp()))
        observed=datetime(2026,10,21,23,tzinfo=timezone.utc)
        raw=encoded(contract())
        report=assess('DK_SUPERLIGA',contract_raw=raw,contract_sha256=fingerprint(raw),
            pages=(page([row],retrieved=observed),),as_of=observed)
        self.assertEqual(report['markets']['btts']['history']['reason'],'LATEST_RESULT_STALE')
        row=fixture()
        for stat in row['statistics']: stat['type_id']=9999
        self.assertEqual(run(rows=[row])['markets']['corners']['history']['state'],'MISSING')

    def test_missing_and_incomplete_pagination(self):
        self.assertEqual(assess('DK_SUPERLIGA',as_of=NOW)['reason'],'REVIEWED_CONTRACT_MISSING')
        self.assertEqual(run(pages=())['reason'],'SAVED_PAGES_MISSING')
        self.assertEqual(run(pages=(page([fixture()],more=True),))['reason'],'PAGINATION_INCOMPLETE')
        self.assertEqual(run(pages=(page([],number=2),))['reason'],'PAGINATION_REJECTED')

    def test_determinism_immutable_provenance_and_counts(self):
        row=fixture(); second=fixture(); second.update(id=999102,starting_at='2026-10-21 18:00:00',starting_at_timestamp=row['starting_at_timestamp']+86400)
        pages=(page([row],1,True),page([second],2,False))
        before=deepcopy(pages)
        self.assertEqual(encoded(run(pages=pages)),encoded(run(pages=pages[::-1])))
        self.assertEqual(pages,before)
        report=run(pages=pages)
        self.assertEqual(report['fixtures'][0]['source_sha256'],pages[0].sha256)
        self.assertEqual(report['markets']['btts']['history']['team_venue_samples'][0]['home'],2)

    def test_supplied_index_never_proves_completeness(self):
        report=run(); row=report['fixtures'][0]
        keys=('fixture_id','stage_id','group_id','kickoff_utc','home_provider_id','away_provider_id')
        index=dict(competition='DK_SUPERLIGA',season_id=999001,source_reference='Synthetic index',fixtures=[{k:row[k] for k in keys}])
        def indexed():
            raw=encoded(index)
            return run(calendar_index_raw=raw,calendar_index_sha256=fingerprint(raw))
        result=indexed()
        self.assertEqual(result['calendar_completeness'],'NOT_ESTABLISHED')
        self.assertEqual(result['markets']['btts']['calendar']['state'],'MATCHED_SUPPLIED_INDEX')
        self.assertEqual(result['markets']['btts']['input_state'],'BLOCKED')
        index['fixtures'][0]['fixture_id']+=1
        self.assertEqual(indexed()['markets']['btts']['calendar']['missing_fixtures'],1)
        index['fixtures']*=2
        self.assertEqual(indexed()['reason'],'CALENDAR_INDEX_REJECTED')

    def test_season_boundaries_and_contract_identity(self):
        c=contract(); c['season_end']='2026-09-30'
        self.assertEqual(run(c)['reason'],'FIXTURE_OUTSIDE_SEASON')
        c=contract(); c['league_id']=501
        self.assertEqual(run(c)['reason'],'CONTRACT_IDENTITY_REJECTED')
        c=contract(); c['stages'][1]['group_ids']=[None]
        self.assertEqual(run(c)['reason'],'GROUP_CONTRACT_REJECTED')
        c=contract(); c['team_bindings'][1]=c['team_bindings'][0]
        self.assertEqual(run(c)['reason'],'TEAM_CONTRACT_REJECTED')

if __name__ == '__main__': unittest.main()
