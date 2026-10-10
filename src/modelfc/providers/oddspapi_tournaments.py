"""Separately guarded FanDuel tournament transport. No discovery, retry or fallback."""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from modelfc.acquisition_planner import LEAGUES
from modelfc.providers.oddspapi import BASE_URL, USER_AGENT, _NoRedirect

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
TOURNAMENT_KIND = "TOURNAMENT_ODDS"


class TournamentRejected(ValueError):
    """Fixed, non-secret code only."""


def request_parameters(tournament_ids):
    try:
        ids = tuple(tournament_ids)
    except TypeError:
        raise TournamentRejected("REQUEST_REJECTED") from None
    approved = {league.tournament_id for league in LEAGUES}
    if (not 1 <= len(ids) <= 5
            or any(type(i) is not int or i not in approved for i in ids)
            or len(set(ids)) != len(ids)):
        raise TournamentRejected("REQUEST_REJECTED")
    # Singular bookmaker is the verified Lab contract. Registry order is canonical.
    ids = tuple(league.tournament_id for league in LEAGUES if league.tournament_id in ids)
    return {"tournamentIds": ",".join(map(str, ids)), "bookmaker": "fanduel",
            "language": "en", "verbosity": 3}


@dataclass(frozen=True)
class RetrievedBatch:
    payload: bytes
    retrieved_at: datetime
    http_status: int


class OddsPapiTournamentClient:
    """Requires an already-reserved shared guard; credential stays inside transport."""
    def __init__(self, guard):
        self.guard = guard
        self._key = os.environ.get("ODDSPAPI_API_KEY", "").strip()
        if not self._key:
            raise TournamentRejected("CREDENTIAL_MISSING")
        self._opener = build_opener(_NoRedirect())
        self._attempted = False

    def retrieve(self, tournament_ids):
        params = request_parameters(tournament_ids)
        if self._attempted:
            raise TournamentRejected("ALREADY_ATTEMPTED")
        self._attempted = True
        self.guard.before_request(TOURNAMENT_KIND)
        try:
            request = Request(BASE_URL + "/odds-by-tournaments?" + urlencode(
                {"apiKey": self._key, **params}), headers={"User-Agent": USER_AGENT,
                                                         "Accept": "application/json"})
            with self._opener.open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if not raw or len(raw) > MAX_RESPONSE_BYTES:
                    raise TournamentRejected("RESPONSE_SIZE_REJECTED")
                return RetrievedBatch(raw, datetime.now(timezone.utc), response.status)
        except HTTPError as error:
            # Never persist the URL, error body, headers or exception text.
            raise TournamentRejected(f"HTTP_{error.code}" if error.code in
                (400, 401, 403, 404, 408, 429, 500, 502, 503, 504) else "HTTP_REJECTED") from None
        except (URLError, OSError, TimeoutError):
            raise TournamentRejected("TRANSPORT_REJECTED") from None
        finally:
            self.guard.after_request()
