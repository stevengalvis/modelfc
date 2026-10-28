"""Synthetic native responses and mappings; no authentic data or provider calls."""
from datetime import timedelta
import json
from unittest import TestCase
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.five_league_readiness import report
from modelfc.providers.football_data_org_saved import normalize, COMPETITIONS
from modelfc.spain_italy_readiness import CalendarRejected
from tests.test_pl_calendar import synthetic, NOW


def inputs(code='SP1'):
    old, payload = synthetic()
    native_code, native_id = COMPETITIONS[code]
    payload['competition'].update(code=native_code, id=native_id)
    for row in payload['matches']:
        row['competition'].update(code=native_code, id=native_id)
        row['season']['id'] = 900001
        row['score'].update(duration='REGULAR', winner=None)
    payload['matches'][0].update(utcDate='2026-10-16T14:00:00Z', status='FINISHED')
    payload['matches'][0]['score'].update(fullTime={'home':0,'away':0},winner='DRAW')
    payload['resultSet']['played'] = 1
    mapping = {'version':1,'competition':code,'source':'football-data.org',
        'source_competition_id':str(native_id),'source_season_id':'900001','season_year':2026,
        'timezone':'Europe/Madrid' if code=='SP1' else 'Europe/Rome',
        'team_bindings':[{**t,'source_team_id':str(t['source_team_id'])} for t in old['team_bindings']]}
    return payload, mapping


def convert(payload=None, mapping=None, code='SP1', **kwargs):
    p,m = inputs(code)
    raw=payload if isinstance(payload,bytes) else json.dumps(p if payload is None else payload,ensure_ascii=False,indent=2).encode()
    contract=mapping if isinstance(mapping,bytes) else encoded(m if mapping is None else mapping)
    args=dict(source_sha256=fingerprint(raw), retrieved_at=NOW, mapping=contract,
        mapping_sha256=fingerprint(contract), competition=code, season_year=2026,
        selected_day=NOW.date(), as_of=NOW, provenance='SYNTHETIC')
    args.update(kwargs)
    return normalize(raw,**args)


def cal(e, at=NOW):
    r=report({e.competition:e},as_of=at,spain_italy=True)
    return next(r for r in r['leagues'] if r['competition']==e.competition)['markets']['btts']


