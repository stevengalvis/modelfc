from __future__ import annotations

from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from modelfc.evaluation_records import (
    ActualOutcomeReference,
    AnalysisRecord,
    CalibrationMetricRecord,
    EvaluationHistoryRecord,
    FixtureRecord,
    InputSnapshotRef,
    ModelComparisonRecord,
    PredictionRecord,
)


UTC = timezone.utc
KICKOFF = datetime(2026, 9, 17, 18, 45, tzinfo=UTC)
PREDICTED = datetime(2026, 9, 16, 12, tzinfo=UTC)


def input_snapshot(**overrides):
    value = {
        "source_name": "football-data-history",
        "snapshot_hash": "history-sha256",
        "snapshot_version": "history-2026-09-16",
        "captured_at_utc": datetime(2026, 9, 16, 11, tzinfo=UTC),
        "data_as_of_utc": datetime(2026, 9, 15, 23, 59, tzinfo=UTC),
    }
    value.update(overrides)
    return InputSnapshotRef(**value)


class EvaluationRecordTests(unittest.TestCase):
    def test_fixture_and_prediction_preserve_longitudinal_metadata(self) -> None:
        fixture = FixtureRecord(
            fixture_id="e1-1001",
            provider_name="recorded-schedule",
            provider_snapshot_id="schedule-snapshot",
            competition="Championship",
            competition_id="E1",
            season="2026/2027",
            kickoff_timestamp_utc=KICKOFF,
            home_team="Birmingham",
            away_team="Millwall",
            status="scheduled",
            historical_data_snapshot_hash="history-sha256",
            data_snapshot_version="history-2026-09-16",
            recorded_at_utc=PREDICTED,
        )
        prediction = PredictionRecord(
            prediction_id="prediction-1",
            analysis_id="analysis-1",
            fixture_id=fixture.fixture_id,
            provider_snapshot_id=fixture.provider_snapshot_id,
            competition=fixture.competition,
            competition_id=fixture.competition_id,
            season=fixture.season,
            historical_data_snapshot_hash=fixture.historical_data_snapshot_hash,
            data_snapshot_version="history-2026-09-16",
            feature_set_version="corners-v1",
            model_name="venue-opponent-negative-binomial",
            model_version="2026-09-16",
            prediction_version="prediction-schema-v1",
            prediction_timestamp_utc=PREDICTED,
            kickoff_timestamp_utc=fixture.kickoff_timestamp_utc,
            input_data_as_of_utc=datetime(2026, 9, 15, 23, 59, tzinfo=UTC),
            input_snapshots=(input_snapshot(),),
            market_type="team_total_corners",
            team="Birmingham",
            team_side="home",
            direction="over",
            line=4.5,
            american_odds=-110,
            predicted_probabilities={"over": 0.57, "under": 0.43},
        )
        dumped = prediction.model_dump(mode="json")
        self.assertEqual(dumped["fixture_id"], "e1-1001")
        self.assertEqual(dumped["competition"], "Championship")
        self.assertEqual(dumped["competition_id"], "E1")
        self.assertEqual(dumped["season"], "2026/2027")
        self.assertEqual(dumped["provider_snapshot_id"], "schedule-snapshot")
        self.assertEqual(dumped["historical_data_snapshot_hash"], "history-sha256")
        self.assertEqual(dumped["data_snapshot_version"], "history-2026-09-16")
        self.assertEqual(dumped["feature_set_version"], "corners-v1")
        self.assertEqual(dumped["prediction_version"], "prediction-schema-v1")
        self.assertEqual(dumped["settlement_status"], "open")

    def test_analysis_is_pre_kickoff_and_timestamped(self) -> None:
        record = AnalysisRecord(
            analysis_id="analysis-1",
            fixture_id="e1-1001",
            provider_snapshot_id="schedule-snapshot",
            competition="Championship",
            competition_id="E1",
            season="2026/2027",
            historical_data_snapshot_hash="history-sha256",
            data_snapshot_version="history-2026-09-16",
            feature_set_version="corners-v1",
            model_name="venue-opponent-negative-binomial",
            model_version="2026-09-16",
            analysis_timestamp_utc=PREDICTED,
            kickoff_timestamp_utc=KICKOFF,
            input_data_as_of_utc=datetime(2026, 9, 15, 23, 59, tzinfo=UTC),
            prediction_ids=("prediction-1",),
        )
        self.assertEqual(record.prediction_ids, ("prediction-1",))

    def test_future_or_post_match_input_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PredictionRecord(
                prediction_id="prediction-leak",
                analysis_id="analysis-1",
                fixture_id="e1-1001",
                provider_snapshot_id="schedule-snapshot",
                competition="Championship",
                competition_id="E1",
                season="2026/2027",
                historical_data_snapshot_hash="history-sha256",
                data_snapshot_version="history-2026-09-16",
                feature_set_version="corners-v1",
                model_name="venue-opponent-negative-binomial",
                model_version="2026-09-16",
                prediction_version="prediction-schema-v1",
                prediction_timestamp_utc=PREDICTED,
                kickoff_timestamp_utc=KICKOFF,
                input_data_as_of_utc=datetime(2026, 9, 17, 20, tzinfo=UTC),
                input_snapshots=(input_snapshot(data_as_of_utc=datetime(2026, 9, 17, 20, tzinfo=UTC)),),
                market_type="match_total_corners",
                direction="over",
                line=9.5,
                american_odds=-110,
                predicted_probabilities={"over": 0.5, "under": 0.5},
            )

        with self.assertRaises(ValidationError):
            PredictionRecord(
                prediction_id="prediction-after-kickoff",
                analysis_id="analysis-1",
                fixture_id="e1-1001",
                provider_snapshot_id="schedule-snapshot",
                competition="Championship",
                competition_id="E1",
                season="2026/2027",
                historical_data_snapshot_hash="history-sha256",
                data_snapshot_version="history-2026-09-16",
                feature_set_version="corners-v1",
                model_name="venue-opponent-negative-binomial",
                model_version="2026-09-16",
                prediction_version="prediction-schema-v1",
                prediction_timestamp_utc=datetime(2026, 9, 17, 19, tzinfo=UTC),
                kickoff_timestamp_utc=KICKOFF,
                input_data_as_of_utc=PREDICTED,
                input_snapshots=(input_snapshot(),),
                market_type="match_total_corners",
                direction="under",
                line=10.5,
                american_odds=110,
                predicted_probabilities={"over": 0.4, "under": 0.6},
            )

    def test_outcome_evaluation_calibration_and_comparison_are_extension_points(self) -> None:
        outcome = ActualOutcomeReference(
            outcome_id="outcome-1",
            fixture_id="e1-1001",
            provider_snapshot_id="result-snapshot",
            source_data_hash="results-sha256",
            outcome_source_version="football-data-result-v1",
            retrieved_at_utc=datetime(2026, 9, 18, 8, tzinfo=UTC),
            completed_at_utc=datetime(2026, 9, 17, 20, 45, tzinfo=UTC),
            home_corners=5,
            away_corners=4,
            status="completed",
        )
        evaluation = EvaluationHistoryRecord(
            evaluation_id="evaluation-1",
            prediction_id="prediction-1",
            fixture_id=outcome.fixture_id,
            actual_outcome_reference=outcome.outcome_id,
            evaluated_at_utc=datetime(2026, 9, 18, 9, tzinfo=UTC),
            evaluation_run_version="evaluation-run-v1",
            scoring_version="settlement-v1",
            settlement_status="win",
            metrics={"profit": 0.91, "brier": 0.18},
        )
        calibration = CalibrationMetricRecord(
            metric_id="calibration-1",
            dataset_id="recorded-2026-09",
            model_name="venue-opponent-negative-binomial",
            model_version="2026-09-16",
            feature_set_version="corners-v1",
            evaluation_run_version="evaluation-run-v1",
            market_type="team_total_corners",
            scored_from_utc=datetime(2026, 8, 1, tzinfo=UTC),
            scored_to_utc=datetime(2026, 9, 15, tzinfo=UTC),
            sample_count=100,
            brier_score=0.19,
            log_loss=0.57,
        )
        comparison = ModelComparisonRecord(
            comparison_id="comparison-1",
            dataset_id="recorded-2026-09",
            fixture_ids=("e1-1001",),
            model_names=("venue-opponent-negative-binomial", "league-average"),
            feature_set_versions=("corners-v1",),
            evaluation_run_version="evaluation-run-v1",
            compared_at_utc=datetime(2026, 9, 18, 10, tzinfo=UTC),
            metrics_by_model={"venue-opponent-negative-binomial": {"mae": 2.1}},
        )
        self.assertEqual(outcome.home_corners, 5)
        self.assertEqual(evaluation.settlement_status, "win")
        self.assertEqual(calibration.sample_count, 100)
        self.assertIn("league-average", comparison.model_names)


if __name__ == "__main__":
    unittest.main()
