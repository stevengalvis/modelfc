from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from modelfc.btts_model import history_from_bytes
from modelfc.history_completeness import CalendarMatch, HistoryCalendar, verify_completeness
from modelfc.supplemental_history import (augment_sources, make_result, publish_result,
                                          load_results)
from modelfc.ledger_storage import LedgerError


NOW = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)
TEAMS = tuple(f"Team {index:02d}" for index in range(24))


def matches():
    result = []
    for index in range(106):
        home = TEAMS[index % 24]
        away = TEAMS[(index * 5 + 1) % 24]
        if home == away:
            away = TEAMS[(index + 1) % 24]
        day = date(2026, 8, 1) + timedelta(days=index // 12)
        result.append(CalendarMatch(10_000 + index,
            datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=12),
            day, index // 12 + 1, home, away, "FINISHED", (index % 4, index % 3)))
    return tuple(result)


def result(match):
    return make_result(competition="E1", provider="sportmonks",
        provider_fixture_id=match.fixture_id + 1_000_000, provider_league_id=9,
        provider_season_id=27903, kickoff_utc=match.kickoff.isoformat(),
        match_date=match.day, home_provider_team_id=match.fixture_id + 1,
        away_provider_team_id=match.fixture_id + 2, home_team=match.home,
        away_team=match.away, home_goals=match.goals[0], away_goals=match.goals[1],
        home_corners=4 + match.fixture_id % 3, away_corners=2 + match.fixture_id % 4,
        retrieved_at_utc=NOW.isoformat(), response_sha256="a" * 64)


class SupplementalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        prior = matches()
        current = CalendarMatch(99_999, datetime(2026, 10, 13, 18, 45, tzinfo=timezone.utc),
            date(2026, 10, 13), 10, TEAMS[0], TEAMS[3], "TIMED", None)
        self.calendar = HistoryCalendar("b"*64, "c"*64, NOW, 2026, prior + (current,))
        header = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
        rows = []
        for match in prior[:95]:
            ftr = "H" if match.goals[0] > match.goals[1] else "A" if match.goals[1] > match.goals[0] else "D"
            corners = result(match)
            rows.append(f"E1,{match.day:%d/%m/%Y},{match.home},{match.away},{match.goals[0]},{match.goals[1]},{ftr},{corners.home_corners},{corners.away_corners}\n")
        self.sources = (("E1_2627.csv", (header + "".join(rows)).encode()),)
        self.extra = tuple(result(match) for match in prior[95:])

    def test_95_plus_eleven_is_deterministic_complete_106(self):
        first = augment_sources(self.sources, self.extra, self.calendar)
        second = augment_sources(self.sources, reversed(self.extra), self.calendar)
        self.assertEqual(first, second)
        history = history_from_bytes("E1", first)
        self.assertEqual(len(history.results), 106)
        proof = verify_completeness(self.calendar, first, cutoff=date(2026, 10, 13), as_of=NOW)
        self.assertEqual(len(proof["completed_fixture_ids"]), 106)

    def test_missing_prior_fixture_and_current_week_exclusion(self):
        with self.assertRaisesRegex(ValueError, "MISSING"):
            verify_completeness(self.calendar, augment_sources(self.sources, self.extra[:-1], self.calendar),
                                cutoff=date(2026, 10, 13), as_of=NOW)
        current = CalendarMatch(99_999, datetime(2026, 10, 13, 18, 45, tzinfo=timezone.utc),
            date(2026, 10, 13), 10, TEAMS[0], TEAMS[3], "FINISHED", (1, 1))
        extended = HistoryCalendar("b"*64, "c"*64, NOW, 2026,
                                   self.calendar.matches[:-1] + (current,))
        proof = verify_completeness(extended, augment_sources(self.sources, self.extra, extended),
                                    cutoff=date(2026, 10, 13), as_of=NOW)
        self.assertNotIn(current.fixture_id, proof["completed_fixture_ids"])

    def test_csv_conflict_and_calendar_conflict_fail_closed(self):
        exact = self.extra[0]
        raw = self.sources[0][1] + (f"E1,{exact.match_date:%d/%m/%Y},{exact.home_team},{exact.away_team},"
            f"{exact.home_goals},{exact.away_goals},D,99,{exact.away_corners}\n").encode()
        with self.assertRaisesRegex(ValueError, "SUPPLEMENTAL_CSV_CONFLICT"):
            augment_sources((("E1_2627.csv", raw),), self.extra, self.calendar)
        changed = HistoryCalendar("b"*64, "c"*64, NOW, 2026,
            self.calendar.matches[:-2] + (CalendarMatch(**{**self.calendar.matches[-2].__dict__,
                "goals": (9, 9)}), self.calendar.matches[-1]))
        with self.assertRaisesRegex(ValueError, "SUPPLEMENTAL_CALENDAR_CONFLICT"):
            augment_sources(self.sources, self.extra, changed)

    def test_immutable_publication_and_conflict(self):
        self.assertTrue(publish_result(self.state, self.extra[0]))
        self.assertFalse(publish_result(self.state, self.extra[0]))
        self.assertEqual(load_results(self.state), (self.extra[0],))
        path = next((self.state/"supplemental-history"/"E1").glob("*.json"))
        value = json.loads(path.read_text()); value["home_corners"] += 1
        path.write_text(json.dumps(value))
        with self.assertRaises(LedgerError):
            load_results(self.state)


if __name__ == "__main__": unittest.main()
