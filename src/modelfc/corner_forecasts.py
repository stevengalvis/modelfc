"""Fixture corner forecasts from strictly earlier, provider-independent history."""

from dataclasses import dataclass
from datetime import date
import math
from typing import Iterable

from modelfc.corners import (
    estimate_expected_corners,
    estimate_negative_binomial_size,
    negative_binomial_log_probability,
)
from modelfc.matches import TeamCornerObservation, UpcomingFixture, Venue


CORNER_FIXTURE_MODELS = (
    "venue-opponent-negative-binomial", "venue-opponent-poisson",
)


@dataclass(frozen=True)
class CornerLineProbability:
    """Probabilities of counts strictly above, below, or equal to a line."""

    line: float
    over: float
    under: float
    equal: float


@dataclass(frozen=True)
class TeamCornerForecast:
    team: str
    venue: Venue
    expected_corners: float
    historical_match_count: int
    venue_match_count: int
    latest_match_date: date
    latest_venue_match_date: date
    lines: tuple[CornerLineProbability, ...]


@dataclass(frozen=True)
class MatchCornerForecast:
    expected_corners: float
    method: str
    assumes_independence: bool
    lines: tuple[CornerLineProbability, ...]


@dataclass(frozen=True)
class CornerFixturePrediction:
    fixture: UpcomingFixture
    model: str
    home: TeamCornerForecast
    away: TeamCornerForecast
    historical_observation_count: int
    latest_history_date: date
    excluded_observation_count: int
    smoothing_matches: float
    dispersion_size: float | None
    total: MatchCornerForecast | None = None


def _log_count_probability(count: int, mean: float, size: float | None) -> float:
    if size is not None:
        return negative_binomial_log_probability(count, mean, size)
    if mean == 0:
        return 0.0 if count == 0 else -math.inf
    return count * math.log(mean) - mean - math.lgamma(count + 1)


def _upper_tail(mean: float, first_count: int, size: float | None) -> float:
    """Sum the survival tail directly, with a bound on the remaining mass."""
    if mean == 0:
        return 0.0
    log_first = _log_count_probability(first_count, mean, size)
    # Relative weights avoid underflow of the first term before summation.
    weights = [1.0]
    total = 1.0
    limiting_ratio = mean / (mean + size) if size is not None else 0.0
    for count in range(first_count, first_count + 100_000):
        ratio = (
            limiting_ratio * ((size + count) / (count + 1))
            if size is not None else mean / (count + 1)
        )
        # Poisson ratios decrease. NB ratios approach limiting_ratio, from
        # above when size > 1 and from below when size < 1.
        bound = max(ratio, limiting_ratio)
        if bound < 1 and weights[-1] * bound / (1 - bound) <= total * 1e-15:
            return min(1.0, math.exp(log_first + math.log(math.fsum(weights))))
        weight = weights[-1] * ratio
        weights.append(weight)
        total += weight
    raise ValueError("upper-tail summation did not converge for these model parameters")


def _validate_line_and_mean(mean: float, line: float) -> None:
    if (
        isinstance(line, bool) or not isinstance(line, (int, float))
        or not 0 <= line <= 1000 or line % 0.5 != 0
    ):
        raise ValueError("line must be a whole or half number between 0 and 1000")
    if (
        isinstance(mean, bool) or not isinstance(mean, (int, float))
        or not math.isfinite(mean) or mean < 0
    ):
        raise ValueError("mean must be a finite non-negative number")


def corner_line_probabilities(
    mean: float, line: float, dispersion_size: float | None = None,
) -> CornerLineProbability:
    """Use the full Poisson/NB distribution, never the truncated display grid.

    Integer lines have a separate equality probability. Half-integer lines
    cannot tie. The 0..1000 line bound limits work for malformed CLI input.
    """
    _validate_line_and_mean(mean, line)

    whole_line = line == math.floor(line)
    probabilities = [
        math.exp(_log_count_probability(count, mean, dispersion_size))
        for count in range(math.floor(line) + 1)
    ]
    equal = probabilities[-1] if whole_line else 0.0
    under = math.fsum(probabilities[:-1] if whole_line else probabilities)
    # Guard roundoff at the probability boundaries without renormalizing tails.
    equal = min(1.0, equal)
    under = min(1.0 - equal, under)
    lower_mass = math.fsum((under, equal))
    over = (
        _upper_tail(mean, math.floor(line) + 1, dispersion_size)
        if lower_mass > 0.5 else 1.0 - lower_mass
    )
    return CornerLineProbability(float(line), over, under, equal)


