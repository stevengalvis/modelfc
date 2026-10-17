"""Offline PL full-season validation. No live provider, cache writes or model gate."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import re
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.history_completeness import CalendarMatch, MAX_CALENDAR_BYTES, MAX_CALENDAR_AGE
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.upcoming_fixtures import FixtureSnapshot, ProviderFixture, FixtureStatus

_LONDON = ZoneInfo("Europe/London")
_TERMINAL = {"FINISHED", "POSTPONED", "CANCELLED"}
_ACTIVE = {"IN_PLAY", "PAUSED", "SUSPENDED"}
_STATUSES = _TERMINAL | {"SCHEDULED", "TIMED"} | _ACTIVE


class CalendarRejected(ValueError):
    """Fixed diagnostic, never source text."""


def _require(condition, reason="PL_CALENDAR_REJECTED"):
    if not condition:
        raise CalendarRejected(reason)


def _time(value):
    _require(isinstance(value, str) and len(value) <= 40)
    _require("T" in value)
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _contract(raw, pin, competition, year):
    _require(isinstance(raw, bytes) and 0 < len(raw) <= 20_000)
    _require(isinstance(pin, str) and re.fullmatch(r"[0-9a-f]{64}", pin)
             and hashlib.sha256(raw).hexdigest() == pin, "PL_CONTRACT_PIN_REJECTED")
    value = _json(raw)
    _require(isinstance(value, dict) and set(value) == {
        "version", "source", "competition", "source_competition_code",
        "source_competition_id", "season_year", "team_bindings"})
    _require(type(value["version"]) is int and value["version"] == 1
             and value["source"] == "football-data.org"
             and competition == value["competition"] == "E0"
             and value["source_competition_code"] == "PL"
             and type(value["source_competition_id"]) is int
             and 0 < value["source_competition_id"] < 2**63
             and type(year) is int and 2020 <= year <= 2100
             and type(value["season_year"]) is int and value["season_year"] == year)
    bindings = value["team_bindings"]
    _require(isinstance(bindings, list) and len(bindings) == 20)
    for row in bindings:
        _require(isinstance(row, dict) and set(row) == {
            "source_team_id", "source_name", "history_name"})
        _require(type(row["source_team_id"]) is int and 0 < row["source_team_id"] < 2**63)
        for key in ("source_name", "history_name"):
            name = row[key]
            _require(isinstance(name, str) and 0 < len(name) <= 80
                     and name == name.strip() and all(c.isprintable() for c in name))
    for key in ("source_team_id", "source_name", "history_name"):
        _require(len({row[key] for row in bindings}) == 20)
    _require(len({row["history_name"].casefold() for row in bindings}) == 20)
    return value


@dataclass(frozen=True)
class SavedPLCalendar:
    bundle_sha256: str
    source_sha256: str
    retrieved_at: datetime
    season_year: int
    matches: tuple[CalendarMatch, ...]
    raw_bundle: bytes = field(repr=False)
    raw_payload: bytes = field(repr=False)
    contract_sha256: str

    def exact_fixture(self, *, fixture_id, kickoff, home, away):
        """Names are reviewed history bindings; ID belongs to football-data.org only."""
        matches = [m for m in self.matches if
                   (m.fixture_id, m.kickoff, m.home, m.away) ==
                   (fixture_id, utc(kickoff), home, away)]
        _require(type(fixture_id) is int and len(matches) == 1, "PL_FIXTURE_IDENTITY_REJECTED")
        return matches[0]

    def snapshot(self):
        """Only losslessly representable statuses; live/paused/suspended fail closed."""
        statuses = {"TIMED": FixtureStatus.SCHEDULED, "SCHEDULED": FixtureStatus.SCHEDULED,
                    "FINISHED": FixtureStatus.COMPLETED, "POSTPONED": FixtureStatus.POSTPONED,
                    "CANCELLED": FixtureStatus.CANCELLED}
        _require(all(m.status in statuses for m in self.matches), "PL_SNAPSHOT_STATUS_UNSUPPORTED")
        return FixtureSnapshot.create(provider_name="football-data.org",
            provider_request_id="saved-pl-" + self.bundle_sha256,
            retrieved_at=self.retrieved_at, provider_payload_hash=self.source_sha256,
            fixtures=tuple(ProviderFixture(str(m.fixture_id), "E0", "Premier League",
                str(self.season_year), m.kickoff, m.home, m.away, statuses[m.status],
                "football-data.org", self.retrieved_at) for m in self.matches))


def parse_calendar(raw: bytes, *, contract_raw: bytes, reviewed_contract_sha256: str,
                   competition: str, season_year: int) -> SavedPLCalendar:
    """Validate saved football-data.org PL bytes against a separately reviewed contract.

    Structural validation is independent of age. No network or live registration.
    A caller-supplied pin records review intent; it does not authenticate a source.
    """
    try:
        _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_CALENDAR_BYTES)
        contract = _contract(contract_raw, reviewed_contract_sha256, competition, season_year)
        bundle = _json(raw)
        _require(isinstance(bundle, dict) and set(bundle) == {
            "version", "source", "retrieved_at", "payload"})
        _require(type(bundle["version"]) is int and bundle["version"] == 1
                 and bundle["source"] == "football-data.org")
        retrieved = _time(bundle["retrieved_at"])
        _require(isinstance(bundle["payload"], str))
        source = bundle["payload"].encode("utf-8")
        value = _json(source)
        _require(isinstance(value, dict) and isinstance(value.get("filters"), dict))
        filters = value["filters"]
        _require(set(filters) <= {"season", "permission"} and "season" in filters)
        year = season_year
        _require(str(filters["season"]) == str(year) and not isinstance(filters["season"], bool))
        _require(value["competition"]["code"] == "PL"
                 and value["competition"]["type"] == "LEAGUE"
                 and type(value["competition"]["id"]) is int
                 and value["competition"]["id"] == contract["source_competition_id"])
        bindings = contract["team_bindings"]
        _NAMES = frozenset(row["history_name"] for row in bindings)
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
                 "PL_CALENDAR_INCOMPLETE")
        _require(type(value["resultSet"]["count"]) is int and value["resultSet"]["count"] == len(rows))
        matches, ids, pairs, season_bounds = [], set(), set(), set()
        start, end = date(year, 7, 1), date(year+1, 7, 1)
        for row in rows:
            identity = row["id"]
            kickoff = _time(row["utcDate"])
            day = kickoff.astimezone(_LONDON).date()
            _require(type(identity) is int and 0 < identity < 2**63 and identity not in ids
                     and row["competition"]["code"] == "PL"
                     and type(row["competition"]["id"]) is int
                     and row["competition"]["id"] == value["competition"]["id"]
                     and row["stage"] == "REGULAR_SEASON"
                     and start <= date.fromisoformat(row["season"]["startDate"]) <= day
                     < end and day <= date.fromisoformat(row["season"]["endDate"]))
            season_start = date.fromisoformat(row["season"]["startDate"])
            season_end = date.fromisoformat(row["season"]["endDate"])
            _require(start <= season_start <= season_end < end)
            season_bounds.add((season_start, season_end))
            _require(len(season_bounds) == 1)
            sides = []
            for side in ("homeTeam", "awayTeam"):
                team = row[side]
                _require(type(team["id"]) is int and team["id"] in teams
                         and teams[team["id"]][0] == team["name"])
                sides.append(teams[team["id"]][1])
            pair = tuple(sides)
            _require(pair[0] != pair[1] and pair not in pairs)
            matchday = row["matchday"]
            _require(type(matchday) is int and 1 <= matchday <= 38)
            status = row["status"]
            _require(status in _STATUSES)
            if status in _ACTIVE:
                _require(kickoff <= retrieved)
            score = row["score"]["fullTime"]
            goals = (score["home"], score["away"])
            if status == "FINISHED":
                _require(kickoff < retrieved and all(type(n) is int and n >= 0 for n in goals))
            else:
                _require(goals == (None, None))
                goals = None
            ids.add(identity); pairs.add(pair)
            matches.append(CalendarMatch(identity, kickoff, day, matchday, *sides, status, goals))
        # Each round is a complete partition of the verified season roster.
        # Range checks alone allow arbitrary relabelling to hide prior results.
        for matchday in range(1, 39):
            members = [m for m in matches if m.matchday == matchday]
            participants = [team for m in members for team in (m.home, m.away)]
            _require(len(members) == len(_NAMES)//2 and set(participants) == _NAMES
                     and len(participants) == len(set(participants)),
                     "PL_CALENDAR_MATCHDAY_REJECTED")
        _require(type(value["resultSet"]["played"]) is int and
                 value["resultSet"]["played"] == sum(m.status == "FINISHED" for m in matches))
        return SavedPLCalendar(hashlib.sha256(raw).hexdigest(), hashlib.sha256(source).hexdigest(),
                               retrieved, year, tuple(sorted(matches, key=lambda m: (m.kickoff, m.fixture_id))),
                               raw, source, reviewed_contract_sha256)
    except CalendarRejected:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, OverflowError):
        raise CalendarRejected("PL_CALENDAR_REJECTED") from None