class NativeSavedTests(TestCase):
    def test_both_native_identities_exact_bytes_receipts_and_roster(self):
        for code in COMPETITIONS:
            p,m=inputs(code)
            with patch('socket.socket',side_effect=AssertionError('network')):
                e=convert(p,m,code)
                market=cal(e)
            b=json.loads(e.calendar)
            raw=json.dumps(p,ensure_ascii=False,indent=2).encode()
            self.assertEqual(b['source_payload'].encode(),raw)
            self.assertEqual(b['source_sha256'],fingerprint(raw))
            self.assertEqual(b['retrieved_at'],NOW.isoformat())
            self.assertEqual(e.mapping,encoded(m))
            self.assertEqual(len(b['matches']),380)
            self.assertEqual(b['matches'][0]['goals'],[0,0])
            self.assertEqual(market['gates']['calendar']['state'],'VALID')
            self.assertEqual(market['input_state'],'UNSUPPORTED')
            self.assertFalse(market['validated']); self.assertFalse(market['live'])

    def test_partial_and_filtered_response_cannot_be_full_season(self):
        for variant in ('partial','count','played','status','dateFrom','matchday','pagination'):
            p,m=inputs()
            if variant=='partial':p['matches'].pop();p['resultSet']['count']=379
            elif variant in ('count','played'):p['resultSet'][variant]=True
            elif variant=='pagination':p['pagination']={'has_more':False}
            else:p['filters'][variant]='synthetic-filter'
            with self.subTest(variant=variant),self.assertRaises(CalendarRejected):convert(p,m)

    def test_wrong_namespace_competition_season_and_stage(self):
        for variant in ('top','row','season','mapping','source','stage','bounds'):
            p,m=inputs()
            if variant=='top':p['competition']['id']=564
            if variant=='row':p['matches'][0]['competition']['code']='SA'
            if variant=='season':p['matches'][0]['season']['id']=True
            if variant=='mapping':m['source_season_id']='900002'
            if variant=='source':m['source']='sportmonks'
            if variant=='stage':p['matches'][0]['stage']='PLAYOFFS'
            if variant=='bounds':p['matches'][0]['season']['endDate']='2027-06-01'
            with self.subTest(variant=variant),self.assertRaises(CalendarRejected):convert(p,m)
        with self.assertRaises(CalendarRejected):convert(competition='E0')

    def test_missing_team_ids_name_conflicts_and_ambiguous_aliases(self):
        for variant in ('missing','bool','name','alias','case','roster','leading_zero'):
            p,m=inputs()
            if variant=='missing':del p['matches'][0]['homeTeam']['id']
            if variant=='bool':p['matches'][0]['homeTeam']['id']=True
            if variant=='name':p['matches'][0]['homeTeam']['name']='Unreviewed alias'
            if variant=='alias':m['team_bindings'][1]['history_name']=m['team_bindings'][0]['history_name']
            if variant=='case':m['team_bindings'][1]['source_name']=m['team_bindings'][0]['source_name'].upper()
            if variant=='roster':m['team_bindings'].pop()
            if variant=='leading_zero':m['team_bindings'][0]['source_team_id']='01000'
            with self.subTest(variant=variant),self.assertRaises(CalendarRejected):convert(p,m)

    def test_duplicate_fixture_pair_and_rounds(self):
        for variant in ('id','pair','round'):
            p,m=inputs();a,b=p['matches'][:2]
            if variant=='id':b['id']=a['id']
            if variant=='pair':b['homeTeam']=a['homeTeam'];b['awayTeam']=a['awayTeam']
            if variant=='round':a['matchday']=2
            with self.subTest(variant=variant),self.assertRaises(CalendarRejected):convert(p,m)

    def test_regulation_scores_and_unrepresentable_states(self):
        for variant in ('aet','penalty','missingduration','boolgoal','missinggoal','winner','future','active','awarded','unfinishedscore'):
            p,m=inputs();r=p['matches'][0]
            if variant=='aet':r['score']['duration']='EXTRA_TIME'
            if variant=='penalty':r['score']['duration']='PENALTY_SHOOTOUT'
            if variant=='missingduration':del r['score']['duration']
            if variant=='boolgoal':r['score']['fullTime']['home']=True
            if variant=='missinggoal':r['score']['fullTime']['home']=None
            if variant=='winner':r['score']['winner']='HOME_TEAM'
            if variant=='future':r['utcDate']='2026-10-18T14:00:00Z'
            if variant=='active':r['status']='IN_PLAY'
            if variant=='awarded':r['status']='AWARDED'
            if variant=='unfinishedscore':r['status']='TIMED'
            with self.subTest(variant=variant),self.assertRaises(CalendarRejected):convert(p,m)

    def test_stale_unknown_and_future_receipts_are_separate_from_structure(self):
        old=NOW-timedelta(hours=7)
        e=convert(retrieved_at=old)
        self.assertEqual(json.loads(e.calendar)['retrieved_at'],old.isoformat())
        self.assertEqual(cal(e)['gates']['calendar']['state'],'STALE')
        e=convert(retrieved_at=None)
        self.assertIsNone(json.loads(e.calendar)['retrieved_at'])
        self.assertEqual(cal(e)['gates']['calendar']['state'],'UNKNOWN')
        self.assertEqual(cal(convert(provenance='UNKNOWN'))['gates']['calendar']['state'],'UNKNOWN')
        with self.assertRaises(CalendarRejected):convert(retrieved_at=NOW+timedelta(seconds=1))

    def test_pin_json_and_bounds_fail_closed(self):
        for kwargs in ({'source_sha256':'0'*64},{'mapping_sha256':'0'*64},{'selected_day':NOW.date().replace(year=2025)},
                       {'retrieved_at':NOW.replace(tzinfo=None)}):
            with self.assertRaises(CalendarRejected):convert(**kwargs)
        for bad_mapping in (b'bad', b'{"version":1,"version":1}', b'[]'):
            with self.assertRaises(CalendarRejected):convert(mapping=bad_mapping)
        for raw in (b'{}',b'{"filters":{},"filters":{}}',b'\xff',b'[]'):
            with self.assertRaises(CalendarRejected):convert(payload=raw)
        with patch('modelfc.providers.football_data_org_saved.MAX_CALENDAR_BYTES',100),self.assertRaises(CalendarRejected):
            convert()

    def test_local_day_and_determinism(self):
        p,m=inputs();p['matches'][0]['utcDate']='2026-10-15T23:30:00Z'
        e=convert(p,m)
        self.assertEqual(e,convert(p,m))
        self.assertEqual(json.loads(e.calendar)['matches'][0]['kickoff'],'2026-10-15T23:30:00Z')


# Synthetic season-policy tests. Official policy identifiers are configuration,
# not a claim that these generated team/fixture records are genuine.
def policy_inputs():
    p, m = inputs('I1')
    m.update(source_season_id='2494', season_calendar_policy='SA_2026_CU200_CU208')
    for row in p['matches']:
        row['season'].update(id=2494, startDate='2026-08-23', endDate='2027-05-30')
    p['matches'][0]['utcDate'] = '2026-08-22T16:30:00Z'
    return p, m