def match_total_line_probabilities(
    home_mean: float, away_mean: float, line: float,
    dispersion_size: float | None = None,
) -> CornerLineProbability:
    """Price the sum of two conditionally independent corner counts.

    Poisson sums are exactly Poisson. For NB2 forecasts, the component PMFs
    generally have different success probabilities, so their sum is evaluated
    by discrete convolution rather than approximated by another NB2 variable.
    The upper tail is summed directly when complementing the lower mass would
    lose precision.
    """
    _validate_line_and_mean(home_mean, line)
    _validate_line_and_mean(away_mean, line)
    if dispersion_size is None:
        return corner_line_probabilities(home_mean + away_mean, line)

    last_count = math.floor(line)
    home = [
        math.exp(_log_count_probability(count, home_mean, dispersion_size))
        for count in range(last_count + 1)
    ]
    away = [
        math.exp(_log_count_probability(count, away_mean, dispersion_size))
        for count in range(last_count + 1)
    ]
    totals = [
        math.fsum(home[index] * away[count - index] for index in range(count + 1))
        for count in range(last_count + 1)
    ]
    whole_line = line == last_count
    equal = totals[-1] if whole_line else 0.0
    under = math.fsum(totals[:-1] if whole_line else totals)
    equal = min(1.0, equal)
    under = min(1.0 - equal, under)
    lower_mass = math.fsum((under, equal))
    if lower_mass > 0.5:
        first_over = last_count + 1
        # Partition on the home count. For home counts below first_over, use
        # P(away >= first_over-home); the final term is P(home >= first_over).
        # Build every away survival value by stable backward addition from one
        # directly summed tail rather than subtracting a CDF from one.
        away_tails = [0.0] * (first_over + 1)
        away_tails[first_over] = _upper_tail(
            away_mean, first_over, dispersion_size,
        )
        for count in range(first_over - 1, -1, -1):
            away_tails[count] = math.fsum((away[count], away_tails[count + 1]))
        over = math.fsum((
            _upper_tail(home_mean, first_over, dispersion_size),
            math.fsum(
                home[count] * away_tails[first_over - count]
                for count in range(first_over)
            ),
        ))
        over = min(1.0, over)
    else:
        over = 1.0 - lower_mass
    return CornerLineProbability(float(line), over, under, equal)


def predict_corner_fixture(
    observations: Iterable[TeamCornerObservation],
    fixture: UpcomingFixture,
    home_lines: Iterable[float] = (),
    away_lines: Iterable[float] = (),
    total_lines: Iterable[float] = (),
    *,
    model: str = "venue-opponent-negative-binomial",
    min_history: int = 100,
    min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
) -> CornerFixturePrediction:
    """Predict both teams with explicit coverage gates and date cutoff.

    Minimum history counts are operational gates, not confidence guarantees.
    Supply observations from a single competition with non-overlapping files.
    """
    if model not in CORNER_FIXTURE_MODELS:
        raise ValueError(f"unsupported fixture corner model: {model}")
    for name, value in (("min_history", min_history), ("min_venue_history", min_venue_history)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    fixture = UpcomingFixture(
        fixture.match_date, fixture.home_team.strip(), fixture.away_team.strip(),
    )
    history = []
    excluded_count = 0
    seen = set()
    for item in observations:
        if item.match_date >= fixture.match_date:
            excluded_count += 1
            continue
        identity = (item.match_date, item.team, item.opponent, item.venue)
        if identity in seen:
            raise ValueError(
                f"duplicate historical observation: {item.match_date} "
                f"{item.team!r} vs {item.opponent!r} ({item.venue.value}); "
                "use non-overlapping history files"
            )
        seen.add(identity)
        history.append(item)
    if len(history) < min_history:
        raise ValueError(
            f"insufficient history before {fixture.match_date}: "
            f"{len(history)} team observations; need at least {min_history}"
        )
    history.sort(key=lambda item: item.match_date)
    size = (
        estimate_negative_binomial_size(history)
        if model == "venue-opponent-negative-binomial" else None
    )

    def predict_team(team: str, opponent: str, venue: Venue, lines: Iterable[float]) -> TeamCornerForecast:
        team_history = [item for item in history if item.team == team]
        if not team_history:
            available = ", ".join(sorted({item.team for item in history}))
            raise ValueError(
                f"no history for team {team!r} before {fixture.match_date}; "
                f"use an exact dataset name. Available teams: {available}"
            )
        venue_history = [item for item in team_history if item.venue is venue]
        if len(venue_history) < min_venue_history:
            raise ValueError(
                f"insufficient {venue.value} history for {team!r} before "
                f"{fixture.match_date}: {len(venue_history)} matches; "
                f"need at least {min_venue_history}"
            )
        mean = estimate_expected_corners(history, team, opponent, venue, smoothing_matches)
        return TeamCornerForecast(
            team, venue, mean, len(team_history), len(venue_history),
            max(item.match_date for item in team_history),
            max(item.match_date for item in venue_history),
            tuple(corner_line_probabilities(mean, line, size) for line in lines),
        )

    home = predict_team(fixture.home_team, fixture.away_team, Venue.HOME, home_lines)
    away = predict_team(fixture.away_team, fixture.home_team, Venue.AWAY, away_lines)
    total = MatchCornerForecast(
        home.expected_corners + away.expected_corners,
        ("poisson-sum" if size is None else "independent-discrete-convolution"),
        True,
        tuple(match_total_line_probabilities(
            home.expected_corners, away.expected_corners, line, size,
        ) for line in total_lines),
    )
    return CornerFixturePrediction(
        fixture, model, home, away, len(history),
        max(item.match_date for item in history), excluded_count,
        smoothing_matches, size, total,
    )
