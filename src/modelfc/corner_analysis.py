"""Batch analysis of sportsbook corner markets for one fixture."""

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from modelfc.corner_forecasts import (
    CORNER_FIXTURE_MODELS, CornerFixturePrediction, CornerLineProbability,
    TeamCornerForecast, corner_line_probabilities, predict_corner_fixture,
)
from modelfc.corner_markets import (
    CornerMarketValue, american_odds_terms, price_corner_market,
)
from modelfc.matches import TeamCornerObservation, UpcomingFixture


SUPPORTED_MARKET_TYPES = ("TEAM_TOTAL", "MATCH_TOTAL")


@dataclass(frozen=True)
class CornerMarketRequest:
    client_market_id: str
    market_type: str
    team_side: str | None
    side: str
    line: float
    american_odds: int


@dataclass(frozen=True)
class AnalysisWarning:
    code: str
    message: str


@dataclass(frozen=True)
class AnalyzedCornerMarket:
    request: CornerMarketRequest
    team: str | None
    status: str
    unsupported_reason: str | None
    probability: CornerLineProbability | None
    value: CornerMarketValue | None
    expected_corners: float | None
    warnings: tuple[AnalysisWarning, ...]


@dataclass(frozen=True)
class CornerBatchAnalysis:
    prediction: CornerFixturePrediction
    markets: tuple[AnalyzedCornerMarket, ...]
    warnings: tuple[AnalysisWarning, ...]


def _validate_requests(
    markets: Iterable[CornerMarketRequest],
) -> tuple[CornerMarketRequest, ...]:
    items = tuple(markets)
    if not items:
        raise ValueError("markets must contain at least one item")
    identifiers = set()
    normalized = []
    for item in items:
        if not isinstance(item.client_market_id, str) or not item.client_market_id.strip():
            raise ValueError("client_market_id must be non-empty text")
        identifier = item.client_market_id.strip()
        if identifier in identifiers:
            raise ValueError(f"duplicate client_market_id: {identifier}")
        identifiers.add(identifier)
        market_type = item.market_type.upper()
        team_side = item.team_side.upper() if item.team_side is not None else None
        side = item.side.upper()
        if market_type not in SUPPORTED_MARKET_TYPES:
            raise ValueError(f"unsupported market type: {market_type}")
        if side not in ("OVER", "UNDER"):
            raise ValueError("side must be OVER or UNDER")
        # Production probability validation supplies the shared line rules.
        corner_line_probabilities(0, item.line)
        american_odds_terms(item.american_odds)
        if market_type == "TEAM_TOTAL" and team_side not in ("HOME", "AWAY"):
            raise ValueError("TEAM_TOTAL requires team_side HOME or AWAY")
        if market_type == "MATCH_TOTAL" and team_side is not None:
            raise ValueError("MATCH_TOTAL requires a null team_side")
        normalized.append(CornerMarketRequest(
            identifier, market_type, team_side, side, float(item.line),
            item.american_odds,
        ))
    return tuple(normalized)


def _line(
    values: tuple[CornerLineProbability, ...], requested: float,
) -> CornerLineProbability:
    for item in values:
        if item.line == requested:
            return item
    raise RuntimeError(f"prediction omitted requested line {requested:g}")


def _team_freshness_warnings(
    fixture_date: date, forecast: TeamCornerForecast, max_age_days: int,
) -> tuple[AnalysisWarning, ...]:
    warnings = []
    history_age = (fixture_date - forecast.latest_match_date).days
    if history_age > max_age_days:
        warnings.append(AnalysisWarning(
            "TEAM_HISTORY_AGE",
            f"Latest {forecast.team} observation is {history_age} days before the fixture.",
        ))
    venue_age = (fixture_date - forecast.latest_venue_match_date).days
    if venue_age > max_age_days:
        warnings.append(AnalysisWarning(
            "TEAM_VENUE_HISTORY_AGE",
            f"Latest {forecast.team} {forecast.venue.value} observation is "
            f"{venue_age} days before the fixture.",
        ))
    return tuple(warnings)


def analyze_corner_markets(
    observations: Iterable[TeamCornerObservation], fixture: UpcomingFixture,
    markets: Iterable[CornerMarketRequest], *,
    model: str = "venue-opponent-negative-binomial",
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0, max_age_days: int = 14,
) -> CornerBatchAnalysis:
    """Run one fixture forecast and price every supplied corner market."""
    if model not in CORNER_FIXTURE_MODELS:
        raise ValueError(f"unsupported fixture corner model: {model}")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 1:
        raise ValueError("max_age_days must be a positive integer")
    requests = _validate_requests(markets)
    home_lines = sorted({
        item.line for item in requests
        if item.market_type == "TEAM_TOTAL" and item.team_side == "HOME"
    })
    away_lines = sorted({
        item.line for item in requests
        if item.market_type == "TEAM_TOTAL" and item.team_side == "AWAY"
    })
    total_lines = sorted({
        item.line for item in requests if item.market_type == "MATCH_TOTAL"
    })
    prediction = predict_corner_fixture(
        observations, fixture, home_lines, away_lines, total_lines,
        model=model, min_history=min_history,
        min_venue_history=min_venue_history,
        smoothing_matches=smoothing_matches,
    )
    warnings = []
    history_age = (fixture.match_date - prediction.latest_history_date).days
    if history_age > max_age_days:
        warnings.append(AnalysisWarning(
            "STALE_DATA",
            f"Latest usable history is {history_age} days before the fixture.",
        ))
    home_warnings = _team_freshness_warnings(
        fixture.match_date, prediction.home, max_age_days,
    )
    away_warnings = _team_freshness_warnings(
        fixture.match_date, prediction.away, max_age_days,
    )
    results = []
    for request in requests:
        if request.market_type == "MATCH_TOTAL":
            if prediction.total is None:
                raise RuntimeError("match-total prediction was not created")
            probability = _line(prediction.total.lines, request.line)
            team = None
            expected = prediction.total.expected_corners
            market_warnings = home_warnings + away_warnings
        else:
            forecast = prediction.home if request.team_side == "HOME" else prediction.away
            probability = _line(forecast.lines, request.line)
            team = forecast.team
            expected = forecast.expected_corners
            market_warnings = (
                home_warnings if request.team_side == "HOME" else away_warnings
            )
        try:
            value = price_corner_market(probability, request.side, request.american_odds)
            status, reason = "SUPPORTED", None
        except ValueError as error:
            if str(error) != "market has no decisive outcomes":
                raise
            value, status, reason = None, "UNSUPPORTED", "NO_DECISIVE_OUTCOMES"
        results.append(AnalyzedCornerMarket(
            request, team, status, reason, probability, value, expected,
            market_warnings,
        ))
    return CornerBatchAnalysis(prediction, tuple(results), tuple(warnings))
