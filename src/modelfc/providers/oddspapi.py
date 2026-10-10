"""E1/SP1 pre-match OddsPapi v4 boundary, with optional immutable pre-match captures.

List: python -m modelfc.providers.oddspapi --date YYYY-MM-DD
Quotes: add --fixture-id ID. Analysis: add --analyze --data-config corner_data.json.
Save: add --state-dir PATH --capture-key KEY to --analyze. No retries.
Replay: --state-dir PATH --replay-analysis ID (offline, including after kickoff).
Endpoint documentation: https://oddspapi.io/us/docs (v4).
"""

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from modelfc.corner_analysis import (
    MAX_MARKETS_PER_ANALYSIS, CornerMarketRequest, analyze_corner_markets,
)
from modelfc.corner_market_data import (
    CornerMarketObservation, MarketDataError, MarketFixture,
    MarketSelection as OddsPapiSelection, RequestGuard,
)
from modelfc.corner_analysis_store import analysis_response, load_analysis, store_analysis_capture
from modelfc.corner_capabilities import supported_markets_for
from modelfc.corner_data import (
    configured_history, configured_history_lock, configured_history_paths, load_data_config,
)
from modelfc.ledger_storage import utc_timestamp
from modelfc.corner_markets import american_odds_terms
from modelfc.matches import UpcomingFixture


BASE_URL = "https://api.oddspapi.io/v4"
BOOKMAKERS = ("draftkings", "fanduel")
MAX_QUOTE_AGE_SECONDS = 300
USER_AGENT = "ModelFC/1.0 (OddsPapi integration)"
FAMILIES = {
    "totals-corners": ("MATCH_TOTAL", None),
    "teamtotals-corners-team1": ("TEAM_TOTAL", "HOME"),
    "teamtotals-corners-team2": ("TEAM_TOTAL", "AWAY"),
}
# E1-only identities verified in the Oct 9 VPS acceptance report, PR #120:
# https://github.com/stevengalvis/modelfc/pull/120#issuecomment-6091241691
# (stable provider ID, exact provider name, exact historical CSV name).
E1_VERIFIED_TEAM_IDENTITIES = (
    (1, "Queens Park Rangers", "QPR"),
    (2, "Portsmouth FC", "Portsmouth"),
    (3, "Wolverhampton Wanderers", "Wolves"),
    (5, "Bolton Wanderers", "Bolton"),
    (6, "Burnley FC", "Burnley"),
    (8, "West Bromwich Albion", "West Brom"),
    (9, "Birmingham City", "Birmingham"),
    (15, "Sheffield United", "Sheffield United"),
    (21, "Preston North End", "Preston"),
    (24, "Watford FC", "Watford"),
    (25, "Millwall FC", "Millwall"),
    (27, "Derby County", "Derby"),
    (29, "Stoke City", "Stoke"),
    (36, "Middlesbrough FC", "Middlesbrough"),
    (37, "West Ham United", "West Ham"),
    (45, "Southampton FC", "Southampton"),
    (46, "Blackburn Rovers", "Blackburn"),
    (47, "Charlton Athletic", "Charlton"),
    (58, "Bristol City", "Bristol City"),
    (61, "Cardiff City", "Cardiff"),
    (64, "Wrexham AFC", "Wrexham"),
    (74, "Swansea City", "Swansea"),
    (92, "Lincoln City", "Lincoln"),
    (263, "Norwich City", "Norwich"),
)


def _verified_e1_aliases(identities):
    """Fail closed on conflicting ID/name/canonical declarations, not fuzzy matching."""
    ids, source_names, canonical_names = set(), set(), set()
    for pid, source, canonical in identities:
        if (type(pid) is not int or pid <= 0 or pid in ids
                or not all(isinstance(n, str) and n and n == n.strip() for n in (source, canonical))
                or source in source_names or canonical in canonical_names):
            raise ValueError("INVALID_VERIFIED_TEAM_REGISTRY")
        ids.add(pid); source_names.add(source); canonical_names.add(canonical)
    if any(source != canonical and source in canonical_names for _, source, canonical in identities):
        raise ValueError("INVALID_VERIFIED_TEAM_REGISTRY")
    return {source: canonical for _, source, canonical in identities if source != canonical}


