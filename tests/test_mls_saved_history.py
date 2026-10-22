"""Synthetic schema/receipts only, not genuine MLS acceptance."""
import csv
from datetime import datetime, timezone, timedelta
from io import StringIO
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.goal_totals import GoalObservation, GoalFixture, freeze
from modelfc.mls_saved_history import normalize, corner_observations, goal_observations

AT = datetime(2022, 8, 2, tzinfo=timezone.utc)


def match(**changes):
    row = dict(id='1.0', home='Synthetic Home', away='Synthetic Away', date='Monday, August 1',
        year='2022', league='2022 MLS', part_of_competition='Regular Season 2022', game_status='FT',
        synthetic_home_score='2', synthetic_away_score='1', home_wonCorners='', away_wonCorners='')
    return row | changes


def source(rows):
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode()


def arguments(rows=None, **changes):
    raw = source(rows or [match()])
    contract = encoded(dict(version=1, source_sha256=fingerprint(raw), home_goals_column='synthetic_home_score',
        away_goals_column='synthetic_away_score', score_period='REGULATION', stage='REGULAR_SEASON'))
    return dict(raw=raw, source_sha256=fingerprint(raw), contract_raw=contract, contract_sha256=fingerprint(contract),
        retrieved_at=AT, as_of=AT, enabled=True) | changes


def binding(batch, **changes):
    row = dict(source_id='1', provider='synthetic-saved-identity', fixture_id='synthetic-1',
        home='Synthetic Home', away='Synthetic Away', kickoff='2022-08-01T18:00:00Z',
        completed_at='2022-08-01T20:00:00Z', timezone='America/New_York') | changes
    raw = encoded(dict(source_sha256=batch['source_sha256'], contract_sha256=batch['contract_sha256'], fixtures=[row]))
    return dict(bindings_raw=raw, bindings_sha256=fingerprint(raw))


