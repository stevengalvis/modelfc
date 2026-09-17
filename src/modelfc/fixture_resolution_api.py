"""Versioned, provider-grounded response models for fixture resolution.

The models in this module are deliberately independent of FastAPI.  They are
the stable boundary that a future HTTP handler, parser, or language-model
adapter can use.  Every field is explicit and unknown fields are rejected so
that an accidental parser claim cannot silently become part of the contract.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FIXTURE_RESOLUTION_API_VERSION = "1.0"

ResolutionStatus = Literal["resolved", "needs_confirmation", "unresolved", "invalid"]
EvaluationStatus = Literal["passed", "failed", "not_run"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourcedValue(_StrictModel):
    """A normalized value and where it came from."""

    value: str | None = None
    source: Literal["explicit_input", "schedule_provider", "alias_map", "parser"]
    confidence: float | None = Field(default=None, ge=0, le=1)


class MarketResponse(_StrictModel):
    """One market claim made by a normalized response."""

    market_type: Literal["team_total_corners", "match_total_corners"]
    team: str | None = None
    team_side: Literal["home", "away"] | None = None
    direction: Literal["over", "under"]
    line: float = Field(ge=0)
    american_odds: int
    source: Literal["explicit_input", "parser"] = "explicit_input"


class CandidateFixtureResponse(_StrictModel):
    """A provider candidate shown when a request needs confirmation."""

    fixture_id: str
    competition_id: str
    competition_label: str
    season: str
    kickoff_utc: str
    home_team: str
    away_team: str
    status: Literal["scheduled", "postponed", "completed", "cancelled"]


class ResolvedFixtureResponse(_StrictModel):
    """A normalized fixture plus the markets attached to it."""

    fixture_id: str | None = None
    competition_id: SourcedValue | None = None
    competition: SourcedValue | None = None
    match_date: SourcedValue | None = None
    kickoff_utc: SourcedValue | None = None
    home_team: SourcedValue | None = None
    away_team: SourcedValue | None = None
    markets: list[MarketResponse] = Field(default_factory=list)
    candidate_fixtures: list[CandidateFixtureResponse] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GroundingResponse(_StrictModel):
    """Provenance for the provider snapshot used by a response."""

    provider_name: str | None = None
    provider_request_id: str | None = None
    retrieved_at: str | None = None
    provider_snapshot_id: str | None = None
    all_resolved_fixtures_found_in_provider: bool = False


class EvaluationResponse(_StrictModel):
    """Result of post-retrieval grounding evaluation."""

    ground_truth_available: bool = False
    evaluation_status: EvaluationStatus = "not_run"
    grounded: bool = False
    unsupported_claims: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    hallucination_count: int = Field(default=0, ge=0)


class UsageResponse(_StrictModel):
    """Reserved usage fields, kept zero until an LLM adapter exists."""

    model: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    cache_hit: bool = False
    latency_ms: float | None = Field(default=None, ge=0)


class FixtureResolutionResponse(_StrictModel):
    """Stable versioned response envelope for fixture resolution."""

    api_version: Literal["1.0"] = FIXTURE_RESOLUTION_API_VERSION
    request_id: str
    status: ResolutionStatus
    fixtures: list[ResolvedFixtureResponse] = Field(default_factory=list)
    grounding: GroundingResponse = Field(default_factory=GroundingResponse)
    evaluation: EvaluationResponse = Field(default_factory=EvaluationResponse)
    usage: UsageResponse = Field(default_factory=UsageResponse)

    @model_validator(mode="after")
    def validate_status_invariants(self) -> "FixtureResolutionResponse":
        if self.status == "resolved":
            if not self.fixtures:
                raise ValueError("resolved response must contain at least one fixture")
            if any(not fixture.fixture_id for fixture in self.fixtures):
                raise ValueError("resolved response requires a provider fixture ID")
            if not self.grounding.provider_snapshot_id:
                raise ValueError("resolved response requires provider snapshot provenance")
            if not self.grounding.all_resolved_fixtures_found_in_provider:
                raise ValueError("resolved response must be provider grounded")
            if self.evaluation.evaluation_status != "passed":
                raise ValueError("resolved response requires a passed evaluation")
        if self.status != "resolved" and self.evaluation.evaluation_status == "passed":
            raise ValueError("only a resolved response may have a passed evaluation")
        return self


__all__ = [
    "FIXTURE_RESOLUTION_API_VERSION",
    "CandidateFixtureResponse",
    "EvaluationResponse",
    "FixtureResolutionResponse",
    "GroundingResponse",
    "MarketResponse",
    "ResolvedFixtureResponse",
    "SourcedValue",
    "UsageResponse",
]
