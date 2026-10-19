"""Saved research-quality contract; no priced publications or simulated ROI."""
from typing import Any, Literal
from modelfc.btts_market_data import ResearchContract
from modelfc.btts_performance_schema import Metrics, Count, Probability


class Selection(ResearchContract):
    competition: Literal['ALL', 'E1', 'E0']
    market: Literal['ALL', 'CORNER', 'BTTS']
    cohort: Literal['ALL', 'ODDS_FREE', 'BOOKMAKER_RESEARCH']
    as_of_utc: str


class Source(ResearchContract):
    kind: str
    sha256: str
    retrieved_at_utc: str | None


class SavedProbability(ResearchContract):
    target_id: str
    team_side: Literal['HOME', 'AWAY'] | None
    direction: Literal['OVER', 'UNDER', 'YES', 'NO']
    line: float | None
    probability: Probability
    push_probability: Probability
    decisive_probability: Probability
    recorded_at_utc: str
    result: Literal['WIN', 'LOSS', 'PUSH'] | None


class Revision(ResearchContract):
    record_id: str
    recorded_at_utc: str
    supersedes_id: str | None
    reason: str | None
    source_sha256: str | None
    result: dict[str, Any]


class Forecast(ResearchContract):
    competition: Literal['E1']
    home_team: str
    away_team: str
    kickoff_utc: str
    fixture_id: str
    forecast_id: str
    source_forecast_id: str
    market: Literal['CORNER', 'BTTS']
    origins: list[Literal['ODDS_FREE', 'BOOKMAKER_RESEARCH']]
    provider: str
    provider_fixture_id: str
    frozen_at_utc: str
    model_name: str
    model_version: str
    selected_for_metrics: bool
    source_references: list[Source]
    probabilities: list[SavedProbability]
    expected_home_corners: float | None
    expected_away_corners: float | None
    status: Literal['SETTLED', 'UPCOMING', 'UNRESOLVED']
    result: dict[str, Any] | None
    outcome_id: str | None
    outcome_recorded_at_utc: str | None
    effective_record_hash: str | None
    result_retrieved_at_utc: str | None
    result_provider: str | None
    result_source_sha256: str | None
    corrections: list[Revision]
    correction_support: Literal['SUPPORTED', 'UNSUPPORTED']


class MarketSummary(ResearchContract):
    market: Literal['CORNER', 'BTTS']
    unique_fixtures: Count
    forecast_cohorts: Count
    settled_fixtures: Count
    unresolved_fixtures: Count
    upcoming_fixtures: Count
    probability_unit: Literal['fixture-yes', 'saved-decisive-team-total-target']
    saved_probability_targets: Count
    pushes_excluded: Count
    probability_scores: Metrics
    team_count_sample_count: Count
    team_corner_mae: float | None
    team_corner_rmse: float | None


class ResearchPerformanceResponse(ResearchContract):
    selection: Selection
    policy: Literal['earliest-frozen-per-exact-fixture-and-market-v1']
    coverage: Literal['SUPPORTED', 'PARTIAL', 'UNSUPPORTED']
    supported_competitions: list[Literal['E1']]
    unavailable_competitions: list[Literal['E0']]
    historical_store_membership: Literal['UNVERIFIED']
    unique_fixtures: Count
    forecast_cohorts: Count
    markets: list[MarketSummary]
    limitations: list[str]
    forecasts: list[Forecast]
