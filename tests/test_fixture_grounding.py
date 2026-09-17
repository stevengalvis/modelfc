from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from modelfc.fixture_grounding import (
    GroundTruthFixture,
    evaluate_fixture_response,
    load_recorded_cases,
    run_recorded_evaluation,
)
from modelfc.fixture_resolution_api import FixtureResolutionResponse
from modelfc.upcoming_fixtures import FixtureSnapshot


ROOT = Path(__file__).parent / "fixtures" / "fixture_resolution"


def load_snapshot() -> FixtureSnapshot:
    return FixtureSnapshot.from_dict(json.loads((ROOT / "provider_snapshot.json").read_text()))


def sourced(value: str | None, source: str = "schedule_provider") -> dict[str, str]:
    return {"value": value, "source": source}


def candidate(fixture) -> dict[str, object]:
    return {
        "fixture_id": fixture.provider_fixture_id,
        "competition_id": fixture.competition_id,
        "competition_label": fixture.competition_label,
        "season": fixture.season,
        "kickoff_utc": fixture.kickoff_utc.isoformat().replace("+00:00", "Z"),
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "status": fixture.status.value,
    }


def fixture_response(
    snapshot: FixtureSnapshot,
    fixture_id: str | None,
    *,
    status: str = "resolved",
    match_date: str = "2026-09-17",
    snapshot_id: str | None = None,
    markets: list[dict[str, object]] | None = None,
    candidates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    provider_fixture = next(
        (item for item in snapshot.fixtures if item.provider_fixture_id == fixture_id),
        snapshot.fixtures[0],
    )
    fixture = {
        "fixture_id": fixture_id,
        "competition_id": sourced(provider_fixture.competition_id),
        "competition": sourced(provider_fixture.competition_label),
        "match_date": sourced(match_date),
        "kickoff_utc": sourced(provider_fixture.kickoff_utc.isoformat().replace("+00:00", "Z")),
        "home_team": sourced(provider_fixture.home_team),
        "away_team": sourced(provider_fixture.away_team),
        "markets": markets or [],
        "candidate_fixtures": candidates or [],
    }
    return {
        "api_version": "1.0",
        "request_id": "test-request",
        "status": status,
        "fixtures": [] if status == "unresolved" and fixture_id is None else [fixture],
        "grounding": {
            "provider_name": snapshot.provider_name,
            "provider_request_id": snapshot.provider_request_id,
            "retrieved_at": snapshot.retrieved_at.isoformat().replace("+00:00", "Z"),
            "provider_snapshot_id": snapshot_id or snapshot.provider_snapshot_id,
            "all_resolved_fixtures_found_in_provider": True,
        },
        "evaluation": {
            "ground_truth_available": True,
            "evaluation_status": "passed" if status == "resolved" else "not_run",
            "grounded": status == "resolved",
        },
    }


class FixtureGroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = load_snapshot()
        self.exact_market = {
            "market_type": "team_total_corners",
            "team": "Birmingham",
            "team_side": "home",
            "direction": "over",
            "line": 4.5,
            "american_odds": -110,
        }
        self.exact_truth = GroundTruthFixture(
            fixture_id="e1-1001",
            competition_id="E1",
            competition_label="Championship",
            match_date="2026-09-17",
            home_team="Birmingham",
            away_team="Millwall",
            expected_status="resolved",
            expected_behavior="accept",
            markets=[self.exact_market],
        )

    def test_exact_provider_backed_match_passes(self) -> None:
        result = evaluate_fixture_response(
            fixture_response(self.snapshot, "e1-1001", markets=[self.exact_market]),
            self.snapshot,
            self.exact_truth,
            explicit_clues={
                "competition": "E1",
                "match_date": "2026-09-17",
                "home_team": "Birmingham",
                "away_team": "Millwall",
            },
        )
        self.assertEqual(result.evaluation_status, "passed")
        self.assertTrue(result.grounded)
        self.assertTrue(result.exact_fixture_match)
        self.assertEqual(result.hallucination_count, 0)

    def test_versioned_response_models_cover_all_outcomes(self) -> None:
        for status in ("needs_confirmation", "unresolved", "invalid"):
            with self.subTest(status=status):
                response = FixtureResolutionResponse(
                    request_id=f"request-{status}",
                    status=status,
                )
                self.assertEqual(response.api_version, "1.0")
                self.assertEqual(response.status, status)
        with self.assertRaises(ValidationError):
            FixtureResolutionResponse(
                request_id="request-extra",
                status="invalid",
                unexpected="not-in-contract",
            )

    def test_multiple_candidates_need_confirmation(self) -> None:
        candidates = [candidate(self.snapshot.fixtures[0]), candidate(self.snapshot.fixtures[1])]
        response = fixture_response(
            self.snapshot, None, status="needs_confirmation", candidates=candidates,
        )
        truth = GroundTruthFixture(
            fixture_id=None,
            competition_id="E1",
            competition_label="Championship",
            match_date="2026-09-17",
            home_team="Birmingham",
            away_team="",
            expected_status="needs_confirmation",
            expected_behavior="ask_confirmation",
            candidate_fixture_ids=["e1-1001", "e1-1002"],
        )
        result = evaluate_fixture_response(response, self.snapshot, truth)
        self.assertEqual(result.evaluation_status, "passed")
        self.assertTrue(result.correct_abstention)

    def test_no_candidate_is_unresolved(self) -> None:
        truth = GroundTruthFixture(
            fixture_id=None, competition_id="E1", competition_label="Championship",
            match_date="2026-09-18", home_team="Birmingham", away_team="Millwall",
            expected_status="unresolved", expected_behavior="abstain",
        )
        result = evaluate_fixture_response(
            fixture_response(self.snapshot, None, status="unresolved"), self.snapshot, truth,
        )
        self.assertEqual(result.evaluation_status, "passed")
        self.assertTrue(result.correct_abstention)

    def test_completed_fixture_is_rejected_from_resolution(self) -> None:
        fixture = self.snapshot.fixtures[3]
        truth = GroundTruthFixture(
            fixture_id=fixture.provider_fixture_id, competition_id="E1", competition_label="Championship",
            match_date="2026-05-02", home_team="Birmingham", away_team="Millwall",
            expected_status="unresolved", expected_behavior="reject",
        )
        result = evaluate_fixture_response(
            fixture_response(self.snapshot, fixture.provider_fixture_id, status="unresolved", match_date="2026-05-02"),
            self.snapshot, truth,
        )
        self.assertEqual(result.evaluation_status, "passed")

        bad = fixture_response(self.snapshot, fixture.provider_fixture_id, match_date="2026-05-02")
        bad_result = evaluate_fixture_response(bad, self.snapshot, truth)
        self.assertEqual(bad_result.evaluation_status, "failed")
        self.assertTrue(any("not scheduled" in item for item in bad_result.conflicts))

    def test_postponed_fixture_is_rejected_from_resolution(self) -> None:
        fixture = self.snapshot.fixtures[2]
        truth = GroundTruthFixture(
            fixture_id=fixture.provider_fixture_id, competition_id="E1", competition_label="Championship",
            match_date="2026-09-20", home_team="Leicester", away_team="Millwall",
            expected_status="unresolved", expected_behavior="reject",
        )
        response = fixture_response(self.snapshot, fixture.provider_fixture_id, status="resolved", match_date="2026-09-20")
        result = evaluate_fixture_response(response, self.snapshot, truth)
        self.assertEqual(result.evaluation_status, "failed")
        self.assertTrue(any("not scheduled" in item for item in result.conflicts))

    def test_unsupported_fixture_is_rejected(self) -> None:
        truth = GroundTruthFixture(
            fixture_id=None, competition_id="SP2", competition_label="La Liga 2",
            match_date="2026-09-17", home_team="Unavailable FC", away_team="Unknown CF",
            expected_status="unresolved", expected_behavior="abstain",
        )
        result = evaluate_fixture_response(
            fixture_response(self.snapshot, None, status="unresolved"), self.snapshot, truth,
        )
        self.assertEqual(result.evaluation_status, "passed")

    def test_invented_fixture_id_is_rejected(self) -> None:
        response = fixture_response(self.snapshot, "e1-4041", markets=[self.exact_market])
        result = evaluate_fixture_response(response, self.snapshot, self.exact_truth)
        self.assertEqual(result.evaluation_status, "failed")
        self.assertGreaterEqual(result.hallucination_count, 1)
        self.assertTrue(any("absent from provider" in item for item in result.unsupported_claims))

    def test_conflicting_date_is_rejected(self) -> None:
        response = fixture_response(self.snapshot, "e1-1001", match_date="2026-09-18", markets=[self.exact_market])
        result = evaluate_fixture_response(
            response, self.snapshot, self.exact_truth,
            explicit_clues={"match_date": "2026-09-17"},
        )
        self.assertEqual(result.evaluation_status, "failed")
        self.assertTrue(any("date" in item for item in result.conflicts))

    def test_provider_snapshot_mismatch_is_rejected(self) -> None:
        response = fixture_response(
            self.snapshot, "e1-1001", snapshot_id="different-snapshot", markets=[self.exact_market],
        )
        result = evaluate_fixture_response(response, self.snapshot, self.exact_truth)
        self.assertEqual(result.evaluation_status, "failed")
        self.assertTrue(any("snapshot ID" in item for item in result.conflicts))

    def test_missing_provider_fixture_id_is_schema_invalid(self) -> None:
        response = fixture_response(self.snapshot, None, markets=[self.exact_market])
        with self.assertRaises(ValidationError):
            FixtureResolutionResponse.model_validate(response)
        result = evaluate_fixture_response(response, self.snapshot, self.exact_truth)
        self.assertFalse(result.schema_valid)
        self.assertEqual(result.evaluation_status, "failed")

    def test_invented_market_line_or_odds_is_rejected(self) -> None:
        invented = {**self.exact_market, "line": 6.5, "american_odds": 150}
        result = evaluate_fixture_response(
            fixture_response(self.snapshot, "e1-1001", markets=[invented]),
            self.snapshot,
            self.exact_truth,
        )
        self.assertEqual(result.evaluation_status, "failed")
        self.assertTrue(any("market" in item for item in result.unsupported_claims))

    def test_ambiguous_candidates_cannot_be_auto_resolved(self) -> None:
        candidates = [candidate(self.snapshot.fixtures[0]), candidate(self.snapshot.fixtures[1])]
        response = fixture_response(
            self.snapshot, "e1-1001", candidates=candidates, markets=[self.exact_market],
        )
        truth = GroundTruthFixture(
            fixture_id=None, competition_id="E1", competition_label="Championship",
            match_date="2026-09-17", home_team="Birmingham", away_team="",
            expected_status="needs_confirmation", expected_behavior="ask_confirmation",
            candidate_fixture_ids=["e1-1001", "e1-1002"],
        )
        result = evaluate_fixture_response(response, self.snapshot, truth)
        self.assertEqual(result.evaluation_status, "failed")
        self.assertTrue(any("ambiguous" in item for item in result.conflicts))

    def test_recorded_evaluation_report_is_reproducible(self) -> None:
        cases = load_recorded_cases(str(ROOT / "evaluation_cases.json"))
        responses: dict[str, dict[str, object]] = {
            "exact-provider-match": fixture_response(self.snapshot, "e1-1001", markets=[self.exact_market]),
            "multiple-candidates": fixture_response(
                self.snapshot, None, status="needs_confirmation",
                candidates=[candidate(self.snapshot.fixtures[0]), candidate(self.snapshot.fixtures[1])],
            ),
            "no-candidate": fixture_response(self.snapshot, None, status="unresolved"),
            "completed-fixture": fixture_response(self.snapshot, "e1-0999", status="unresolved", match_date="2026-05-02"),
            "postponed-fixture": fixture_response(self.snapshot, "e1-1003", status="unresolved", match_date="2026-09-20"),
            "unsupported-fixture": fixture_response(self.snapshot, None, status="unresolved"),
            "invented-fixture": fixture_response(self.snapshot, "e1-4041", markets=[self.exact_market]),
            "conflicting-date": fixture_response(self.snapshot, "e1-1001", match_date="2026-09-18"),
            "provider-snapshot-mismatch": fixture_response(self.snapshot, "e1-1001", snapshot_id="wrong"),
            "missing-provider-fixture-id": fixture_response(self.snapshot, None, markets=[self.exact_market]),
        }
        report = run_recorded_evaluation(self.snapshot, cases, responses)
        self.assertEqual(report.total_cases, 10)
        self.assertGreaterEqual(report.schema_validity, 0.8)
        self.assertEqual(report.hallucination_count, 2)
        self.assertIn("missing-provider-fixture-id", report.failed_cases)


if __name__ == "__main__":
    unittest.main()
