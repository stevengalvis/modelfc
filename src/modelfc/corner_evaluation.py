"""Chronologically evaluate simple team-corner count baselines."""

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

from modelfc.corners import CornerPrediction, rolling_corner_predictions
from modelfc.providers.football_data import load_corner_history


@dataclass(frozen=True)
class CornerEvaluation:
    observation_count: int
    mae: float
    rmse: float
    average_negative_log_likelihood: float | None


def negative_log_likelihood(prediction: CornerPrediction) -> float:
    """Return the true Poisson NLL, independent of display truncation."""

    if prediction.probabilities is None:
        raise ValueError("prediction has no probability distribution")
    observed = prediction.observation.corners_for
    rate = prediction.expected_corners
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


def format_corner_evaluation(evaluation: CornerEvaluation) -> str:
    lines = [
        f"Team observations evaluated: {evaluation.observation_count}",
        f"MAE: {evaluation.mae:.6f}",
        f"RMSE: {evaluation.rmse:.6f}",
    ]
    if evaluation.average_negative_log_likelihood is not None:
        lines.append(f"Poisson average negative log likelihood: {evaluation.average_negative_log_likelihood:.6f}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="+", help="Football-Data season CSV(s)")
    parser.add_argument(
        "--model",
        choices=(
            "league-average", "team-average", "poisson", "venue-opponent",
            "venue-opponent-poisson",
        ),
        default="league-average",
    )
    parser.add_argument("--min-history", type=int, default=100, help="earlier team observations required (default: 100)")
    parser.add_argument("--max-corners", type=int, default=20, help="Poisson distribution maximum (default: 20)")
    parser.add_argument(
        "--smoothing-matches", type=float, default=5.0,
        help="venue-rate pseudo-observations (default: 5.0)",
    )
    args = parser.parse_args()
    predictions = rolling_corner_predictions(
        load_corner_history(args.csv), args.model, args.min_history,
        args.max_corners, args.smoothing_matches,
    )
    if not predictions:
        parser.error("no predictions generated; use a lower --min-history or more data")
    print(format_corner_evaluation(evaluate_corner_predictions(predictions)))


if __name__ == "__main__":
    main()
