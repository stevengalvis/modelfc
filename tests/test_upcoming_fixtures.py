from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from modelfc.fixture_cache import load_fixture_snapshot, save_fixture_snapshot
from modelfc.upcoming_fixtures import (
    FixtureClues,
    FixtureResolutionStatus,
    FixtureSnapshot,
    FixtureStatus,
    RecordedFixtureProvider,
    resolve_fixture,
)


FIXTURE = Path(__file__).parent / "fixtures" / "fixture_resolution" / "provider_snapshot.json"


def load_recorded_snapshot() -> FixtureSnapshot:
    return FixtureSnapshot.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))


class UpcomingFixtureTests(unittest.TestCase):
    def test_recorded_snapshot_has_required_provenance(self) -> None:
        snapshot = load_recorded_snapshot()
        self.assertEqual(snapshot.provider_name, "recorded-schedule")
        self.assertEqual(snapshot.fixtures[0].provider_fixture_id, "e1-1001")
        self.assertEqual(snapshot.fixtures[0].competition_id, "E1")
        self.assertEqual(snapshot.fixtures[0].season, "2026/2027")
        self.assertEqual(snapshot.fixtures[0].kickoff_utc.tzinfo, timezone.utc)
        self.assertEqual(snapshot.fixtures[0].status, FixtureStatus.SCHEDULED)
        self.assertEqual(snapshot.provider_snapshot_id, "46461ded399ee172531b3b21dc86a3319229fcd81a3f57a1187ed4508da4a9e0")

    def test_resolves_exact_single_provider_candidate(self) -> None:
        result = resolve_fixture(
            load_recorded_snapshot(),
            FixtureClues(
                competition="Championship", match_date=date(2026, 9, 17),
                home_team="Birmingham", away_team="Millwall",
            ),
        )
        self.assertEqual(result.status, FixtureResolutionStatus.RESOLVED)
        self.assertEqual(result.fixture.provider_fixture_id, "e1-1001")
        self.assertEqual(result.provider_snapshot_id, load_recorded_snapshot().provider_snapshot_id)

    def test_aliases_are_exact_canonical_mappings_not_fuzzy_guesses(self) -> None:
        result = resolve_fixture(
            load_recorded_snapshot(),
            FixtureClues(competition="EFL Championship", team="Bham", team_side="home"),
            competition_aliases={"efl championship": "E1"},
            team_aliases={"bham": "Birmingham"},
        )
        self.assertEqual(result.status, FixtureResolutionStatus.NEEDS_CONFIRMATION)
        self.assertEqual(len(result.candidates), 2)

    def test_multiple_candidates_need_confirmation(self) -> None:
        result = resolve_fixture(
            load_recorded_snapshot(),
            FixtureClues(competition="E1", match_date=date(2026, 9, 17), home_team="Birmingham"),
        )
        self.assertEqual(result.status, FixtureResolutionStatus.NEEDS_CONFIRMATION)
        self.assertIsNone(result.fixture)
        self.assertEqual(len(result.candidates), 2)

    def test_no_provider_candidate_is_unresolved(self) -> None:
        result = resolve_fixture(
            load_recorded_snapshot(),
            FixtureClues(competition="E1", match_date=date(2026, 9, 18), home_team="Birmingham", away_team="Millwall"),
        )
        self.assertEqual(result.status, FixtureResolutionStatus.UNRESOLVED)
        self.assertEqual(result.candidates, ())

    def test_postponed_or_completed_fixture_cannot_resolve(self) -> None:
        for fixture_date, status_text in (
            (date(2026, 9, 20), "postponed"), (date(2026, 5, 2), "completed"),
        ):
            with self.subTest(status=status_text):
                result = resolve_fixture(
                    load_recorded_snapshot(),
                    FixtureClues(match_date=fixture_date, team="Millwall"),
                )
                self.assertEqual(result.status, FixtureResolutionStatus.UNRESOLVED)
                self.assertEqual(result.candidates[0].status.value, status_text)

    def test_invalid_clue_is_rejected(self) -> None:
        result = resolve_fixture(
            load_recorded_snapshot(),
            {"home_team": "Birmingham", "away_team": "Birmingham"},
        )
        self.assertEqual(result.status, FixtureResolutionStatus.INVALID)

    def test_snapshot_round_trips_through_cache(self) -> None:
        snapshot = load_recorded_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = save_fixture_snapshot(directory, snapshot)
            self.assertTrue(path.exists())
            self.assertEqual(load_fixture_snapshot(directory, snapshot.provider_snapshot_id), snapshot)
            self.assertEqual(save_fixture_snapshot(directory, snapshot), path)

    def test_recorded_provider_filters_without_network(self) -> None:
        provider = RecordedFixtureProvider(load_recorded_snapshot())
        filtered = provider.fetch_snapshot(competition_ids=["E1"], from_utc=datetime(2026, 9, 17, 0, tzinfo=timezone.utc), to_utc=datetime(2026, 9, 17, 23, 59, tzinfo=timezone.utc))
        self.assertEqual([item.provider_fixture_id for item in filtered.fixtures], ["e1-1001", "e1-1002"])


if __name__ == "__main__":
    unittest.main()