class SeasonPolicyTests(TestCase):
    def test_explicit_opt_in_preserves_native_metadata_and_fresh_response_identity(self):
        p, m = policy_inputs()
        absent = dict(m); absent.pop('season_calendar_policy')
        with self.assertRaisesRegex(CalendarRejected, 'FIXTURE_TIME_REJECTED'):
            convert(p, absent, 'I1')
        e = convert(p, m, 'I1')
        b = json.loads(e.calendar)
        self.assertEqual(b['season_start'], '2026-08-23')
        self.assertEqual(json.loads(b['source_payload']), p)
        gate = cal(e)['gates']['calendar']
        self.assertEqual(gate['season_calendar_policy']['kickoff_start'], '2026-08-22')
        self.assertEqual(gate['season_calendar_policy']['kickoff_end'], '2027-05-30')
        p['matches'][10]['utcDate'] = '2027-05-02T14:00:00Z'
        fresh = convert(p, m, 'I1', retrieved_at=NOW+timedelta(minutes=1), as_of=NOW+timedelta(minutes=1))
        self.assertNotEqual(e.calendar_sha256, fresh.calendar_sha256)
        self.assertEqual(json.loads(fresh.calendar)['retrieved_at'], (NOW+timedelta(minutes=1)).isoformat())
        self.assertEqual(cal(fresh, NOW+timedelta(minutes=1))['gates']['calendar']['season_calendar_policy'], gate['season_calendar_policy'])
        self.assertFalse(cal(e)['validated']); self.assertFalse(cal(e)['live'])

    def test_policy_scope_and_unknown_policy_fail_closed(self):
        for key, value in (('season_calendar_policy', None), ('season_calendar_policy', {}),
                           ('season_calendar_policy', 'UNREVIEWED'), ('source', 'other'),
                           ('source_competition_id', '2014'), ('timezone', 'UTC'),
                           ('season_year', 2025), ('source_season_id', '2495')):
            p, m = policy_inputs(); m[key] = value
            if key == 'source_season_id':
                for row in p['matches']: row['season']['id'] = 2495
            with self.subTest(key=key, value=value), self.assertRaises(CalendarRejected):
                convert(p, m, 'I1')
        p, m = inputs('SP1'); m['season_calendar_policy'] = 'SA_2026_CU200_CU208'
        with self.assertRaises(CalendarRejected): convert(p, m)

    def test_exact_local_interval_and_native_consistency(self):
        for kickoff in ('2026-08-21T21:59:59Z', '2027-05-30T22:00:00Z'):
            p, m = policy_inputs(); p['matches'][1]['utcDate'] = kickoff
            with self.subTest(kickoff=kickoff), self.assertRaisesRegex(CalendarRejected, 'FIXTURE_TIME_REJECTED'):
                convert(p, m, 'I1')
        p, m = policy_inputs(); p['matches'][0]['utcDate'] = '2026-08-21T22:00:00Z'
        p['matches'][1]['utcDate'] = '2027-05-30T21:59:59Z'
        convert(p, m, 'I1')
        p['matches'][1]['season']['startDate'] = '2026-08-22'
        with self.assertRaisesRegex(CalendarRejected, 'SOURCE_SEASON_REJECTED'): convert(p, m, 'I1')

    def test_policy_does_not_override_other_gates(self):
        p, m = policy_inputs()
        self.assertEqual(cal(convert(p, m, 'I1', provenance='UNKNOWN'))['gates']['calendar']['state'], 'UNKNOWN')
        self.assertEqual(cal(convert(p, m, 'I1', retrieved_at=NOW-timedelta(hours=7)))['gates']['calendar']['state'], 'STALE')
        row = p['matches'][0]
        row['status'] = 'CANCELLED'; row['score'].update(fullTime={'home':None,'away':None},winner=None)
        p['resultSet']['played'] = 0
        e = convert(p, m, 'I1')
        self.assertIsNone(json.loads(e.calendar)['matches'][0]['goals'])
        self.assertEqual(cal(e)['gates']['calendar']['reason'], 'CALENDAR_PRIOR_UNRESOLVED')

    def test_cancelled_placeholder_is_never_a_played_draw(self):
        for code in COMPETITIONS:
            p, m = inputs(code)
            row = p['matches'][0]
            row['status'] = 'CANCELLED'
            row['score']['halfTime'] = {'home':None, 'away':None}
            p['resultSet']['played'] = 0
            # Exact score shape observed in PD564698, with synthetic identities.
            with self.assertRaisesRegex(CalendarRejected, 'UNFINISHED_SCORE_REJECTED'):
                convert(p, m, code)
