"""One-request football-data.org E1 calendar capture for forecast authorization."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from modelfc.history_completeness import MAX_CALENDAR_BYTES, parse_calendar


URL = "https://api.football-data.org/v4/competitions/ELC/matches"
USER_AGENT = "ModelFC/1.0 (E1 forecast calendar)"


class CalendarFetchRejected(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class CalendarFetch:
    raw: bytes
    source_sha256: str
    retrieved_at: datetime
    requests: int = 1


def bundle(payload: bytes, *, retrieved_at: datetime, team_bindings) -> bytes:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_CALENDAR_BYTES:
        raise CalendarFetchRejected("CALENDAR_RESPONSE_SIZE_REJECTED")
    try:
        payload.decode("utf-8")
        value = {"version": 1, "source": "football-data.org",
                 "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
                 "team_bindings": team_bindings, "payload": payload.decode("utf-8")}
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
        parse_calendar(raw, as_of=retrieved_at)
        return raw
    except (UnicodeError, TypeError, ValueError):
        raise CalendarFetchRejected("CALENDAR_RESPONSE_REJECTED") from None


class FootballDataCalendarClient:
    def __init__(self, token: str, *, opener=None, clock=None, on_request=None):
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(character.isspace() for character in token)):
            raise CalendarFetchRejected("CALENDAR_CREDENTIAL_REJECTED")
        self._token = token
        self._opener = opener or build_opener(_NoRedirect())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_request = on_request or (lambda: None)

    def fetch(self, season_year: int, *, team_bindings) -> CalendarFetch:
        if type(season_year) is not int or not 2020 <= season_year <= 2100:
            raise CalendarFetchRejected("CALENDAR_SEASON_REJECTED")
        request = Request(f"{URL}?season={season_year}", headers={
            "X-Auth-Token": self._token, "User-Agent": USER_AGENT}, method="GET")
        self._on_request()
        try:
            with self._opener.open(request, timeout=30) as response:
                if response.getcode() != 200:
                    raise CalendarFetchRejected("CALENDAR_HTTP_REJECTED")
                payload = response.read(MAX_CALENDAR_BYTES + 1)
        except HTTPError as error:
            raise CalendarFetchRejected(f"CALENDAR_HTTP_{error.code}" if error.code in
                (400, 401, 403, 404, 408, 429, 500, 502, 503, 504) else
                "CALENDAR_HTTP_REJECTED") from None
        except (URLError, TimeoutError, OSError):
            raise CalendarFetchRejected("CALENDAR_TRANSPORT_REJECTED") from None
        if len(payload) > MAX_CALENDAR_BYTES:
            raise CalendarFetchRejected("CALENDAR_RESPONSE_SIZE_REJECTED")
        retrieved = self._clock().astimezone(timezone.utc)
        raw = bundle(payload, retrieved_at=retrieved, team_bindings=team_bindings)
        return CalendarFetch(raw, hashlib.sha256(payload).hexdigest(), retrieved)