TEAM_ALIASES = MappingProxyType(_verified_e1_aliases(E1_VERIFIED_TEAM_IDENTITIES))
E1_TEAMS_BY_ID = MappingProxyType({pid: (source, canonical) for pid, source, canonical in E1_VERIFIED_TEAM_IDENTITIES})
E1_TEAM_IDS_BY_NAME = MappingProxyType({name: pid for pid, source, canonical in E1_VERIFIED_TEAM_IDENTITIES
                                      for name in (source, canonical)})


@dataclass(frozen=True)
class Competition:
    code: str
    tournament_id: int
    tournament_slug: str
    category: str
    aliases: tuple[tuple[str, str], ...] = ()


COMPETITIONS = MappingProxyType({
    "E1": Competition("E1", 18, "championship", "england", tuple(TEAM_ALIASES.items())),
    # Team-name differences verified against SP1_2627.csv during live validation.
    "SP1": Competition("SP1", 8, "laliga", "spain", (
        ("Valencia CF", "Valencia"),
        ("Real Sociedad San Sebastian", "Sociedad"),
    )),
})


class OddsPapiError(ValueError):
    """An explicit provider/normalization failure; messages never include URLs."""


def _object(value):
    if not isinstance(value, dict):
        raise OddsPapiError("Malformed response: expected an object")
    return value


def _array(value):
    if not isinstance(value, list):
        raise OddsPapiError("Malformed response: expected an array")
    return value


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise OddsPapiError("Missing or malformed timezone-aware timestamp") from None


def _now():
    return datetime.now(timezone.utc)


def _decimal_price(value):
    try:
        price = Decimal(str(value))
        if not price.is_finite() or price <= 1:
            raise ValueError
        return price
    except (InvalidOperation, ValueError):
        raise OddsPapiError("Unusable decimal price: expected a finite number above 1") from None


