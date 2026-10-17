"""Entirely SYNTHETIC PL contracts/calendars; no authentic provider acceptance."""
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.pl_calendar import CalendarRejected, parse_calendar
from modelfc.pl_calendar_readiness import main, readiness, read_bounded

NOW = datetime(2026, 10, 17, 10, tzinfo=timezone.utc)


def raw(value):
    return json.dumps(value, ensure_ascii=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def synthetic():
    bindings = [{"source_team_id": 1000+i, "source_name": f"Synthetic Provider {i}",
                 "history_name": f"Synthetic History {i}"} for i in range(20)]
    contract = {"version": 1, "source": "football-data.org", "competition": "E0",
                "source_competition_code": "PL", "source_competition_id": 9999,
                "season_year": 2026, "team_bindings": bindings}
    ring, rounds = list(bindings), []
    for _ in range(19):
        rounds.append([(ring[i], ring[-1-i]) for i in range(10)])
        ring = [ring[0], ring[-1], *ring[1:-1]]
    rounds += [[(away, home) for home, away in pairs] for pairs in rounds]
    rows = []
    for index, pairs in enumerate(rounds):
        for home, away in pairs:
            rows.append({"id": 2000+len(rows), "utcDate": "2027-05-01T14:00:00Z",
                "status": "TIMED", "matchday": index+1, "stage": "REGULAR_SEASON",
                "competition": {"id": 9999, "code": "PL"},
                "season": {"startDate": "2026-08-01", "endDate": "2027-05-31"},
                "homeTeam": {"id": home["source_team_id"], "name": home["source_name"]},
                "awayTeam": {"id": away["source_team_id"], "name": away["source_name"]},
                "score": {"fullTime": {"home": None, "away": None}}})
    for row in rows[:8]:
        row["utcDate"] = "2026-10-17T14:00:00Z"
    payload = {"filters": {"season": "2026", "permission": "TIER_ONE"},
               "competition": {"code": "PL", "id": 9999, "type": "LEAGUE"},
               "resultSet": {"count": 380, "played": 0}, "matches": rows}
    return contract, payload


class PLCalendarTests(unittest.TestCase):
    def setUp(self):
        self.contract, self.payload = synthetic()
        self.retrieved = NOW.isoformat()

    def bundle(self):
        return raw({"version": 1, "source": "football-data.org",
                    "retrieved_at": self.retrieved, "payload": json.dumps(self.payload, indent=2)})

    def parse(self, **kwargs):
        options = dict(contract_raw=raw(self.contract), reviewed_contract_sha256=sha(raw(self.contract)),
                       competition="E0", season_year=2026)
        options.update(kwargs)
        return parse_calendar(self.bundle(), **options)

    def report(self, **kwargs):
        options = dict(contract_raw=raw(self.contract), reviewed_contract_sha256=sha(raw(self.contract)),
            competition="E0", season_year=2026, selected_day=NOW.date(), as_of=NOW,
            bundle_sha256=sha(self.bundle()))
        options.update(kwargs)
        return readiness(self.bundle(), **options)

    def test_full_contract_bytes_hash_snapshot_and_eight_fixture_day(self):
        calendar = self.parse()
        self.assertEqual(calendar.raw_bundle, self.bundle())
        self.assertEqual(calendar.raw_payload, json.dumps(self.payload, indent=2).encode())
        self.assertEqual(calendar.source_sha256, sha(calendar.raw_payload))
        self.assertEqual(calendar.retrieved_at, NOW)
        self.assertNotIn('Synthetic Provider', repr(calendar))
        snapshot = calendar.snapshot()
        self.assertEqual(len(snapshot.fixtures), 380)
        self.assertEqual(snapshot.provider_payload_hash, calendar.source_sha256)
        self.assertTrue(all(f.competition_id == 'E0' and f.fetched_at == NOW for f in snapshot.fixtures))
        report = self.report()
        self.assertEqual((report['validity'], report['freshness'], report['selected_count']),
                         ('VALID', 'FRESH', 8))
        self.assertEqual(report['prior_state'], 'RESOLVED')

    def test_identity_requires_id_exact_kickoff_and_order(self):
        calendar = self.parse()
        fixture = calendar.matches[0]
        args = dict(fixture_id=fixture.fixture_id, kickoff=fixture.kickoff, home=fixture.home, away=fixture.away)
        self.assertEqual(calendar.exact_fixture(**args), fixture)
        for change in [dict(fixture_id=True), dict(fixture_id=123),
                       dict(kickoff=fixture.kickoff+timedelta(seconds=1)),
                       dict(home=fixture.away, away=fixture.home), dict(home=fixture.home.lower())]:
            with self.subTest(change=change), self.assertRaises(CalendarRejected):
                calendar.exact_fixture(**{**args, **change})

    def test_invalid_schema_and_incomplete_payloads(self):
        mutations = [
            lambda p: p['matches'].pop(),
            lambda p: p.update(matches=[]),
            lambda p: p['resultSet'].update(count=379),
            lambda p: p['resultSet'].update(played=1),
            lambda p: p['filters'].update(dateFrom='2026-10-17'),
            lambda p: p['filters'].update(season='2025'),
            lambda p: p['competition'].update(code='ELC'),
            lambda p: p['competition'].update(id=17),
            lambda p: p['matches'][0].update(id=p['matches'][1]['id']),
            lambda p: p['matches'][0].update(id=True),
            lambda p: p['matches'][0]['homeTeam'].update(name='Guessed Alias'),
            lambda p: p['matches'][0]['homeTeam'].update(id=17),
            lambda p: p['matches'][0].update(homeTeam=p['matches'][0]['awayTeam']),
            lambda p: p['matches'][0].update(homeTeam=p['matches'][1]['homeTeam'],awayTeam=p['matches'][1]['awayTeam']),
            lambda p: p['matches'][0].update(matchday=2),
            lambda p: p['matches'][0].update(matchday=39),
            lambda p: p['matches'][0].update(utcDate='2026-10-17T14:00:00'),
            lambda p: p['matches'][0].update(utcDate='2028-01-01T14:00:00Z'),
            lambda p: p['matches'][0].update(status='UNKNOWN'),
            lambda p: p['matches'][0]['season'].update(endDate='2028-05-31'),
            lambda p: p['matches'][0]['season'].update(startDate='2026-08-02'),
            lambda p: p['matches'][0].update(status='FINISHED'),
            lambda p: p['matches'][0]['score']['fullTime'].update(home=1, away=0),
        ]
        original = copy.deepcopy(self.payload)
        for mutation in mutations:
            self.payload = copy.deepcopy(original)
            mutation(self.payload)
            with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(CalendarRejected):
                self.parse()
            self.assertEqual(self.report()['selected_day_state'], 'UNKNOWN')
        self.payload = original
        self.payload['matches'].pop()
        self.payload['resultSet']['count'] = 379
        with self.assertRaisesRegex(CalendarRejected, 'INCOMPLETE'):
            self.parse()

    def test_reversed_pair_is_not_duplicate_coverage(self):
        row = self.payload['matches'][0]
        row['homeTeam'], row['awayTeam'] = row['awayTeam'], row['homeTeam']
        with self.assertRaises(CalendarRejected):
            self.parse()

    def test_contract_no_alias_or_id_inference(self):
        original = copy.deepcopy(self.contract)
        for key in ['source_team_id', 'source_name', 'history_name']:
            self.contract = copy.deepcopy(original)
            self.contract['team_bindings'][0][key] = self.contract['team_bindings'][1][key]
            with self.subTest(key=key), self.assertRaises(CalendarRejected):
                self.parse()
        self.contract = original
        for change in [dict(competition='E1'), dict(season_year=2025),
                       dict(reviewed_contract_sha256='0'*64)]:
            with self.subTest(change=change), self.assertRaises(CalendarRejected):
                self.parse(**change)
        self.contract['team_bindings'].pop()
        with self.assertRaises(CalendarRejected):
            self.parse()

    def test_missing_stale_future_invalid_and_empty_are_distinct(self):
        missing = readiness(None, contract_raw=b'', reviewed_contract_sha256='', bundle_sha256='',
            competition='E0', season_year=2026, selected_day=NOW.date(), as_of=NOW)
        self.assertEqual((missing['validity'], missing['selected_day_state']), ('MISSING','UNKNOWN'))
        self.assertEqual(self.report(selected_day=date(2026,10,18))['selected_day_state'], 'EMPTY')
        self.assertEqual(self.report(as_of=NOW+timedelta(hours=6))['freshness'], 'FRESH')
        for observed, state in [(NOW+timedelta(hours=6,seconds=1),'STALE'),
                                (NOW-timedelta(seconds=1),'FUTURE')]:
            report = self.report(as_of=observed, selected_day=date(2026,10,18))
            self.assertEqual((report['validity'], report['freshness'],report['selected_day_state']),
                             ('VALID',state,'UNKNOWN'))
        self.retrieved = ''
        self.assertEqual(self.report()['validity'], 'INVALID_OR_UNSUPPORTED')

    def test_unresolved_prior_and_postponed_cancelled(self):
        self.payload['matches'][0]['utcDate'] = '2026-10-16T14:00:00Z'
        self.assertEqual(self.report()['unresolved_count'], 1)
        self.payload['matches'][0]['status'] = 'POSTPONED'
        self.payload['matches'][1]['status'] = 'CANCELLED'
        report = self.report()
        self.assertEqual(report['prior_state'], 'RESOLVED')
        self.assertEqual(report['status_counts']['CANCELLED'],1)
        self.payload['matches'][2]['utcDate'] = '2026-10-17T09:00:00Z'
        self.assertEqual(self.report()['prior_state'],'UNRESOLVED')

    def test_finished_scores_and_unrepresentable_statuses(self):
        row = self.payload['matches'][0]
        row.update(utcDate='2026-10-16T12:00:00Z', status='FINISHED')
        row['score']['fullTime'] = {'home': 2, 'away': 1}
        self.payload['resultSet']['played'] = 1
        self.assertEqual(self.report()['prior_state'], 'RESOLVED')
        for status in ['IN_PLAY','PAUSED','SUSPENDED']:
            row['status'] = status
            row['score']['fullTime'] = {'home':None,'away':None}
            self.payload['resultSet']['played'] = 0
            with self.subTest(status=status):
                self.assertEqual(self.report()['prior_state'], 'UNRESOLVED')
                with self.assertRaisesRegex(CalendarRejected, 'STATUS_UNSUPPORTED'):
                    self.parse().snapshot()

    def test_london_midnight_and_both_dst_transitions(self):
        cases = [('2026-10-17T23:30:00Z','2026-10-18'),
                 ('2026-10-25T00:30:00Z','2026-10-25'),
                 ('2026-10-25T01:30:00Z','2026-10-25'),
                 ('2027-03-28T00:30:00Z','2027-03-28'),
                 ('2027-03-28T01:30:00Z','2027-03-28'),
                 ('2027-03-28T23:30:00Z','2027-03-29'),
                 ('2026-12-01T23:30:00Z','2026-12-01')]
        for kickoff, expected in cases:
            self.payload['matches'][0]['utcDate'] = kickoff
            with self.subTest(kickoff=kickoff):
                fixture = next(m for m in self.parse().matches if m.fixture_id == 2000)
                self.assertEqual(fixture.day.isoformat(), expected)
                self.assertEqual(self.report(selected_day=date.fromisoformat(expected))['selected_count'], 1)
                self.assertEqual(fixture.kickoff, datetime.fromisoformat(kickoff.replace('Z','+00:00')))

    def test_invalid_raw_json_and_bounds(self):
        for data in [b'{', self.bundle()[:-1], b'{"version":1,"version":1}',
                     b'{"version":NaN}', b'x'*2_000_001]:
            with self.subTest(size=len(data)), self.assertRaises(CalendarRejected):
                parse_calendar(data, contract_raw=raw(self.contract),
                    reviewed_contract_sha256=sha(raw(self.contract)), competition='E0',season_year=2026)

    def test_cli_zero_network_no_mutation_bounded_sanitized(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle, contract = Path(folder)/'private-token', Path(folder)/'contract'
            bundle.write_bytes(self.bundle()); contract.write_bytes(raw(self.contract))
            before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (bundle,contract)]
            args = ['--bundle',str(bundle),'--bundle-sha256',sha(bundle.read_bytes()),
                    '--contract',str(contract),'--reviewed-contract-sha256',sha(contract.read_bytes()),
                    '--competition','E0','--season-year','2026','--day','2026-10-17','--as-of',NOW.isoformat()]
            out = io.StringIO()
            with patch('socket.socket',side_effect=AssertionError('network forbidden')), \
                 patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')), redirect_stdout(out):
                self.assertEqual(main(args),0)
            output = out.getvalue()
            self.assertLess(len(output),2000)
            for secret in [folder,'private-token','Synthetic','payload']:
                self.assertNotIn(secret,output)
            self.assertEqual(before,[(p.read_bytes(), p.stat().st_mtime_ns) for p in (bundle,contract)])
            self.assertEqual(set(Path(folder).iterdir()),{bundle,contract})
            link = Path(folder)/'symlink'; link.symlink_to(bundle)
            with self.assertRaises(OSError): read_bounded(link,2_000_000)
            with self.assertRaises(CalendarRejected): read_bounded(bundle,10)
            with self.assertRaises(CalendarRejected): read_bounded(Path(folder),2_000_000)
            bundle.unlink()
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(args),1)
            self.assertEqual(json.loads(out.getvalue())['validity'],'MISSING')

    def test_cli_failures_do_not_echo_input_or_paths(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(['--bundle','/private/secret','--bundle-sha256','bad',
                '--contract','/private/secret-contract','--reviewed-contract-sha256','bad',
                '--competition','credential-secret','--season-year','2026',
                '--day','2026-10-17','--as-of',NOW.isoformat()])
        self.assertEqual(code,1)
        self.assertNotIn('secret',out.getvalue())
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            main(['--secret-credential'])
        self.assertEqual(err.getvalue(),'PL_ARGUMENTS_REJECTED\n')

    def test_out_of_season_day_never_empty(self):
        self.assertEqual(self.report(selected_day=date(2028,1,1))['selected_day_state'],'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
