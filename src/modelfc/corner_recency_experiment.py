"""Leakage-safe experiment for recent-form and time-decayed corner rates."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import groupby
import math
from pathlib import Path
from typing import Iterable

from modelfc.corner_data import (
    configured_history, configured_history_paths, load_data_config,
)
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corner_sources import PROVIDERS, load_provider_observations
from modelfc.corners import (
    POISSON_LIKE_SIZE, negative_binomial_log_probability,
)
from modelfc.matches import TeamCornerObservation, Venue


DEFAULT_WINDOWS = (5, 10, 20, 40)
DEFAULT_HALF_LIVES = (30.0, 60.0, 90.0, 180.0, 365.0)

_PROVIDER_COMPETITIONS = {
    "brasileirao": "Campeonato Brasileiro",
    "argentina": "Argentina Primera División",
    "mls": "Major League Soccer",
    "liga-mx": "Liga MX",
}


@dataclass(frozen=True)
class RecencyRow:
    observation: TeamCornerObservation
    dispersion_size: float
    expanding_mean: float
    window_means: tuple[tuple[int, float], ...]
    decay_means: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class RecencyMetrics:
    count: int
    mae: float
    rmse: float
    negative_log_likelihood: float
    line_brier: float
    line_log_loss: float


@dataclass(frozen=True)
class RecencyVariant:
    name: str
    parameter: float | None
    development: RecencyMetrics
    holdout: RecencyMetrics


@dataclass(frozen=True)
class RecencyExperiment:
    competition: str
    holdout_from: date
    development_start: date
    development_end: date
    holdout_start: date
    holdout_end: date
    min_history: int
    min_venue_history: int
    smoothing_matches: float
    lines: tuple[float, ...]
    window_candidates: tuple[int, ...]
    half_life_candidates: tuple[float, ...]
    variants: tuple[RecencyVariant, ...]


@dataclass
class _RunningMoments:
    count: int = 0
    total: int = 0
    total_squared: int = 0

    def add(self, value: int) -> None:
        self.count += 1
        self.total += value
        self.total_squared += value * value

    def size(self) -> float:
        if self.count < 2:
            return POISSON_LIKE_SIZE
        mean = self.total / self.count
        variance = (
            self.total_squared - self.total * self.total / self.count
        ) / (self.count - 1)
        if mean == 0 or variance <= mean:
            return POISSON_LIKE_SIZE
        return mean * mean / (variance - mean)


def _positive_number(name: str, value: float) -> None:
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value <= 0
    ):
        raise ValueError(f"{name} must be a finite positive number")


def _positive_integers(name: str, values: Iterable[int]) -> tuple[int, ...]:
    items = tuple(values)
    if not items or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in items
    ):
        raise ValueError(f"{name} must contain positive integers")
    if len(set(items)) != len(items):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(sorted(items))


def _positive_numbers(name: str, values: Iterable[float]) -> tuple[float, ...]:
    items = tuple(values)
    if not items:
        raise ValueError(f"{name} must not be empty")
    for value in items:
        _positive_number(name, value)
    normalized = tuple(float(value) for value in items)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(sorted(normalized))


def _lines(values: Iterable[float]) -> tuple[float, ...]:
    items = tuple(values)
    if not items or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value <= 0 or value >= 1000
        or value % 1 != 0.5
        for value in items
    ):
        raise ValueError("lines must be half-integers from 0.5 to 999.5")
    normalized = tuple(float(value) for value in items)
    if len(set(normalized)) != len(normalized):
        raise ValueError("lines must not contain duplicates")
    return tuple(sorted(normalized))


def _ordered_observations(
    observations: Iterable[TeamCornerObservation],
) -> list[TeamCornerObservation]:
    ordered = sorted(observations, key=lambda item: item.match_date)
    if not ordered:
        raise ValueError("corner history is empty")
    seen = set()
    for item in ordered:
        key = item.match_date, item.team, item.opponent, item.venue
        if key in seen:
            raise ValueError(f"duplicate corner observation: {key}")
        seen.add(key)
    return ordered


def _competition_label(
    provider: str, requested: str | None,
    country: str | None, league: str | None,
) -> str:
    """Return a label that cannot contradict the selected data source."""
    if provider == "football-data":
        if not requested or not requested.strip():
            raise ValueError("football-data requires --competition")
        return requested.strip()
    if requested is not None:
        raise ValueError("--competition is only valid with football-data")
    if provider == "kaggle-match-stats":
        if not country or not league:
            raise ValueError(
                "kaggle-match-stats requires both --country and --league"
            )
        return f"{country} / {league}"
    try:
        return _PROVIDER_COMPETITIONS[provider]
    except KeyError as error:
        raise ValueError(f"unsupported provider: {provider}") from error


def _mean_from_rates(
    league_sum: float, league_count: float,
    attack_sum: float, attack_count: float,
    concession_sum: float, concession_count: float,
    smoothing_matches: float,
) -> float:
    league_rate = (
        league_sum + smoothing_matches
    ) / (league_count + smoothing_matches)
    attack_rate = (
        attack_sum + smoothing_matches * league_rate
    ) / (attack_count + smoothing_matches)
    concession_rate = (
        concession_sum + smoothing_matches * league_rate
    ) / (concession_count + smoothing_matches)
    return attack_rate * concession_rate / league_rate


def _window_mean(
    league: list[TeamCornerObservation],
    attack: list[TeamCornerObservation],
    concession: list[TeamCornerObservation],
    window: int,
    smoothing_matches: float,
) -> float:
    recent_attack = attack[-window:]
    recent_concession = concession[-window:]
    return _mean_from_rates(
        sum(item.corners_for for item in league), len(league),
        sum(item.corners_for for item in recent_attack), len(recent_attack),
        sum(item.corners_against for item in recent_concession),
        len(recent_concession), smoothing_matches,
    )


def _decay_mean(
    league: list[TeamCornerObservation],
    attack: list[TeamCornerObservation],
    concession: list[TeamCornerObservation],
    reference: date,
    half_life_days: float,
    smoothing_matches: float,
) -> float:
    decay = math.log(2) / half_life_days

    def weighted(
        records: list[TeamCornerObservation], field: str,
    ) -> tuple[float, float]:
        weights = [
            math.exp(-decay * (reference - item.match_date).days)
            for item in records
        ]
        return (
            math.fsum(weight * getattr(item, field)
                      for weight, item in zip(weights, records)),
            math.fsum(weights),
        )

    league_sum, league_count = weighted(league, "corners_for")
    attack_sum, attack_count = weighted(attack, "corners_for")
    concession_sum, concession_count = weighted(
        concession, "corners_against",
    )
    return _mean_from_rates(
        league_sum, league_count, attack_sum, attack_count,
        concession_sum, concession_count, smoothing_matches,
    )


def rolling_recency_rows(
    observations: Iterable[TeamCornerObservation], *,
    windows: Iterable[int] = DEFAULT_WINDOWS,
    half_life_days: Iterable[float] = DEFAULT_HALF_LIVES,
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
) -> list[RecencyRow]:
    """Build every candidate from strictly earlier calendar dates."""
    selected_windows = _positive_integers("windows", windows)
    selected_half_lives = _positive_numbers("half_life_days", half_life_days)
    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history must be a positive integer")
    if (
        isinstance(min_venue_history, bool)
        or not isinstance(min_venue_history, int) or min_venue_history < 1
    ):
        raise ValueError("min_venue_history must be a positive integer")
    _positive_number("smoothing_matches", smoothing_matches)
    ordered = _ordered_observations(observations)
    venue_history: dict[Venue, list[TeamCornerObservation]] = defaultdict(list)
    team_venue_history: dict[
        tuple[str, Venue], list[TeamCornerObservation]
    ] = defaultdict(list)
    moments = _RunningMoments()
    prior_count = 0
    rows = []
    for target_date, group in groupby(ordered, key=lambda item: item.match_date):
        batch = list(group)
        if prior_count >= min_history:
            size = moments.size()
            for observation in batch:
                opposite = (
                    Venue.AWAY if observation.venue is Venue.HOME else Venue.HOME
                )
                league = venue_history[observation.venue]
                attack = team_venue_history[observation.team, observation.venue]
                concession = team_venue_history[observation.opponent, opposite]
                if (
                    len(attack) < min_venue_history
                    or len(concession) < min_venue_history
                ):
                    continue
                expanding = _window_mean(
                    league, attack, concession,
                    max(len(attack), len(concession)), smoothing_matches,
                )
                rows.append(RecencyRow(
                    observation, size, expanding,
                    tuple((window, _window_mean(
                        league, attack, concession, window, smoothing_matches,
                    )) for window in selected_windows),
                    tuple((half_life, _decay_mean(
                        league, attack, concession, target_date,
                        half_life, smoothing_matches,
                    )) for half_life in selected_half_lives),
                ))
        for observation in batch:
            venue_history[observation.venue].append(observation)
            team_venue_history[observation.team, observation.venue].append(observation)
            moments.add(observation.corners_for)
            prior_count += 1
    return rows


def _row_mean(row: RecencyRow, kind: str, parameter: float | None) -> float:
    if kind == "expanding":
        if parameter is not None:
            raise ValueError("expanding history does not accept a parameter")
        return row.expanding_mean
    if kind not in {"window", "decay"}:
        raise ValueError(f"unknown recency kind: {kind}")
    values = row.window_means if kind == "window" else row.decay_means
    for candidate, mean in values:
        if candidate == parameter:
            return mean
    raise ValueError(f"unknown {kind} parameter: {parameter}")


def score_recency_rows(
    rows: Iterable[RecencyRow], kind: str, parameter: float | None,
    lines: Iterable[float],
) -> RecencyMetrics:
    items = list(rows)
    if not items:
        raise ValueError("cannot score an empty recency cohort")
    selected_lines = _lines(lines)
    errors = []
    count_losses = []
    brier_losses = []
    log_losses = []
    for row in items:
        mean = _row_mean(row, kind, parameter)
        actual = row.observation.corners_for
        errors.append(mean - actual)
        count_losses.append(-negative_binomial_log_probability(
            actual, mean, row.dispersion_size,
        ))
        for line in selected_lines:
            probability = corner_line_probabilities(
                mean, line, row.dispersion_size,
            ).over
            happened = actual > line
            brier_losses.append((probability - happened) ** 2)
            selected = probability if happened else 1 - probability
            log_losses.append(-math.log(selected) if selected > 0 else math.inf)
    return RecencyMetrics(
        len(items), math.fsum(abs(error) for error in errors) / len(items),
        math.sqrt(math.fsum(error * error for error in errors) / len(items)),
        math.fsum(count_losses) / len(items),
        math.fsum(brier_losses) / len(brier_losses),
        math.fsum(log_losses) / len(log_losses),
    )


def _select_parameter(
    rows: list[RecencyRow], kind: str, candidates: Iterable[float],
) -> float:
    # On an exact tie, prefer the larger window or half-life because it is
    # closer to the lower-variance expanding baseline.
    return min(
        candidates,
        key=lambda value: (
            math.fsum(
                -negative_binomial_log_probability(
                    row.observation.corners_for,
                    _row_mean(row, kind, value), row.dispersion_size,
                )
                for row in rows
            ) / len(rows),
            -value,
        ),
    )


def run_recency_experiment(
    observations: Iterable[TeamCornerObservation], *, competition: str,
    holdout_from: date, windows: Iterable[int] = DEFAULT_WINDOWS,
    half_life_days: Iterable[float] = DEFAULT_HALF_LIVES,
    lines: Iterable[float] = (3.5, 4.5, 5.5, 6.5),
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
) -> RecencyExperiment:
    if not isinstance(competition, str) or not competition.strip():
        raise ValueError("competition must be non-empty text")
    if type(holdout_from) is not date:
        raise ValueError("holdout_from must be a calendar date")
    selected_windows = _positive_integers("windows", windows)
    selected_half_lives = _positive_numbers("half_life_days", half_life_days)
    selected_lines = _lines(lines)
    rows = rolling_recency_rows(
        observations, windows=selected_windows,
        half_life_days=selected_half_lives, min_history=min_history,
        min_venue_history=min_venue_history,
        smoothing_matches=smoothing_matches,
    )
    development = [
        row for row in rows if row.observation.match_date < holdout_from
    ]
    holdout = [
        row for row in rows if row.observation.match_date >= holdout_from
    ]
    if not development:
        raise ValueError("no eligible development observations before holdout_from")
    if not holdout:
        raise ValueError("no eligible holdout observations on or after holdout_from")
    selected_window = int(_select_parameter(
        development, "window", selected_windows,
    ))
    selected_half_life = _select_parameter(
        development, "decay", selected_half_lives,
    )
    variants = tuple(
        RecencyVariant(
            name, parameter,
            score_recency_rows(development, kind, parameter, selected_lines),
            score_recency_rows(holdout, kind, parameter, selected_lines),
        )
        for name, kind, parameter in (
            ("expanding", "expanding", None),
            ("recent-window", "window", selected_window),
            ("time-decay", "decay", selected_half_life),
        )
    )
    return RecencyExperiment(
        competition.strip(), holdout_from,
        development[0].observation.match_date,
        development[-1].observation.match_date,
        holdout[0].observation.match_date,
        holdout[-1].observation.match_date,
        min_history, min_venue_history, smoothing_matches, selected_lines,
        selected_windows, selected_half_lives, variants,
    )


def format_recency_experiment(experiment: RecencyExperiment) -> str:
    lines = [
        f"Competition: {experiment.competition}",
        f"Development dates: {experiment.development_start} through "
        f"{experiment.development_end}",
        f"Holdout dates: {experiment.holdout_start} through {experiment.holdout_end}",
        f"Coverage gates: {experiment.min_history} prior team observations; "
        f"{experiment.min_venue_history} prior venue matches per team",
        f"Smoothing matches: {experiment.smoothing_matches:g}",
        "Window candidates: " + ", ".join(
            str(value) for value in experiment.window_candidates
        ),
        "Half-life candidates (days): " + ", ".join(
            f"{value:g}" for value in experiment.half_life_candidates
        ),
        "Parameters minimize development Negative Binomial NLL and are frozen for holdout.",
        "Recent windows limit team attack and opponent concession records while "
        "retaining the expanding league venue prior.",
        "Time decay weights league venue, team attack and opponent concession "
        "records by age.",
        "Every variant uses the same observations and strictly earlier dates.",
        "",
        "Model  Parameter  Split  N  MAE  RMSE  Count NLL  Line Brier  Line log loss",
    ]
    for variant in experiment.variants:
        parameter = "all" if variant.parameter is None else f"{variant.parameter:g}"
        for split, metrics in (
            ("development", variant.development),
            ("holdout", variant.holdout),
        ):
            lines.append(
                f"{variant.name}  {parameter}  {split}  {metrics.count}  "
                f"{metrics.mae:.6f}  {metrics.rmse:.6f}  "
                f"{metrics.negative_log_likelihood:.6f}  "
                f"{metrics.line_brier:.6f}  {metrics.line_log_loss:.6f}"
            )
    lines.extend((
        "", "Lower is better for every reported metric.",
        "Holdout parameters are frozen; this is an offline experiment, not a live model change.",
    ))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="*", help="provider CSV file(s)")
    parser.add_argument("--data-config", type=Path)
    parser.add_argument("--competition")
    parser.add_argument("--provider", choices=PROVIDERS, default="football-data")
    parser.add_argument("--country")
    parser.add_argument("--league")
    parser.add_argument("--holdout-from", type=date.fromisoformat, required=True)
    parser.add_argument("--windows", type=int, nargs="+", default=DEFAULT_WINDOWS)
    parser.add_argument(
        "--half-life-days", type=float, nargs="+", default=DEFAULT_HALF_LIVES,
    )
    parser.add_argument("--lines", type=float, nargs="+", default=[3.5, 4.5, 5.5, 6.5])
    parser.add_argument("--min-history", type=int, default=100)
    parser.add_argument("--min-venue-history", type=int, default=5)
    parser.add_argument("--smoothing-matches", type=float, default=5.0)
    args = parser.parse_args()
    try:
        competition = _competition_label(
            args.provider, args.competition, args.country, args.league,
        )
        if args.data_config:
            if (
                args.csv or args.provider != "football-data"
                or args.country or args.league
            ):
                raise ValueError(
                    "--data-config uses only --competition and football-data"
                )
            config = load_data_config(args.data_config)
            source_paths = configured_history_paths(config, competition)
            observations = configured_history(
                config, competition,
            )
        else:
            source_paths = args.csv
            observations = load_provider_observations(
                args.provider, args.csv, args.country, args.league,
                competition=(competition if args.provider == "football-data" else None),
            )
        experiment = run_recency_experiment(
            observations, competition=competition,
            holdout_from=args.holdout_from, windows=args.windows,
            half_life_days=args.half_life_days, lines=args.lines,
            min_history=args.min_history,
            min_venue_history=args.min_venue_history,
            smoothing_matches=args.smoothing_matches,
        )
    except ValueError as error:
        parser.error(str(error))
    print("History: " + ", ".join(str(path) for path in source_paths))
    print(format_recency_experiment(experiment))


if __name__ == "__main__":
    main()
