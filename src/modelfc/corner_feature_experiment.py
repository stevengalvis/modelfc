"""Leakage-safe experiment for adding shot history to corner forecasts."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import groupby
import math
from typing import Iterable

from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corners import negative_binomial_log_probability, POISSON_LIKE_SIZE
from modelfc.matches import Venue
from modelfc.team_match_stats import TeamMatchStats


DEFAULT_WEIGHT_GRID = tuple(index / 20 for index in range(-10, 11))


@dataclass(frozen=True)
class FeatureRow:
    """One historical prediction made from strictly earlier dates."""

    record: TeamMatchStats
    baseline_mean: float
    shot_ratio: float
    shots_on_target_ratio: float
    dispersion_size: float


@dataclass(frozen=True)
class FeatureMetrics:
    count: int
    mae: float
    rmse: float
    negative_log_likelihood: float
    line_brier: float
    line_log_loss: float


@dataclass(frozen=True)
class FeatureVariant:
    name: str
    shot_weight: float
    shots_on_target_weight: float
    development: FeatureMetrics
    holdout: FeatureMetrics


@dataclass(frozen=True)
class FeatureExperiment:
    competition: str
    holdout_from: date
    lines: tuple[float, ...]
    min_history: int
    min_venue_history: int
    smoothing_matches: float
    development_start: date
    development_end: date
    holdout_start: date
    holdout_end: date
    excluded_incomplete_fixtures: int
    variants: tuple[FeatureVariant, ...]


@dataclass
class _FeatureTotals:
    count: int = 0
    corners_for: int = 0
    corners_against: int = 0
    shots_for: int = 0
    shots_against: int = 0
    shots_on_target_for: int = 0
    shots_on_target_against: int = 0

    def add(self, record: TeamMatchStats) -> None:
        self.count += 1
        for field in (
            "corners_for", "corners_against", "shots_for", "shots_against",
            "shots_on_target_for", "shots_on_target_against",
        ):
            value = getattr(record, field)
            if value is None:  # Guarded by _validate_records.
                raise ValueError(f"{field} is required for the shot-feature experiment")
            setattr(self, field, getattr(self, field) + value)


@dataclass
class _CornerMoments:
    count: int = 0
    total: int = 0
    total_squared: int = 0

    def add(self, value: int) -> None:
        self.count += 1
        self.total += value
        self.total_squared += value * value

    def negative_binomial_size(self) -> float:
        if self.count < 2:
            return POISSON_LIKE_SIZE
        mean = self.total / self.count
        variance = (
            self.total_squared - self.total * self.total / self.count
        ) / (self.count - 1)
        if mean == 0 or variance <= mean:
            return POISSON_LIKE_SIZE
        return mean * mean / (variance - mean)


def _validate_positive_number(name: str, value: float) -> None:
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value <= 0
    ):
        raise ValueError(f"{name} must be a finite positive number")


def _validate_lines(lines: Iterable[float]) -> tuple[float, ...]:
    values = tuple(lines)
    if not values or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or not 0 < value < 1000
        or value % 1 != 0.5
        for value in values
    ):
        raise ValueError("lines must be half-integers from 0.5 to 999.5")
    if len(set(values)) != len(values):
        raise ValueError("duplicate lines are not allowed")
    return tuple(sorted(float(value) for value in values))


def _validate_weight_grid(values: Iterable[float]) -> tuple[float, ...]:
    grid = tuple(values)
    if not grid or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < -2 or value > 2
        for value in grid
    ):
        raise ValueError("weight grid values must be finite numbers from -2 to 2")
    if len(set(grid)) != len(grid):
        raise ValueError("duplicate weight grid values are not allowed")
    if 0 not in grid:
        raise ValueError("weight grid must include 0 for the nested baseline")
    return tuple(sorted(float(value) for value in grid))


def _validate_records(
    records: Iterable[TeamMatchStats],
) -> tuple[list[TeamMatchStats], int]:
    ordered = sorted(records, key=lambda item: item.match_date)
    if not ordered:
        raise ValueError("team-match history is empty")
    competitions = {item.competition for item in ordered}
    if len(competitions) != 1:
        raise ValueError("shot-feature history must contain one competition")
    pairs: dict[tuple[str, date, str, str], dict[Venue, TeamMatchStats]] = {}
    for record in ordered:
        pair = pairs.setdefault(record.fixture_key, {})
        if record.venue in pair:
            raise ValueError(f"duplicate team-match record: {record.fixture_key}")
        pair[record.venue] = record
    complete = []
    excluded = 0
    for key, pair in pairs.items():
        if set(pair) != {Venue.HOME, Venue.AWAY}:
            raise ValueError(f"missing home/away record for fixture {key}")
        home, away = pair[Venue.HOME], pair[Venue.AWAY]
        for statistic in ("corners", "shots", "shots_on_target"):
            if (
                getattr(home, f"{statistic}_for") != getattr(away, f"{statistic}_against")
                or getattr(home, f"{statistic}_against") != getattr(away, f"{statistic}_for")
            ):
                raise ValueError(f"inconsistent {statistic} pair for fixture {key}")
        if any(
            getattr(record, field) is None
            for record in pair.values()
            for field in (
                "corners_for", "corners_against", "shots_for", "shots_against",
                "shots_on_target_for", "shots_on_target_against",
            )
        ):
            excluded += 1
        else:
            complete.extend((home, away))
    return sorted(complete, key=lambda item: item.match_date), excluded


def _expected_count(
    league: _FeatureTotals, attack: _FeatureTotals, concession: _FeatureTotals,
    statistic: str, smoothing_matches: float,
) -> tuple[float, float]:
    league_rate = (
        getattr(league, f"{statistic}_for") + smoothing_matches
    ) / (league.count + smoothing_matches)
    attack_rate = (
        getattr(attack, f"{statistic}_for") + smoothing_matches * league_rate
    ) / (attack.count + smoothing_matches)
    concession_rate = (
        getattr(concession, f"{statistic}_against")
        + smoothing_matches * league_rate
    ) / (concession.count + smoothing_matches)
    return attack_rate * concession_rate / league_rate, league_rate


def rolling_feature_rows(
    records: Iterable[TeamMatchStats], *, min_history: int = 100,
    min_venue_history: int = 5, smoothing_matches: float = 5.0,
) -> list[FeatureRow]:
    """Build a common candidate cohort using only prior calendar dates."""
    _validate_rolling_settings(min_history, min_venue_history, smoothing_matches)
    ordered, _ = _validate_records(records)
    return _rolling_validated_feature_rows(
        ordered, min_history, min_venue_history, smoothing_matches,
    )


def _validate_rolling_settings(
    min_history: int, min_venue_history: int, smoothing_matches: float,
) -> None:
    for name, value in (("min_history", min_history),
                        ("min_venue_history", min_venue_history)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    _validate_positive_number("smoothing_matches", smoothing_matches)


def _rolling_validated_feature_rows(
    ordered: list[TeamMatchStats], min_history: int,
    min_venue_history: int, smoothing_matches: float,
) -> list[FeatureRow]:
    venue_totals: dict[Venue, _FeatureTotals] = defaultdict(_FeatureTotals)
    team_venue_totals: dict[tuple[str, Venue], _FeatureTotals] = defaultdict(_FeatureTotals)
    moments = _CornerMoments()
    prior_count = 0
    rows = []
    for _, group in groupby(ordered, key=lambda item: item.match_date):
        batch = list(group)
        if prior_count >= min_history:
            size = moments.negative_binomial_size()
            for record in batch:
                opposite = Venue.AWAY if record.venue is Venue.HOME else Venue.HOME
                attack = team_venue_totals[record.team, record.venue]
                concession = team_venue_totals[record.opponent, opposite]
                if (
                    attack.count < min_venue_history
                    or concession.count < min_venue_history
                ):
                    continue
                baseline, _ = _expected_count(
                    venue_totals[record.venue], attack, concession,
                    "corners", smoothing_matches,
                )
                shots, league_shots = _expected_count(
                    venue_totals[record.venue], attack, concession,
                    "shots", smoothing_matches,
                )
                target, league_target = _expected_count(
                    venue_totals[record.venue], attack, concession,
                    "shots_on_target", smoothing_matches,
                )
                rows.append(FeatureRow(
                    record, baseline, shots / league_shots,
                    target / league_target, size,
                ))
        for record in batch:
            venue_totals[record.venue].add(record)
            team_venue_totals[record.team, record.venue].add(record)
            moments.add(record.corners_for)  # type: ignore[arg-type]
            prior_count += 1
    return rows


def feature_mean(row: FeatureRow, shot_weight: float,
                 shots_on_target_weight: float) -> float:
    """Apply transparent multiplicative feature weights to the corner mean."""
    return (
        row.baseline_mean
        * row.shot_ratio ** shot_weight
        * row.shots_on_target_ratio ** shots_on_target_weight
    )


def score_feature_rows(
    rows: Iterable[FeatureRow], shot_weight: float,
    shots_on_target_weight: float, lines: Iterable[float],
) -> FeatureMetrics:
    items = list(rows)
    if not items:
        raise ValueError("cannot score an empty feature cohort")
    for name, value in (("shot_weight", shot_weight),
                        ("shots_on_target_weight", shots_on_target_weight)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < -2 or value > 2):
            raise ValueError(f"{name} must be a finite number from -2 to 2")
    selected_lines = _validate_lines(lines)
    absolute_errors = []
    squared_errors = []
    count_losses = []
    brier_losses = []
    binary_losses = []
    for row in items:
        expected = feature_mean(row, shot_weight, shots_on_target_weight)
        actual = row.record.corners_for
        if actual is None:
            raise ValueError("corners_for is required for scoring")
        error = expected - actual
        absolute_errors.append(abs(error))
        squared_errors.append(error * error)
        count_losses.append(-negative_binomial_log_probability(
            actual, expected, row.dispersion_size,
        ))
        for line in selected_lines:
            probabilities = corner_line_probabilities(
                expected, line, row.dispersion_size,
            )
            happened = actual > line
            brier_losses.append((probabilities.over - happened) ** 2)
            probability = probabilities.over if happened else probabilities.under
            binary_losses.append(-math.log(probability) if probability > 0 else math.inf)
    count = len(items)
    return FeatureMetrics(
        count=count,
        mae=math.fsum(absolute_errors) / count,
        rmse=math.sqrt(math.fsum(squared_errors) / count),
        negative_log_likelihood=math.fsum(count_losses) / count,
        line_brier=math.fsum(brier_losses) / len(brier_losses),
        line_log_loss=math.fsum(binary_losses) / len(binary_losses),
    )


def _select_weights(
    rows: list[FeatureRow], candidates: Iterable[tuple[float, float]],
) -> tuple[float, float]:
    scored = []
    for shot_weight, target_weight in candidates:
        # Prefer the smaller nested model on an exact loss tie.
        scored.append((_mean_count_nll(rows, shot_weight, target_weight),
                       abs(shot_weight) + abs(target_weight),
                       shot_weight, target_weight))
    _, _, shot_weight, target_weight = min(scored)
    return shot_weight, target_weight


def _mean_count_nll(
    rows: list[FeatureRow], shot_weight: float, target_weight: float,
) -> float:
    return math.fsum(
        -negative_binomial_log_probability(
            row.record.corners_for,  # type: ignore[arg-type]
            feature_mean(row, shot_weight, target_weight),
            row.dispersion_size,
        )
        for row in rows
    ) / len(rows)


def run_feature_experiment(
    records: Iterable[TeamMatchStats], *, holdout_from: date,
    lines: Iterable[float] = (3.5, 4.5, 5.5, 6.5),
    weight_grid: Iterable[float] = DEFAULT_WEIGHT_GRID,
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
) -> FeatureExperiment:
    """Fit feature weights on development rows and score a later holdout."""
    if type(holdout_from) is not date:
        raise ValueError("holdout_from must be a calendar date")
    _validate_rolling_settings(min_history, min_venue_history, smoothing_matches)
    selected_lines = _validate_lines(lines)
    grid = _validate_weight_grid(weight_grid)
    ordered, excluded = _validate_records(records)
    rows = _rolling_validated_feature_rows(
        ordered, min_history, min_venue_history, smoothing_matches,
    )
    development = [row for row in rows if row.record.match_date < holdout_from]
    holdout = [row for row in rows if row.record.match_date >= holdout_from]
    if not development:
        raise ValueError("no eligible development observations before holdout_from")
    if not holdout:
        raise ValueError("no eligible holdout observations on or after holdout_from")
    candidates = {
        "corners-only": ((0.0, 0.0),),
        "corners+shots": tuple((weight, 0.0) for weight in grid),
        "corners+shots+sot": tuple((shot, target) for shot in grid for target in grid),
    }
    variants = []
    for name, choices in candidates.items():
        shot_weight, target_weight = _select_weights(development, choices)
        variants.append(FeatureVariant(
            name, shot_weight, target_weight,
            score_feature_rows(development, shot_weight, target_weight, selected_lines),
            score_feature_rows(holdout, shot_weight, target_weight, selected_lines),
        ))
    return FeatureExperiment(
        competition=development[0].record.competition,
        holdout_from=holdout_from,
        lines=selected_lines,
        min_history=min_history,
        min_venue_history=min_venue_history,
        smoothing_matches=smoothing_matches,
        development_start=development[0].record.match_date,
        development_end=development[-1].record.match_date,
        holdout_start=holdout[0].record.match_date,
        holdout_end=holdout[-1].record.match_date,
        excluded_incomplete_fixtures=excluded,
        variants=tuple(variants),
    )


def format_feature_experiment(experiment: FeatureExperiment) -> str:
    """Format enough metadata and metrics to reproduce the comparison."""
    result = [
        f"Competition: {experiment.competition}",
        f"Development dates: {experiment.development_start} through {experiment.development_end}",
        f"Holdout dates: {experiment.holdout_start} through {experiment.holdout_end}",
        f"Coverage gates: {experiment.min_history} prior team observations; "
        f"{experiment.min_venue_history} prior venue matches per team",
        f"Smoothing matches: {experiment.smoothing_matches:g}",
        f"Incomplete fixtures excluded before history construction: "
        f"{experiment.excluded_incomplete_fixtures}",
        "Lines: " + ", ".join(f"{line:g}" for line in experiment.lines),
        "Weights minimize development Negative Binomial NLL and are frozen for holdout.",
        "Every variant uses the same fixtures and strictly earlier calendar dates.",
        "",
        "Model  shot_w  sot_w  Split  N  MAE  RMSE  Count NLL  Line Brier  Line log loss",
    ]
    for variant in experiment.variants:
        for split, metrics in (("development", variant.development),
                               ("holdout", variant.holdout)):
            result.append(
                f"{variant.name}  {variant.shot_weight:g}  "
                f"{variant.shots_on_target_weight:g}  {split}  {metrics.count}  "
                f"{metrics.mae:.6f}  {metrics.rmse:.6f}  "
                f"{metrics.negative_log_likelihood:.6f}  "
                f"{metrics.line_brier:.6f}  {metrics.line_log_loss:.6f}"
            )
    result.extend((
        "", "Lower is better for every reported metric.",
        "Holdout means weights were frozen; this period is not claimed as an untouched final test.",
        "This is an offline model experiment, not a production prediction or betting result.",
    ))
    return "\n".join(result)
