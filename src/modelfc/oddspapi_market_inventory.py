"""Offline OddsPapi market inventory and bounded metadata artifact IO.

Consumes supplied payloads only. No credentials, provider clients, budgets,
production state, execution authorization or network access.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

BOOKMAKERS = ("draftkings", "fanduel")


TOURNAMENTS = (
    (27070, "Colombia Primera A", "primera-a-apertura", "CACHED_ID_SCOPE_UNCERTAIN"),
    (325, "Brazil Serie A", None, None),
    (17, "Premier League", None, None),
    (18, "Championship", None, None),
    (8, "La Liga", None, None),
    (35, "Bundesliga", None, None),
    (34, "Ligue 1", None, None),
    (52, "Turkish Super Lig", None, None),
    (37, "Eredivisie", None, None),
    (238, "Primeira Liga", None, None),
)
TOURNAMENT_IDS = tuple(row[0] for row in TOURNAMENTS)
TOURNAMENT_BY_ID = {row[0]: row for row in TOURNAMENTS}
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_METADATA_ROWS = 25_000
MAX_FIXTURES = 500
MAX_MARKETS_PER_BOOK = 3_000
FAMILIES = (
    "MATCH_CORNER_TOTALS", "HOME_TEAM_CORNERS", "AWAY_TEAM_CORNERS",
    "FIRST_HALF_CORNERS", "BTTS", "MATCH_GOAL_TOTALS",
    "ALTERNATE_GOAL_TOTALS", "HOME_TEAM_GOAL_TOTALS", "AWAY_TEAM_GOAL_TOTALS",
)

class MarketInventoryError(ValueError):
    """Fixed-code offline metadata, artifact IO, or inventory failure."""


def _fail(code: str):
    raise MarketInventoryError(code)


def _utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        _fail("TIMESTAMP_INVALID")
    if parsed.tzinfo is None:
        _fail("TIMESTAMP_INVALID")
    return parsed.astimezone(timezone.utc)


def _read_regular(path: Path, limit: int, code: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            raw = source.read(limit + 1)
    except OSError:
        _fail(code)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not raw or len(raw) > limit):
        _fail(code)
    return raw


def _write_bytes(path: Path, payload: bytes, *, directory_fd: int | None = None) -> None:
    try:
        descriptor = os.open(path if directory_fd is None else path.name,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        directory = (os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                     if directory_fd is None else os.dup(directory_fd))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError:
        _fail("EXPERIMENT_ALREADY_ATTEMPTED")
    except OSError:
        _fail("RESEARCH_STORAGE_UNAVAILABLE")


# Provider participant semantics follow the existing corner normalizer:
# team1 is home, team2 is away. No display-name substring matching.
RESEARCH_MARKET_TYPES = {
    ("bothteamsscore", "fulltime"): "BTTS",
    ("totals", "fulltime"): "MATCH_GOAL_TOTALS",
    ("teamtotals-team1", "fulltime"): "HOME_TEAM_GOAL_TOTALS",
    ("teamtotals-team2", "fulltime"): "AWAY_TEAM_GOAL_TOTALS",
    ("totals-corners", "fulltime"): "MATCH_CORNER_TOTALS",
    ("teamtotals-corners-team1", "fulltime"): "HOME_TEAM_CORNERS",
    ("teamtotals-corners-team2", "fulltime"): "AWAY_TEAM_CORNERS",
    ("totals-corners", "p1"): "FIRST_HALF_CORNERS",
}
METADATA_FILTER_VERSION = "core-betting-definitions-v2"
MAX_METADATA_ID_INDEX = 100_000  # IDs only, never additional market definitions.


def _classify_metadata(item: dict) -> str | None:
    if item.get("sportId") != 10 or item.get("playerProp") is not False:
        return None
    kind, period = item.get("marketType"), item.get("period")
    if not isinstance(kind, str) or not isinstance(period, str):
        return None  # Null never means fulltime.
    family = RESEARCH_MARKET_TYPES.get((kind, period))
    if family is None:
        return None
    # Preserve the exact legacy BTTS shape already used by the offline adapter.
    if (kind == "totals" and item.get("marketName") == "Both Teams To Score"
            and type(item.get("handicap")) in (int, float) and item["handicap"] == 0):
        family = "BTTS"
    outcomes = item.get("outcomes")
    if not isinstance(outcomes, list):
        _fail("MARKET_METADATA_INVALID")
    names = [row.get("outcomeName") for row in outcomes if isinstance(row, dict)]
    required = {"Yes", "No"} if family == "BTTS" else {"Over", "Under"}
    if len(names) != 2 or any(not isinstance(name, str) for name in names) or set(names) != required:
        _fail("MARKET_METADATA_INVALID")
    if family != "BTTS":
        line = item.get("handicap")
        try:
            valid_line = type(line) in (int, float) and math.isfinite(line) and line >= 0
        except OverflowError:
            valid_line = False
        if not valid_line:
            _fail("MARKET_METADATA_INVALID")
    return family


def _metadata_exclusion(item: dict) -> str:
    if not isinstance(item.get("marketType"), str) or item["marketType"] not in {kind for kind, _ in RESEARCH_MARKET_TYPES}:
        return "UNSUPPORTED_FAMILY"
    return "EXCLUDED_BY_ALLOWLIST"


def _metadata_parts(metadata: object) -> tuple[list, dict[str, str]]:
    if isinstance(metadata, list):
        return metadata, {}
    expected = {"filter_version", "source_sha256", "source_entries", "markets", "excluded_ids"}
    if (not isinstance(metadata, dict) or set(metadata) != expected
            or metadata["filter_version"] != METADATA_FILTER_VERSION
            or not isinstance(metadata["source_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata["source_sha256"])
            or type(metadata["source_entries"]) is not int
            or not 1 <= metadata["source_entries"] <= MAX_METADATA_ID_INDEX
            or not isinstance(metadata["markets"], list)
            or not isinstance(metadata["excluded_ids"], dict)
            or set(metadata["excluded_ids"]) != {"EXCLUDED_BY_ALLOWLIST", "UNSUPPORTED_FAMILY"}):
        _fail("MARKET_METADATA_INVALID")
    excluded = {}
    for reason, identities in metadata["excluded_ids"].items():
        if not isinstance(identities, list) or len(identities) > MAX_METADATA_ID_INDEX:
            _fail("MARKET_METADATA_INVALID")
        for identity in identities:
            if type(identity) is not int or identity <= 0 or str(identity) in excluded:
                _fail("MARKET_METADATA_INVALID")
            excluded[str(identity)] = reason
    if len(excluded) + len(metadata["markets"]) != metadata["source_entries"]:
        _fail("MARKET_METADATA_INVALID")
    return metadata["markets"], excluded


def _metadata_index(metadata: object) -> tuple[dict[str, tuple[dict, str]], frozenset[str]]:
    rows, excluded = _metadata_parts(metadata)
    if not 1 <= len(rows) <= MAX_METADATA_ROWS:
        _fail("MARKET_METADATA_INVALID")
    indexed: dict[str, tuple[dict, str]] = {}
    known_ids = set(excluded)
    for item in rows:
        if not isinstance(item, dict) or type(item.get("marketId")) is not int or item["marketId"] <= 0:
            _fail("MARKET_METADATA_INVALID")
        key = str(item["marketId"])
        if key in known_ids:
            _fail("MARKET_METADATA_INVALID")
        known_ids.add(key)
        outcomes = item.get("outcomes")
        if not isinstance(outcomes, list):
            _fail("MARKET_METADATA_INVALID")
        outcome_ids = set()
        for outcome in outcomes:
            if (not isinstance(outcome, dict) or type(outcome.get("outcomeId")) is not int
                    or outcome["outcomeId"] <= 0 or outcome["outcomeId"] in outcome_ids):
                _fail("MARKET_METADATA_INVALID")
            outcome_ids.add(outcome["outcomeId"])
        family = _classify_metadata(item)
        if family is not None:
            indexed[key] = (item, family)
        elif isinstance(metadata, dict):
            _fail("MARKET_METADATA_INVALID")  # Filtered definitions must be allowlisted.
    return indexed, frozenset(known_ids)


def _priced(player: object, *, observed_at: datetime) -> tuple[bool, bool, str | None]:
    if not isinstance(player, dict):
        return False, False, None
    stale = player.get("staleOdds") is True
    active = player.get("active") is True
    price = player.get("price")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 1:
        return False, stale, None
    latest = None
    for field in ("changedAt", "bookmakerChangedAt"):
        if player.get(field) is not None:
            timestamp = _utc(player[field])
            if timestamp > observed_at:
                return False, stale, None
            value = timestamp.isoformat().replace("+00:00", "Z")
            latest = max(latest, value) if latest else value
    return active and not stale, stale, latest


def analyze_batch(payload: object, metadata: object, *, observed_at: datetime) -> dict:
    """Inventory priced outcomes from supplied bytes only, without model logic."""
    fixtures = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else None
    if fixtures is None or len(fixtures) > MAX_FIXTURES:
        _fail("BATCH_RESPONSE_INVALID")
    dictionary, known_market_ids = _metadata_index(metadata)
    rows, excluded_ids = _metadata_parts(metadata)
    excluded_ids.update({str(row["marketId"]): _metadata_exclusion(row)
                         for row in rows if str(row["marketId"]) not in dictionary})
    competition_rows = {value: {"tournament_id": value, "name": TOURNAMENT_BY_ID[value][1],
        "cached_slug": TOURNAMENT_BY_ID[value][2], "uncertainty": TOURNAMENT_BY_ID[value][3],
        "fixture_count": 0, "fixtures": []} for value in TOURNAMENT_IDS}
    seen_fixtures = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or fixture.get("sportId") != 10:
            _fail("BATCH_RESPONSE_INVALID")
        tournament_id, fixture_id = fixture.get("tournamentId"), fixture.get("fixtureId")
        if tournament_id not in competition_rows or not isinstance(fixture_id, str) or not fixture_id:
            _fail("BATCH_RESPONSE_INVALID")
        if fixture_id in seen_fixtures:
            _fail("BATCH_RESPONSE_INVALID")
        seen_fixtures.add(fixture_id)
        books = fixture.get("bookmakerOdds")
        if books is None:
            books = {}
        if not isinstance(books, dict) or set(books) - set(BOOKMAKERS):
            _fail("BATCH_RESPONSE_INVALID")
        fixture_row = {"fixture_id": fixture_id, "start_time_utc": fixture.get("startTime"),
                       "status_id": fixture.get("statusId"), "has_odds": fixture.get("hasOdds"),
                       "bookmakers": {}}
        if fixture.get("startTime") is not None:
            _utc(fixture["startTime"])
        for bookmaker in BOOKMAKERS:
            fixture_row["bookmakers"][bookmaker] = _analyze_book(
                books.get(bookmaker), dictionary, observed_at=observed_at,
                fixture_stale=fixture.get("staleOdds") is True,
                known_market_ids=known_market_ids, excluded_ids=excluded_ids,
            )
        row = competition_rows[tournament_id]
        row["fixture_count"] += 1
        row["fixtures"].append(fixture_row)
    for row in competition_rows.values():
        row["fixtures"].sort(key=lambda value: (str(value["start_time_utc"]), value["fixture_id"]))
    return {"competitions": [competition_rows[value] for value in TOURNAMENT_IDS],
            "fixture_count": len(fixtures)}


def _analyze_book(book: object, dictionary: dict, *, observed_at: datetime,
                  fixture_stale: bool, known_market_ids: frozenset[str], excluded_ids: dict[str, str]) -> dict:
    empty = {family: {"status": "MISSING", "usable_priced_outcomes": 0,
                      "market_count": 0, "complete_market_count": 0,
                      "latest_changed_at_utc": None}
             for family in FAMILIES}
    if book is None:
        return {"status": "MISSING", "unsupported_metadata_markets": 0,
                "unsupported_metadata_outcomes": 0, "metadata_diagnostics": [], "families": empty}
    if not isinstance(book, dict) or not isinstance(book.get("markets"), dict):
        _fail("BATCH_RESPONSE_INVALID")
    markets = book["markets"]
    if len(markets) > MAX_MARKETS_PER_BOOK:
        _fail("BATCH_RESPONSE_INVALID")
    book_stale = fixture_stale or book.get("staleOdds") is True
    book_inactive = book.get("bookmakerIsActive") is False or book.get("suspended") is True
    book_incomplete = (book.get("bookmakerIsActive") is not True
                       or book.get("suspended") is not False) and not book_inactive
    accumulators = {family: {"markets": 0, "usable": 0, "stale": False,
                             "inactive": False, "incomplete": False,
                             "complete_markets": 0, "latest": None} for family in FAMILIES}
    unsupported_markets = 0
    unsupported_outcomes = 0
    metadata_diagnostics = []
    for market_id, market in sorted(markets.items()):
        definition = dictionary.get(market_id)
        if definition is None:
            unsupported_markets += int(market_id not in known_market_ids)
            metadata_diagnostics.append({"market_id": market_id,
                "status": excluded_ids.get(market_id, "MISSING_METADATA")})
            continue
        if not isinstance(market, dict) or not isinstance(market.get("outcomes"), dict):
            _fail("BATCH_RESPONSE_INVALID")
        metadata, base_family = definition
        families_seen = set()
        market_inactive = book_inactive or market.get("marketActive") is False
        market_incomplete = book_incomplete or market.get("marketActive") not in (True, False)
        market_stale = book_stale or market.get("staleOdds") is True
        outcome_family = {str(value["outcomeId"]): base_family for value in metadata["outcomes"]}
        usable_outcomes = {}
        for outcome_id, outcome in market["outcomes"].items():
            family = outcome_family.get(outcome_id)
            if family is None:
                unsupported_outcomes += 1
                continue
            if not isinstance(outcome, dict) or not isinstance(outcome.get("players"), dict):
                _fail("BATCH_RESPONSE_INVALID")
            outcome_states = {}
            for player in outcome["players"].values():
                actual_family = family
                line_incomplete = False
                if family == "MATCH_GOAL_TOTALS" and isinstance(player, dict):
                    if player.get("mainLine") is True:
                        actual_family = "MATCH_GOAL_TOTALS"
                    elif player.get("mainLine") is False:
                        actual_family = "ALTERNATE_GOAL_TOTALS"
                    else:
                        line_incomplete = True
                families_seen.add(actual_family)
                usable, stale, latest = _priced(player, observed_at=observed_at)
                player_inactive = isinstance(player, dict) and player.get("active") is False
                player_incomplete = (not isinstance(player, dict)
                                     or player.get("active") not in (True, False)
                                     or line_incomplete)
                state = outcome_states.setdefault(actual_family, {
                    "usable": False, "stale": False, "inactive": False,
                    "incomplete": False, "latest": None,
                })
                state["stale"] |= stale or market_stale
                state["inactive"] |= market_inactive or player_inactive
                state["incomplete"] |= market_incomplete or player_incomplete
                state["usable"] |= (usable and not line_incomplete and not market_inactive
                                    and not market_incomplete and not market_stale)
                if latest and (state["latest"] is None or latest > state["latest"]):
                    state["latest"] = latest
            for actual_family, state in outcome_states.items():
                accumulator = accumulators[actual_family]
                accumulator["stale"] |= state["stale"]
                accumulator["inactive"] |= state["inactive"]
                accumulator["incomplete"] |= state["incomplete"]
                accumulator["usable"] += int(state["usable"])
                if state["usable"]:
                    usable_outcomes.setdefault(actual_family, set()).add(outcome_id)
                if (state["latest"] and (accumulator["latest"] is None
                                         or state["latest"] > accumulator["latest"])):
                    accumulator["latest"] = state["latest"]
        if not families_seen:
            # The definition is known and present, even when no mapped outcome
            # has a player quote. Do not fabricate sportsbook absence.
            families_seen.add(base_family)
            accumulators[base_family]["incomplete"] = True
            accumulators[base_family]["inactive"] |= market_inactive
            accumulators[base_family]["stale"] |= market_stale
        for family in families_seen:
            accumulators[family]["markets"] += 1
            required_outcomes = len(outcome_family) if family == base_family else 2
            accumulators[family]["complete_markets"] += int(
                required_outcomes >= 2
                and len(usable_outcomes.get(family, ())) == required_outcomes)
    result = {}
    for family, value in accumulators.items():
        if value["markets"] == 0:
            # An unknown returned market could belong to any unobserved family.
            # Do not report confirmed absence when the cache cannot classify it.
            status = ("UNSUPPORTED_METADATA" if unsupported_markets else
                      "EXCLUDED_BY_ALLOWLIST" if any(row["status"] == "EXCLUDED_BY_ALLOWLIST"
                                                    for row in metadata_diagnostics) else "MISSING")
        elif value["complete_markets"]:
            status = "AVAILABLE"
        elif value["stale"]:
            status = "STALE"
        elif value["inactive"] and value["usable"] == 0:
            status = "INACTIVE"
        elif value["incomplete"]:
            status = "INCOMPLETE"
        else:
            status = "INCOMPLETE"
        result[family] = {"status": status, "usable_priced_outcomes": value["usable"],
                          "market_count": value["markets"],
                          "complete_market_count": value["complete_markets"],
                          "latest_changed_at_utc": value["latest"]}
    status = ("STALE" if book_stale else "INACTIVE" if book_inactive
              else "INCOMPLETE" if book_incomplete else "AVAILABLE")
    return {"status": status, "unsupported_metadata_markets": unsupported_markets,
            "unsupported_metadata_outcomes": unsupported_outcomes,
            "metadata_diagnostics": metadata_diagnostics, "families": result}


