"""Synthetic reproductions of PR201's observed headers; never original-file acceptance.

Values, header ordering, receipts and regulation attestations below are synthetic.
The VPS inventory establishes column names, not regulation score semantics.
"""
import unittest

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.mls_saved_history import normalize, corner_observations, goal_observations
from tests.test_mls_saved_history import AT, source


def row(**changes):
    fields = ('id,home,away,date,year,time (utc),attendance,venue,league,part_of_competition,'
              'game_status,shootout,home_score,away_score,referee,home_goal_minutes,'
              'home_goal_scorers,away_goal_minutes,away_goal_scorers').split(',')
    for side in ('home', 'away'):
        fields += [f'{side}_starting_{n}{suffix}' for n in range(1, 23) for suffix in ('', '_num')]
        fields += [f'{side}_bench_{n}{suffix}' for n in range(1, 15) for suffix in ('', '_num', '_minute')]
        fields += [f'{side}_{stat}' for stat in ('possessionPct', 'shotsSummary', 'foulsCommitted',
            'yellowCards', 'redCards', 'offsides', 'wonCorners', 'saves', 'formation')]
    return dict.fromkeys(fields, '') | dict(id='1', home='Synthetic Home', away='Synthetic Away',
        date='Monday, August 1', year='2022', league='2022 MLS', part_of_competition=' Regular Season',
        game_status='FT', shootout='False', home_score='2', away_score='0') | changes


def args(rows, *, period='REGULATION'):
    raw = source(rows)
    contract = encoded(dict(version=1, source_sha256=fingerprint(raw), home_goals_column='home_score',
        away_goals_column='away_score', score_period=period, stage='REGULAR_SEASON'))
    return dict(raw=raw, source_sha256=fingerprint(raw), contract_raw=contract,
        contract_sha256=fingerprint(contract), as_of=AT, retrieved_at=None, enabled=True)


class MLSInventorySchemaTests(unittest.TestCase):
    def test_209_columns_missing_corners_and_zero_remain_distinct(self):
        self.assertEqual(len(row()), 209)
        for values, expected, reason in [(('', ''), 0, 'STATISTICS_MISSING'), (('0', '0'), 1, None)]:
            with self.subTest(values=values):
                b = normalize(**args([row(home_wonCorners=values[0], away_wonCorners=values[1])]))
                self.assertEqual((b['goal_rows'], b['corner_rows']), (1, expected))
                self.assertEqual(b['matches'][0].goals, (2, 0))
                self.assertEqual(b['matches'][0].corner_reason, reason)
                self.assertEqual(b['provenance'], 'UNKNOWN')
                self.assertFalse(b['validated']); self.assertFalse(b['live'])
                with self.assertRaisesRegex(ValueError, 'PROVENANCE_UNKNOWN'):
                    corner_observations(b)
                with self.assertRaisesRegex(ValueError, 'PROVENANCE_UNKNOWN'):
                    goal_observations(b, bindings_raw=None, bindings_sha256=None)

    def test_missing_identity_and_numeric_date_reject_whole_batch_even_out_of_scope(self):
        for changes in (dict(id=''), dict(date='8/1/2022')):
            for stage in ('Regular Season', 'Playoffs'):
                with self.subTest(changes=changes, stage=stage):
                    with self.assertRaises(ValueError):
                        normalize(**args([row(), row(id='2', home='Synthetic Other',
                            part_of_competition=stage) | changes]))

    def test_full_status_not_first_line_controls_exclusion(self):
        # Aggregate wording is invented; the observed fact is a second status line.
        for status in ('AET', 'FT-Pens', 'Shootout', 'Abandoned', 'FT\nSynthetic aggregate 3-2'):
            with self.subTest(status=status):
                b = normalize(**args([row(game_status=status)]))
                self.assertEqual(b['matches'], ())
                self.assertEqual(b['excluded'][0]['reason'], 'NON_REGULATION_STATUS_UNSUPPORTED')

    def test_knockout_and_shootout_markers_never_become_regular_finals(self):
        for stage in ('Preseason', 'Playoffs', 'MLS is Back Knockout', 'MLS Cup', 'Conference Final'):
            with self.subTest(stage=stage):
                b = normalize(**args([row(part_of_competition=stage)]))
                self.assertEqual(b['matches'], ())
                self.assertEqual(b['excluded'][0]['reason'], 'STAGE_OR_YEAR_UNSUPPORTED')
        for marker in ('True', '4-3'):
            b = normalize(**args([row(shootout=marker)]))
            self.assertEqual(b['matches'], ())
            self.assertEqual(b['excluded'][0]['reason'], 'SHOOTOUT_MARKER_UNSUPPORTED')

    def test_actual_column_names_and_false_marker_do_not_establish_score_period(self):
        for marker in ('False', ''):
            with self.assertRaisesRegex(ValueError, 'SEMANTICS'):
                normalize(**args([row(shootout=marker)], period='UNKNOWN'))
            with self.assertRaisesRegex(ValueError, 'PIN_REJECTED'):
                normalize(**(args([row(shootout=marker)]) | dict(contract_raw=None, contract_sha256=None)))
