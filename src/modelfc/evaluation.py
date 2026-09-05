"""Evaluation metrics and command-line reporting for probabilistic forecasts."""

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from modelfc.forecasts import (
    Forecast,
    rolling_league_frequency_forecasts,
    rolling_poisson_forecasts,
)
from modelfc.matches import MatchResult
from modelfc.providers.football_data import load_matches


def multiclass_brier_score(forecast: Forecast) -> float:
    """Return the sum of squared probability errors for a 1X2 forecast."""

    outcomes = (
        MatchResult.HOME_WIN,
        MatchResult.DRAW,
        MatchResult.AWAY_WIN,
    )
    return sum(
        (probability - float(forecast.match.result is outcome)) ** 2
        for probability, outcome in zip(forecast.probabilities, outcomes)
    )


@dataclass(frozen=True)
class Evaluation:
    """Aggregate evaluation statistics for a non-empty forecast collection."""

    forecast_count: int
    average_brier_score: float
    average_probabilities: tuple[float, float, float]
    actual_frequencies: tuple[float, float, float]


def evaluate(forecasts: Iterable[Forecast]) -> Evaluation:
    """Aggregate Brier scores, predictions, and observed result frequencies."""

    forecast_list = list(forecasts)
    if not forecast_list:
        raise ValueError("cannot evaluate an empty forecast collection")

    count = len(forecast_list)
    result_counts = Counter(forecast.match.result for forecast in forecast_list)
    return Evaluation(
        forecast_count=count,
        average_brier_score=sum(map(multiclass_brier_score, forecast_list)) / count,
        average_probabilities=tuple(
            sum(forecast.probabilities[index] for forecast in forecast_list) / count
            for index in range(3)
        ),
        actual_frequencies=tuple(
            result_counts[result] / count
            for result in (
                MatchResult.HOME_WIN,
                MatchResult.DRAW,
                MatchResult.AWAY_WIN,
            )
        ),
    )


def format_evaluation(evaluation: Evaluation) -> str:
    """Format aggregate statistics as a compact text report."""

    predicted = evaluation.average_probabilities
    actual = evaluation.actual_frequencies
    return "\n".join(
        (
            f"Forecasts evaluated: {evaluation.forecast_count}",
            f"Average Brier score: {evaluation.average_brier_score:.6f}",
            "Average predicted probabilities (home/draw/away): "
            f"{predicted[0]:.6f} / {predicted[1]:.6f} / {predicted[2]:.6f}",
            "Actual frequencies (home/draw/away): "
            f"{actual[0]:.6f} / {actual[1]:.6f} / {actual[2]:.6f}",
        )
    )


def main() -> None:
    """Evaluate a rolling forecasting model on a local season CSV."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="path to a Football-Data season CSV")
    parser.add_argument(
        "--model",
        choices=("baseline", "poisson"),
        default="baseline",
        help="forecasting model to evaluate (default: baseline)",
    )
    parser.add_argument(
        "--min-history",
        type=int,
        default=100,
        help="completed earlier matches required before forecasting (default: 100)",
    )
    parser.add_argument(
        "--max-goals",
        type=int,
        default=10,
        help="Poisson scoreline grid maximum for each team (default: 10)",
    )
    parser.add_argument(
        "--smoothing-matches",
        type=float,
        default=5.0,
        help="Poisson team-rate pseudo-match weight (default: 5)",
    )
    args = parser.parse_args()
    matches = load_matches(args.csv)
    if args.model == "baseline":
        forecasts = rolling_league_frequency_forecasts(matches, args.min_history)
    else:
        forecasts = rolling_poisson_forecasts(
            matches, args.min_history, args.max_goals, args.smoothing_matches
        )
    if not forecasts:
        parser.error("no forecasts generated; use a lower --min-history or a larger CSV")
    print(format_evaluation(evaluate(forecasts)))


if __name__ == "__main__":
    main()
