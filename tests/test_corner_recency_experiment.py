from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.corner_recency_experiment import (
    format_recency_experiment, main, rolling_recency_rows,
    run_recency_experiment, score_recency_rows,
)
from modelfc.corners import rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, Venue


def match(day, index, home="A", away="B"):
    home_corners = 2 + index % 7
    away_corners = 1 + index % 5
    return [
        TeamCornerObservation(
            day, home, away, Venue.HOME, home_corners, away_corners,
        ),
        TeamCornerObservation(
            day, away, home, Venue.AWAY, away_corners, home_corners,
        ),
    ]


def history(days=50):
    result = []
    start = date(2025, 1, 1)
    for index in range(days):
        teams = ("A", "B") if index % 2 == 0 else ("B", "A")
        result.extend(match(start + timedelta(days=index), index, *teams))
    return result


class CornerRecencyExperimentTests(unittest.TestCase):
    def settings(self):
        return dict(
            windows=(3, 5, 10), half_life_days=(5, 15, 30),
            min_history=2, min_venue_history=1, smoothing_matches=5,
        )

    def test_expanding_variant_matches_existing_corner_baseline(self):
        observations = history()
        existing = rolling_corner_predictions(
            observations, "venue-opponent-negative-binomial",
            min_history=2, smoothing_matches=5,
        )
        expected = {
            (item.observation.match_date, item.observation.team): item
            for item in existing
        }
        rows = rolling_recency_rows(observations, **self.settings())
        self.assertTrue(rows)
        for row in rows:
            previous = expected[
                row.observation.match_date, row.observation.team,
            ]
            self.assertAlmostEqual(row.expanding_mean, previous.expected_corners)
            self.assertAlmostEqual(
                row.dispersion_size, previous.dispersion_size,
            )

    def test_target_day_and_future_corners_cannot_leak(self):
        observations = history()
        target = date(2025, 1, 30)
        before = rolling_recency_rows(observations, **self.settings())
        changed = [
            TeamCornerObservation(
                item.match_date, item.team, item.opponent, item.venue,
                50, 50,
            ) if item.match_date >= target else item
            for item in observations
        ]
        after = rolling_recency_rows(changed, **self.settings())

        def values(rows):
            return [
                (row.observation.team, row.expanding_mean,
                 row.window_means, row.decay_means, row.dispersion_size)
                for row in rows if row.observation.match_date == target
            ]

        self.assertEqual(values(before), values(after))

    def test_parameters_are_selected_on_development_and_frozen(self):
        observations = history()
        split = date(2025, 2, 5)
        first = run_recency_experiment(
            observations, competition="SP1", holdout_from=split,
            **self.settings(),
        )
        changed = [
            TeamCornerObservation(
                item.match_date, item.team, item.opponent, item.venue, 25, 25,
            ) if item.match_date >= split else item
            for item in observations
        ]
        second = run_recency_experiment(
            changed, competition="SP1", holdout_from=split,
            **self.settings(),
        )
        self.assertEqual(
            [(item.parameter, item.development) for item in first.variants],
            [(item.parameter, item.development) for item in second.variants],
        )
        self.assertNotEqual(
            first.variants[0].holdout, second.variants[0].holdout,
        )
        self.assertEqual(
            {item.holdout.count for item in first.variants},
            {first.variants[0].holdout.count},
        )

    def test_scoring_report_and_validation(self):
        rows = rolling_recency_rows(history(), **self.settings())
        metrics = score_recency_rows(rows, "window", 5, [3.5, 5.5])
        self.assertEqual(metrics.count, len(rows))
        experiment = run_recency_experiment(
            history(), competition="SP1", holdout_from=date(2025, 2, 5),
            **self.settings(),
        )
        text = format_recency_experiment(experiment)
        self.assertIn("recent-window", text)
        self.assertIn("time-decay", text)
        self.assertIn("frozen for holdout", text)
        for kwargs in (
            {"windows": ()}, {"windows": (0,)},
            {"half_life_days": (0,)}, {"min_history": 0},
            {"min_venue_history": True}, {"smoothing_matches": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                rolling_recency_rows(
                    history(), **dict(self.settings(), **kwargs),
                )
        with self.assertRaises(ValueError):
            score_recency_rows(rows, "unknown", None, [3.5])
        with self.assertRaises(ValueError):
            score_recency_rows([], "expanding", None, [3.5])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SP1.csv"
            lines = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC"]
            for index in range(50):
                day = date(2025, 1, 1) + timedelta(days=index)
                home, away = (("A", "B") if index % 2 == 0 else ("B", "A"))
                lines.append(
                    f"SP1,{day:%d/%m/%Y},{home},{away},1,0,H,"
                    f"{2 + index % 7},{1 + index % 5}"
                )
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            output = StringIO()
            arguments = [
                "corner_recency_experiment", str(path),
                "--competition", "SP1", "--holdout-from", "2025-02-05",
                "--min-history", "2", "--min-venue-history", "1",
                "--windows", "3", "5", "--half-life-days", "5", "15",
            ]
            with patch("sys.argv", arguments), redirect_stdout(output):
                main()
            self.assertIn("Competition: SP1", output.getvalue())
            self.assertIn("time-decay", output.getvalue())

            error = StringIO()
            with patch("sys.argv", arguments[:-4] + [
                "--half-life-days", "0",
            ]), redirect_stderr(error), self.assertRaises(SystemExit):
                main()
            self.assertIn("half_life_days", error.getvalue())


if __name__ == "__main__":
    unittest.main()
