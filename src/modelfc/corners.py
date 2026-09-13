"""Small, chronological baselines for team corner counts."""

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Iterable

from modelfc.matches import TeamCornerObservation, Venue


@dataclass(frozen=True)
class CornerPrediction:
    """A point prediction and, when applicable, a count distribution."""

    observation: TeamCornerObservation
    expected_corners: float
    probabilities: tuple[float, ...] | None = None


def poisson_probabilities(rate: float, max_corners: int = 20) -> tuple[float, ...]:
    """Return a Poisson count distribution conditioned on 0..max_corners."""

    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
        raise ValueError("rate must be a finite non-negative number")
    if isinstance(max_corners, bool) or not isinstance(max_corners, int) or max_corners < 0:
        raise ValueError("max_corners must be a non-negative integer")
    if rate == 0:
        return (1.0,) + (0.0,) * max_corners
    log_weights = [
        count * math.log(rate) - math.lgamma(count + 1)
        for count in range(max_corners + 1)
    ]
    largest = max(log_weights)
    weights = [math.exp(weight - largest) for weight in log_weights]
    total = sum(weights)
    return tuple(weight / total for weight in weights)


def estimate_expected_corners(
    history: Iterable[TeamCornerObservation],
    team: str,
    opponent: str,
    venue: Venue,
    smoothing_matches: float = 5.0,
) -> float:
    """Estimate corners from venue attack and opponent concession records.

    Each relevant team rate is shrunk toward the corresponding league venue
    rate with ``smoothing_matches`` pseudo-observations.  As in the goal-rate
    estimator, the league rates use one-corner pseudo-observations so that the
    estimate remains defined for empty or all-zero histories.
    """

    if (
        not isinstance(smoothing_matches, (int, float))
        or isinstance(smoothing_matches, bool)
        or not math.isfinite(smoothing_matches)
        or smoothing_matches <= 0
    ):
        raise ValueError("smoothing_matches must be a finite positive number")

    if not isinstance(venue, Venue):
        raise ValueError("venue must be a Venue")

    observations = list(history)
    venue_history = [item for item in observations if item.venue is venue]
    league_rate = (
        sum(item.corners_for for item in venue_history) + smoothing_matches
    ) / (len(venue_history) + smoothing_matches)

    attack_history = [
        item for item in venue_history if item.team == team
    ]
    opponent_venue = Venue.AWAY if venue is Venue.HOME else Venue.HOME
    concession_history = [
        item
        for item in observations
        if item.team == opponent and item.venue is opponent_venue
    ]
    attack_rate = (
        sum(item.corners_for for item in attack_history)
        + smoothing_matches * league_rate
    ) / (len(attack_history) + smoothing_matches)
    concession_rate = (
        sum(item.corners_against for item in concession_history)
        + smoothing_matches * league_rate
    ) / (len(concession_history) + smoothing_matches)
    return attack_rate * concession_rate / league_rate


def rolling_corner_predictions(
    observations: Iterable[TeamCornerObservation],
    model: str,
    min_history: int = 100,
    max_corners: int = 20,
    smoothing_matches: float = 5.0,
) -> list[CornerPrediction]:
    """Predict observations using counts from strictly earlier dates."""

    if model not in {
        "league-average", "team-average", "poisson", "venue-opponent",
        "venue-opponent-poisson",
    }:
        raise ValueError("unknown corner model")
    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history must be a positive integer")
    # Validate even when the selected model does not use smoothing, so the CLI
    # and Python API handle this option consistently.
    estimate_expected_corners([], "team", "opponent", Venue.HOME, smoothing_matches)

    ordered = sorted(observations, key=lambda item: item.match_date)
    league_total = 0
    league_count = 0
    team_totals: dict[str, int] = defaultdict(int)
    team_counts: dict[str, int] = defaultdict(int)
    predictions: list[CornerPrediction] = []
    index = 0
    while index < len(ordered):
        target_date = ordered[index].match_date
        end = index
        while end < len(ordered) and ordered[end].match_date == target_date:
            end += 1

        if league_count >= min_history:
            league_mean = league_total / league_count
            for observation in ordered[index:end]:
                if model == "league-average":
                    expected = league_mean
                elif model in {"venue-opponent", "venue-opponent-poisson"}:
                    expected = estimate_expected_corners(
                        ordered[:index], observation.team,
                        observation.opponent, observation.venue,
                        smoothing_matches,
                    )
                else:
                    count = team_counts[observation.team]
                    expected = (
                        team_totals[observation.team] / count
                        if count else league_mean
                    )
                distribution = (
                    poisson_probabilities(expected, max_corners)
                    if model in {"poisson", "venue-opponent-poisson"} else None
                )
                predictions.append(CornerPrediction(observation, expected, distribution))

        # The entire date is added only after every prediction has been made.
        for observation in ordered[index:end]:
            league_total += observation.corners_for
            league_count += 1
            team_totals[observation.team] += observation.corners_for
            team_counts[observation.team] += 1
        index = end
    return predictions
