import contextlib
from dataclasses import replace
from datetime import date, timedelta
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.corner_feature_experiment import (
    feature_mean, format_feature_experiment, rolling_feature_rows,
    run_feature_experiment, score_feature_rows,
)
from modelfc.corner_shot_experiment import main
from modelfc.corners import rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, Venue
from modelfc.team_match_stats import TeamMatchStats


def pair(day, home="A", away="B", hc=4, ac=3, hs=12, ass=9, hst=4, ast=3):
    season_start = day.year - (day.month < 7)
    common = dict(match_date=day, competition="SP1",
                  season=f"{season_start}/{season_start + 1}",
                  source="test", goals_for=1, goals_against=0)
    return [
        TeamMatchStats(**common, team=home, opponent=away, venue=Venue.HOME,
                       corners_for=hc, corners_against=ac,
                       shots_for=hs, shots_against=ass,
                       shots_on_target_for=hst, shots_on_target_against=ast),
        TeamMatchStats(**dict(common, goals_for=0, goals_against=1),
                       team=away, opponent=home, venue=Venue.AWAY,
                       corners_for=ac, corners_against=hc,
                       shots_for=ass, shots_against=hs,
                       shots_on_target_for=ast, shots_on_target_against=hst),
    ]


def history(days=36):
    rows = []
    start = date(2025, 6, 1)
    for index in range(days):
        day = start + timedelta(days=index)
        if index % 2:
            rows.extend(pair(day, "B", "A", hc=2 + index % 6,
                             ac=1 + index % 5, hs=8 + index % 7,
                             ass=7 + index % 6, hst=2 + index % 4,
                             ast=1 + index % 4))
        else:
            rows.extend(pair(day, hc=3 + index % 7, ac=2 + index % 4,
                             hs=10 + index % 8, ass=6 + index % 7,
                             hst=3 + index % 5, ast=2 + index % 3))
    return rows


