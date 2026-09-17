"""FastAPI boundary for the Model FC V1 corner-analysis API."""

from datetime import date
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from modelfc.corner_analysis import MAX_MARKETS_PER_ANALYSIS, CornerMarketRequest
from modelfc.corner_analysis_store import analyze_and_store, load_analysis
from modelfc.corner_capabilities import api_capabilities
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


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
    if isinstance(error, LedgerStorageUnavailable):
        return _error("STATE_STORAGE_UNAVAILABLE", message, 503, retryable=True)
    if message.startswith("invalid corner analysis record"):
        return _error("LEDGER_INTEGRITY_FAILURE", message, 409)
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

    return app


app = create_app()
