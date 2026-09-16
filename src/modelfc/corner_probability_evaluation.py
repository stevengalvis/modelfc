"""Historical team-line diagnostics; no model fitting or recalibration."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from itertools import groupby
import math
from typing import Iterable

from modelfc.corner_forecasts import CORNER_FIXTURE_MODELS, corner_line_probabilities
from modelfc.corners import rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, Venue


@dataclass(frozen=True)
class LineOutcome:
    observation: TeamCornerObservation
    line: float
    over_probability: float
    under_probability: float

    @property
    def over_happened(self) -> bool:
        return self.observation.corners_for > self.line


@dataclass(frozen=True)
class ProbabilitySummary:
    count: int
    mean_probability: float
    hit_rate: float
    brier: float
    log_loss: float


def summarize(outcomes: Iterable[LineOutcome]) -> ProbabilitySummary:
    items = list(outcomes)
    if not items:
        raise ValueError("cannot summarize empty outcomes")
    losses = []
    for item in items:
        probability = item.over_probability if item.over_happened else item.under_probability
        losses.append(-math.log(probability) if probability > 0 else math.inf)
    n = len(items)
    return ProbabilitySummary(
        n, math.fsum(x.over_probability for x in items)/n,
        sum(x.over_happened for x in items)/n,
        math.fsum((x.over_probability-x.over_happened)**2 for x in items)/n,
        math.fsum(losses)/n,
    )


@dataclass(frozen=True)
class ProbabilityReport:
    model: str
    lines: tuple[float, ...]
    min_history: int
    min_venue_history: int
    smoothing_matches: float
    fixtures_in_window: int
    eligible_fixtures: int
    outcomes: tuple[LineOutcome, ...]


def _fixture_key(item: TeamCornerObservation) -> tuple[date, str, str]:
    home, away = ((item.team, item.opponent) if item.venue is Venue.HOME
                  else (item.opponent, item.team))
    return item.match_date, home, away


def evaluate_corner_probabilities(
    observations: Iterable[TeamCornerObservation], *,
    model: str = "venue-opponent-negative-binomial",
    lines: Iterable[float] = (3.5, 4.5, 5.5, 6.5),
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
    start_date: date | None = None, end_date: date | None = None,
) -> ProbabilityReport:
    """Score half-lines on paired fixtures from one competition.

    Date limits select the inclusive scoring window; earlier records still
    supply history. Both teams must pass the same venue gate as fixture
    prediction. Same-day observations enter history only after all gates for
    that date are checked. No odds or market-selected lines are implied.
    """
    if model not in CORNER_FIXTURE_MODELS:
        raise ValueError("unsupported probability model")
    for name, value in (("min_history", min_history), ("min_venue_history", min_venue_history)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if (isinstance(smoothing_matches, bool) or not isinstance(smoothing_matches, (float, int))
            or not math.isfinite(smoothing_matches) or smoothing_matches <= 0):
        raise ValueError("smoothing_matches must be finite and positive")
    for value in (start_date, end_date):
        if value is not None and type(value) is not date:
            raise ValueError("date limits must be calendar dates")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    raw_lines = tuple(lines)
    if not raw_lines or any(
        isinstance(x, bool) or not isinstance(x, (float, int))
        or not math.isfinite(x) or not 0 < x < 1000 or x % 1 != 0.5
        for x in raw_lines
    ):
        raise ValueError("lines must be half-integers from 0.5 to 999.5 (no pushes)")
    if len(set(raw_lines)) != len(raw_lines):
        raise ValueError("duplicate lines are not allowed")
    selected_lines = tuple(sorted(float(x) for x in raw_lines))
    ordered = sorted(observations, key=lambda x: x.match_date)
    if end_date:
        ordered = [x for x in ordered if x.match_date <= end_date]
    pairs = {}
    for item in ordered:
        key = _fixture_key(item)
        pair = pairs.setdefault(key, {})
        if item.venue in pair:
            raise ValueError(f"duplicate observation for fixture {key}")
        pair[item.venue] = item
    for key, pair in pairs.items():
        if set(pair) != {Venue.HOME, Venue.AWAY}:
            raise ValueError(f"missing home/away observation for fixture {key}")
        home, away = pair[Venue.HOME], pair[Venue.AWAY]
        if (home.corners_for, home.corners_against) != (away.corners_against, away.corners_for):
            raise ValueError(f"inconsistent corner pair for fixture {key}")
    counts = Counter()
    prior_count = 0
    eligible = set()
    fixtures_in_window = 0
    for day, group in groupby(ordered, key=lambda x: x.match_date):
        batch = list(group)
        if start_date is None or day >= start_date:
            for home in (x for x in batch if x.venue is Venue.HOME):
                fixtures_in_window += 1
                if (prior_count >= min_history
                    and counts[home.team, Venue.HOME] >= min_venue_history
                    and counts[home.opponent, Venue.AWAY] >= min_venue_history):
                    eligible.add(_fixture_key(home))
        counts.update((x.team, x.venue) for x in batch)
        prior_count += len(batch)
    # Reuse the optimized rolling means and dispersion. Display distributions
    # are deliberately ignored: team-line probabilities use the full tails.
    predictions = rolling_corner_predictions(
        ordered, model, min_history=min_history, smoothing_matches=smoothing_matches,
    )
    outcomes = []
    for prediction in predictions:
        if _fixture_key(prediction.observation) not in eligible:
            continue
        for line in selected_lines:
            probability = corner_line_probabilities(
                prediction.expected_corners, line, prediction.dispersion_size,
            )
            outcomes.append(LineOutcome(prediction.observation, line,
                                        probability.over, probability.under))
    if not outcomes:
        raise ValueError("no eligible fixtures in scoring window; check history and coverage")
    return ProbabilityReport(model, selected_lines, min_history, min_venue_history,
                             smoothing_matches, fixtures_in_window, len(eligible), tuple(outcomes))


def format_probability_report(report: ProbabilityReport) -> str:
    outcomes = report.outcomes
    dates = [x.observation.match_date for x in outcomes]
    result = [
        f"Model: {report.model}",
        f"Coverage gates: {report.min_history} prior team observations; "
        f"{report.min_venue_history} prior venue matches per team",
        f"Smoothing matches: {report.smoothing_matches:g}",
        f"Scored dates: {min(dates)} through {max(dates)}",
        f"Fixtures in scoring window: {report.fixtures_in_window}",
        f"Eligible fixtures: {report.eligible_fixtures}",
        f"Fixtures skipped for history/venue coverage: {report.fixtures_in_window-report.eligible_fixtures}",
        f"Unique team observations scored: {report.eligible_fixtures*2}",
        "", "OVER probabilities (UNDER is the complement; not counted twice)",
        "Line  Count  Mean probability  Hit rate  Gap(hit-pred)  Brier  Log loss",
    ]

    def row(label, items):
        s = summarize(items)
        return (f"{label}  {s.count}  {s.mean_probability:.2%}  {s.hit_rate:.2%}  "
                f"{s.hit_rate-s.mean_probability:+.2%}  {s.brier:.6f}  {s.log_loss:.6f}")

    for line in report.lines:
        items = [x for x in outcomes if x.line == line]
        result.append(row(f"{line:g}", items))
    result.extend(("", "Per-line calibration bins: [lower, upper), final bin includes 100%"))
    for line in report.lines:
        bins = [[] for _ in range(10)]
        for item in outcomes:
            if item.line == line:
                bins[min(9, int(item.over_probability*10))].append(item)
        result.append(f"Line {line:g}: bin  Count  Mean probability  Hit rate  Gap(hit-pred)  Brier  Log loss")
        for index, items in enumerate(bins):
            label = f"{index*10}-{(index+1)*10}%"
            result.append(row(label, items) if items else f"{label}  0  n/a")
    result.extend(("", "Calendar-year breakdown: year/line  Count  Mean probability  Hit rate  Gap(hit-pred)  Brier  Log loss"))
    for year in sorted({x.observation.match_date.year for x in outcomes}):
        for line in report.lines:
            result.append(row(f"{year}/{line:g}", [x for x in outcomes
                if x.line == line and x.observation.match_date.year == year]))
    result.extend(("", "Brier and log loss: lower is better. Probabilities are not recalibrated.",
                   "Bins are descriptive; small samples are uncertain. Teams and lines within a match are dependent.",
                   "Fixed half-lines are not historical bookmaker offers. No profitability or market edge is measured.",
                   "Local history is not refreshed. Each forecast uses strictly earlier dates, including history before the scoring window."))
    return "\n".join(result)