class CornerFeatureExperimentTests(unittest.TestCase):
    def settings(self):
        return dict(min_history=2, min_venue_history=1, smoothing_matches=5)

    def test_baseline_matches_existing_rolling_corner_mean(self):
        records = history()
        observations = [TeamCornerObservation(
            item.match_date, item.team, item.opponent, item.venue,
            item.corners_for, item.corners_against,
        ) for item in records]
        old = rolling_corner_predictions(
            observations, "venue-opponent-negative-binomial",
            min_history=2, smoothing_matches=5,
        )
        old_by_key = {
            (item.observation.match_date, item.observation.team): item
            for item in old
        }
        rows = rolling_feature_rows(records, **self.settings())
        self.assertTrue(rows)
        for row in rows:
            previous = old_by_key[row.record.match_date, row.record.team]
            self.assertAlmostEqual(row.baseline_mean, previous.expected_corners)
            self.assertAlmostEqual(row.dispersion_size, previous.dispersion_size)

    def test_target_same_day_and_future_values_cannot_leak(self):
        records = history()
        target = date(2025, 6, 20)
        before = rolling_feature_rows(records, **self.settings())
        changed = [replace(item, corners_for=20, corners_against=20,
                           shots_for=30, shots_against=30,
                           shots_on_target_for=10, shots_on_target_against=10)
                   if item.match_date == target else item for item in records]
        after = rolling_feature_rows(changed, **self.settings())
        get = lambda values: [
            (item.record.team, item.baseline_mean, item.shot_ratio,
             item.shots_on_target_ratio, item.dispersion_size)
            for item in values if item.record.match_date == target
        ]
        self.assertEqual(get(before), get(after))
        future = records + pair(date(2026, 1, 1), hc=50, ac=50, hs=50, ass=50,
                                hst=20, ast=20)
        self.assertEqual(before, rolling_feature_rows(future, **self.settings())[:-2])

    def test_weights_use_development_only_and_cohorts_are_identical(self):
        records = history()
        split = date(2025, 6, 22)
        first = run_feature_experiment(records, holdout_from=split, **self.settings())
        changed = [replace(item, corners_for=15, corners_against=15)
                   if item.match_date >= split else item for item in records]
        second = run_feature_experiment(changed, holdout_from=split, **self.settings())
        self.assertEqual(
            [(x.shot_weight, x.shots_on_target_weight, x.development)
             for x in first.variants],
            [(x.shot_weight, x.shots_on_target_weight, x.development)
             for x in second.variants],
        )
        self.assertNotEqual(first.variants[0].holdout, second.variants[0].holdout)
        self.assertEqual({x.development.count for x in first.variants},
                         {first.variants[0].development.count})
        self.assertEqual({x.holdout.count for x in first.variants},
                         {first.variants[0].holdout.count})
        baseline = first.variants[0]
        self.assertEqual((baseline.shot_weight, baseline.shots_on_target_weight),
                         (0, 0))

    def test_scoring_and_report(self):
        rows = rolling_feature_rows(history(), **self.settings())
        metrics = score_feature_rows(rows, 0.25, 0.5, [3.5, 5.5])
        self.assertEqual(metrics.count, len(rows))
        self.assertTrue(all(math_value >= 0 for math_value in (
            metrics.mae, metrics.rmse, metrics.negative_log_likelihood,
            metrics.line_brier, metrics.line_log_loss,
        )))
        self.assertGreater(feature_mean(rows[0], 0, 0), 0)
        report = run_feature_experiment(
            history(), holdout_from=date(2025, 6, 22), **self.settings(),
        )
        text = format_feature_experiment(report)
        self.assertIn("corners+shots+sot", text)
        self.assertIn("frozen for holdout", text)
        self.assertIn("Lower is better", text)

    def test_rejects_missing_inconsistent_and_invalid_inputs(self):
        records = history()
        cases = [
            [replace(records[0], shots_for=99)] + records[1:],
            records[:-1],
            records + records[:2],
            [replace(records[0], competition="E0")] + records[1:],
        ]
        for rows in cases:
            with self.subTest(case=len(rows)), self.assertRaises(ValueError):
                rolling_feature_rows(rows, **self.settings())
        incomplete = [replace(records[0], shots_for=None)] + [
            replace(records[1], shots_against=None)
        ] + records[2:]
        report = run_feature_experiment(
            incomplete, holdout_from=date(2025, 6, 22), **self.settings(),
        )
        self.assertEqual(report.excluded_incomplete_fixtures, 1)
        for kwargs in (
            dict(min_history=0), dict(min_venue_history=True),
            dict(smoothing_matches=0),
        ):
            with self.assertRaises(ValueError):
                rolling_feature_rows(records, **dict(self.settings(), **kwargs))
        for kwargs in (
            dict(holdout_from="2025-06-22"), dict(lines=[4]),
            dict(lines=[3.5, 3.5]), dict(weight_grid=[]),
            dict(weight_grid=[0.25]), dict(weight_grid=[0, 3]),
            dict(holdout_from=date(2024, 1, 1)),
            dict(holdout_from=date(2027, 1, 1)),
        ):
            settings = dict(holdout_from=date(2025, 6, 22), **self.settings())
            settings.update(kwargs)
            with self.assertRaises(ValueError):
                run_feature_experiment(records, **settings)
        rows = rolling_feature_rows(records, **self.settings())
        for weights in ((-3, 0), (0, float("inf")), (True, 0)):
            with self.assertRaises(ValueError):
                score_feature_rows(rows, *weights, [3.5])
        with self.assertRaises(ValueError):
            score_feature_rows([], 0, 0, [3.5])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SP1.csv"
            lines = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC,HS,AS,HST,AST"]
            for index in range(36):
                day = date(2025, 6, 1) + timedelta(days=index)
                home, away = (("A", "B") if index % 2 == 0 else ("B", "A"))
                lines.append(
                    f"SP1,{day:%d/%m/%Y},{home},{away},1,0,H,"
                    f"{3 + index % 5},{2 + index % 4},{10 + index % 6},"
                    f"{8 + index % 5},{3 + index % 3},{2 + index % 3}"
                )
            path.write_text("\n".join(lines) + "\n")
            output = io.StringIO()
            args = ["experiment", "--history", str(path),
                    "--holdout-from", "2025-06-22", "--min-history", "2",
                    "--min-venue-history", "1"]
            with patch("sys.argv", args), contextlib.redirect_stdout(output):
                main()
            self.assertIn("Competition: SP1", output.getvalue())
            self.assertIn("corners-only", output.getvalue())


if __name__ == "__main__":
    unittest.main()
