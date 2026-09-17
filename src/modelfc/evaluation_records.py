"""Immutable record schemas for future longitudinal model evaluation.

These records are extension points only.  They do not retrain models, settle
ledger picks, or change the current prediction API.  A future persistence layer
can serialize them with ``model_dump(mode="json")`` and retain their stable
IDs and version metadata for replayable evaluation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


class _ImmutableRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputSnapshotRef(_ImmutableRecord):
    """A source snapshot and its information cutoff."""

    source_name: str
    snapshot_hash: str
    snapshot_version: str
    captured_at_utc: datetime
    data_as_of_utc: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "InputSnapshotRef":
        _require_aware(self.captured_at_utc, "captured_at_utc")
        _require_aware(self.data_as_of_utc, "data_as_of_utc")
        if self.data_as_of_utc > self.captured_at_utc:
            raise ValueError("data_as_of_utc cannot be after captured_at_utc")
        return self


class FixtureRecord(_ImmutableRecord):
    """Stable fixture identity and provider provenance."""

    fixture_id: str
    provider_name: str
    provider_snapshot_id: str
    competition: str
    competition_id: str
    season: str
    kickoff_timestamp_utc: datetime
    home_team: str
    away_team: str
    status: Literal["scheduled", "postponed", "completed", "cancelled"]
    historical_data_snapshot_hash: str
    data_snapshot_version: str
    recorded_at_utc: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "FixtureRecord":
        _require_aware(self.kickoff_timestamp_utc, "kickoff_timestamp_utc")
        _require_aware(self.recorded_at_utc, "recorded_at_utc")
        if self.home_team.casefold() == self.away_team.casefold():
            raise ValueError("home_team and away_team must be different")
        return self


class AnalysisRecord(_ImmutableRecord):
    """One batch analysis, linked to an immutable fixture and input cutoff."""

    analysis_id: str
    fixture_id: str
    provider_snapshot_id: str
    competition: str
    competition_id: str
    season: str
    historical_data_snapshot_hash: str
    data_snapshot_version: str
    feature_set_version: str
    model_name: str
    model_version: str
    analysis_timestamp_utc: datetime
    kickoff_timestamp_utc: datetime
    input_data_as_of_utc: datetime
    prediction_ids: tuple[str, ...] = ()
    status: Literal["analyzed", "invalid", "needs_confirmation", "unresolved"] = "analyzed"

    @model_validator(mode="after")
    def validate_pre_kickoff_cutoff(self) -> "AnalysisRecord":
        for name, value in (
            ("analysis_timestamp_utc", self.analysis_timestamp_utc),
            ("kickoff_timestamp_utc", self.kickoff_timestamp_utc),
            ("input_data_as_of_utc", self.input_data_as_of_utc),
        ):
            _require_aware(value, name)
        if self.analysis_timestamp_utc >= self.kickoff_timestamp_utc:
            raise ValueError("analysis_timestamp_utc must be before kickoff")
        if self.input_data_as_of_utc > self.analysis_timestamp_utc:
            raise ValueError("input_data_as_of_utc cannot be after analysis timestamp")
        return self


class PredictionRecord(_ImmutableRecord):
    """One market prediction with a timestamped, leakage-safe input boundary."""

    prediction_id: str
    analysis_id: str
    fixture_id: str
    provider_snapshot_id: str
    competition: str
    competition_id: str
    season: str
    historical_data_snapshot_hash: str
    data_snapshot_version: str
    feature_set_version: str
    model_name: str
    model_version: str
    prediction_version: str
    prediction_timestamp_utc: datetime
    kickoff_timestamp_utc: datetime
    input_data_as_of_utc: datetime
    input_snapshots: tuple[InputSnapshotRef, ...] = ()
    market_type: Literal["team_total_corners", "match_total_corners"]
    team: str | None = None
    team_side: Literal["home", "away"] | None = None
    direction: Literal["over", "under"]
    line: float = Field(ge=0)
    american_odds: int
    predicted_probabilities: dict[str, float]
    actual_outcome_reference: str | None = None
    settlement_status: Literal["open", "win", "loss", "push", "needs_review"] = "open"

    @model_validator(mode="after")
    def validate_pre_kickoff_inputs(self) -> "PredictionRecord":
        _require_aware(self.prediction_timestamp_utc, "prediction_timestamp_utc")
        _require_aware(self.kickoff_timestamp_utc, "kickoff_timestamp_utc")
        _require_aware(self.input_data_as_of_utc, "input_data_as_of_utc")
        if self.prediction_timestamp_utc >= self.kickoff_timestamp_utc:
            raise ValueError("pre-kickoff prediction timestamp must be before kickoff")
        if self.input_data_as_of_utc > self.prediction_timestamp_utc:
            raise ValueError("prediction inputs cannot be from after prediction timestamp")
        if not self.predicted_probabilities:
            raise ValueError("predicted_probabilities must not be empty")
        if any(not 0 <= probability <= 1 for probability in self.predicted_probabilities.values()):
            raise ValueError("predicted probabilities must be between 0 and 1")
        for snapshot in self.input_snapshots:
            if snapshot.data_as_of_utc > self.input_data_as_of_utc:
                raise ValueError("input snapshot contains data newer than the prediction cutoff")
            if snapshot.captured_at_utc > self.prediction_timestamp_utc:
                raise ValueError("input snapshot was captured after the prediction timestamp")
        return self


class ActualOutcomeReference(_ImmutableRecord):
    """Immutable final result used to score a prediction later."""

    outcome_id: str
    fixture_id: str
    provider_snapshot_id: str
    source_data_hash: str
    outcome_source_version: str
    retrieved_at_utc: datetime
    completed_at_utc: datetime | None = None
    home_corners: int | None = Field(default=None, ge=0)
    away_corners: int | None = Field(default=None, ge=0)
    status: Literal["completed", "postponed", "cancelled"]

    @model_validator(mode="after")
    def validate_result(self) -> "ActualOutcomeReference":
        _require_aware(self.retrieved_at_utc, "retrieved_at_utc")
        if self.completed_at_utc is not None:
            _require_aware(self.completed_at_utc, "completed_at_utc")
        if self.status == "completed" and (self.home_corners is None or self.away_corners is None):
            raise ValueError("completed outcomes require both corner counts")
        return self


class EvaluationHistoryRecord(_ImmutableRecord):
    """Versioned scoring event, separate from the original prediction."""

    evaluation_id: str
    prediction_id: str
    fixture_id: str
    actual_outcome_reference: str
    evaluated_at_utc: datetime
    evaluation_run_version: str
    scoring_version: str
    settlement_status: Literal["win", "loss", "push", "needs_review"]
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamp(self) -> "EvaluationHistoryRecord":
        _require_aware(self.evaluated_at_utc, "evaluated_at_utc")
        return self


class CalibrationMetricRecord(_ImmutableRecord):
    """Future calibration result over a fixed, versioned evaluation dataset."""

    metric_id: str
    dataset_id: str
    model_name: str
    model_version: str
    feature_set_version: str
    evaluation_run_version: str
    market_type: str
    scored_from_utc: datetime
    scored_to_utc: datetime
    sample_count: int = Field(ge=0)
    brier_score: float | None = Field(default=None, ge=0)
    log_loss: float | None = Field(default=None, ge=0)
    calibration_bins: tuple[dict[str, float], ...] = ()

    @model_validator(mode="after")
    def validate_window(self) -> "CalibrationMetricRecord":
        _require_aware(self.scored_from_utc, "scored_from_utc")
        _require_aware(self.scored_to_utc, "scored_to_utc")
        if self.scored_from_utc > self.scored_to_utc:
            raise ValueError("scored_from_utc cannot be after scored_to_utc")
        return self


class ModelComparisonRecord(_ImmutableRecord):
    """Comparable model outputs over the same fixture and data snapshots."""

    comparison_id: str
    dataset_id: str
    fixture_ids: tuple[str, ...]
    model_names: tuple[str, ...]
    feature_set_versions: tuple[str, ...]
    evaluation_run_version: str
    compared_at_utc: datetime
    metrics_by_model: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_comparison(self) -> "ModelComparisonRecord":
        _require_aware(self.compared_at_utc, "compared_at_utc")
        if not self.fixture_ids:
            raise ValueError("model comparison requires at least one fixture")
        if not self.model_names:
            raise ValueError("model comparison requires at least one model")
        return self


__all__ = [
    "ActualOutcomeReference",
    "AnalysisRecord",
    "CalibrationMetricRecord",
    "EvaluationHistoryRecord",
    "FixtureRecord",
    "InputSnapshotRef",
    "ModelComparisonRecord",
    "PredictionRecord",
]
