"""FastAPI boundary for the Model FC V1 corner-analysis API."""

from datetime import date
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import hashlib
import json
from modelfc.team_intelligence import (
    TeamList, TeamProfile, InsightList, TeamIntelligenceError, load_population, BY_ID,
)
from pydantic import BaseModel, Field

from modelfc.corner_analysis import MAX_MARKETS_PER_ANALYSIS, CornerMarketRequest
from modelfc.corner_analysis_store import analyze_and_store, load_analysis
from modelfc.corner_capabilities import api_capabilities
from modelfc.corner_prospective_read import (
    read_opportunities, read_opportunity, read_performance, read_predictions, read_recommendations,
)
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable
from modelfc.btts_research import BttsResearchResponse, read_btts_research
from modelfc.btts_market_data import ENABLED_COMPETITIONS


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class RecommendationResponse(StrictModel):
    prediction_id: str
    target_id: str
    competition: str
    provider: str
    provider_fixture_id: str
    kickoff_utc: str
    home_team: str
    away_team: str
    market_type: Literal["TEAM_TOTAL"]
    team_side: Literal["HOME", "AWAY"]
    team: str
    direction: Literal["OVER", "UNDER"]
    line: float = Field(ge=0, allow_inf_nan=False)
    bookmaker: str
    american_odds: int
    decimal_odds: float = Field(gt=1, allow_inf_nan=False)
    retrieved_at_utc: str
    observation_age_seconds: float = Field(ge=0, allow_inf_nan=False)
    availability_checked_at_utc: str
    model_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    push_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    decisive_model_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    sportsbook_implied_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    no_vig_market_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    no_vig_probability_edge: float = Field(ge=-1, le=1, allow_inf_nan=False)
    expected_profit: float = Field(gt=0, allow_inf_nan=False)
    qualified: Literal[True]
    policy_version: str


class FixtureRequest(StrictModel):
    competition: str
    date: date
    home_team: str
    away_team: str


class MarketRequest(StrictModel):
    client_market_id: str
    market_type: str
    team_side: str | None = None
    side: str
    line: float
    american_odds: int


class AnalysisRequest(StrictModel):
    idempotency_key: str
    fixture: FixtureRequest
    model: str = "venue-opponent-negative-binomial"
    markets: list[MarketRequest] = Field(
        min_length=1, max_length=MAX_MARKETS_PER_ANALYSIS,
    )


class WarningResponse(StrictModel):
    code: str
    message: str


class PickLoggingResponse(StrictModel):
    status: str
    reason: str | None


class FixtureResponse(StrictModel):
    competition: str
    date: date
    kickoff_at: str | None
    home_team: str
    away_team: str


class SourceHashResponse(StrictModel):
    filename: str
    sha256: str


class ForecastConfigurationResponse(StrictModel):
    min_history: int
    min_venue_history: int
    smoothing_matches: float
    dispersion_size: float | None
    match_total_method: str


class ForecastResponse(StrictModel):
    model: str
    model_version: str
    configuration: ForecastConfigurationResponse
    home_expected_corners: float
    away_expected_corners: float
    match_expected_corners: float
    latest_history_date: date
    source_data_hashes: list[SourceHashResponse]
    # Frozen in captures for prospective use; preserve the existing analysis API.
    historical_context: "HistoricalContextResponse | None" = Field(default=None, exclude=True)


class MarketResponse(StrictModel):
    client_market_id: str
    market_type: str
    team_side: str | None
    team: str | None
    side: str
    line: float
    american_odds: int
    status: str
    unsupported_reason: str | None
    model_probability: float | None
    push_probability: float | None
    decisive_model_probability: float | None
    implied_probability: float | None
    probability_edge: float | None
    expected_profit: float | None
    expected_corners: float | None
    warnings: list[WarningResponse]


class AnalysisResponse(StrictModel):
    analysis_id: str
    forecast_id: str
    created_at: str
    pick_logging: PickLoggingResponse
    fixture: FixtureResponse
    forecast: ForecastResponse
    markets: list[MarketResponse]
    warnings: list[WarningResponse]


class MarketCapabilityResponse(StrictModel):
    market_type: str
    status: str
    reason: str | None


class TeamsBySideResponse(StrictModel):
    HOME: list[str]
    AWAY: list[str]


class CompetitionCapabilityResponse(StrictModel):
    code: str
    name: str
    provider: str
    analysis: bool
    markets: list[str]
    teams: list[str]
    teams_by_side: TeamsBySideResponse
    automatic_refresh: bool
    refresh_job_status: str
    last_refresh_status: str | None
    automatic_settlement: str
    trusted_kickoff_source: str | None
    latest_result_date: date | None
    last_refresh_at: str | None
    stale: bool
    warnings: list[WarningResponse]


