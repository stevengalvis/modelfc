"""Offline E1 completeness proof from an independently reviewed results calendar.

No HTTP, history writes or model changes. The caller must root-authorize the exact
bundle hash: neither a hash nor a source label authenticates a downloaded feed.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.btts_model import history_from_bytes
from modelfc.providers.oddspapi import E1_TEAMS_BY_ID
from modelfc.providers.oddspapi_saved_response import _json

MAX_CALENDAR_BYTES = 2_000_000
MAX_CALENDAR_AGE = timedelta(hours=6)
MAX_HISTORY_AGE = timedelta(days=14)
RULE_VERSION = "e1-calendar-completeness-v1"
_NAMES = frozenset(canonical for _, canonical in E1_TEAMS_BY_ID.values())
_TERMINAL = {"FINISHED", "POSTPONED", "CANCELLED"}
_STATUSES = _TERMINAL | {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "SUSPENDED"}
_LONDON = ZoneInfo("Europe/London")


class CompletenessRejected(ValueError):
    """Fixed diagnostic only, never provider text or paths."""


def _require(condition, reason="HISTORY_CALENDAR_REJECTED"):
    if not condition:
        raise CompletenessRejected(reason)


def _time(value):
    _require(isinstance(value, str))
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


@dataclass(frozen=True)
class CalendarMatch:
    fixture_id: int
    day: date
    home: str
    away: str
    status: str
    goals: tuple[int, int] | None

    @property
    def key(self): return self.day, self.home, self.away


@dataclass(frozen=True)
class HistoryCalendar:
    bundle_sha256: str
    source_sha256: str
    retrieved_at: datetime
    season_year: int
    matches: tuple[CalendarMatch, ...]


def parse_calendar(raw: bytes, *, as_of: datetime) -> HistoryCalendar:
    """Full unfiltered football-data.org ELC season, not the OddsPapi schedule.

    A complete regular-season ordered-pair matrix prevents a truncated feed with
    self-consistent counts from claiming completeness. Playoffs are not supported.
    Source team IDs/names are bound explicitly by the reviewing operator, never
    inferred from another provider's IDs or fuzzy display-name matching.
    """
    try:
        _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_CALENDAR_BYTES)
        bundle = _json(raw)
        _require(isinstance(bundle, dict) and set(bundle) == {
            "version", "source", "retrieved_at", "team_bindings", "payload"})
        _require(type(bundle["version"]) is int and bundle["version"] == 1
                 and bundle["source"] == "football-data.org")
        retrieved = _time(bundle["retrieved_at"])
        _require(timedelta(0) <= utc(as_of)-retrieved <= MAX_CALENDAR_AGE,
                 "HISTORY_CALENDAR_STALE")
        _require(isinstance(bundle["payload"], str))
        source = bundle["payload"].encode("utf-8")
        value = _json(source)
        _require(isinstance(value, dict) and isinstance(value.get("filters"), dict))
        filters = value["filters"]
        _require(set(filters) <= {"season", "permission"} and "season" in filters)
        year = retrieved.year if retrieved.month >= 7 else retrieved.year-1
        _require(str(filters["season"]) == str(year) and not isinstance(filters["season"], bool))
        _require(value["competition"]["code"] == "ELC"
                 and value["competition"]["type"] == "LEAGUE"
                 and type(value["competition"]["id"]) is int and value["competition"]["id"] > 0)
        bindings = bundle["team_bindings"]
        _require(isinstance(bindings, list) and len(bindings) == len(_NAMES))
        teams, names, source_names = {}, set(), set()
        for row in bindings:
            _require(isinstance(row, dict) and set(row) == {"source_team_id", "source_name", "history_name"})
            identity, name, canonical = row["source_team_id"], row["source_name"], row["history_name"]
            _require(type(identity) is int and identity > 0 and identity not in teams
                     and isinstance(name, str) and name and name == name.strip() and name not in source_names
                     and canonical in _NAMES and canonical not in names)
            teams[identity] = (name, canonical); names.add(canonical); source_names.add(name)
        rows = value["matches"]
        _require(isinstance(rows, list) and len(rows) == len(_NAMES)*(len(_NAMES)-1),
                 "HISTORY_CALENDAR_INCOMPLETE")
        _require(type(value["resultSet"]["count"]) is int and value["resultSet"]["count"] == len(rows))
        matches, ids, pairs = [], set(), set()
        start, end = date(year, 7, 1), date(year+1, 7, 1)
        for row in rows:
            identity = row["id"]
            kickoff = _time(row["utcDate"])
            day = kickoff.astimezone(_LONDON).date()
            _require(type(identity) is int and identity > 0 and identity not in ids
                     and row["competition"]["code"] == "ELC"
                     and type(row["competition"]["id"]) is int
                     and row["competition"]["id"] == value["competition"]["id"]
                     and row["stage"] == "REGULAR_SEASON"
                     and start <= date.fromisoformat(row["season"]["startDate"]) <= day
                     < end and day <= date.fromisoformat(row["season"]["endDate"]))
            sides = []
            for side in ("homeTeam", "awayTeam"):
                team = row[side]
                _require(type(team["id"]) is int and team["id"] in teams
                         and teams[team["id"]][0] == team["name"])
                sides.append(teams[team["id"]][1])
            pair = tuple(sides)
            _require(pair[0] != pair[1] and pair not in pairs)
            status = row["status"]
            _require(status in _STATUSES)
            score = row["score"]["fullTime"]
            goals = (score["home"], score["away"])
            if status == "FINISHED":
                _require(kickoff < retrieved and all(type(n) is int and n >= 0 for n in goals))
            else:
                _require(goals == (None, None))
                goals = None
            ids.add(identity); pairs.add(pair)
            matches.append(CalendarMatch(identity, day, *sides, status, goals))
        _require(type(value["resultSet"]["played"]) is int and
                 value["resultSet"]["played"] == sum(m.status == "FINISHED" for m in matches))
        return HistoryCalendar(hashlib.sha256(raw).hexdigest(), hashlib.sha256(source).hexdigest(),
                               retrieved, year, tuple(sorted(matches, key=lambda m: (m.day, m.fixture_id))))
    except CompletenessRejected:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, OverflowError):
        raise CompletenessRejected("HISTORY_CALENDAR_REJECTED") from None


def verify_completeness(calendar: HistoryCalendar, sources, *, cutoff: date, as_of: datetime):
    """One-to-one ID -> local-date/ordered-teams -> installed completed result join.

    CSV has no provider fixture IDs. The unique composite join must therefore be
    exact and bidirectional; scores are checked too. Both models retain their
    existing covered-corner cohort. No scheduled kickoff implies a result.
    """
    _require(type(cutoff) is date and cutoff <= utc(as_of).date()
             and timedelta(0) <= utc(as_of)-calendar.retrieved_at <= MAX_CALENDAR_AGE,
             "HISTORY_CALENDAR_STALE")
    start = date(calendar.season_year, 7, 1)
    _require(start < cutoff <= calendar.retrieved_at.date()
             and cutoff <= max(m.day for m in calendar.matches), "HISTORY_CALENDAR_INCOMPLETE")
    prior = [m for m in calendar.matches if m.day < cutoff]
    _require(all(m.status in _TERMINAL for m in prior), "HISTORY_CALENDAR_UNRESOLVED")
    completed = {m.key: m for m in prior if m.status == "FINISHED"}
    _require(completed, "HISTORY_CALENDAR_INCOMPLETE")
    try:
        history = history_from_bytes("E1", sources)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise CompletenessRejected("HISTORY_RESULTS_REJECTED") from None
    installed = { (r.match_date, r.home_team, r.away_team): r for r in history.results
                  if start <= r.match_date < cutoff }
    _require(set(completed) == set(installed), "HISTORY_RESULTS_MISSING_OR_CONFLICTING")
    _require(all((installed[k].home_goals, installed[k].away_goals) == m.goals
                 for k, m in completed.items()), "HISTORY_RESULTS_CONFLICTING")
    return {"rule_version": RULE_VERSION, "mode": "CALENDAR_COMPLETE", "competition": "E1",
            "cutoff": cutoff.isoformat(), "calendar_sha256": calendar.bundle_sha256,
            "source_sha256": calendar.source_sha256,
            "completed_fixture_ids": sorted(m.fixture_id for m in completed.values()),
            "history_sources": [s.model_dump(mode="json") for s in history.sources]}


def assess_freshness(sources, *, latest: date, cutoff: date, as_of: datetime,
                     calendar: HistoryCalendar | None = None):
    """Absent proof retains the original gate; supplied proof must pass even when fresh."""
    _require(type(latest) is date and type(cutoff) is date and latest < cutoff, "STALE_HISTORY")
    if calendar is not None:
        return verify_completeness(calendar, sources, cutoff=cutoff, as_of=as_of)
    _require(timedelta(0) < utc(as_of).date()-latest <= MAX_HISTORY_AGE, "STALE_HISTORY")
    return {"rule_version": RULE_VERSION, "mode": "AGE_GATE", "competition": "E1",
            "cutoff": cutoff.isoformat()}
