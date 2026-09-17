"""Historical match-total corner probability diagnostics."""

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from modelfc.corner_forecasts import (
    CORNER_FIXTURE_MODELS, match_total_line_probabilities,
)
from modelfc.corner_probability_evaluation import (
    ProbabilitySummary, _fixture_key, evaluate_corner_probabilities, summarize,
)
from modelfc.corners import rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, Venue


@dataclass(frozen=True)
class MatchTotalLineOutcome:
    match_date: date
    home_team: str
    away_team: str
    actual_total: int
    line: float
    over_probability: float
    under_probability: float

    @property
    def over_happened(self) -> bool:
        return self.actual_total > self.line


@dataclass(frozen=True)
class MatchTotalProbabilityReport:
    model: str
    lines: tuple[float, ...]
    fixtures_in_window: int
    eligible_fixtures: int
    outcomes: tuple[MatchTotalLineOutcome, ...]


def evaluate_match_total_probabilities(
    observations: Iterable[TeamCornerObservation], *,
    model: str = "venue-opponent-negative-binomial",
    lines: Iterable[float] = (8.5, 9.5, 10.5, 11.5),
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
    start_date: date | None = None, end_date: date | None = None,
) -> MatchTotalProbabilityReport:
    """Score independent-sum probabilities on leakage-safe paired fixtures."""
    if model not in CORNER_FIXTURE_MODELS:
        raise ValueError("unsupported probability model")
    items = list(observations)
    selected_lines = tuple(lines)
    # Reuse the established pairing, line, date, and both-team coverage gates.
    eligibility = evaluate_corner_probabilities(
        items, model=model, lines=(0.5,), min_history=min_history,
        min_venue_history=min_venue_history, smoothing_matches=smoothing_matches,
        start_date=start_date, end_date=end_date,
    )
    # Validate requested total lines through the production pricing function.
    for line in selected_lines:
        match_total_line_probabilities(1, 1, line, 1)
    if not selected_lines or len(set(selected_lines)) != len(selected_lines):
        raise ValueError("lines must be unique whole or half numbers")
    selected_lines = tuple(sorted(float(line) for line in selected_lines))
    eligible = {_fixture_key(item.observation) for item in eligibility.outcomes}
    ordered = sorted(
        (item for item in items if end_date is None or item.match_date <= end_date),
        key=lambda item: item.match_date,
    )
    predictions = rolling_corner_predictions(
        ordered, model, min_history=min_history,
        smoothing_matches=smoothing_matches,
    )
    pairs: dict[tuple[date, str, str], dict[Venue, object]] = {}
    for prediction in predictions:
        key = _fixture_key(prediction.observation)
        if key in eligible:
            pairs.setdefault(key, {})[prediction.observation.venue] = prediction

    outcomes = []
    for key in sorted(eligible):
        pair = pairs.get(key, {})
        if set(pair) != {Venue.HOME, Venue.AWAY}:
            raise ValueError(f"missing predictions for eligible fixture {key}")
        home, away = pair[Venue.HOME], pair[Venue.AWAY]
        if home.dispersion_size != away.dispersion_size:
            raise ValueError(f"team dispersion mismatch for fixture {key}")
        actual = home.observation.corners_for + away.observation.corners_for
        for line in selected_lines:
            probability = match_total_line_probabilities(
                home.expected_corners, away.expected_corners, line,
                home.dispersion_size,
            )
            outcomes.append(MatchTotalLineOutcome(
                key[0], key[1], key[2], actual, line,
                probability.over, probability.under,
            ))
    return MatchTotalProbabilityReport(
        model, selected_lines, eligibility.fixtures_in_window,
        eligibility.eligible_fixtures, tuple(outcomes),
    )


def match_total_summaries(
    report: MatchTotalProbabilityReport,
) -> dict[float, ProbabilitySummary]:
    return {
        line: summarize(item for item in report.outcomes if item.line == line)
        for line in report.lines
    }


def format_match_total_probability_report(report: MatchTotalProbabilityReport) -> str:
    lines = [
        f"Model: {report.model}",
        "Method: conditionally independent team distributions",
        f"Fixtures in scoring window: {report.fixtures_in_window}",
        f"Eligible paired fixtures: {report.eligible_fixtures}",
        "Line  Count  Mean probability  Hit rate  Gap(hit-pred)  Brier  Log loss",
    ]
    for line, summary in match_total_summaries(report).items():
        lines.append(
            f"{line:g}  {summary.count}  {summary.mean_probability:.2%}  "
            f"{summary.hit_rate:.2%}  "
            f"{summary.hit_rate-summary.mean_probability:+.2%}  "
            f"{summary.brier:.6f}  {summary.log_loss:.6f}"
        )
    lines.extend((
        "",
        "Lower Brier and log loss are better.",
        "The V1 total assumes conditional independence between team corner counts.",
        "Each forecast uses strictly earlier dates and the live coverage gates.",
    ))
    return "\n".join(lines)