class CapabilitiesResponse(StrictModel):
    api_version: str
    stake: float
    models: list[str]
    markets: list[str]
    market_capabilities: list[MarketCapabilityResponse]
    competitions: list[CompetitionCapabilityResponse]


class OpportunityResponse(StrictModel):
    opportunity_id: str
    prediction_id: str
    target_id: str
    observation_id: str
    provider: str
    provider_fixture_id: str
    competition: str
    kickoff_utc: str
    home_team: str
    away_team: str
    bookmaker: str
    market_type: str
    team_side: str
    team: str
    direction: str
    line: float
    american_odds: int
    decimal_odds: float
    qualified_at_utc: str
    model_decisive_probability: float
    no_vig_market_probability: float
    no_vig_probability_edge: float
    policy_version: str
    settlement_status: str
    result: str | None
    actual_team_corners: int | None
    realized_profit_units: float | None


class HistoricalContextResponse(StrictModel):
    earlier_team_observations: int
    home_team_observations: int
    home_venue_observations: int
    away_team_observations: int
    away_venue_observations: int
    min_history: int
    min_venue_history: int


class OpportunityForecastResponse(StrictModel):
    expected_team_corners: float
    expected_home_corners: float
    expected_away_corners: float
    expected_match_corners: float
    model_probability: float
    push_probability: float
    decisive_model_probability: float
    model_name: str
    model_version: str
    created_at_utc: str
    materialized_at_utc: str
    latest_history_date: date
    historical_context: HistoricalContextResponse | None


class OpportunityQualificationResponse(StrictModel):
    minimum_no_vig_edge: float
    minimum_american_odds: int
    edge_pass: bool
    price_pass: bool
    policy_version: str
    market_type: str
    bookmaker: str


class PairedPriceResponse(StrictModel):
    direction: str
    american_odds: int
    decimal_odds: float
    implied_probability: float
    no_vig_probability: float
    qualified: bool


class RecordedPriceResponse(StrictModel):
    observation_id: str
    retrieved_at_utc: str
    bookmaker: str
    direction: str
    line: float
    american_odds: int
    decimal_odds: float
    no_vig_market_probability: float | None
    price_consistent: bool
    qualifying_observation: bool


class MarketMovementResponse(StrictModel):
    status: Literal["TOWARD_ZENO", "AWAY_FROM_ZENO", "UNCHANGED", "NO_LATER_OBSERVATION", "UNAVAILABLE"]
    market_change_percentage_points: float | None
    latest_comparable_observation_id: str | None


class OpportunityDetailResponse(OpportunityResponse):
    forecast: OpportunityForecastResponse
    qualification: OpportunityQualificationResponse
    market_at_qualification: list[PairedPriceResponse]
    recorded_market: list[RecordedPriceResponse]
    recorded_market_count: int
    market_movement: MarketMovementResponse
    source_observation_id: str
    actual_home_corners: int | None
    actual_away_corners: int | None
    outcome_recorded_at_utc: str | None


class PredictionResponse(StrictModel):
    prediction_id: str
    source_observation_id: str
    created_at_utc: str
    competition: str
    provider: str
    provider_fixture_id: str
    kickoff_utc: str
    home_team: str
    away_team: str
    model_name: str
    model_version: str
    expected_home_corners: float
    expected_away_corners: float
    expected_match_corners: float
    dispersion_size: float | None
    latest_history_date: date
    source_data_hashes: list[SourceHashResponse]
    target_count: int
    opportunity_count: int
    settlement_status: str
    actual_home_corners: int | None
    actual_away_corners: int | None


class ModelVersionPerformanceResponse(StrictModel):
    model_name: str
    model_version: str
    total_prediction_runs: int
    settled_prediction_runs: int


class CalibrationBucketResponse(StrictModel):
    lower_bound: float
    upper_bound: float
    sample_count: int
    mean_predicted_probability: float | None
    observed_win_rate: float | None


class ModelPerformanceResponse(StrictModel):
    total_prediction_runs: int
    settled_prediction_runs: int
    settled_team_forecasts: int
    team_corner_mae: float | None
    team_corner_rmse: float | None
    team_corner_mean_error: float | None
    match_total_mae: float | None
    match_total_rmse: float | None
    match_total_mean_error: float | None
    model_versions: list[ModelVersionPerformanceResponse]
    total_unique_prediction_targets: int
    supported_prediction_targets: int
    settled_prediction_targets: int
    unsettled_supported_prediction_targets: int
    probability_targets_scored: int
    decisive_probability_targets_scored: int
    pushes_excluded_from_decisive_scoring: int
    brier_score: float | None
    log_loss: float | None
    calibration: list[CalibrationBucketResponse]


class OpportunityPerformanceResponse(StrictModel):
    total_opportunity_events: int
    settled_opportunities: int
    wins: int
    losses: int
    pushes: int
    win_rate_excluding_pushes: float | None
    realized_profit_units: float
    roi_on_settled_opportunities: float | None
    unresolved_open_opportunities: int


