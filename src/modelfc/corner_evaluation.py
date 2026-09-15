"""Chronologically evaluate simple team-corner count baselines."""

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

from modelfc.corners import (
    CornerPrediction, negative_binomial_log_probability,
    rolling_corner_predictions,
)
from modelfc.matches import TeamCornerObservation
from modelfc.providers.argentina import load_argentina_corner_observations
from modelfc.providers.brasileirao import load_br_corner_observations
from modelfc.providers.football_data import load_corner_history
from modelfc.providers.kaggle_match_stats import (
    load_kaggle_match_stats_corner_observations,
)
from modelfc.providers.mls import load_mls_corner_observations


@dataclass(frozen=True)
class CornerEvaluation:
    observation_count: int
    mae: float
    rmse: float
    average_negative_log_likelihood: float | None


class CornerProviderError(ValueError):
    """Raised when corner-evaluation provider arguments are invalid."""


def load_provider_observations(
    provider: str, csv_paths: list[Path],
    country: str | None = None, league: str | None = None,
) -> list[TeamCornerObservation]:
    """Normalize provider files into the shared corner-observation model."""

    if provider == "football-data":
        if not csv_paths:
            raise CornerProviderError("football-data requires at least one CSV file")
        return load_corner_history(csv_paths)
    if provider == "brasileirao":
        if len(csv_paths) != 2:
            raise CornerProviderError(
                "brasileirao requires exactly two CSV files: matches and statistics"
            )
        return load_br_corner_observations(csv_paths[0], csv_paths[1])
    if provider == "argentina":
        if len(csv_paths) != 1:
            raise CornerProviderError("argentina requires exactly one CSV file")
        return load_argentina_corner_observations(csv_paths[0])
    if provider == "mls":
        if len(csv_paths) != 1:
            raise CornerProviderError("mls requires exactly one CSV file")
        return load_mls_corner_observations(csv_paths[0])
    if provider == "kaggle-match-stats":
        if len(csv_paths) != 1:
            raise CornerProviderError(
                "kaggle-match-stats requires exactly one CSV file"
            )
        if not country or not league:
            raise CornerProviderError(
                "kaggle-match-stats requires both --country and --league"
            )
        return load_kaggle_match_stats_corner_observations(
            csv_paths[0], country, league,
        )
    raise CornerProviderError(f"unsupported provider: {provider}")


def negative_log_likelihood(prediction: CornerPrediction) -> float:
    """Return true count NLL, independent of the truncated display values."""

    if prediction.probabilities is None:
        raise ValueError("prediction has no probability distribution")
    observed = prediction.observation.corners_for
    rate = prediction.expected_corners
    if prediction.dispersion_size is not None:
        return -negative_binomial_log_probability(
            observed, rate, prediction.dispersion_size,
        )
    if rate == 0:
        return 0.0 if observed == 0 else math.inf
    return rate - observed * math.log(rate) + math.lgamma(observed + 1)


def evaluate_corner_predictions(predictions: Iterable[CornerPrediction]) -> CornerEvaluation:
    """Calculate point metrics and probability NLL when distributions exist."""

    items = list(predictions)
    if not items:
        raise ValueError("cannot evaluate an empty prediction collection")
    errors = [item.expected_corners - item.observation.corners_for for item in items]
    probabilistic = [item for item in items if item.probabilities is not None]
    if probabilistic and len(probabilistic) != len(items):
        raise ValueError("predictions must consistently include probability distributions")
    return CornerEvaluation(
        observation_count=len(items),
        mae=sum(abs(error) for error in errors) / len(items),
        rmse=math.sqrt(sum(error * error for error in errors) / len(items)),
        average_negative_log_likelihood=(
            sum(negative_log_likelihood(item) for item in probabilistic) / len(items)
            if probabilistic else None
        ),
    )


def format_corner_evaluation(
    evaluation: CornerEvaluation, distribution_name: str = "Poisson",
) -> str:
    lines = [
        f"Team observations evaluated: {evaluation.observation_count}",
        f"MAE: {evaluation.mae:.6f}",
        f"RMSE: {evaluation.rmse:.6f}",
    ]
    if evaluation.average_negative_log_likelihood is not None:
        lines.append(
            f"{distribution_name} average negative log likelihood: "
            f"{evaluation.average_negative_log_likelihood:.6f}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv", type=Path, nargs="*", help="provider CSV file(s)",
    )
    parser.add_argument(
        "--provider",
        choices=(
            "football-data", "brasileirao", "argentina", "kaggle-match-stats",
            "mls",
        ),
        default="football-data",
        help="input CSV provider (default: football-data)",
    )
    parser.add_argument("--country", help="exact Kaggle Country value")
    parser.add_argument("--league", help="exact Kaggle League value")
    parser.add_argument(
        "--model",
        choices=(
            "league-average", "team-average", "poisson", "venue-opponent",
            "venue-opponent-poisson",
            "venue-opponent-negative-binomial",
        ),
        default="league-average",
    )
    parser.add_argument("--min-history", type=int, default=100, help="earlier team observations required (default: 100)")
    parser.add_argument(
        "--max-corners", type=int, default=20,
        help="display distribution maximum (default: 20)",
    )
    parser.add_argument(
        "--smoothing-matches", type=float, default=5.0,
        help="venue-rate pseudo-observations (default: 5.0)",
    )
    args = parser.parse_args()
    try:
        if args.provider == "kaggle-match-stats":
            observations = load_provider_observations(
                args.provider, args.csv, args.country, args.league,
            )
        else:
            observations = load_provider_observations(args.provider, args.csv)
    except CornerProviderError as error:
        parser.error(str(error))
    predictions = rolling_corner_predictions(
        observations, args.model, args.min_history,
        args.max_corners, args.smoothing_matches,
    )
    if not predictions:
        parser.error("no predictions generated; use a lower --min-history or more data")
    distribution_name = (
        "Negative Binomial"
        if args.model == "venue-opponent-negative-binomial" else "Poisson"
    )
    print(format_corner_evaluation(
        evaluate_corner_predictions(predictions), distribution_name,
    ))


if __name__ == "__main__":
    main()
