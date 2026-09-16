"""Predict one fixture's team corners from local historical CSV files."""

import argparse
from datetime import date
from pathlib import Path

from modelfc.corner_forecasts import (
    CORNER_FIXTURE_MODELS, CornerFixturePrediction, predict_corner_fixture,
)
from modelfc.corner_sources import PROVIDERS, load_provider_observations
from modelfc.matches import UpcomingFixture


def format_corner_prediction(prediction: CornerFixturePrediction) -> str:
    """Report predictions alongside their history coverage and age."""
    fixture = prediction.fixture

    def dated(value: date) -> str:
        return f"{value} ({(fixture.match_date - value).days} days before fixture)"

    lines = [
        f"Fixture: {fixture.match_date} | {fixture.home_team} vs {fixture.away_team}",
        f"Model: {prediction.model}",
        f"Smoothing matches: {prediction.smoothing_matches:g}",
        f"Historical team observations used: {prediction.historical_observation_count}",
        f"Latest historical match: {dated(prediction.latest_history_date)}",
        "Observations excluded on/after fixture date: "
        f"{prediction.excluded_observation_count}",
    ]
    if prediction.dispersion_size is not None:
        lines.append(f"Negative Binomial size: {prediction.dispersion_size:.6f}")
    for team in (prediction.home, prediction.away):
        lines.extend((
            "",
            f"{team.team} ({team.venue.value})",
            f"Expected corners: {team.expected_corners:.6f}",
            f"Team history: {team.historical_match_count} matches; "
            f"{team.venue_match_count} at this venue",
            f"Latest team match: {dated(team.latest_match_date)}",
            f"Latest team match at this venue: {dated(team.latest_venue_match_date)}",
        ))
        for probability in team.lines:
            text = (
                f"Line {probability.line:g}: OVER={probability.over:.6%} "
                f"UNDER={probability.under:.6%}"
            )
            if probability.line.is_integer():
                text += f" EXACT={probability.equal:.6%}"
            lines.append(text)
    lines.extend((
        "",
        "OVER means strictly more; UNDER means strictly fewer; EXACT means equal.",
        "History age is reported relative to the fixture date; data is not refreshed.",
    ))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, nargs="+", required=True,
                        help="non-overlapping provider CSVs for one competition")
    parser.add_argument("--provider", choices=PROVIDERS, default="football-data")
    parser.add_argument("--country", help="exact Kaggle Country value")
    parser.add_argument("--league", help="exact Kaggle League value")
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="fixture date YYYY-MM-DD")
    parser.add_argument("--home", required=True, help="exact dataset home-team name")
    parser.add_argument("--away", required=True, help="exact dataset away-team name")
    parser.add_argument("--home-lines", type=float, nargs="+", default=[],
                        help="home-team whole or half corner lines, e.g. 4.5 5.5")
    parser.add_argument("--away-lines", type=float, nargs="+", default=[],
                        help="away-team whole or half corner lines, e.g. 3.5 4.5")
    parser.add_argument("--model", choices=CORNER_FIXTURE_MODELS,
                        default="venue-opponent-negative-binomial")
    parser.add_argument("--min-history", type=int, default=100,
                        help="minimum prior team observations (default: 100)")
    parser.add_argument("--min-venue-history", type=int, default=5,
                        help="minimum prior matches at each team's fixture venue (default: 5)")
    parser.add_argument("--smoothing-matches", type=float, default=5.0)
    args = parser.parse_args()
    try:
        fixture = UpcomingFixture(args.date, args.home, args.away)
        observations = load_provider_observations(
            args.provider, args.history, args.country, args.league,
        )
        prediction = predict_corner_fixture(
            observations, fixture, args.home_lines, args.away_lines,
            model=args.model, min_history=args.min_history,
            min_venue_history=args.min_venue_history,
            smoothing_matches=args.smoothing_matches,
        )
    except ValueError as error:
        parser.error(str(error))
    print(f"Provider: {args.provider}")
    if args.provider == "kaggle-match-stats":
        print(f"Country / league: {args.country} / {args.league}")
    print(format_corner_prediction(prediction))


if __name__ == "__main__":
    main()
