"""Bounded Sportmonks E1 recent-result adapter.

Only completed Championship fixtures and full-match home/away corners are
normalized.  It has no model, odds, discovery, fallback or retry behavior.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import math
from types import MappingProxyType
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from modelfc.supplemental_history import SupplementalResult, make_result
from modelfc.providers.oddspapi_saved_response import _json


HOST = "api.sportmonks.com"
BASE = f"https://{HOST}/v3/football"
LEAGUE_ID = 9
CORNER_TYPE_ID = 34
MAX_DAYS = 31
MAX_PAGES = 4
MAX_REQUESTS = 4
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
USER_AGENT = "ModelFC/1.0 (recent E1 result overlay)"


class SportmonksRejected(ValueError):
    """Fixed diagnostic boundary. Provider bodies and URLs never escape."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class TeamBinding:
    provider_team_id: int
    provider_name: str
    history_name: str


def validate_bindings(values) -> MappingProxyType:
    if not isinstance(values, (list, tuple)) or len(values) != 24:
        raise SportmonksRejected("SPORTMONKS_BINDINGS_REJECTED")
    result, source_names, history_names = {}, set(), set()
    for raw in values:
        try:
            row = raw if isinstance(raw, TeamBinding) else TeamBinding(**raw)
        except (TypeError, ValueError):
            raise SportmonksRejected("SPORTMONKS_BINDINGS_REJECTED") from None
        if (type(row.provider_team_id) is not int or row.provider_team_id <= 0
                or row.provider_team_id in result
                or not all(isinstance(name, str) and name and name == name.strip()
                           for name in (row.provider_name, row.history_name))
                or row.provider_name in source_names or row.history_name in history_names):
            raise SportmonksRejected("SPORTMONKS_BINDINGS_REJECTED")
        result[row.provider_team_id] = row
        source_names.add(row.provider_name); history_names.add(row.history_name)
    return MappingProxyType(result)


def _time(value) -> datetime:
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED") from None


