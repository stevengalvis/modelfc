"""Read-only BTTS performance response, distinct from corner evaluation."""
from typing import Annotated, Literal
from pydantic import Field
from modelfc.btts_market_data import Digest, ResearchContract, Text
from modelfc.btts_model import HistorySource

Count = Annotated[int, Field(ge=0)]
Probability = Annotated[float, Field(ge=0, le=1)]


class CalibrationBin(ResearchContract):
    lower: Probability
    upper: Probability
    sample_count: Annotated[int, Field(gt=0)]
    mean_probability: Probability
    observed_frequency: Probability


class Metrics(ResearchContract):
    sample_count: Count
    brier_score: Probability | None
    log_loss: Annotated[float, Field(ge=0)] | None
    calibration: list[CalibrationBin]


class ForecastView(ResearchContract):
    fixture_id: Digest
    forecast_id: Digest
    competition: Literal['E1']
    home_team: Text
    away_team: Text
    kickoff_utc: Text
    provider: Text
    provider_fixture_id: Text
    origins: list[Literal['BOOKMAKER_RESEARCH', 'ODDS_FREE']]
    selected_for_metrics: bool
    frozen_at_utc: Text
    model_version: Text
    yes_probability: Probability
    no_probability: Probability
    source_data_hashes: list[HistorySource]
    status: Literal['SETTLED', 'UPCOMING', 'UNRESOLVED']
    actual_btts: Literal['YES', 'NO'] | None
    home_goals: Count | None
    away_goals: Count | None
    outcome_id: Digest | None
    recorded_at_utc: Text | None
    result_provider: Literal['football-data.co.uk', 'sportmonks'] | None
    result_source_sha256: Digest | None


class BttsPerformanceResponse(ResearchContract):
    competition: Literal['E1']
    research_only: Literal[True]
    selection_policy: Literal['e1-earliest-frozen-per-exact-fixture-v1']
    log_loss_epsilon: Literal[1e-15]
    unique_fixtures: Count
    forecast_cohorts: Count
    settled_fixtures: Count
    unresolved_fixtures: Count
    upcoming_fixtures: Count
    metrics: Metrics
    forecasts: list[ForecastView]
