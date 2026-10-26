"""Synthetic saved inputs only; no authentic league qualification."""
from dataclasses import replace
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.five_league_readiness import CONTRACTS, Evidence, SavedQuotes, report, _history, LeagueContract
from tests import test_pl_history_readiness as pl_tests
from tests.test_history_completeness import bundle, raw, histories
from tests.oddspapi_inventory_fixtures import NOW, metadata
from tests.test_oddspapi_saved_response import saved_fixture
from tests.test_mls import HEADER as MLS_HEADER, row as mls_row


def pinned_sources(rows, now=NOW):
    return tuple((n, b, fingerprint(b), now) for n, b in rows)


def e1():
    b = raw(bundle())
    # The coverage adapter requires FTR; preserve the existing underlying values.
    rows = tuple((n, b.replace(b'FTAG,HC', b'FTAG,FTR,HC').replace(b',2,1,5,4', b',2,1,H,5,4'))
                 for n, b in histories())
    return Evidence('E1', 18, 2026, date(2026, 10, 9), calendar=b, calendar_sha256=fingerprint(b),
                    sources=pinned_sources(rows), provenance='SYNTHETIC')


def e0():
    fixture = pl_tests.PLHistoryTests(); fixture.setUp()
    b, opts = fixture.options()
    return Evidence('E0', 17, 2026, opts['selected_day'], calendar=b, calendar_sha256=fingerprint(b),
        mapping=opts['contract_raw'], mapping_sha256=opts['reviewed_contract_sha256'],
        sources=tuple(fixture.sources), provenance='SYNTHETIC'), opts['as_of']


def market(value, code, kind='corners'):
    return next(r for r in value['leagues'] if r['competition'] == code)['markets'][kind]