def _whole(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
    number = float(value)
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
    return int(number)


def _location_participants(row, bindings):
    participants = row.get("participants")
    if not isinstance(participants, list) or len(participants) != 2:
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
    sides = {}
    for participant in participants:
        try:
            identity = participant["id"]
            name = participant["name"]
            location = participant["meta"]["location"]
            binding = bindings[identity]
        except (KeyError, TypeError):
            raise SportmonksRejected("SPORTMONKS_IDENTITY_REJECTED") from None
        if (location not in ("home", "away") or location in sides
                or binding.provider_name != name):
            raise SportmonksRejected("SPORTMONKS_IDENTITY_REJECTED")
        sides[location] = binding
    return sides


def _scores(row, sides):
    values = {"home": [], "away": []}
    scores = row.get("scores")
    if not isinstance(scores, list):
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
    by_id = {binding.provider_team_id: location for location, binding in sides.items()}
    for score in scores:
        if score.get("description") != "CURRENT":
            continue
        try:
            location = by_id[score["participant_id"]]
            values[location].append(_whole(score["score"]["goals"]))
        except (KeyError, TypeError):
            raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED") from None
    if any(len(values[side]) != 1 for side in values):
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
    return values["home"][0], values["away"][0]


def _corners(row, sides):
    values = {"home": [], "away": []}
    statistics = row.get("statistics")
    if not isinstance(statistics, list):
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
    by_id = {binding.provider_team_id: location for location, binding in sides.items()}
    for statistic in statistics:
        if statistic.get("type_id") != CORNER_TYPE_ID:
            continue
        try:
            location = statistic["location"]
            if by_id[statistic["participant_id"]] != location:
                raise KeyError
            values[location].append(_whole(statistic["data"]["value"]))
        except (KeyError, TypeError):
            raise SportmonksRejected("SPORTMONKS_CORNER_REJECTED") from None
    if any(len(values[side]) != 1 for side in values):
        raise SportmonksRejected("SPORTMONKS_CORNER_REJECTED")
    return values["home"][0], values["away"][0]


def normalize_page(raw: bytes, *, retrieved_at: datetime, bindings,
                   expected_season_id: int) -> tuple[SupplementalResult, ...]:
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_PAGE_BYTES:
            raise SportmonksRejected("SPORTMONKS_RESPONSE_SIZE_REJECTED")
        value = _json(raw)
        rows = value["data"]
        if not isinstance(value, dict) or not isinstance(rows, list) or len(rows) > 200:
            raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
        binding_map = validate_bindings(bindings)
        response_hash = hashlib.sha256(raw).hexdigest()
        records, ids = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
            fixture_id = row.get("id")
            if type(fixture_id) is not int or fixture_id <= 0 or fixture_id in ids:
                raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
            ids.add(fixture_id)
            if row.get("league_id") != LEAGUE_ID or row.get("season_id") != expected_season_id:
                raise SportmonksRejected("SPORTMONKS_COMPETITION_REJECTED")
            state = row.get("state")
            if not isinstance(state, dict) or state.get("short_name") != "FT":
                continue
            kickoff = _time(row.get("starting_at"))
            timestamp = row.get("starting_at_timestamp")
            if type(timestamp) is not int or _time(timestamp) != kickoff:
                raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
            sides = _location_participants(row, binding_map)
            goals = _scores(row, sides)
            corners = _corners(row, sides)
            records.append(make_result(
                competition="E1", provider="sportmonks",
                provider_fixture_id=fixture_id, provider_league_id=LEAGUE_ID,
                provider_season_id=expected_season_id, kickoff_utc=kickoff.isoformat(),
                match_date=kickoff.astimezone(ZoneInfo("Europe/London")).date(),
                home_provider_team_id=sides["home"].provider_team_id,
                away_provider_team_id=sides["away"].provider_team_id,
                home_team=sides["home"].history_name,
                away_team=sides["away"].history_name,
                home_goals=goals[0], away_goals=goals[1],
                home_corners=corners[0], away_corners=corners[1],
                retrieved_at_utc=retrieved_at.astimezone(timezone.utc).isoformat(),
                response_sha256=response_hash,
            ))
        return tuple(records)
    except SportmonksRejected:
        raise
    except (KeyError, TypeError, ValueError):
        raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED") from None


@dataclass(frozen=True)
class FetchResult:
    records: tuple[SupplementalResult, ...]
    requests: int
    response_hashes: tuple[str, ...]
    retrieved_at: datetime


class SportmonksRecentResultsClient:
    def __init__(self, token: str, *, opener=None, clock=None, on_request=None):
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(character.isspace() for character in token)):
            raise SportmonksRejected("SPORTMONKS_CREDENTIAL_REJECTED")
        self._token = token
        self._opener = opener or build_opener(_NoRedirect())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_request = on_request or (lambda: None)

    def fetch(self, start: date, end: date, *, bindings, season_id: int) -> FetchResult:
        if (type(start) is not date or type(end) is not date or start > end
                or (end-start).days + 1 > MAX_DAYS or type(season_id) is not int or season_id <= 0):
            raise SportmonksRejected("SPORTMONKS_RANGE_REJECTED")
        page_number = 1
        pages, hashes, requests, total = [], [], 0, 0
        while True:
            if requests >= MAX_REQUESTS:
                raise SportmonksRejected("SPORTMONKS_PAGINATION_REJECTED")
            params = {"include": "participants;scores;state;statistics.type;metadata",
                      "filters": f"fixtureLeagues:{LEAGUE_ID}", "timezone": "UTC", "per_page": "50"}
            if page_number > 1:
                params["page"] = str(page_number)
            endpoint = f"{BASE}/fixtures/between/{start.isoformat()}/{end.isoformat()}?{urlencode(params)}"
            request = Request(endpoint, headers={"Authorization": self._token,
                                                  "User-Agent": USER_AGENT}, method="GET")
            self._on_request()
            try:
                with self._opener.open(request, timeout=30) as response:
                    if response.getcode() != 200:
                        raise SportmonksRejected("SPORTMONKS_HTTP_REJECTED")
                    raw = response.read(MAX_PAGE_BYTES + 1)
            except HTTPError as error:
                raise SportmonksRejected(f"SPORTMONKS_HTTP_{error.code}" if error.code in
                    (400, 401, 403, 404, 408, 429, 500, 502, 503, 504) else
                    "SPORTMONKS_HTTP_REJECTED") from None
            except (URLError, TimeoutError, OSError):
                raise SportmonksRejected("SPORTMONKS_TRANSPORT_REJECTED") from None
            requests += 1; total += len(raw)
            if len(raw) > MAX_PAGE_BYTES or total > MAX_TOTAL_BYTES:
                raise SportmonksRejected("SPORTMONKS_RESPONSE_SIZE_REJECTED")
            try:
                body = _json(raw)
                page = body["pagination"]
                has_more = page["has_more"]
            except (ValueError, KeyError, TypeError):
                raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED") from None
            pages.append(raw)
            hashes.append(hashlib.sha256(raw).hexdigest())
            if has_more is False:
                break
            if has_more is not True or requests >= MAX_PAGES:
                raise SportmonksRejected("SPORTMONKS_PAGINATION_REJECTED")
            next_url = page.get("next_page")
            parsed = urlsplit(next_url) if isinstance(next_url, str) else None
            query = parse_qs(parsed.query) if parsed else {}
            values = query.get("page", [])
            if (parsed.scheme != "https" or parsed.hostname != HOST
                    or parsed.path != f"/v3/football/fixtures/between/{start.isoformat()}/{end.isoformat()}"
                    or len(values) != 1 or not values[0].isdigit()
                    or int(values[0]) != page_number + 1):
                raise SportmonksRejected("SPORTMONKS_PAGINATION_REJECTED")
            page_number += 1
        retrieved = self._clock().astimezone(timezone.utc)
        records = [record for raw in pages for record in normalize_page(
            raw, retrieved_at=retrieved, bindings=bindings,
            expected_season_id=season_id)]
        ids = [record.provider_fixture_id for record in records]
        keys = [record.key for record in records]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise SportmonksRejected("SPORTMONKS_RESPONSE_REJECTED")
        return FetchResult(tuple(sorted(records, key=lambda item: (item.match_date,
            item.kickoff_utc, item.provider_fixture_id))), requests, tuple(hashes), retrieved)