def decimal_to_american(value):
    """Fallback only: round half away from zero; preserve decimal provenance."""
    try:
        price = _decimal_price(value)
        american = (price - 1) * 100 if price >= 2 else -100 / (price - 1)
        result = int(american.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        american_odds_terms(result)
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise OddsPapiError("Unusable decimal price: expected a finite number above 1") from None


def _selection_american(price):
    _decimal_price(price.get("price"))
    try:
        supplied = Decimal(str(price.get("priceAmerican")))
        if not supplied.is_finite() or supplied != supplied.to_integral_value():
            raise ValueError
        result = int(supplied)
        american_odds_terms(result)
        return result
    except (InvalidOperation, ValueError, OverflowError):
        return decimal_to_american(price.get("price"))


def validate_team_identity(name, provider_team_id, competition="E1"):
    """Reject contradictions to verified E1 IDs; unknown teams still need exact history."""
    if competition != "E1":
        return
    expected = E1_TEAM_IDS_BY_NAME.get(name)
    # Legacy unregistered names may omit an ID; verified E1 names may not.
    if provider_team_id is None and expected is None:
        return
    if (type(provider_team_id) is not int or provider_team_id <= 0
            or expected is not None and provider_team_id != expected
            or provider_team_id in E1_TEAMS_BY_ID and name not in E1_TEAMS_BY_ID[provider_team_id]):
        raise OddsPapiError("Provider team ID/name does not match verified identity")


def normalize_team(name, historical_names, competition="E1"):
    config = COMPETITIONS[competition]
    names = set(historical_names)
    alias = dict(config.aliases).get(name)
    if name in names and alias in names and alias != name:
        raise OddsPapiError("Ambiguous verified historical identity")
    if name in names:
        return name
    if alias in names:
        return alias
    raise OddsPapiError(f"No exact or verified {config.code} historical identity for {name!r}")


def validate_fixture(fixture, now, competition="E1"):
    config = COMPETITIONS[competition]
    fixture = _object(fixture)
    if (fixture.get("sportId") != 10 or fixture.get("tournamentId") != config.tournament_id
            or fixture.get("categorySlug") != config.category
            or fixture.get("tournamentSlug") != config.tournament_slug):
        raise OddsPapiError(f"Fixture is not {config.code} / {config.category} {config.tournament_slug}")
    for key in ("fixtureId", "participant1Name", "participant2Name"):
        if not isinstance(fixture.get(key), str) or not fixture[key].strip():
            raise OddsPapiError("Missing fixture or participant identity")
    for key in ("participant1Id", "participant2Id"):
        if type(fixture.get(key)) is not int:
            raise OddsPapiError("Missing participant ID")
    if (fixture["participant1Id"] == fixture["participant2Id"]
            or fixture["participant1Name"] == fixture["participant2Name"]):
        raise OddsPapiError("Ambiguous home/away participant identity")
    for side in (1, 2):
        validate_team_identity(fixture[f"participant{side}Name"], fixture[f"participant{side}Id"], competition)
    kickoff = _timestamp(fixture.get("startTime"))
    if type(fixture.get("statusId")) is not int or fixture["statusId"] != 0 or kickoff <= now:
        raise OddsPapiError("Fixture is not upcoming pre-match; live/historical odds are unsupported")
    return kickoff


def select_fixture(fixtures, fixture_id, now, competition="E1"):
    matches = [_object(f) for f in _array(fixtures) if _object(f).get("fixtureId") == fixture_id]
    if not matches:
        raise OddsPapiError(f"Fixture not found in requested {competition} date range")
    if len(matches) != 1:
        raise OddsPapiError("Ambiguous fixture ID in provider response")
    validate_fixture(matches[0], now, competition)
    return matches[0]


@dataclass(frozen=True)
class OddsPapiFixtureSnapshot:
    """One supplied retrieval, retained in memory only; no independent fetch path."""
    fixture: dict
    payload: dict
    metadata: list
    retrieved_at: str
    competition: str


@dataclass(frozen=True)
class OddsPapiQuotes:
    fixture: dict
    selections: tuple[OddsPapiSelection, ...]
    availability: dict
    competition: str = "E1"
    retrieved_at: str | None = None


def normalize_odds(payload, metadata, fixture, *, retrieved_at, now=None, competition="E1"):
    """Parse only recorded fulltime families. Missing selections stay missing.

    Retrieval must be within five minutes. Provider stale flags reject quotes;
    changedAt is a change timestamp, not a heartbeat or an expiry timestamp.
    """
    try:
        return _normalize_odds(payload, metadata, fixture, retrieved_at, now or _now(), competition)
    except (KeyError, TypeError, AttributeError, OverflowError):
        raise OddsPapiError("Malformed OddsPapi response structure") from None


def _normalize_odds(payload, metadata, fixture, retrieved_at, now, competition):
    validate_fixture(fixture, now, competition)
    validate_fixture(payload, now, competition)
    identity = ("fixtureId", "participant1Id", "participant2Id", "participant1Name",
                "participant2Name", "startTime", "tournamentId")
    if any(payload.get(k) != fixture.get(k) for k in identity):
        raise OddsPapiError("Odds response does not match the selected fixture")
    age = (now - _timestamp(retrieved_at)).total_seconds()
    if not 0 <= age <= MAX_QUOTE_AGE_SECONDS:
        raise OddsPapiError("Stale or future retrieval timestamp; fetch current odds")
    dictionary = {}
    for entry in _array(metadata):
        entry = _object(entry)
        mid = entry.get("marketId")
        if type(mid) is not int or str(mid) in dictionary:
            raise OddsPapiError("Missing or duplicate market dictionary ID")
        dictionary[str(mid)] = entry
    books = _object(payload.get("bookmakerOdds"))
    selections, availability = [], {}
    for bookmaker in BOOKMAKERS:
        state = {"status": "BOOKMAKER_UNAVAILABLE", "families": {}, "issues": []}
        availability[bookmaker] = state
        if bookmaker not in books:
            continue
        book = _object(books[bookmaker])
        if (book.get("bookmakerIsActive") is not True or book.get("suspended") is not False
                or book.get("staleOdds") is True or payload.get("staleOdds") is True):
            state["status"] = "BOOKMAKER_UNUSABLE"
            continue
        counts = {family: 0 for family in FAMILIES}
        seen = set()
        for mid, market in sorted(_object(book.get("markets")).items()):
            meta = dictionary.get(mid)
            if meta is None:
                state["issues"].append({"market_id": mid, "reason": "MISSING_MARKET_METADATA"})
                continue
            family = meta.get("marketType")
            if (family not in FAMILIES or meta.get("period") != "fulltime"
                    or meta.get("sportId") != 10 or meta.get("playerProp") is not False):
                continue
            seen.add(family)
            market = _object(market)
            line = meta.get("handicap")
            if (type(line) not in (int, float) or not 0 <= line < float("inf")
                    or line * 2 != int(line * 2)):
                raise OddsPapiError("Unsupported or malformed corner line in metadata")
            directions = {}
            for outcome in _array(meta.get("outcomes")):
                outcome = _object(outcome)
                oid, direction = outcome.get("outcomeId"), outcome.get("outcomeName")
                if type(oid) is not int or str(oid) in directions or direction not in ("Over", "Under"):
                    raise OddsPapiError("Malformed corner outcome dictionary")
                directions[str(oid)] = direction.upper()
            if sorted(directions.values()) != ["OVER", "UNDER"]:
                raise OddsPapiError("Incomplete corner outcome dictionary")
            outcomes = _object(market.get("outcomes"))
            for oid in sorted(set(outcomes) | set(directions)):
                reason = None
                price = None
                if oid not in directions:
                    reason = "UNKNOWN_OUTCOME"
                elif oid not in outcomes:
                    reason = "OUTCOME_UNAVAILABLE"
                elif market.get("marketActive") is not True or market.get("staleOdds") is True:
                    reason = "MARKET_UNUSABLE"
                else:
                    players = _object(_object(outcomes[oid]).get("players"))
                    price = players.get("0")
                    if not isinstance(price, dict) or price.get("active") is not True or price.get("staleOdds") is True:
                        reason = "PRICE_UNUSABLE"
                if reason is None:
                    try:
                        american = _selection_american(price)
                        for field in ("changedAt", "bookmakerChangedAt"):
                            if price.get(field) is not None and _timestamp(price[field]) > now:
                                raise OddsPapiError("Future price timestamp")
                    except OddsPapiError:
                        reason = "PRICE_OR_TIMESTAMP_UNUSABLE"
                if reason is not None:
                    state["issues"].append({"market_id": mid, "outcome_id": oid, "reason": reason})
                    continue
                market_type, team_side = FAMILIES[family]
                identifier = f"oddspapi:{fixture['fixtureId']}:{bookmaker}:{mid}:{oid}"
                request = CornerMarketRequest(identifier, market_type, team_side,
                                              directions[oid], float(line), american)
                selections.append(OddsPapiSelection(
                    request, fixture["fixtureId"], bookmaker, mid, oid,
                    meta.get("marketName", ""), float(price["price"]),
                    price.get("mainLine"), price.get("changedAt"),
                    price.get("bookmakerChangedAt"), retrieved_at,
                ))
                counts[family] += 1
        state["families"] = {family: {
            "status": "RETURNED" if count else "NO_USABLE_PRICES" if family in seen
            else "METADATA_INCOMPLETE" if any(i["reason"] == "MISSING_MARKET_METADATA" for i in state["issues"])
            else "MARKET_UNAVAILABLE", "selection_count": count,
        } for family, count in counts.items()}
        state["status"] = "CORNERS_RETURNED" if sum(counts.values()) else (
            "NO_USABLE_CORNERS" if seen else "METADATA_INCOMPLETE" if state["issues"]
            else "CORNER_MARKETS_UNAVAILABLE")
    return OddsPapiQuotes(
        dict(fixture), tuple(selections), availability, competition, retrieved_at,
    )


def _validate_current_quotes(quotes):
    now = _now()
    validate_fixture(quotes.fixture, now, quotes.competition)
    if any(not 0 <= (now - _timestamp(s.retrieved_at)).total_seconds() <= MAX_QUOTE_AGE_SECONDS
           for s in quotes.selections):
        raise OddsPapiError("Stale or future quote snapshot; fetch current odds")


def analyze_quotes(quotes, observations, *, max_age_days=14,
                   model="venue-opponent-negative-binomial", min_history=100,
                   min_venue_history=5, smoothing_matches=5.0):
    """Delegate forecasting/pricing and capability decisions to Model FC."""
    _validate_current_quotes(quotes)
    observations = tuple(observations)
    day = _timestamp(quotes.fixture["startTime"]).date()
    names = {o.team for o in observations if o.match_date < day}
    fixture = UpcomingFixture(day, normalize_team(quotes.fixture["participant1Name"], names, quotes.competition),
                              normalize_team(quotes.fixture["participant2Name"], names, quotes.competition))
    requests = [selection.request for selection in quotes.selections]
    return tuple(analyze_corner_markets(
        observations, fixture, requests[offset:offset + MAX_MARKETS_PER_ANALYSIS],
        available_market_types=supported_markets_for(quotes.competition), max_age_days=max_age_days,
        model=model, min_history=min_history, min_venue_history=min_venue_history,
        smoothing_matches=smoothing_matches,
    ) for offset in range(0, len(requests), MAX_MARKETS_PER_ANALYSIS))


def capture_quotes(quotes, *, data_config_path, state_dir, capture_key,
                   model="venue-opponent-negative-binomial", min_history=100,
                   min_venue_history=5, smoothing_matches=5.0):
    """Save one quote observation using the authoritative analysis store.

    Identical snapshots replay before clock/history checks. A fresh retrieval is
    a different observation and must use a different capture key.
    """
    quotes = deepcopy(quotes)
    if not quotes.selections:
        raise OddsPapiError("No usable corner selections to capture")
    ids = [s.request.client_market_id for s in quotes.selections]
    if len(ids) != len(set(ids)) or any(s.fixture_id != quotes.fixture.get("fixtureId")
                                      for s in quotes.selections):
        raise OddsPapiError("Duplicate selection or inconsistent fixture identity")
    config = load_data_config(Path(data_config_path))
    settings = dict(model=model, min_history=min_history,
                    min_venue_history=min_venue_history, smoothing_matches=smoothing_matches,
                    max_age_days=config.max_age_days)
    # Allowlist fixture metadata: never persist a raw HTTP payload or request URL.
    fixture = {k: quotes.fixture.get(k) for k in (
        "fixtureId", "sportId", "tournamentId", "tournamentSlug", "categorySlug",
        "participant1Id", "participant2Id", "participant1Name", "participant2Name",
        "startTime", "statusId",
    )}
    request = {
        "idempotency_key": capture_key, "competition": quotes.competition,
        "configuration": settings,
        "prematch": {"provider": "oddspapi", "fixture": fixture,
                     "max_quote_age_seconds": MAX_QUOTE_AGE_SECONDS,
                     "availability": quotes.availability,
                     "selections": [asdict(s) for s in quotes.selections]},
    }
    key = os.environ.get("ODDSPAPI_API_KEY", "").strip()
    if key and key in json.dumps(request):
        raise OddsPapiError("Refusing capture containing authentication material")

    def build_response(analysis_id):
        _validate_current_quotes(quotes)
        with configured_history_lock(config):
            observations = configured_history(config, quotes.competition)
            batches = analyze_quotes(quotes, observations, **settings)
            combined = replace(
                batches[0], markets=tuple(m for b in batches for m in b.markets),
                warnings=tuple(dict.fromkeys(w for b in batches for w in b.warnings)),
            )
            response = analysis_response(
                combined, analysis_id=analysis_id, forecast_id=uuid.uuid4().hex,
                created_at=utc_timestamp(), competition=quotes.competition,
                sources=configured_history_paths(config, quotes.competition),
            )
            response["warnings"] = [asdict(w) for w in combined.warnings]
            response["fixture"]["kickoff_at"] = _timestamp(fixture["startTime"]).isoformat()
            response["pick_logging"] = {"status": "DISABLED", "reason": "PICK_LOGGING_NOT_ENABLED"}
            response["forecast"]["configuration"].update(
                min_history=min_history, min_venue_history=min_venue_history,
            )
            response["forecast"]["historical_context"].update(
                min_history=min_history, min_venue_history=min_venue_history,
            )
            return response

    return store_analysis_capture(
        state_dir=state_dir, request=request, build_response=build_response,
        before_publish=lambda: _validate_current_quotes(quotes),
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward query credentials to a redirected destination.
        return None


class OddsPapiClient:
    def __init__(self, competition="E1"):
        if competition not in COMPETITIONS:
            raise OddsPapiError("Unsupported competition; choose E1 or SP1")
        self.config = COMPETITIONS[competition]
        self._key = os.environ.get("ODDSPAPI_API_KEY", "").strip()
        if not self._key:
            raise OddsPapiError("Missing ODDSPAPI_API_KEY environment variable")
        self._opener = build_opener(_NoRedirect())
        self._last_request = None
        self.requests = 0
        self.usage_headers = {}

    def _get(self, endpoint, *, _fixture_discovery=False, **params):
        if self._last_request is not None:
            time.sleep(max(0, 2.1 - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()
        self.requests += 1
        url = f"{BASE_URL}/{endpoint}?" + urlencode(dict(params, apiKey=self._key))
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=30) as response:
                self.usage_headers = {k: v for k, v in response.headers.items()
                                      if k.lower().startswith(("x-ratelimit", "x-requests"))}
                return json.loads(response.read())
        except HTTPError as error:
            status = error.code
            # Classify safely; never print the response body, URL or Location header.
            content_type = error.headers.get("Content-Type", "").lower() if error.headers else ""
            try:
                body = error.read(8192)
            except OSError:
                body = b""
            if _fixture_discovery and endpoint == "fixtures" and status == 404:
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError):
                    payload = None
                if (isinstance(payload, dict) and isinstance(payload.get("error"), dict)
                        and payload["error"].get("code") == "FIXTURE_NOT_FOUND"):
                    error.close()
                    return []
            body = body.lower()
            cloudflare = b"cloudflare" in body or (error.headers and "cloudflare" in error.headers.get("Server", "").lower())
            diagnostic = ("; Cloudflare response" if cloudflare else "")
            diagnostic += "; error 1010" if b"1010" in body and cloudflare else ""
            diagnostic += "; HTML body" if "text/html" in content_type or b"<html" in body else "; JSON body" if "application/json" in content_type else ""
            error.close()
            label = "rate limited" if status == 429 else "authentication/access denied" if status in (401, 403) else "HTTP failure"
            raise OddsPapiError(f"OddsPapi {label} ({status}){diagnostic}; no retry performed") from None
        except (URLError, TimeoutError, OSError):
            raise OddsPapiError("OddsPapi network failure; no retry performed") from None
        except (ValueError, UnicodeError):
            raise OddsPapiError("OddsPapi returned malformed JSON") from None

    def fixtures(self, day):
        now = _now()
        payload = self._get("fixtures", _fixture_discovery=True, tournamentId=self.config.tournament_id, statusId=0,
                            language="en", bookmakers=",".join(BOOKMAKERS),
                            **{"from": f"{day}T00:00:00Z", "to": f"{day + timedelta(days=1)}T00:00:00Z"})
        fixtures = []
        for fixture in _array(payload):
            fixture = _object(fixture)
            kickoff = _timestamp(fixture.get("startTime"))
            if fixture.get("statusId") != 0 or kickoff <= now or kickoff.date() != day:
                continue
            validate_fixture(fixture, now, self.config.code)
            fixtures.append(fixture)
        ids = [f["fixtureId"] for f in fixtures]
        if len(ids) != len(set(ids)):
            raise OddsPapiError("Ambiguous duplicate fixture IDs")
        return sorted(fixtures, key=lambda f: (f["startTime"], f["fixtureId"]))

    def quotes(self, fixture):
        validate_fixture(fixture, _now(), self.config.code)
        metadata = self._get("markets", language="en")
        payload = self._get("odds", fixtureId=fixture["fixtureId"],
                            bookmakers=",".join(BOOKMAKERS), verbosity=3,
                            language="en", oddsFormat="american")
        return normalize_odds(payload, metadata, fixture, retrieved_at=_now().isoformat(), competition=self.config.code)


class OddsPapiMarketData(OddsPapiClient):
    """Existing OddsPapi boundary presented as normalized corner capabilities.

    Standalone calls retain one-shot behavior.  A runner may explicitly enable
    validated market-metadata reuse for this client instance only.
    """

    provider_name = "oddspapi"
    _cache_fields = ("fixtureId", "sportId", "tournamentId", "tournamentSlug",
                     "categorySlug", "participant1Id", "participant2Id",
                     "participant1Name", "participant2Name", "startTime", "statusId")

    def __init__(self, competition="E1", request_guard: RequestGuard | None = None):
        try:
            super().__init__(competition)
        except OddsPapiError:
            raise MarketDataError("PROVIDER_CONFIGURATION") from None
        self.request_guard = request_guard
        self._reuse_market_metadata = False
        self._market_metadata = None

    def enable_run_metadata_reuse(self) -> None:
        """Reuse validated shared metadata only for this client instance."""
        self._reuse_market_metadata = True

    def corner_market_request_count(self) -> int:
        """Return the requests the next observation will reserve."""
        return 1 if self._reuse_market_metadata and self._market_metadata is not None else 2

    @staticmethod
    def fixture_from_provenance(raw: dict, as_of: datetime, competition="E1") -> MarketFixture:
        try:
            kickoff = validate_fixture(raw, as_of, competition)
            aliases = dict(COMPETITIONS[competition].aliases)
            return MarketFixture(
                competition, aliases.get(raw["participant1Name"], raw["participant1Name"]),
                aliases.get(raw["participant2Name"], raw["participant2Name"]),
                kickoff, "oddspapi", raw["fixtureId"], dict(raw),
            )
        except (OddsPapiError, KeyError, TypeError):
            raise MarketDataError("FIXTURE_REVIEW") from None

    @classmethod
    def cache_fixture(cls, fixture: MarketFixture) -> dict:
        if fixture.provider != cls.provider_name:
            raise MarketDataError("FIXTURE_REVIEW")
        try:
            return {key: fixture.provenance[key] for key in cls._cache_fields}
        except (KeyError, TypeError):
            raise MarketDataError("FIXTURE_REVIEW") from None

    @classmethod
    def cached_fixture(cls, raw: dict, as_of: datetime, competition="E1") -> MarketFixture:
        if not isinstance(raw, dict) or set(raw) != set(cls._cache_fields):
            raise MarketDataError("FIXTURE_REVIEW")
        return cls.fixture_from_provenance(raw, as_of, competition)

    def discover_fixtures(self, competition: str, day: date) -> tuple[MarketFixture, ...]:
        if competition != self.config.code:
            raise MarketDataError("DISCOVERY_INVALID")
        try:
            return tuple(self.fixture_from_provenance(raw, _now(), competition)
                         for raw in self.fixtures(day))
        except OddsPapiError:
            raise MarketDataError("DISCOVERY_INVALID") from None

    def get_corner_markets(self, fixture: MarketFixture) -> CornerMarketObservation:
        if fixture.provider != self.provider_name or fixture.competition != self.config.code:
            raise MarketDataError("FIXTURE_REVIEW")
        try:
            validate_fixture(fixture.provenance, _now(), self.config.code)
            metadata = self._market_metadata if self._reuse_market_metadata else None
            if metadata is None:
                metadata = self._get("markets", language="en")
                if self._reuse_market_metadata:
                    self._market_metadata = metadata
            payload = self._get(
                "odds", fixtureId=fixture.provider_fixture_id,
                bookmakers=",".join(BOOKMAKERS), verbosity=3,
                language="en", oddsFormat="american",
            )
            retrieved_at = _now().isoformat()
            quotes = normalize_odds(payload, metadata, fixture.provenance,
                retrieved_at=retrieved_at, competition=self.config.code)
            snapshot = OddsPapiFixtureSnapshot(dict(fixture.provenance), payload, metadata,
                                               retrieved_at, self.config.code)
            return CornerMarketObservation(fixture, quotes.selections, quotes.availability,
                                           quotes, snapshot)
        except OddsPapiError:
            raise MarketDataError("FIXTURE_REVIEW") from None

    @staticmethod
    def normalize_btts_snapshot(observation: CornerMarketObservation, *, historical_names, as_of):
        """Pure second consumer of the exact supplied odds/metadata, never HTTP."""
        from modelfc.providers.oddspapi_btts import normalize_btts
        snapshot = observation.supplied_snapshot
        if (not isinstance(snapshot, OddsPapiFixtureSnapshot)
                or snapshot.competition != observation.fixture.competition
                or snapshot.fixture != observation.fixture.provenance):
            raise OddsPapiError("INVALID_BTTS_SNAPSHOT")
        return normalize_btts(snapshot.payload, snapshot.metadata, snapshot.fixture,
            competition=snapshot.competition, historical_names=historical_names,
            retrieved_at=snapshot.retrieved_at, as_of=as_of)

    def capture(self, observation: CornerMarketObservation, *, data_config_path,
                state_dir, capture_key: str) -> tuple[dict, bool]:
        if (observation.fixture.provider != self.provider_name
                or not isinstance(observation.provenance, OddsPapiQuotes)):
            raise MarketDataError("FIXTURE_REVIEW")
        try:
            return capture_quotes(observation.provenance, data_config_path=data_config_path,
                                  state_dir=state_dir, capture_key=capture_key)
        except OddsPapiError:
            raise MarketDataError("FIXTURE_REVIEW") from None

    @staticmethod
    def _validate_market_metadata(metadata):
        # The normalizer combines shared metadata and fixture odds errors.
        # Validate shared structure before requesting a fixture's odds.
        try:
            ids = set()
            for entry in _array(metadata):
                entry = _object(entry)
                mid = entry.get("marketId")
                if type(mid) is not int or mid in ids:
                    raise ValueError
                ids.add(mid)
                if (entry.get("marketType") not in FAMILIES
                        or entry.get("period") != "fulltime" or entry.get("sportId") != 10
                        or entry.get("playerProp") is not False):
                    continue
                line = entry.get("handicap")
                if (type(line) not in (int, float) or not math.isfinite(line)
                        or line < 0 or line * 2 != int(line * 2)):
                    raise ValueError
                outcomes = _array(entry.get("outcomes"))
                outcome_ids, directions = set(), []
                for outcome in outcomes:
                    outcome = _object(outcome)
                    oid = outcome.get("outcomeId")
                    if type(oid) is not int or oid in outcome_ids:
                        raise ValueError
                    outcome_ids.add(oid)
                    directions.append(outcome.get("outcomeName"))
                if len(directions) != 2 or "Over" not in directions or "Under" not in directions:
                    raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise MarketDataError("MARKET_METADATA_INVALID") from None

    def _get(self, endpoint, **params):
        kind = {"fixtures": "FIXTURE_DISCOVERY", "markets": "MARKET_METADATA",
                "odds": "FIXTURE_ODDS"}.get(endpoint)
        if kind is None:
            raise MarketDataError("REQUEST_BUDGET")
        guard = self.request_guard
        if guard is not None:
            guard.before_request(kind)
            # The durable runner guard already applied host spacing.
            self._last_request = None
        try:
            payload = super()._get(endpoint, **params)
            if endpoint == "markets":
                self._validate_market_metadata(payload)
            return payload
        except OddsPapiError:
            raise MarketDataError("PROVIDER_FAILURE") from None
        finally:
            if guard is not None:
                guard.after_request()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competition", choices=COMPETITIONS, default="E1")
    parser.add_argument("--date", type=date.fromisoformat, default=_now().date(), help="UTC date")
    parser.add_argument("--fixture-id")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--data-config", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--capture-key")
    parser.add_argument("--replay-analysis", help="Load an existing analysis ID without API calls")
    args = parser.parse_args(argv)
    if args.capture_key is not None and (not args.analyze or not args.state_dir):
        parser.error("--capture-key requires --analyze and --state-dir")
    if args.replay_analysis and (not args.state_dir or args.capture_key is not None or args.analyze or args.fixture_id):
        parser.error("--replay-analysis requires --state-dir and cannot fetch/analyze a fixture")
    if args.state_dir and args.capture_key is None and not args.replay_analysis:
        parser.error("--state-dir requires --capture-key or --replay-analysis")
    if args.analyze and (not args.fixture_id or not args.data_config):
        parser.error("--analyze requires --fixture-id and --data-config")
    client = None
    output = {}
    code = 0
    try:
        if args.replay_analysis:
            print(json.dumps({"analysis": load_analysis(args.state_dir, args.replay_analysis),
                              "created": False}, indent=2))
            return 0
        client = OddsPapiClient(args.competition)
        fixtures = client.fixtures(args.date)
        if not args.fixture_id:
            output["fixtures"] = [{k: f[k] for k in ("fixtureId", "startTime", "participant1Name", "participant2Name")}
                                  for f in fixtures]
        else:
            fixture = select_fixture(fixtures, args.fixture_id, _now(), args.competition)
            quotes = client.quotes(fixture)
            output["quotes"] = asdict(quotes)
            if args.capture_key is not None:
                output["analysis"], output["created"] = capture_quotes(
                    quotes, data_config_path=args.data_config, state_dir=args.state_dir,
                    capture_key=args.capture_key,
                )
            elif args.analyze and quotes.selections:
                config = load_data_config(args.data_config)
                with configured_history_lock(config):
                    history = configured_history(config, args.competition)
                output["analyses"] = [asdict(batch) for batch in analyze_quotes(
                    quotes, history, max_age_days=config.max_age_days)]
    except ValueError as error:
        output["error"] = str(error)
        code = 1
    if client:
        output["usage"] = {"requests_attempted": client.requests, "headers": client.usage_headers}
    text = json.dumps(output, default=str, indent=2)
    key = os.environ.get("ODDSPAPI_API_KEY", "").strip()
    print(text.replace(key, "[REDACTED]") if key else text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