class FiveLeagueTests(unittest.TestCase):
    def test_missing_exactly_five_not_empty(self):
        r = report({}, as_of=NOW)
        self.assertEqual([(v['competition'], v['tournament_id']) for v in r['leagues']],
                         [('E1', 18), ('E0', 17), ('SP1', 8), ('I1', 23), ('MLS', 242)])
        self.assertEqual(r['max_tournaments'], 5)
        for row in r['leagues']:
            for m in row['markets'].values():
                self.assertEqual(m['gates']['calendar']['state'], 'MISSING')
                self.assertFalse(m['validated']); self.assertFalse(m['live'])

    def test_e1_contract_positive_both_no_network_no_persistence(self):
        with patch('socket.socket', side_effect=AssertionError('network')), patch(
            'modelfc.acquisition_storage.Store.put', side_effect=AssertionError('store')), patch(
            'modelfc.ledger_storage.write_new_record', side_effect=AssertionError('ledger')):
            r = report({'E1': e1()}, as_of=NOW)
        for m in ('corners', 'btts'):
            self.assertEqual(market(r, 'E1', m)['input_state'], 'ELIGIBLE', r)
        self.assertEqual(market(r, 'E1')['gates']['history']['reason'], 'HISTORY_RESULTS_STALE')
        self.assertEqual(market(r, 'E1')['gates']['model']['reason'], 'E1_CONTRACT_PASSED')

    def test_e0_exact_zen56_contract_and_mixed_report(self):
        e, now = e0()
        r = report({'E0': e, 'I1': Evidence('I1', 23, 2026, now.date()),
                    'MLS': Evidence('MLS', 242, 2026, now.date())}, as_of=now)
        self.assertEqual(market(r, 'E0')['input_state'], 'ELIGIBLE', r)
        self.assertEqual(market(r, 'E0', 'btts')['input_state'], 'UNSUPPORTED')
        self.assertEqual(market(r, 'MLS')['input_state'], 'UNSUPPORTED')
        fixture = pl_tests.PLHistoryTests(); fixture.setUp()
        self.assertEqual(next(v for v in r['leagues'] if v['competition'] == 'E0')['zen56'], fixture.report())

    def test_e1_btts_can_pass_while_corner_venue_blocks(self):
        e = e1(); changed = []
        for name, data, _, observed in e.sources:
            if name == 'E1_2526.csv':
                rows = data.splitlines(keepends=True)
                data = b''.join(rows[:4] + [r.replace(b'West Ham', b'Burnley').replace(b'QPR', b'Cardiff') for r in rows[4:]])
            changed.append((name, data, fingerprint(data), observed))
        r = report({'E1': replace(e, sources=tuple(changed))}, as_of=NOW)
        self.assertEqual(market(r, 'E1')['input_state'], 'BLOCKED')
        self.assertEqual(market(r, 'E1', 'btts')['input_state'], 'ELIGIBLE', r)

    def test_invalid_unknown_and_stale_sources_fail_closed(self):
        for kind in ('pin', 'unknown', 'future', 'stale', 'malformed'):
            with self.subTest(kind=kind):
                e = e1(); sources = list(e.sources)
                n, b, pin, at = sources[-1]
                if kind == 'pin': pin = '0'*64
                if kind == 'unknown': at = None
                if kind == 'future': at = NOW+timedelta(seconds=1)
                if kind == 'stale':
                    sources = [(n, b, pin, NOW-timedelta(days=15)) for n,b,pin,_ in sources]
                else:
                    if kind == 'malformed': b = b'bad'; pin = fingerprint(b)
                    sources[-1] = n,b,pin,at
                r = report({'E1': replace(e, sources=tuple(sources))}, as_of=NOW)
                self.assertEqual(market(r, 'E1')['input_state'], 'BLOCKED', r)

    def test_unknown_and_supplied_labels_cannot_validate(self):
        e, now = e0()
        for provenance in ('UNKNOWN', 'SUPPLIED', 'SYNTHETIC'):
            r = report({'E0': replace(e, provenance=provenance)}, as_of=now)
            m = market(r, 'E0')
            self.assertFalse(m['validated']); self.assertFalse(m['live'])
            if provenance == 'UNKNOWN': self.assertEqual(m['input_state'], 'BLOCKED')

    def test_calendar_missing_stale_invalid_and_provider_contradiction(self):
        for code in ('E1', 'E0'):
            e, now = (e1(), NOW) if code == 'E1' else e0()
            for kind, expected in (('missing','MISSING'), ('pin','INVALID'), ('stale','STALE'), ('identity','INVALID')):
                with self.subTest(code=code, kind=kind):
                    if kind == 'missing': changed = replace(e, calendar=None)
                    elif kind == 'pin': changed = replace(e, calendar_sha256='0'*64)
                    else:
                        b = json.loads(e.calendar)
                        if kind == 'stale': b['retrieved_at'] = (now-timedelta(hours=7)).isoformat()
                        else:
                            payload = json.loads(b['payload'])
                            payload['matches'][0]['competition']['id'] = 999
                            b['payload'] = json.dumps(payload)
                        data = raw(b); changed = replace(e, calendar=data, calendar_sha256=fingerprint(data))
                    m = market(report({code: changed}, as_of=now), code)
                    self.assertEqual(m['gates']['calendar']['state'], expected)
                    self.assertNotIn(m['input_state'], {'ELIGIBLE', 'EMPTY'})

    def test_identity_scope_and_stage_cannot_cross_leagues(self):
        for e in (replace(e1(), tournament_id=17), replace(e1(), competition='E0'),
                  replace(e1(), season_year=2025), replace(e1(), stage='PLAYOFFS')):
            self.assertEqual(market(report({'E1': e}, as_of=NOW), 'E1')['input_state'], 'BLOCKED')
        with self.assertRaises(ValueError): report({'D1': e1()}, as_of=NOW)

    def test_valid_empty_is_distinct_from_missing(self):
        e, now = e0()
        b = json.loads(e.calendar); payload = json.loads(b['payload'])
        for m in payload['matches']:
            if m['utcDate'].startswith(e.selected_day.isoformat()): m['utcDate'] = '2026-10-25T14:00:00Z'
        b['payload'] = json.dumps(payload); data = raw(b)
        m = market(report({'E0': replace(e, calendar=data, calendar_sha256=fingerprint(data))}, as_of=now), 'E0')
        self.assertEqual(m['input_state'], 'EMPTY', m)
        self.assertEqual(m['gates']['calendar']['state'], 'EMPTY')
        m = market(report({'E0': replace(e, calendar=data, calendar_sha256=fingerprint(data), sources=())}, as_of=now), 'E0')
        self.assertEqual(m['gates']['calendar']['state'], 'EMPTY')
        self.assertEqual(m['input_state'], 'BLOCKED')

    def test_zero_is_observed_missing_stays_missing_and_bad_stats_invalid(self):
        for stats, expected in (('0,0', 'VALID'), (',', 'MISSING'), ('-1,0', 'INVALID')):
            data = ('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nSP1,07/10/2026,A,B,0,0,D,'+stats+'\n').encode()
            e = Evidence('SP1', 8, 2026, NOW.date(), sources=pinned_sources((('SP1_2627.csv', data),)), provenance='SYNTHETIC')
            r = report({'SP1': e}, as_of=NOW)
            self.assertEqual(market(r, 'SP1')['gates']['history']['state'], expected)
            self.assertEqual(market(r, 'SP1')['input_state'], 'UNSUPPORTED')
            if stats == ',': self.assertEqual(market(r, 'SP1', 'btts')['gates']['history']['state'], 'VALID')

    def test_mls_calendar_year_regular_ft_partial_history_only(self):
        for year, stage, status, expected in (('2022', 'Regular Season', 'FT', 'VALID'),
                ('2022', 'Playoffs', 'FT', 'MISSING'), ('2022', 'Regular Season', 'HT', 'MISSING'),
                ('2021', 'Regular Season', 'FT', 'MISSING')):
            at = NOW.replace(year=2022, month=3, day=30)
            data = (MLS_HEADER + mls_row(year=year, league=year+' MLS', competition=stage,
                 named_date='Tuesday, March 29' if year == '2022' else 'Monday, March 29',
                 status=status, home_corners='0', away_corners='0')).encode()
            e = Evidence('MLS', 242, 2022, at.date(), sources=pinned_sources((('matches.csv', data),), at), provenance='SYNTHETIC')
            m = market(report({'MLS': e}, as_of=at), 'MLS')
            self.assertEqual(m['gates']['history']['state'], expected, m)
            self.assertEqual(m['input_state'], 'UNSUPPORTED')
        e = replace(e, season_year=2021)
        self.assertEqual(market(report({'MLS':e}, as_of=at),'MLS')['gates']['calendar']['reason'], 'SEASON_REJECTED')

    def test_quotes_never_satisfy_calendar_history_or_model(self):
        payload = raw([saved_fixture(tid, str(tid)) for tid in (18,17,8,23,242)])
        meta = raw(metadata()); q = SavedQuotes(payload, fingerprint(payload), meta, fingerprint(meta), NOW)
        r = report({}, as_of=NOW, quotes=q)
        self.assertEqual(market(r, 'E1')['gates']['quotes']['state'], 'AVAILABLE')
        self.assertEqual(market(r, 'E1')['input_state'], 'BLOCKED')
        for changed, state in ((replace(q, payload_sha256='0'*64), 'INVALID'),
                               (replace(q, retrieved_at=NOW-timedelta(hours=7)), 'STALE')):
            self.assertEqual(market(report({}, as_of=NOW, quotes=changed), 'E1')['gates']['quotes']['state'], state)

    def test_determinism_configuration_extension_and_no_input_mutation(self):
        e, now = e0(); before = e
        a = report({'E0':e}, as_of=now)
        b = report({'E0':replace(e, sources=tuple(reversed(e.sources)))}, as_of=now,
                   contracts=tuple(reversed(CONTRACTS)))
        self.assertEqual(encoded(a), encoded(b))
        self.assertEqual(e, before)
        self.assertEqual(next(r for r in a['leagues'] if r['competition']=='E0')['scope'],
                         {'season_year':2026, 'selected_day':e.selected_day.isoformat(), 'stage':'REGULAR_SEASON'})
        # A new European source uses the same observed-history adapter, no copied pipeline.
        data = b'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nD1,07/10/2026,A,B,0,0,D,0,0\n'
        c = LeagueContract('D1', 'JULY_YEAR_PAIR', None, 'FOOTBALL_DATA', None, None)
        h, _ = _history(Evidence('D1', 35, 2026, NOW.date(), sources=pinned_sources((('D1_2627.csv',data),)), provenance='SYNTHETIC'), c, NOW)
        self.assertEqual(h['corners']['state'], 'VALID')
        with self.assertRaises(ValueError):
            report({}, as_of=now, contracts=tuple(replace(c, btts_consumer='E1') if c.competition=='I1' else c for c in CONTRACTS))

    def test_review_per_source_chronology_and_current_source_freshness(self):
        e = e1()
        old, current = e.sources
        old = (*old[:3], NOW.replace(year=2025, month=12))
        r = report({'E1':replace(e, sources=(old, current))}, as_of=NOW)
        self.assertEqual(market(r, 'E1')['input_state'], 'ELIGIBLE')
        stale = (*current[:3], NOW-timedelta(days=15))
        r = report({'E1':replace(e, sources=(e.sources[0], stale))}, as_of=NOW)
        self.assertEqual(market(r, 'E1')['gates']['history']['reason'], 'HISTORY_SOURCE_STALE')
        self.assertEqual(market(r, 'E1')['input_state'], 'BLOCKED')

    def test_review_unknown_div_wrong_season_and_per_file_chronology(self):
        base = b'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nSP1,07/10/2026,A,B,0,0,D,0,0\n'
        for name, data, observed in (('SP1_2526.csv', base, NOW),
                    ('SP1_2627.csv', base.replace(b'SP1,', b','), NOW),
                    ('SP1_2627.csv', base, NOW-timedelta(days=3))):
            e = Evidence('SP1', 8, 2026, NOW.date(), sources=pinned_sources(((name,data),), observed), provenance='SYNTHETIC')
            self.assertEqual(market(report({'SP1': e}, as_of=NOW), 'SP1')['gates']['history']['state'], 'INVALID')

    def test_review_malformed_one_league_isolated(self):
        pl, now = e0()
        for invalid in (replace(pl, sources=list(pl.sources)), replace(pl, sources=(('bad',),)),
                        replace(pl, provenance=[]), replace(pl, provenance=object())):
            r = report({'E0': invalid}, as_of=now)
            self.assertEqual(market(r, 'E0')['input_state'], 'BLOCKED')
            self.assertEqual(market(r, 'I1')['input_state'], 'UNSUPPORTED')

    def test_review_quotes_after_kickoff_are_not_available(self):
        fixture = saved_fixture()
        fixture['startTime'] = (NOW+timedelta(minutes=1)).isoformat()
        payload, meta = raw([fixture]), raw(metadata())
        q = SavedQuotes(payload, fingerprint(payload), meta, fingerprint(meta), NOW)
        r = report({}, as_of=NOW+timedelta(minutes=2), quotes=q)
        self.assertEqual(market(r, 'E1')['gates']['quotes']['state'], 'MISSING')

    def test_review_oversized_csv_field_blocks_only_its_league(self):
        data = ('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nSP1,07/10/2026,'+
                'A'*150000+',B,0,0,D,0,0\n').encode()
        e = Evidence('SP1', 8, 2026, NOW.date(), sources=pinned_sources((('SP1_2627.csv', data),)), provenance='SYNTHETIC')
        r = report({'SP1': e, 'E1': e1()}, as_of=NOW)
        self.assertEqual(market(r, 'SP1')['gates']['history']['state'], 'INVALID')
        self.assertEqual(market(r, 'E1')['input_state'], 'ELIGIBLE')