class ProspectivePerformanceResponse(StrictModel):
    model_performance: ModelPerformanceResponse
    opportunity_performance: OpportunityPerformanceResponse


def _error(code: str, message: str, status: int, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": {},
        "retryable": retryable,
    }})


def _domain_error(error: Exception) -> JSONResponse:
    message = str(error)
    if message == "IDEMPOTENCY_CONFLICT":
        return _error("IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different request.", 409)
    if message.startswith("unknown analysis ID"):
        return _error("ANALYSIS_NOT_FOUND", message, 404)
    if message == "UNKNOWN_OPPORTUNITY":
        return _error("OPPORTUNITY_NOT_FOUND", "Opportunity was not found.", 404)
    if isinstance(error, LedgerStorageUnavailable):
        return _error("STATE_STORAGE_UNAVAILABLE", message, 503, retryable=True)
    if message.startswith("invalid corner analysis record"):
        return _error("LEDGER_INTEGRITY_FAILURE", message, 409)
    if message.startswith(("invalid prospective record ",
                           "invalid analysis outcome record ")):
        return _error(
            "LEDGER_INTEGRITY_FAILURE",
            "Prospective evidence failed validation.", 409,
        )
    if message in ("INVALID_PROSPECTIVE_RECORD", "INVALID_SOURCE_OBSERVATION",
                   "INVALID_OUTCOME", "INVALID_REVISION_CHAIN"):
        return _error("LEDGER_INTEGRITY_FAILURE", "Prospective evidence failed validation.", 409)
    if ("could not read corner data config" in message
            or "config requires" in message
            or "data_directory must" in message
            or "leagues must" in message
            or "max_age_days must" in message
            or "history files" in message
            or "Football-Data CSV" in message
            or "configured corner history" in message
            or "overlapping" in message and "history" in message
            or "could not hash source CSV" in message):
        return _error("DATA_SOURCE_UNAVAILABLE", message, 503, retryable=True)
    if "not enabled in the data config" in message:
        return _error("UNSUPPORTED_COMPETITION", message, 422)
    if ("unsupported market type" in message
            or "requires team_side" in message
            or "requires a null team_side" in message
            or message == "side must be OVER or UNDER"):
        return _error("UNSUPPORTED_MARKET", message, 422)
    if "no history for team" in message:
        return _error("UNKNOWN_TEAM", message, 422)
    if "insufficient" in message and "history" in message:
        return _error("INSUFFICIENT_HISTORY", message, 422)
    if "American odds" in message:
        return _error("INVALID_ODDS", message, 422)
    if "line must" in message:
        return _error("INVALID_LINE", message, 422)
    if isinstance(error, LedgerError):
        return _error("LEDGER_INTEGRITY_FAILURE", message, 500)
    return _error("INVALID_REQUEST", message, 422)


def _public_read_error(error: LedgerError) -> JSONResponse:
    """Public prospective views must not return storage paths or ledger internals."""
    if isinstance(error, LedgerStorageUnavailable):
        return _error("STATE_STORAGE_UNAVAILABLE", "Prospective evidence is temporarily unavailable.",
                      503, retryable=True)
    return _error("LEDGER_INTEGRITY_FAILURE", "Prospective evidence failed validation.", 409)


