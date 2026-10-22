"""Synthetic reproductions of the reported ASA schema, not genuine-byte acceptance."""
from datetime import datetime, timezone
import unittest
import json
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.asa_saved_history import BASE, normalize

NOW = datetime(2026, 10, 22, 23, tzinfo=timezone.utc)


def game(**changes):
    return dict(game_id='synthetic-game', date_time_utc='2023-06-01 23:00:00 UTC',
        home_score=2, away_score=0, home_team_id='synthetic-home', away_team_id='synthetic-away',
        season_name='2023', knockout_game=False, status='FullTime',
        last_updated_utc='2023-06-02 02:00:00 UTC') | changes


def inputs(games=None, teams=None, receipt_change=None, season='2023', extra_season=None):
    teams = teams if teams is not None else [dict(team_id='synthetic-'+side, team_name='Synthetic '+side,
        team_short_name=side, team_abbreviation=side[:1]) for side in ('home', 'away')]
    datasets = [('synthetic-teams', BASE+'/mls/teams', teams),
        ('synthetic-games', BASE+f'/mls/games?season_name={season}&stage_name=Regular%20Season&status=FullTime',
         [game()] if games is None else games)]
    if extra_season:
        datasets.append(('synthetic-other', BASE+f'/mls/games?season_name={extra_season}&stage_name=Regular%20Season&status=FullTime',
            [game(season_name=extra_season, date_time_utc=f'{extra_season}-06-01 23:00:00 UTC',
                  last_updated_utc=f'{extra_season}-06-02 02:00:00 UTC')]))
    payloads, receipts = {}, []
    for name, url, rows in datasets:
        raw = encoded(rows); payloads[name] = raw
        receipt = dict(name=name, url=url, requested_at_utc='2026-10-22T22:05:00+00:00',
            status=200, headers={'content-type': 'application/json'}, bytes=len(raw), sha256=fingerprint(raw),
            path='/not/a/portable/path/'+name, rows=len(rows))
        if name == 'synthetic-games' and receipt_change: receipt.update(receipt_change)
        receipts.append(receipt)
    raw = encoded(receipts)
    return dict(payloads=payloads, receipts_raw=raw, receipts_sha256=fingerprint(raw), as_of=NOW, enabled=True)


