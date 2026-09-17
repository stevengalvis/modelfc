"""Post-retrieval grounding and recorded-evaluation checks.

This module validates a normalized response against two immutable sources:
the provider snapshot used for retrieval and a recorded ground-truth result.
It is intentionally deterministic and has no network or model calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .fixture_resolution_api import FixtureResolutionResponse
from .upcoming_fixtures import FixtureClues, FixtureSnapshot, FixtureStatus


class GroundTruthMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_type: str
    team: str | None = None
    team_side: str | None = None
    direction: str
    line: float
    american_odds: int


class GroundTruthFixture(BaseModel):
    """Expected result recorded independently of a candidate response."""

    model_config = ConfigDict(extra="forbid")

    fixture_id: str | None = None
    competition_id: str
    competition_label: str
    match_date: str
    home_team: str
    away_team: str
    expected_status: str
    expected_behavior: str
    markets: list[GroundTruthMarket] = Field(default_factory=list)
    candidate_fixture_ids: list[str] = Field(default_factory=list)


class GroundingEvaluationResult(BaseModel):
    """Machine-readable result for one recorded evaluation case."""

    model_config = ConfigDict(extra="forbid")

    evaluation_status: str
    schema_valid: bool
    grounded: bool
    exact_fixture_match: bool
    field_accuracy: dict[str, float] = Field(default_factory=dict)
    provider_grounded: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    hallucination_count: int = Field(default=0, ge=0)
    correct_abstention: bool
    ground_truth_available: bool


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _date_from_value(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1] + "+00:00").date().isoformat()
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _field_value(field: object) -> str | None:
    value = getattr(field, "value", None)
    return value if isinstance(value, str) else None


def _market_key(market: object) -> tuple[object, ...]:
    return (
        getattr(market, "market_type", None),
        _norm(getattr(market, "team", None) or ""),
        getattr(market, "team_side", None),
        getattr(market, "direction", None),
        float(getattr(market, "line", 0)),
        int(getattr(market, "american_odds", 0)),
    )


def _clues_from_value(value: FixtureClues | Mapping[str, object] | None) -> FixtureClues | None:
    if value is None:
        return None
    if isinstance(value, FixtureClues):
        return value
    if not isinstance(value, Mapping):
        return None
    raw_date = value.get("match_date")
    if isinstance(raw_date, str):
        try:
            raw_date = date.fromisoformat(raw_date)
        except ValueError:
            raw_date = None
    try:
        return FixtureClues(
            competition=value.get("competition"),
            match_date=raw_date,
            home_team=value.get("home_team"),
            away_team=value.get("away_team"),
            team=value.get("team"),
            team_side=value.get("team_side"),
        )
    except (TypeError, ValueError):
        return None


def _base_result(
    *,
    schema_valid: bool,
    expected: GroundTruthFixture | None,
) -> dict[str, object]:
    return {
        "evaluation_status": "failed",
        "schema_valid": schema_valid,
        "grounded": False,
        "exact_fixture_match": False,
        "field_accuracy": {},
        "provider_grounded": False,
        "unsupported_claims": [],
        "conflicts": [],
        "hallucination_count": 0,
        "correct_abstention": False,
        "ground_truth_available": expected is not None,
    }


def evaluate_fixture_response(
    response: FixtureResolutionResponse | Mapping[str, object],
    snapshot: FixtureSnapshot,
    expected: GroundTruthFixture | Mapping[str, object] | None = None,
    *,
    explicit_clues: FixtureClues | Mapping[str, object] | None = None,
    explicit_markets: Sequence[GroundTruthMarket | Mapping[str, object]] | None = None,
) -> GroundingEvaluationResult:
    """Evaluate a final candidate response against provider and ground truth.

    A failed evaluation is an abstention boundary.  It is never converted into
    a resolved fixture merely because a candidate looks plausible.
    """

    try:
        parsed = (
            response
            if isinstance(response, FixtureResolutionResponse)
            else FixtureResolutionResponse.model_validate(response)
        )
    except ValidationError as error:
        result = _base_result(schema_valid=False, expected=None)
        result["unsupported_claims"] = [f"schema: {error.errors()[0]['msg']}"]
        result["hallucination_count"] = 1
        return GroundingEvaluationResult.model_validate(result)

    try:
        truth = None if expected is None else (
            expected if isinstance(expected, GroundTruthFixture)
            else GroundTruthFixture.model_validate(expected)
        )
    except ValidationError as error:
        result = _base_result(schema_valid=True, expected=None)
        result["unsupported_claims"] = [f"ground_truth_schema: {error.errors()[0]['msg']}"]
        return GroundingEvaluationResult.model_validate(result)

    result = _base_result(schema_valid=True, expected=truth)
    unsupported: list[str] = result["unsupported_claims"]  # type: ignore[assignment]
    conflicts: list[str] = result["conflicts"]  # type: ignore[assignment]
    field_accuracy: dict[str, float] = result["field_accuracy"]  # type: ignore[assignment]

    if parsed.grounding.provider_snapshot_id != snapshot.provider_snapshot_id:
        conflicts.append("provider snapshot ID does not match the retrieved snapshot")
    if parsed.grounding.provider_name and parsed.grounding.provider_name != snapshot.provider_name:
        conflicts.append("provider name does not match the retrieved snapshot")
    if parsed.status == "resolved" and truth is None:
        conflicts.append("resolved response has no recorded ground-truth expected result")

    provider_by_id = {fixture.provider_fixture_id: fixture for fixture in snapshot.fixtures}
    response_fixtures = parsed.fixtures
    resolved_fixture = response_fixtures[0] if len(response_fixtures) == 1 else None
    provider_fixture = None

    for fixture in response_fixtures:
        if not fixture.fixture_id:
            # A confirmation response may intentionally omit a selected ID,
            # provided that every candidate carries its own provider ID.  A
            # resolved response, or a response with no candidate evidence,
            # must never pass without a fixture ID.
            if parsed.status == "resolved" or not fixture.candidate_fixtures:
                unsupported.append("fixture ID is absent")
            for candidate in fixture.candidate_fixtures:
                if candidate.fixture_id not in provider_by_id:
                    unsupported.append(f"candidate fixture ID {candidate.fixture_id!r} is absent from provider snapshot")
            continue
        provider_fixture = provider_by_id.get(fixture.fixture_id)
        if provider_fixture is None:
            unsupported.append(f"fixture ID {fixture.fixture_id!r} is absent from provider snapshot")
            continue
        for candidate in fixture.candidate_fixtures:
            if candidate.fixture_id not in provider_by_id:
                unsupported.append(f"candidate fixture ID {candidate.fixture_id!r} is absent from provider snapshot")
        if parsed.status == "resolved" and len(fixture.candidate_fixtures) > 1:
            conflicts.append("ambiguous candidate was automatically resolved")
        if provider_fixture.status is not FixtureStatus.SCHEDULED and parsed.status == "resolved":
            conflicts.append(f"fixture {fixture.fixture_id!r} is {provider_fixture.status.value}, not scheduled")

        checks = {
            "competition_id": _norm(_field_value(fixture.competition_id)) == _norm(provider_fixture.competition_id),
            "competition": (
                _norm(_field_value(fixture.competition)) in {
                    _norm(provider_fixture.competition_id), _norm(provider_fixture.competition_label)
                }
                if _field_value(fixture.competition) is not None else False
            ),
            "date": _date_from_value(_field_value(fixture.match_date)) == provider_fixture.kickoff_utc.date().isoformat(),
            "home_team": _norm(_field_value(fixture.home_team)) == _norm(provider_fixture.home_team),
            "away_team": _norm(_field_value(fixture.away_team)) == _norm(provider_fixture.away_team),
        }
        field_accuracy.update({key: float(value) for key, value in checks.items()})
        for field, matches in checks.items():
            if not matches:
                conflicts.append(f"{field} conflicts with provider fixture {fixture.fixture_id}")

        if truth is not None:
            expected_fields = {
                "fixture_id": fixture.fixture_id == truth.fixture_id,
                "competition_id_truth": _norm(_field_value(fixture.competition_id)) == _norm(truth.competition_id),
                "date_truth": _date_from_value(_field_value(fixture.match_date)) == truth.match_date,
                "home_team_truth": _norm(_field_value(fixture.home_team)) == _norm(truth.home_team),
                "away_team_truth": _norm(_field_value(fixture.away_team)) == _norm(truth.away_team),
            }
            field_accuracy.update({key: float(value) for key, value in expected_fields.items()})
            if parsed.status == "resolved" and truth.expected_status != "resolved":
                conflicts.append("response resolved a case that ground truth requires to abstain")

        response_market_keys = {_market_key(market) for market in fixture.markets}
        expected_markets = explicit_markets
        if expected_markets is None and truth is not None:
            expected_markets = truth.markets
        if expected_markets is not None:
            expected_market_keys = {
                _market_key(item if isinstance(item, GroundTruthMarket) else GroundTruthMarket.model_validate(item))
                for item in expected_markets
            }
            if response_market_keys != expected_market_keys:
                unsupported.append("market, line, or odds value was invented or omitted")
            for market in fixture.markets:
                if _market_key(market) not in expected_market_keys:
                    unsupported.append("market claim is not present in explicit input or ground truth")

    clues = _clues_from_value(explicit_clues)
    if clues is not None and resolved_fixture is not None:
        explicit_checks = {
            "explicit_competition": (
                not clues.competition
                or _norm(clues.competition) in {
                    _norm(_field_value(resolved_fixture.competition_id)),
                    _norm(_field_value(resolved_fixture.competition)),
                }
            ),
            "explicit_date": (
                not clues.match_date
                or _date_from_value(_field_value(resolved_fixture.match_date)) == clues.match_date.isoformat()
            ),
            "explicit_home_team": (
                not clues.home_team
                or _norm(clues.home_team) == _norm(_field_value(resolved_fixture.home_team))
            ),
            "explicit_away_team": (
                not clues.away_team
                or _norm(clues.away_team) == _norm(_field_value(resolved_fixture.away_team))
            ),
        }
        for field, matches in explicit_checks.items():
            if not matches:
                conflicts.append(f"response conflicts with explicit input: {field}")

    expected_status = truth.expected_status if truth is not None else None
    if truth is not None and expected_status == "needs_confirmation" and parsed.status == "resolved":
        conflicts.append("ambiguous candidate was automatically resolved")
    if truth is not None and truth.candidate_fixture_ids:
        candidate_ids = {
            candidate.fixture_id
            for fixture in response_fixtures
            for candidate in fixture.candidate_fixtures
        }
        if candidate_ids != set(truth.candidate_fixture_ids):
            conflicts.append("candidate fixture set differs from recorded ground truth")

    result["grounded"] = bool(
        parsed.status == "resolved"
        and response_fixtures
        and not unsupported
        and not conflicts
        and parsed.grounding.provider_snapshot_id == snapshot.provider_snapshot_id
        and all(fixture.fixture_id in provider_by_id for fixture in response_fixtures)
    )
    result["provider_grounded"] = bool(result["grounded"])
    result["exact_fixture_match"] = bool(
        truth is not None
        and truth.fixture_id is not None
        and truth.expected_status == "resolved"
        and parsed.status == "resolved"
        and resolved_fixture is not None
        and resolved_fixture.fixture_id == truth.fixture_id
        and not conflicts
    )
    result["correct_abstention"] = bool(
        truth is not None
        and truth.expected_behavior in {"ask_confirmation", "abstain", "reject"}
        and parsed.status == truth.expected_status
        and parsed.status != "resolved"
        and not unsupported
        and not conflicts
    )
    result["unsupported_claims"] = unsupported
    result["conflicts"] = conflicts
    result["hallucination_count"] = len(unsupported)
    result["field_accuracy"] = field_accuracy
    result["evaluation_status"] = "passed" if (
        (bool(result["grounded"]) and (truth is None or truth.expected_behavior == "accept"))
        or bool(result["correct_abstention"])
    ) else "failed"
    return GroundingEvaluationResult.model_validate(result)


@dataclass(frozen=True)
class RecordedEvaluationReport:
    """Aggregate metrics for a recorded response set."""

    total_cases: int
    schema_valid_cases: int
    exact_fixture_accuracy: float
    field_accuracy: float
    provider_grounded_resolution_rate: float
    hallucination_count: int
    correct_abstention_rate: float
    schema_validity: float
    failed_cases: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_cases": self.total_cases,
            "schema_valid_cases": self.schema_valid_cases,
            "exact_fixture_accuracy": self.exact_fixture_accuracy,
            "field_accuracy": self.field_accuracy,
            "provider_grounded_resolution_rate": self.provider_grounded_resolution_rate,
            "hallucination_count": self.hallucination_count,
            "correct_abstention_rate": self.correct_abstention_rate,
            "schema_validity": self.schema_validity,
            "failed_cases": list(self.failed_cases),
        }


def run_recorded_evaluation(
    snapshot: FixtureSnapshot,
    cases: Sequence[Mapping[str, object]],
    responses: Mapping[str, FixtureResolutionResponse | Mapping[str, object]],
) -> RecordedEvaluationReport:
    """Evaluate recorded responses and return reproducible aggregate metrics."""

    results: list[tuple[str, GroundingEvaluationResult]] = []
    for case in cases:
        case_id = str(case["case_id"])
        expected = GroundTruthFixture.model_validate(case.get("ground_truth")) if case.get("ground_truth") else None
        result = evaluate_fixture_response(
            responses[case_id], snapshot, expected,
            explicit_clues=case.get("explicit_clues"),
        )
        results.append((case_id, result))

    total = len(results)
    schema_valid = sum(item.schema_valid for _, item in results)
    positive_cases = sum(1 for case in cases if str(case.get("expected_status")) == "resolved")
    abstention_cases = total - positive_cases
    exact = sum(item.exact_fixture_match for _, item in results)
    abstain = sum(item.correct_abstention for _, item in results)
    resolved = sum(item.provider_grounded for _, item in results)
    field_values = [value for _, item in results for value in item.field_accuracy.values()]
    failures = tuple(case_id for case_id, item in results if item.evaluation_status != "passed")
    return RecordedEvaluationReport(
        total_cases=total,
        schema_valid_cases=schema_valid,
        exact_fixture_accuracy=exact / positive_cases if positive_cases else 0.0,
        field_accuracy=sum(field_values) / len(field_values) if field_values else 0.0,
        provider_grounded_resolution_rate=resolved / positive_cases if positive_cases else 0.0,
        hallucination_count=sum(item.hallucination_count for _, item in results),
        correct_abstention_rate=abstain / abstention_cases if abstention_cases else 0.0,
        schema_validity=schema_valid / total if total else 0.0,
        failed_cases=failures,
    )


def load_recorded_cases(path: str) -> list[dict[str, object]]:
    """Load the small versioned evaluation case file."""

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("recorded evaluation dataset must contain a cases list")
    return payload["cases"]


__all__ = [
    "GroundTruthFixture",
    "GroundTruthMarket",
    "GroundingEvaluationResult",
    "RecordedEvaluationReport",
    "evaluate_fixture_response",
    "load_recorded_cases",
    "run_recorded_evaluation",
]