class MLSSavedTests(unittest.TestCase):
    def test_disabled_no_inputs(self):
        self.assertEqual(normalize()['state'], 'DISABLED')

    def test_goals_without_corners_and_generic_contract(self):
        with patch('socket.socket', side_effect=AssertionError('network')):
            b = normalize(**arguments())
            observations = goal_observations(b, **binding(b))
        self.assertEqual((b['goal_rows'], b['corner_rows']), (1, 0))
        self.assertEqual(b['matches'][0].season, 2022)  # August does not roll to 2022/23.
        self.assertIsInstance(observations[0], GoalObservation)
        self.assertEqual((observations[0].home_goals, observations[0].away_goals), (2, 1))
        self.assertFalse(b['validated']); self.assertFalse(b['live'])
        self.assertEqual(corner_observations(b), ())

    def test_absent_and_zero_corners_are_distinct(self):
        row = match(); del row['home_wonCorners']; del row['away_wonCorners']
        b = normalize(**arguments([row]))
        self.assertEqual(b['matches'][0].corner_reason, 'COLUMNS_ABSENT')
        b = normalize(**arguments([match(home_wonCorners='0.00', away_wonCorners='0')]))
        self.assertEqual([o.corners_for for o in corner_observations(b)], [0, 0])
        self.assertEqual(b['goal_rows'], 1)

    def test_markets_independent_invalid_partial_missing(self):
        for values, reason in [(('', ''), 'STATISTICS_MISSING'), (('', '1'), 'PARTIAL_STATISTICS'),
                               (('-1', '1'), 'INVALID_STATISTICS')]:
            b = normalize(**arguments([match(synthetic_home_score=values[0], synthetic_away_score=values[1],
                                             home_wonCorners='4', away_wonCorners='2')]))
            self.assertEqual((b['goal_rows'], b['corner_rows']), (0, 1))
            self.assertEqual(b['matches'][0].goal_reason, reason)
            b = normalize(**arguments([match(home_wonCorners=values[0], away_wonCorners=values[1])]))
            self.assertEqual((b['goal_rows'], b['corner_rows']), (1, 0))
            self.assertEqual(b['matches'][0].corner_reason, reason)

    def test_absent_goal_columns_preserve_corners(self):
        row = match(home_wonCorners='1', away_wonCorners='2')
        del row['synthetic_home_score']; del row['synthetic_away_score']
        b = normalize(**arguments([row]))
        self.assertEqual((b['goal_rows'], b['corner_rows']), (0, 1))
        self.assertEqual(b['matches'][0].goal_reason, 'COLUMNS_ABSENT')

    def test_stage_status_and_year_exclusions(self):
        for changes in [dict(part_of_competition='Playoffs'), dict(game_status='AET'), dict(game_status='PEN'),
                        dict(game_status='FT (pens)'), dict(part_of_competition='Regular Season 2021'),
                        dict(league='2021 MLS'), dict(part_of_competition='Preseason')]:
            b = normalize(**arguments([match(**changes)]))
            self.assertEqual(b['matches'], ())
            self.assertEqual(len(b['excluded']), 1)
        b = normalize(**arguments([match(year='1999', league='1999 MLS', date='Sunday, August 1',
                                        part_of_competition='Regular Season')]))
        self.assertEqual(b['excluded'][0]['reason'], 'PRE_BENCHMARK_SEASON_UNSUPPORTED')

    def test_pins_and_period_contract(self):
        for changes in [dict(source_sha256='0'*64), dict(contract_sha256='0'*64)]:
            with self.assertRaises(ValueError): normalize(**arguments(**changes))
        import json
        args = arguments(); contract = json.loads(args['contract_raw'])
        for period in ('FINAL', 'EXTRA_TIME', 'SHOOTOUT'):
            raw = encoded(contract | {'score_period': period})
            with self.assertRaisesRegex(ValueError, 'SEMANTICS'):
                normalize(**(args | dict(contract_raw=raw, contract_sha256=fingerprint(raw))))

    def test_shootout_marker_overrides_ft_and_transition_seasons_excluded(self):
        for marker in ('True', '1', '4-3', 'unknown'):
            b = normalize(**arguments([match(shootout=marker)]))
            self.assertEqual(b['matches'], ())
            self.assertEqual(b['excluded'][0]['reason'], 'SHOOTOUT_MARKER_UNSUPPORTED')
        for marker in ('', 'False', '0', '0.0'):
            self.assertEqual(normalize(**arguments([match(shootout=marker)]))['goal_rows'], 1)
        at = datetime(2028, 8, 2, tzinfo=timezone.utc)
        b = normalize(**arguments([match(year='2028', league='2028 MLS',
            part_of_competition='Regular Season', date='Tuesday, August 1')], as_of=at, retrieved_at=at))
        self.assertEqual(b['excluded'][0]['reason'], 'TRANSITION_SEASON_UNSUPPORTED')

    def test_unknown_and_impossible_provenance(self):
        b = normalize(**arguments(retrieved_at=None))
        self.assertEqual(b['provenance'], 'UNKNOWN')
        with self.assertRaisesRegex(ValueError, 'PROVENANCE_UNKNOWN'):
            goal_observations(b, **binding(b))
        b = normalize(**arguments([match(home_wonCorners='1', away_wonCorners='0')], retrieved_at=None))
        with self.assertRaisesRegex(ValueError, 'PROVENANCE_UNKNOWN'):
            corner_observations(b)
        for changes in [dict(retrieved_at=datetime(2022, 8, 3, tzinfo=timezone.utc)),
                        dict(retrieved_at=datetime(2022, 7, 31, tzinfo=timezone.utc))]:
            with self.assertRaises(ValueError): normalize(**arguments(**changes))

    def test_future_scheduled_row_excluded_without_blocking_completed_history(self):
        b = normalize(**arguments([match(), match(id='2', date='Wednesday, August 3', game_status='NS')]))
        self.assertEqual(b['goal_rows'], 1)
        self.assertEqual(b['excluded'][0]['reason'], 'NON_REGULATION_STATUS_UNSUPPORTED')
        with self.assertRaisesRegex(ValueError, 'RESULT_CHRONOLOGY'):
            normalize(**arguments([match(date='Wednesday, August 3')]))

    def test_identity_conflicts_rejected_even_without_stats(self):
        for second in [match(id='001'), match(id='2'), match(id='1', home='Other')]:
            with self.assertRaisesRegex(ValueError, 'DUPLICATE'):
                normalize(**arguments([match(synthetic_home_score='', synthetic_away_score=''), second]))
        for changes in [dict(home='Synthetic Away'), dict(home=''), dict(id='NaN'), dict(date='Sunday, August 1')]:
            with self.assertRaises(ValueError): normalize(**arguments([match(**changes)]))

    def test_bad_schema_shape_rejected(self):
        args = arguments()
        for raw in [args['raw'].replace(b'id,home,', b'id,id,'), args['raw']+b'extra,bad\n']:
            import json
            contract = encoded(json.loads(args['contract_raw']) | {'source_sha256': fingerprint(raw)})
            with self.assertRaises(ValueError):
                normalize(**(args | dict(raw=raw, source_sha256=fingerprint(raw), contract_raw=contract,
                                        contract_sha256=fingerprint(contract))))

    def test_binding_required_exact_identity_chronology_and_pin(self):
        b = normalize(**arguments())
        for changes in [dict(home='Alias'), dict(source_id='2'), dict(kickoff='2022-08-02T18:00:00Z'),
                        dict(completed_at='2022-08-01T17:00:00Z'), dict(completed_at='2022-08-03T20:00:00Z')]:
            with self.assertRaises(ValueError): goal_observations(b, **binding(b, **changes))
        with self.assertRaises(ValueError):
            goal_observations(b, **(binding(b) | {'bindings_sha256': '0'*64}))
        import json
        raw = encoded(json.loads(binding(b)['bindings_raw']) | {'fixtures': []})
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE'):
            goal_observations(b, bindings_raw=raw, bindings_sha256=fingerprint(raw))

    def test_original_bytes_unchanged_and_receipt_binds_mapping(self):
        args = arguments(); raw = args['raw']
        b = normalize(**args)
        first = goal_observations(b, **binding(b))[0]
        second = goal_observations(b, **binding(b, fixture_id='other-reviewed-id'))[0]
        self.assertEqual(raw, args['raw'])
        self.assertNotEqual(first.source_sha256, second.source_sha256)

    def test_generic_totals_fit_from_goal_only_history(self):
        rows, fixtures = [], []
        for index in range(110):
            day = (AT-timedelta(days=110-index)).date()
            rows.append(match(id=str(index+1), date=day.strftime('%A, %B ')+str(day.day)))
            fixtures.append(dict(source_id=str(index+1), provider='synthetic-saved-identity',
                fixture_id=str(index+1), home='Synthetic Home', away='Synthetic Away',
                kickoff=day.isoformat()+'T18:00:00Z', completed_at=day.isoformat()+'T20:00:00Z',
                timezone='America/New_York'))
        b = normalize(**arguments(rows))
        raw = encoded(dict(source_sha256=b['source_sha256'], contract_sha256=b['contract_sha256'], fixtures=fixtures))
        observations = goal_observations(b, bindings_raw=raw, bindings_sha256=fingerprint(raw))
        target = GoalFixture('MLS', 'synthetic-saved-identity', 'future', 'Synthetic Home', 'Synthetic Away',
            AT+timedelta(hours=18), 'America/New_York')
        frozen = freeze(target, observations, frozen_at=AT+timedelta(hours=10), cutoff=AT.date())
        self.assertEqual(frozen.aggregates.history_matches, 110)
        self.assertEqual(b['corner_rows'], 0)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_GOAL_HISTORY'):
            freeze(target, observations, frozen_at=AT-timedelta(seconds=1), cutoff=(AT-timedelta(days=1)).date())