class ASASavedTests(unittest.TestCase):
    def test_disabled_without_inputs(self):
        self.assertEqual(normalize()['state'], 'DISABLED')

    def test_normalizes_outcomes_without_completion_or_false_extra_time(self):
        with patch('socket.socket', side_effect=AssertionError('network')), patch('builtins.open', side_effect=AssertionError('file access')):
            b = normalize(**inputs())
        row = b['results'][0]
        self.assertEqual((row.home_goals, row.away_goals, row.total_goals, row.btts), (2, 0, 2, False))
        self.assertIsNone(row.extra_time); self.assertIsNone(row.completed_at)
        self.assertEqual(b['model_eligibility'], 'BLOCKED_COMPLETION_TIME_UNKNOWN')
        self.assertFalse(b['validated']); self.assertFalse(b['live'])
        self.assertEqual(b['coverage'], 'UNVERIFIED')
        b = normalize(**inputs([game(home_score=1, away_score=1, extra_time=False, expanded_minutes=120, matchday=3)]))
        self.assertTrue(b['results'][0].btts)
        self.assertIs(b['results'][0].extra_time, False)
        self.assertIsNone(b['results'][0].completed_at)

    def test_empty_is_not_coverage(self):
        b = normalize(**inputs([]))
        self.assertEqual(b['state'], 'EMPTY'); self.assertEqual(b['results'], ())
        self.assertEqual(b['model_eligibility'], 'BLOCKED_COMPLETION_TIME_UNKNOWN')

    def test_request_scope_exact_and_no_other_query_parameters(self):
        url = BASE+'/mls/games?season_name=2023&stage_name=Regular%20Season&status=FullTime'
        for bad in (url.replace('Regular%20Season', 'Playoffs'), url.replace('&stage_name=Regular%20Season', ''),
                    url+'&team_id=x', url+'&stage_name=Playoffs', url.replace('https:', 'http:'),
                    url.replace('2023', '2027'), url.replace('/mls/', '/nwsl/'), url+'#fragment'):
            with self.subTest(url=bad), self.assertRaisesRegex(ValueError, 'REQUEST_SCOPE'):
                normalize(**inputs(receipt_change={'url': bad}))

    def test_receipt_hash_metadata_and_chronology(self):
        for change in ({'status': True}, {'status': 201}, {'bytes': 0}, {'rows': True}, {'rows': 2},
                       {'sha256': '0'*64}, {'headers': {'x': 1}}, {'requested_at_utc': '2026-10-23T00:00:00Z'},
                       {'requested_at_utc': '2026-10-22T22:05:00'}, {'name': 'unbound'}):
            with self.subTest(change=change), self.assertRaises(ValueError): normalize(**inputs(receipt_change=change))
        args = inputs(); args['receipts_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'PIN'): normalize(**args)
        args = inputs(); args['payloads']['synthetic-games'] += b' '
        with self.assertRaisesRegex(ValueError, 'PIN'): normalize(**args)

    def test_missing_required_fields_and_invalid_scores(self):
        for field in game():
            row = game(); del row[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'SCHEMA'): normalize(**inputs([row]))
        for score in (True, -1, 1.5, '2', None):
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, 'SCORE'):
                normalize(**inputs([game(home_score=score)]))

    def test_extra_time_stage_and_knockout_fail_closed(self):
        for changes in ({'extra_time': True}, {'extra_time': None}, {'extra_time': 0}, {'extra_time': 'false'},
                        {'knockout_game': True}, {'knockout_game': 0}, {'status': 'Scheduled'},
                        {'stage_name': 'Playoffs'}, {'stage_name': None}, {'completed_at': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): normalize(**inputs([game(**changes)]))

    def test_identities_duplicates_and_cross_season(self):
        for changes in ({'game_id': ''}, {'game_id': 1}, {'home_team_id': 'unknown'},
                        {'home_team_id': 'synthetic-away'}, {'season_name': '2024'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): normalize(**inputs([game(**changes)]))
        for rows in ([game(), game()], [game(), game(game_id='another')]):
            with self.assertRaisesRegex(ValueError, 'DUPLICATE'): normalize(**inputs(rows))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE'): normalize(**inputs(extra_season='2024'))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_SEASON'): normalize(**inputs(extra_season='2023'))

    def test_dates_do_not_become_completion_evidence(self):
        for field, value in (('date_time_utc', '2023-02-30 23:00:00 UTC'),
            ('date_time_utc', '2023-06-01T23:00:00Z'), ('date_time_utc', '2024-06-01 23:00:00 UTC'),
            ('last_updated_utc', '2023-05-01 23:00:00 UTC'), ('last_updated_utc', '2027-01-01 23:00:00 UTC')):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): normalize(**inputs([game(**{field: value})]))
        with self.assertRaisesRegex(ValueError, 'CHRONOLOGY'):
            normalize(**inputs([game(season_name='2026', date_time_utc='2026-10-23 00:00:00 UTC')], season='2026'))

    def test_teams_required_unique_and_unambiguous(self):
        team = dict(team_id='synthetic-home', team_name='Synthetic home', team_short_name='home', team_abbreviation='H')
        for teams in ([], [team, team], [team, team | {'team_id': 'other'}], [team | {'team_name': None}]):
            with self.subTest(teams=teams), self.assertRaises(ValueError): normalize(**inputs(teams=teams))
        for field in team:
            bad = dict(team); del bad[field]
            with self.assertRaisesRegex(ValueError, 'SCHEMA'): normalize(**inputs(teams=[bad]))

    def test_receipt_identity_changes_composite_pin_and_paths_are_opaque(self):
        first = normalize(**inputs())
        second = normalize(**inputs(receipt_change={'path': '/different/opaque/path'}))
        self.assertNotEqual(first['inputs_sha256'], second['inputs_sha256'])
        self.assertEqual(first['results'], second['results'])
        self.assertEqual(first, normalize(**inputs()))

    def test_malformed_json_duplicate_keys_and_envelopes(self):
        args = inputs()
        for raw in (b'[]', b'{"x":1,"x":2}', b'{"items":[]}', b'NaN'):
            with self.assertRaises(ValueError): normalize(**(args | {'receipts_raw': raw, 'receipts_sha256': fingerprint(raw)}))

    def test_response_arrays_and_receipt_binding_reject_malformed_evidence(self):
        for raw in (b'{"items":[]}', b'[null]', b'[{"game_id":"a","game_id":"b"}]', b'[NaN]'):
            args = inputs(); args['payloads']['synthetic-games'] = raw
            receipts = json.loads(args['receipts_raw'])
            receipts[1].update(bytes=len(raw), sha256=fingerprint(raw), rows=1)
            args['receipts_raw'] = encoded(receipts)
            args['receipts_sha256'] = fingerprint(args['receipts_raw'])
            with self.subTest(raw=raw), self.assertRaises(ValueError): normalize(**args)
        for mutation in ('duplicate-name', 'extra-field', 'missing-field', 'unbound-payload'):
            args = inputs(); receipts = json.loads(args['receipts_raw'])
            if mutation == 'duplicate-name': receipts[1]['name'] = receipts[0]['name']
            if mutation == 'extra-field': receipts[1]['unreviewed'] = True
            if mutation == 'missing-field': del receipts[1]['requested_at_utc']
            if mutation == 'unbound-payload': args['payloads']['other'] = b'[]'
            args['receipts_raw'] = encoded(receipts)
            args['receipts_sha256'] = fingerprint(args['receipts_raw'])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): normalize(**args)

    def test_partial_optional_data_and_request_time_are_not_availability(self):
        b = normalize(**inputs([game(referee_id=None, attendance=None,
                                    last_updated_utc='2026-10-22 22:06:00 UTC')]))
        self.assertEqual(b['response_availability_time'], 'UNKNOWN')
        self.assertGreater(b['results'][0].last_updated, b['results'][0].requested_at)
        for value in (True, '90', -1):
            with self.assertRaisesRegex(ValueError, 'OPTIONAL_INTEGER'):
                normalize(**inputs([game(expanded_minutes=value)]))
