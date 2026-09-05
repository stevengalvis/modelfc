"""Provider-independent probabilistic forecasts and simple baselines."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
import math
from typing import Iterable

from modelfc.matches import Match, MatchResult, UpcomingFixture


@dataclass(frozen=True)
class Forecast:
    """A 1X2 probability forecast for a normalized match."""

    match: Match
    home_win_probability: float
    draw_probability: float
    away_win_probability: float

    def __post_init__(self) -> None:
        probabilities = self.probabilities
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in probabilities):
            raise ValueError("forecast probabilities must be finite values between 0 and 1")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("forecast probabilities must sum to 1")

    @property
    def probabilities(self) -> tuple[float, float, float]:
        """Return probabilities in home-win, draw, away-win order."""

        return (
            self.home_win_probability,
            self.draw_probability,
            self.away_win_probability,
        )


@dataclass(frozen=True)
class FixturePrediction:
    """Plain-Poisson prediction for an upcoming fixture."""

    fixture: UpcomingFixture
    expected_home_goals: float
    expected_away_goals: float
    home_win_probability: float
    draw_probability: float
    away_win_probability: float
    historical_match_count: int

    @property
    def probabilities(self) -> tuple[float, float, float]:
        """Return probabilities in home-win, draw, away-win order."""

        return (
            self.home_win_probability,
            self.draw_probability,
            self.away_win_probability,
        )


def rolling_league_frequency_forecasts(
    matches: Iterable[Match], min_history: int = 100
) -> list[Forecast]:
    """Forecast each match from result frequencies on strictly earlier dates.

    Matches are processed chronologically. All matches on one date receive a
    forecast from the same history, because the normalized match model has no
    kickoff time with which to establish an order within that date.
    """

    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history must be a positive integer")

    ordered_matches = sorted(matches, key=lambda match: match.match_date)
    counts: Counter[MatchResult] = Counter()
    forecasts: list[Forecast] = []
    index = 0

    while index < len(ordered_matches):
        match_date = ordered_matches[index].match_date
        end = index
        while end < len(ordered_matches) and ordered_matches[end].match_date == match_date:
            end += 1

        history_size = sum(counts.values())
        if history_size >= min_history:
            probabilities = tuple(
                counts[result] / history_size
                for result in (
                    MatchResult.HOME_WIN,
                    MatchResult.DRAW,
                    MatchResult.AWAY_WIN,
                )
            )
            for match in ordered_matches[index:end]:
                forecasts.append(Forecast(match, *probabilities))

        counts.update(match.result for match in ordered_matches[index:end])
        index = end

    return forecasts


def poisson_1x2_probabilities(
    expected_home_goals: float,
    expected_away_goals: float,
    max_goals: int = 10,
) -> tuple[float, float, float]:
    """Convert independent Poisson goal rates to normalized 1X2 probabilities.

    The finite score grid omits scorelines above ``max_goals``.  Normalizing
    the three outcome totals assigns that truncated mass proportionally and
    guarantees a valid probability distribution.
    """

    for name, value in (
        ("expected_home_goals", expected_home_goals),
        ("expected_away_goals", expected_away_goals),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} must be a finite non-negative number")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
    if isinstance(max_goals, bool) or not isinstance(max_goals, int) or max_goals < 0:
        raise ValueError("max_goals must be a non-negative integer")

    def probabilities(rate: float) -> list[float]:
        if rate == 0:
            return [1.0] + [0.0] * max_goals
        # The common exp(-rate) factor cancels during truncation
        # normalization. Shifting log weights avoids under/overflow.
        log_weights = [
            goals * math.log(rate) - math.lgamma(goals + 1)
            for goals in range(max_goals + 1)
        ]
        largest = max(log_weights)
        weights = [math.exp(weight - largest) for weight in log_weights]
        total = sum(weights)
        return [weight / total for weight in weights]

    home_scores = probabilities(expected_home_goals)
    away_scores = probabilities(expected_away_goals)
    outcomes = [0.0, 0.0, 0.0]
    for home_goals, home_probability in enumerate(home_scores):
        for away_goals, away_probability in enumerate(away_scores):
            index = (
                0
                if home_goals > away_goals
                else 1
                if home_goals == away_goals
                else 2
            )
            outcomes[index] += home_probability * away_probability

    retained_mass = sum(outcomes)
    # Each marginal was conditioned on the retained range; normalize once more
    # to absorb floating-point summation error in the outcome aggregation.
    return tuple(probability / retained_mass for probability in outcomes)


def dixon_coles_correction(
    home_goals: int,
    away_goals: int,
    expected_home_goals: float,
    expected_away_goals: float,
    rho: float,
) -> float:
    """Return the Dixon-Coles multiplier for a scoreline.

    Only 0-0, 1-0, 0-1, and 1-1 differ from the independent-Poisson model.
    """

    if (home_goals, away_goals) == (0, 0):
        return 1.0 - expected_home_goals * expected_away_goals * rho
    if (home_goals, away_goals) == (0, 1):
        return 1.0 + expected_home_goals * rho
    if (home_goals, away_goals) == (1, 0):
        return 1.0 + expected_away_goals * rho
    if (home_goals, away_goals) == (1, 1):
        return 1.0 - rho
    return 1.0


def dixon_coles_1x2_probabilities(
    expected_home_goals: float,
    expected_away_goals: float,
    rho: float,
    max_goals: int = 10,
) -> tuple[float, float, float]:
    """Convert Dixon-Coles-corrected score probabilities to normalized 1X2."""

    # Reuse the Poisson validation, including the score-grid validation.
    poisson_1x2_probabilities(expected_home_goals, expected_away_goals, max_goals)
    if (
        not isinstance(rho, (int, float))
        or isinstance(rho, bool)
        or not math.isfinite(rho)
    ):
        raise ValueError("rho must be a finite number")

    def score_probabilities(rate: float) -> list[float]:
        if rate == 0:
            return [1.0] + [0.0] * max_goals
        log_weights = [
            goals * math.log(rate) - math.lgamma(goals + 1)
            for goals in range(max_goals + 1)
        ]
        largest = max(log_weights)
        weights = [math.exp(weight - largest) for weight in log_weights]
        total = sum(weights)
        return [weight / total for weight in weights]

    home_scores = score_probabilities(expected_home_goals)
    away_scores = score_probabilities(expected_away_goals)
    outcomes = [0.0, 0.0, 0.0]
    for home_goals, home_probability in enumerate(home_scores):
        for away_goals, away_probability in enumerate(away_scores):
            correction = dixon_coles_correction(
                home_goals,
                away_goals,
                expected_home_goals,
                expected_away_goals,
                rho,
            )
            if correction < 0:
                raise ValueError("rho produces a negative low-score probability")
            outcome = (
                0
                if home_goals > away_goals
                else 1
                if home_goals == away_goals
                else 2
            )
            outcomes[outcome] += home_probability * away_probability * correction

    retained_mass = sum(outcomes)
    if retained_mass <= 0:
        raise ValueError("corrected score grid must have positive probability mass")
    return tuple(probability / retained_mass for probability in outcomes)


def estimate_expected_goals(
    history: Iterable[Match],
    home_team: str,
    away_team: str,
    smoothing_matches: float = 5.0,
) -> tuple[float, float]:
    """Estimate goal rates from venue-specific attack and defence records.

    Team rates are shrunk toward the corresponding league scoring rate using
    ``smoothing_matches`` pseudo-matches.  League rates themselves use the
    same number of one-goal pseudo-matches, keeping estimates positive even in
    an unusually scoreless or very small history.
    """

    if (
        not isinstance(smoothing_matches, (int, float))
        or isinstance(smoothing_matches, bool)
        or not math.isfinite(smoothing_matches)
        or smoothing_matches <= 0
    ):
        raise ValueError("smoothing_matches must be a finite positive number")

    matches = list(history)
    match_count = len(matches)
    league_home_rate = (
        sum(match.home_goals for match in matches) + smoothing_matches
    ) / (match_count + smoothing_matches)
    league_away_rate = (
        sum(match.away_goals for match in matches) + smoothing_matches
    ) / (match_count + smoothing_matches)

    home_games = home_scored = home_conceded = 0
    away_games = away_scored = away_conceded = 0
    for match in matches:
        if match.home_team == home_team:
            home_games += 1
            home_scored += match.home_goals
            home_conceded += match.away_goals
        if match.away_team == away_team:
            away_games += 1
            away_scored += match.away_goals
            away_conceded += match.home_goals

    home_attack_rate = (home_scored + smoothing_matches * league_home_rate) / (
        home_games + smoothing_matches
    )
    home_defence_rate = (home_conceded + smoothing_matches * league_away_rate) / (
        home_games + smoothing_matches
    )
    away_attack_rate = (away_scored + smoothing_matches * league_away_rate) / (
        away_games + smoothing_matches
    )
    away_defence_rate = (away_conceded + smoothing_matches * league_home_rate) / (
        away_games + smoothing_matches
    )

    expected_home = home_attack_rate * away_defence_rate / league_home_rate
    expected_away = away_attack_rate * home_defence_rate / league_away_rate
    return expected_home, expected_away


def predict_upcoming_fixture(
    history: Iterable[Match],
    fixture: UpcomingFixture,
    max_goals: int = 10,
    smoothing_matches: float = 5.0,
) -> FixturePrediction:
    """Predict a fixture using completed matches from strictly earlier dates."""

    eligible_history = [
        match for match in history if match.match_date < fixture.match_date
    ]
    expected_home, expected_away = estimate_expected_goals(
        eligible_history,
        fixture.home_team,
        fixture.away_team,
        smoothing_matches,
    )
    probabilities = poisson_1x2_probabilities(
        expected_home, expected_away, max_goals
    )
    return FixturePrediction(
        fixture,
        expected_home,
        expected_away,
        *probabilities,
        len(eligible_history),
    )


def exponential_time_weight(
    match_date: date,
    reference_date: date,
    half_life_days: float = 180.0,
) -> float:
    """Return a match's exponential weight relative to a later date.

    A match exactly one half-life old receives weight 0.5.  Callers must pass
    a strictly earlier match date so this helper cannot silently enable
    target-date or future leakage.
    """

    if (
        not isinstance(half_life_days, (int, float))
        or isinstance(half_life_days, bool)
        or not math.isfinite(half_life_days)
        or half_life_days <= 0
    ):
        raise ValueError("half_life_days must be a finite positive number")
    age_days = (reference_date - match_date).days
    if age_days <= 0:
        raise ValueError("match_date must be strictly earlier than reference_date")
    return math.exp2(-age_days / half_life_days)


def estimate_decay_expected_goals(
    history: Iterable[Match],
    home_team: str,
    away_team: str,
    reference_date: date,
    half_life_days: float = 180.0,
    smoothing_matches: float = 5.0,
) -> tuple[float, float]:
    """Estimate venue-specific rates with exponential recency weighting."""

    # Reuse the established smoothing validation without changing that model.
    estimate_expected_goals([], "home", "away", smoothing_matches)
    weighted_matches = [
        (
            match,
            exponential_time_weight(
                match.match_date, reference_date, half_life_days
            ),
        )
        for match in history
    ]
    total_weight = sum(weight for _, weight in weighted_matches)
    league_home_rate = (
        sum(weight * match.home_goals for match, weight in weighted_matches)
        + smoothing_matches
    ) / (total_weight + smoothing_matches)
    league_away_rate = (
        sum(weight * match.away_goals for match, weight in weighted_matches)
        + smoothing_matches
    ) / (total_weight + smoothing_matches)

    home_weight = home_scored = home_conceded = 0.0
    away_weight = away_scored = away_conceded = 0.0
    for match, weight in weighted_matches:
        if match.home_team == home_team:
            home_weight += weight
            home_scored += weight * match.home_goals
            home_conceded += weight * match.away_goals
        if match.away_team == away_team:
            away_weight += weight
            away_scored += weight * match.away_goals
            away_conceded += weight * match.home_goals

    home_attack_rate = (home_scored + smoothing_matches * league_home_rate) / (
        home_weight + smoothing_matches
    )
    home_defence_rate = (home_conceded + smoothing_matches * league_away_rate) / (
        home_weight + smoothing_matches
    )
    away_attack_rate = (away_scored + smoothing_matches * league_away_rate) / (
        away_weight + smoothing_matches
    )
    away_defence_rate = (away_conceded + smoothing_matches * league_home_rate) / (
        away_weight + smoothing_matches
    )
    return (
        home_attack_rate * away_defence_rate / league_home_rate,
        away_attack_rate * home_defence_rate / league_away_rate,
    )


def _rho_interval(
    rate_pairs: Iterable[tuple[float, float]], rho_bound: float
) -> tuple[float, float]:
    """Find bounds that keep every relevant correction strictly positive."""

    lower, upper = -rho_bound, rho_bound
    epsilon = 1e-12
    for expected_home, expected_away in rate_pairs:
        if expected_home > 0:
            lower = max(lower, -1.0 / expected_home + epsilon)
        if expected_away > 0:
            lower = max(lower, -1.0 / expected_away + epsilon)
        if expected_home * expected_away > 0:
            upper = min(upper, 1.0 / (expected_home * expected_away) - epsilon)
        upper = min(upper, 1.0 - epsilon)
    return lower, upper


def estimate_dixon_coles_rho(
    history: Iterable[Match],
    smoothing_matches: float = 5.0,
    rho_bound: float = 0.2,
    additional_rate_pairs: Iterable[tuple[float, float]] = (),
) -> float:
    """Maximum-likelihood estimate ``rho`` from completed historical matches.

    Expected goals use the existing Poisson team-strength estimator. Because
    its likelihood does not depend on ``rho``, maximizing the Dixon-Coles
    likelihood reduces to a deterministic one-dimensional concave problem.
    ``additional_rate_pairs`` can constrain the estimate for target fixtures;
    it contains no target scores or results.
    """

    if (
        not isinstance(rho_bound, (int, float))
        or isinstance(rho_bound, bool)
        or not math.isfinite(rho_bound)
        or rho_bound <= 0
    ):
        raise ValueError("rho_bound must be a finite positive number")

    matches = list(history)
    # This also validates smoothing_matches for an empty history.
    estimate_expected_goals([], "home", "away", smoothing_matches)
    observations = []
    historical_rates = []
    for match in matches:
        rates = estimate_expected_goals(
            matches, match.home_team, match.away_team, smoothing_matches
        )
        historical_rates.append(rates)
        if (match.home_goals, match.away_goals) == (0, 0):
            coefficient = -rates[0] * rates[1]
        elif (match.home_goals, match.away_goals) == (0, 1):
            coefficient = rates[0]
        elif (match.home_goals, match.away_goals) == (1, 0):
            coefficient = rates[1]
        elif (match.home_goals, match.away_goals) == (1, 1):
            coefficient = -1.0
        else:
            continue
        observations.append(coefficient)

    lower, upper = _rho_interval(
        historical_rates + list(additional_rate_pairs), float(rho_bound)
    )
    if lower > upper:
        raise ValueError("rho_bound has no valid correction interval")
    if not observations:
        return min(max(0.0, lower), upper)

    def derivative(value: float) -> float:
        return sum(
            coefficient / (1.0 + coefficient * value)
            for coefficient in observations
        )

    if derivative(lower) <= 0:
        return lower
    if derivative(upper) >= 0:
        return upper
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        if derivative(midpoint) > 0:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def rolling_poisson_forecasts(
    matches: Iterable[Match],
    min_history: int = 100,
    max_goals: int = 10,
    smoothing_matches: float = 5.0,
) -> list[Forecast]:
    """Forecast matches with venue-specific team strengths from prior dates."""

    if (
        isinstance(min_history, bool)
        or not isinstance(min_history, int)
        or min_history < 1
    ):
        raise ValueError("min_history must be a positive integer")
    # Validate model parameters even when there are no eligible matches.
    poisson_1x2_probabilities(1.0, 1.0, max_goals)
    estimate_expected_goals([], "home", "away", smoothing_matches)

    ordered_matches = sorted(matches, key=lambda match: match.match_date)
    history: list[Match] = []
    forecasts: list[Forecast] = []
    index = 0
    while index < len(ordered_matches):
        match_date = ordered_matches[index].match_date
        end = index
        while end < len(ordered_matches) and ordered_matches[end].match_date == match_date:
            end += 1

        if len(history) >= min_history:
            for match in ordered_matches[index:end]:
                rates = estimate_expected_goals(
                    history, match.home_team, match.away_team, smoothing_matches
                )
                forecasts.append(
                    Forecast(match, *poisson_1x2_probabilities(*rates, max_goals))
                )

        # Updating only after the complete date is forecast preserves isolation.
        history.extend(ordered_matches[index:end])
        index = end

    return forecasts


def rolling_poisson_decay_forecasts(
    matches: Iterable[Match],
    min_history: int = 100,
    max_goals: int = 10,
    smoothing_matches: float = 5.0,
    half_life_days: float = 180.0,
) -> list[Forecast]:
    """Forecast with Poisson team strengths weighted by match recency."""

    if (
        isinstance(min_history, bool)
        or not isinstance(min_history, int)
        or min_history < 1
    ):
        raise ValueError("min_history must be a positive integer")
    poisson_1x2_probabilities(1.0, 1.0, max_goals)
    estimate_expected_goals([], "home", "away", smoothing_matches)
    # Validate the half-life even if no match becomes eligible.
    exponential_time_weight(date.min, date.min.replace(day=2), half_life_days)

    ordered_matches = sorted(matches, key=lambda match: match.match_date)
    history: list[Match] = []
    forecasts: list[Forecast] = []
    index = 0
    while index < len(ordered_matches):
        match_date = ordered_matches[index].match_date
        end = index
        while end < len(ordered_matches) and ordered_matches[end].match_date == match_date:
            end += 1

        if len(history) >= min_history:
            for match in ordered_matches[index:end]:
                rates = estimate_decay_expected_goals(
                    history,
                    match.home_team,
                    match.away_team,
                    match_date,
                    half_life_days,
                    smoothing_matches,
                )
                probabilities = poisson_1x2_probabilities(*rates, max_goals)
                forecasts.append(Forecast(match, *probabilities))

        # The whole date remains invisible until every fixture on it is forecast.
        history.extend(ordered_matches[index:end])
        index = end
    return forecasts


def rolling_dixon_coles_forecasts(
    matches: Iterable[Match],
    min_history: int = 100,
    max_goals: int = 10,
    smoothing_matches: float = 5.0,
    rho_bound: float = 0.2,
) -> list[Forecast]:
    """Forecast with Poisson team strengths plus Dixon-Coles score dependence."""

    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history must be a positive integer")
    dixon_coles_1x2_probabilities(1.0, 1.0, 0.0, max_goals)
    estimate_dixon_coles_rho([], smoothing_matches, rho_bound)

    ordered_matches = sorted(matches, key=lambda match: match.match_date)
    history: list[Match] = []
    forecasts: list[Forecast] = []
    index = 0
    while index < len(ordered_matches):
        match_date = ordered_matches[index].match_date
        end = index
        while end < len(ordered_matches) and ordered_matches[end].match_date == match_date:
            end += 1

        if len(history) >= min_history:
            rates_by_match = [
                estimate_expected_goals(
                    history, match.home_team, match.away_team, smoothing_matches
                )
                for match in ordered_matches[index:end]
            ]
            rho = estimate_dixon_coles_rho(
                history, smoothing_matches, rho_bound, rates_by_match
            )
            for match, rates in zip(ordered_matches[index:end], rates_by_match):
                forecasts.append(
                    Forecast(
                        match,
                        *dixon_coles_1x2_probabilities(*rates, rho, max_goals),
                    )
                )

        # As with Poisson, the complete date enters history only after forecasts.
        history.extend(ordered_matches[index:end])
        index = end

    return forecasts
