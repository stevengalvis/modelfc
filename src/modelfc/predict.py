"""Command-line live fixture prediction using the plain Poisson model."""

import argparse
from datetime import date
from pathlib import Path

from modelfc.forecasts import FixturePrediction, predict_upcoming_fixture
from modelfc.matches import UpcomingFixture
from modelfc.providers.football_data import load_match_history


def format_prediction(prediction: FixturePrediction) -> str:
    """Format a live prediction as a readable text report."""

    fixture = prediction.fixture
    return "\n".join(
        (
            f"Fixture date: {fixture.match_date.isoformat()}",
            f"Home team: {fixture.home_team}",
            f"Away team: {fixture.away_team}",
            f"Expected home goals: {prediction.expected_home_goals:.6f}",
            f"Expected away goals: {prediction.expected_away_goals:.6f}",
            f"Home-win probability: {prediction.home_win_probability:.6f}",
            f"Draw probability: {prediction.draw_probability:.6f}",
            f"Away-win probability: {prediction.away_win_probability:.6f}",
            f"Completed historical matches used: {prediction.historical_match_count}",
        )
    )


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from error


def main() -> None:
    """Load local history and print one plain-Poisson fixture prediction."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        type=Path,
        nargs="+",
        required=True,
        help="Football-Data CSV files containing completed matches",
    )
    parser.add_argument("--date", type=_iso_date, required=True, help="fixture date")
    parser.add_argument("--home", required=True, help="home team")
    parser.add_argument("--away", required=True, help="away team")
    parser.add_argument("--max-goals", type=int, default=10)
    parser.add_argument("--smoothing-matches", type=float, default=5.0)
    args = parser.parse_args()

    fixture = UpcomingFixture(args.date, args.home, args.away)
    prediction = predict_upcoming_fixture(
        load_match_history(args.history),
        fixture,
        args.max_goals,
        args.smoothing_matches,
    )
    print(format_prediction(prediction))


if __name__ == "__main__":
    main()
