"""Pure T-24h planning regressions; no acquisition or state."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from modelfc.acquisition_planner import (
    LEAGUES, Fixture, Obligation, PlanningError, configured_leagues, plan_acquisition,
)

NOW = datetime(2026, 10, 9, 19, 5, tzinfo=timezone.utc)


def fixture(code="E1", hours=24, fid="fixture-1"):
    league = next(row for row in LEAGUES if row.competition == code)
    return Fixture(code, league.tournament_id, fid, NOW + timedelta(hours=hours))


class PlannerTests(unittest.TestCase):
    def test_fixed_registry_and_independent_flags(self):
        self.assertEqual([row.tournament_id for row in LEAGUES], [18, 17, 8, 23, 242])
        self.assertEqual([(row.name, row.country, row.slug) for row in LEAGUES[-2:]],
                         [("Serie A", "Italy", "serie-a"), ("MLS", "USA", "mls")])
        leagues = configured_leagues(["E1", "MLS"])
        self.assertEqual([row.competition for row in leagues if row.enabled], ["E1", "MLS"])
        self.assertTrue(all(row.enabled for row in LEAGUES))
        with self.assertRaises(PlanningError):
            configured_leagues(["BRAZIL"])

    def test_window_inclusive_and_no_daily_polling(self):
        fixtures = [fixture(hours=h, fid=str(h)) for h in (20, 21, 24, 27, 28)]
        plan = plan_acquisition({"E1": fixtures}, as_of=NOW)
        self.assertEqual([o.fixture.fixture_id for o in plan.batches[0].obligations], ["21", "24", "27"])
        self.assertEqual(plan.batches[0].bookmaker, "fanduel")
        self.assertEqual(plan_acquisition({"E1": []}, as_of=NOW).batches, ())

    def test_five_league_batch_and_determinism(self):
        calendar = {row.competition: [fixture(row.competition, fid=row.competition)] for row in LEAGUES}
        before = repr(calendar)
        first = plan_acquisition(calendar, as_of=NOW)
        second = plan_acquisition(dict(reversed(list(calendar.items()))), as_of=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first.batches[0].tournament_ids, (18, 17, 8, 23, 242))
        self.assertLessEqual(len(first.batches[0].tournament_ids), 5)
        self.assertEqual(repr(calendar), before)

    def test_duplicate_completed_obligations_and_rescheduling(self):
        f = fixture()
        done = Obligation(f)
        plan = plan_acquisition({"E1": [f, f]}, as_of=NOW, completed=[done, done])
        self.assertEqual(plan.batches, ())
        self.assertEqual(plan.coverage[0].status, "ALREADY_COMPLETED")
        rescheduled = replace(f, kickoff_utc=f.kickoff_utc + timedelta(hours=1))
        self.assertEqual(len(plan_acquisition({"E1": [rescheduled]}, as_of=NOW,
                                             completed=[done]).batches), 1)

    def test_missing_calendar_distinct_from_known_empty_and_outside_window(self):
        plan = plan_acquisition({"E1": [], "E0": [fixture("E0", hours=40)], "SP1": None}, as_of=NOW)
        self.assertEqual([row.status for row in plan.coverage],
                         ["NO_ELIGIBLE_FIXTURES", "NO_ELIGIBLE_FIXTURES", "MISSING_CALENDAR",
                          "MISSING_CALENDAR", "MISSING_CALENDAR"])

    def test_disabled_league_never_planned(self):
        plan = plan_acquisition({"MLS": [fixture("MLS")]}, as_of=NOW,
                                leagues=configured_leagues(["E1"]))
        self.assertEqual(plan.batches, ())
        self.assertEqual(plan.coverage[-1].status, "DISABLED")

    def test_mls_offset_and_utc_date_boundary(self):
        kickoff = datetime(2026, 10, 10, 20, 5, tzinfo=timezone(timedelta(hours=-7)))
        f = Fixture("MLS", 242, "mls-1", kickoff)
        self.assertEqual(f.kickoff_utc.day, 11)
        plan = plan_acquisition({"MLS": [f]}, as_of=f.kickoff_utc - timedelta(hours=24))
        self.assertEqual(plan.batches[0].tournament_ids, (242,))

    def test_invalid_identity_calendar_and_timestamps(self):
        for code, tid in (("I1", 325), ("MLS", 243), ("XXX", 18), ("E1", 18.0)):
            with self.subTest(code=code, tid=tid), self.assertRaises(PlanningError):
                Fixture(code, tid, "f", NOW)
        with self.assertRaises(PlanningError):
            plan_acquisition({}, as_of=NOW.replace(tzinfo=None))
        with self.assertRaises(PlanningError):
            plan_acquisition({"XXX": []}, as_of=NOW)
        with self.assertRaises(PlanningError):
            plan_acquisition({"E1": [fixture("MLS")]}, as_of=NOW)
        with self.assertRaises(PlanningError):
            plan_acquisition({"E1": [fixture(), fixture(hours=25)]}, as_of=NOW)
        with self.assertRaises(PlanningError):
            plan_acquisition({}, as_of=NOW, leagues=[replace(LEAGUES[0], tournament_id=325), *LEAGUES[1:]])
        with self.assertRaises(PlanningError):
            Obligation(fixture(), bookmaker="draftkings")
        with self.assertRaises(PlanningError):
            plan_acquisition({}, as_of=NOW, completed=["not-a-completion"])