def create_app(
    *, data_config_path: str | Path | None = None,
    state_dir: str | Path | None = None,
    cors_origins: list[str] | None = None,
    min_history: int = 100,
    min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
) -> FastAPI:
    """Create an injectable app; environment variables configure deployment."""
    config = Path(data_config_path or os.environ.get("MODELFC_DATA_CONFIG", "corner_data.json"))
    state = Path(state_dir or os.environ.get("MODELFC_STATE_DIR", "data/model-fc-state"))
    if cors_origins is None:
        cors_origins = [
            item.strip() for item in os.environ.get("MODELFC_CORS_ORIGINS", "").split(",")
            if item.strip()
        ]
    app = FastAPI(title="Model FC API", version="1.0.0")
    if cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=cors_origins,
            allow_credentials=False, allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": {
            "code": "INVALID_REQUEST", "message": "Request validation failed.",
            "details": {"errors": error.errors()}, "retryable": False,
        }})

    @app.post("/api/v1/analyses", response_model=AnalysisResponse, status_code=201)
    def create_analysis(request: AnalysisRequest) -> dict[str, Any]:
        try:
            response, _ = analyze_and_store(
                data_config_path=config, state_dir=state,
                idempotency_key=request.idempotency_key,
                competition=request.fixture.competition,
                fixture_date=request.fixture.date,
                home_team=request.fixture.home_team,
                away_team=request.fixture.away_team,
                markets=[CornerMarketRequest(
                    item.client_market_id, item.market_type, item.team_side,
                    item.side, item.line, item.american_odds,
                ) for item in request.markets],
                model=request.model,
                min_history=min_history,
                min_venue_history=min_venue_history,
                smoothing_matches=smoothing_matches,
            )
            return response
        except (ValueError, LedgerError) as error:
            return _domain_error(error)

    @app.get("/api/v1/capabilities", response_model=CapabilitiesResponse)
    def get_capabilities() -> dict[str, Any]:
        try:
            return api_capabilities(
                config, min_history=min_history,
                min_venue_history=min_venue_history,
            )
        except ValueError as error:
            return _domain_error(error)

    @app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisResponse)
    def get_analysis(analysis_id: str) -> dict[str, Any]:
        try:
            return load_analysis(state, analysis_id)
        except LedgerError as error:
            return _domain_error(error)

    @app.get("/api/v1/opportunities", response_model=list[OpportunityResponse])
    def get_opportunities() -> list[dict[str, Any]]:
        try:
            return read_opportunities(state)
        except LedgerError as error:
            return _public_read_error(error)

    @app.get("/api/v1/research/btts", response_model=list[BttsResearchResponse])
    def get_btts_research(response: Response, competition: str = "E1"):
        response.headers["Cache-Control"] = "no-store"
        if competition not in ENABLED_COMPETITIONS:
            error = _error("UNSUPPORTED_COMPETITION", "BTTS research competition is not enabled.", 422)
        else:
            try:
                return read_btts_research(state, competition)
            except LedgerError as cause:
                error = _public_read_error(cause)
        error.headers["Cache-Control"] = "no-store"
        return error

    @app.get("/api/v1/recommendations", response_model=list[RecommendationResponse])
    def get_recommendations() -> list[dict[str, Any]]:
        try:
            return read_recommendations(state)
        except LedgerError as error:
            return _public_read_error(error)

    @app.get(
        "/api/v1/opportunities/{opportunity_id}",
        response_model=OpportunityDetailResponse,
    )
    def get_opportunity(opportunity_id: str) -> dict[str, Any]:
        try:
            return read_opportunity(state, opportunity_id)
        except LedgerError as error:
            if str(error) == "UNKNOWN_OPPORTUNITY":
                return _error("OPPORTUNITY_NOT_FOUND", "Opportunity was not found.", 404)
            return _public_read_error(error)

    @app.get("/api/v1/predictions", response_model=list[PredictionResponse])
    def get_predictions() -> list[dict[str, Any]]:
        try:
            return read_predictions(state)
        except LedgerError as error:
            return _public_read_error(error)

    @app.get(
        "/api/v1/prospective/performance",
        response_model=ProspectivePerformanceResponse,
    )
    def get_prospective_performance() -> dict[str, Any]:
        try:
            return read_performance(state)
        except LedgerError as error:
            return _public_read_error(error)

    @app.middleware("http")
    async def team_error_cache(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if response.status_code >= 400 and (path in ("/api/v1/teams", "/api/v1/team-insights")
                                           or path.startswith("/api/v1/teams/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def team_read(request: Request, kind: str, team_id: str | None = None):
        try:
            if kind == "profile" and team_id not in BY_ID:
                raise TeamIntelligenceError("TEAM_NOT_FOUND")
            population = load_population(config)
            value = (population.get_team_profile(team_id) if kind == "profile" else
                     population.find_team_insights() if kind == "insights" else
                     population.list_team_intelligence())
            body = value.model_dump(mode="json")
            # Includes route-specific projection and all contract/rule versions.
            etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                                  allow_nan=False).encode()).hexdigest() + '"'
            headers = {"Cache-Control": "public, max-age=60", "ETag": etag}
            if any(tag.strip().removeprefix("W/") in (etag, "*")
                   for tag in request.headers.get("if-none-match", "").split(",")):
                from fastapi import Response
                return Response(status_code=304, headers=headers)
            return JSONResponse(body, headers=headers)
        except TeamIntelligenceError as error:
            unknown = str(error) == "TEAM_NOT_FOUND"
            response = _error("TEAM_NOT_FOUND" if unknown else "TEAM_HISTORY_UNAVAILABLE",
                              "Team was not found." if unknown else "Team history is temporarily unavailable.",
                              404 if unknown else 503, retryable=not unknown)
            response.headers["Cache-Control"] = "no-store"
            return response

    @app.get("/api/v1/teams", response_model=TeamList)
    def get_teams(request: Request):
        return team_read(request, "list")

    @app.get("/api/v1/teams/{team_id}", response_model=TeamProfile)
    def get_team(team_id: str, request: Request):
        return team_read(request, "profile", team_id)

    @app.get("/api/v1/team-insights", response_model=InsightList)
    def get_team_insights(request: Request):
        return team_read(request, "insights")

    return app


app = create_app()
