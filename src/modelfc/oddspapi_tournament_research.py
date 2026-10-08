"""One-shot, private OddsPapi tournament coverage research.

The execution path is intentionally fixed.  It shares the prospective runner
lock and calendar request accounting, but it cannot call production endpoints
or alter production evidence.  Importing this module never performs network IO.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

from modelfc.corner_prospective import (
    RunnerError, _RequestBudgetGuard, _load, _now, _save,
)
from modelfc.corner_prospective_budget import BudgetError, rollover_if_needed
from modelfc.ledger_storage import LedgerError, write_new_record
from modelfc.providers.oddspapi import (
    BOOKMAKERS, OddsPapiTournamentResearchClient,
    OddsPapiTournamentResearchError, RESEARCH_TOURNAMENT_IDS,
)


EXPERIMENT = "ODDSPAPI_TOURNAMENT_BATCH_V1"
SCHEMA_VERSION = 1
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
if TOURNAMENT_IDS != RESEARCH_TOURNAMENT_IDS:
    raise RuntimeError("reviewed tournament identities disagree")
TOURNAMENT_BY_ID = {row[0]: row for row in TOURNAMENTS}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_METADATA_ROWS = 25_000
MAX_FIXTURES = 500
MAX_MARKETS_PER_BOOK = 3_000
RAW_RETENTION_DAYS = 30
AUTHORIZATION_MAX_AGE = timedelta(hours=24)
RESEARCH_KIND = "TOURNAMENT_RESEARCH"
FAMILIES = (
    "MATCH_CORNER_TOTALS", "HOME_TEAM_CORNERS", "AWAY_TEAM_CORNERS",
    "FIRST_HALF_CORNERS", "BTTS", "MATCH_GOAL_TOTALS",
    "ALTERNATE_GOAL_TOTALS", "HOME_TEAM_GOAL_TOTALS", "AWAY_TEAM_GOAL_TOTALS",
)
STATE_PATH = Path("/var/lib/modelfc/state")
AUTHORIZATION_CREDENTIAL = "tournament-authorization.json"
METADATA_PATH = Path("/etc/modelfc/oddspapi-market-metadata.json")


class TournamentResearchError(ValueError):
    """Fixed-code research authorization, storage, or analysis failure."""


def _fail(code: str):
    raise TournamentResearchError(code)


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


def load_authorization(path: Path, metadata_path: Path, *, now: datetime) -> tuple[dict, object]:
    """Validate a root-installed authorization and its exact cached dictionary."""
    try:
        authorization = json.loads(_read_regular(path, 4096, "AUTHORIZATION_INVALID"))
    except (ValueError, UnicodeError):
        _fail("AUTHORIZATION_INVALID")
    expected = {
        "version", "experiment", "authorized_at_utc", "expires_at_utc",
        "provider_requests_remaining", "market_metadata_sha256",
    }
    if (not isinstance(authorization, dict) or set(authorization) != expected
            or authorization.get("version") != SCHEMA_VERSION
            or authorization.get("experiment") != EXPERIMENT
            or type(authorization.get("provider_requests_remaining")) is not int
            or authorization["provider_requests_remaining"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(authorization.get("market_metadata_sha256")))):
        _fail("AUTHORIZATION_INVALID")
    authorized = _utc(authorization["authorized_at_utc"])
    expires = _utc(authorization["expires_at_utc"])
    current = now.astimezone(timezone.utc)
    if not authorized <= current < expires <= authorized + AUTHORIZATION_MAX_AGE:
        _fail("AUTHORIZATION_EXPIRED")
    metadata_raw = _read_regular(metadata_path, MAX_METADATA_BYTES, "MARKET_METADATA_INVALID")
    if hashlib.sha256(metadata_raw).hexdigest() != authorization["market_metadata_sha256"]:
        _fail("MARKET_METADATA_INVALID")
    try:
        metadata = json.loads(metadata_raw)
    except (ValueError, UnicodeError):
        _fail("MARKET_METADATA_INVALID")
    _metadata_index(metadata)
    return authorization, metadata


@contextmanager
def _existing_runner_lock(state: Path):
    """Take the production runner lock without creating or replacing anything."""
    prospective = state / "prospective"
    try:
        directory_fd = os.open(prospective, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open("runner.lock", os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                             dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size != 0):
            _fail("RUNNER_LOCK_INVALID")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _fail("BUSY")
        yield prospective / "control.json"
    except FileNotFoundError:
        _fail("CONTROL_MISSING")
    except OSError:
        _fail("RUNNER_LOCK_INVALID")
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
        if "directory_fd" in locals():
            os.close(directory_fd)


def _private_directory(state: Path) -> Path:
    parent = state / "provider-research"
    directory = parent / "oddspapi-tournament-v1"
    for path in (parent, directory):
        try:
            path.mkdir(mode=0o700, exist_ok=True)
            info = path.lstat()
        except OSError:
            _fail("RESEARCH_STORAGE_UNAVAILABLE")
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            _fail("RESEARCH_STORAGE_INVALID")
    return directory


def _write_bytes(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError:
        _fail("EXPERIMENT_ALREADY_ATTEMPTED")
    except OSError:
        _fail("RESEARCH_STORAGE_UNAVAILABLE")


def _metadata_index(metadata: object) -> dict[str, tuple[dict, str]]:
    if not isinstance(metadata, list) or not 1 <= len(metadata) <= MAX_METADATA_ROWS:
        _fail("MARKET_METADATA_INVALID")
    indexed: dict[str, tuple[dict, str]] = {}
    for item in metadata:
        if not isinstance(item, dict) or type(item.get("marketId")) is not int:
            _fail("MARKET_METADATA_INVALID")
        key = str(item["marketId"])
        if key in indexed:
            _fail("MARKET_METADATA_INVALID")
        outcomes = item.get("outcomes")
        if not isinstance(outcomes, list):
            _fail("MARKET_METADATA_INVALID")
        outcome_ids = set()
        for outcome in outcomes:
            if not isinstance(outcome, dict) or type(outcome.get("outcomeId")) is not int:
                _fail("MARKET_METADATA_INVALID")
            if outcome["outcomeId"] in outcome_ids:
                _fail("MARKET_METADATA_INVALID")
            outcome_ids.add(outcome["outcomeId"])
        family = _classify_metadata(item)
        if family is not None:
            indexed[key] = (item, family)
    return indexed


def _classify_metadata(item: dict) -> str | None:
    if item.get("sportId") != 10 or item.get("playerProp") is not False:
        return None
    kind, period, name = item.get("marketType"), item.get("period"), item.get("marketName")
    if kind == "totals-corners" and period == "fulltime":
        return "MATCH_CORNER_TOTALS"
    if kind == "teamtotals-corners-team1" and period == "fulltime":
        return "HOME_TEAM_CORNERS"
    if kind == "teamtotals-corners-team2" and period == "fulltime":
        return "AWAY_TEAM_CORNERS"
    if kind and "corners" in kind and period == "p1":
        return "FIRST_HALF_CORNERS"
    if (name == "Both Teams To Score" and kind == "totals" and period == "fulltime"
            and item.get("handicap") == 0):
        return "BTTS"
    if kind == "teamtotals-team1" and period == "fulltime":
        return "HOME_TEAM_GOAL_TOTALS"
    if kind == "teamtotals-team2" and period == "fulltime":
        return "AWAY_TEAM_GOAL_TOTALS"
    if kind == "totals" and period == "fulltime":
        return "MATCH_GOAL_TOTALS"
    return None


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
    dictionary = _metadata_index(metadata)
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
            )
        row = competition_rows[tournament_id]
        row["fixture_count"] += 1
        row["fixtures"].append(fixture_row)
    for row in competition_rows.values():
        row["fixtures"].sort(key=lambda value: (str(value["start_time_utc"]), value["fixture_id"]))
    return {"competitions": [competition_rows[value] for value in TOURNAMENT_IDS],
            "fixture_count": len(fixtures)}


def _analyze_book(book: object, dictionary: dict, *, observed_at: datetime,
                  fixture_stale: bool) -> dict:
    empty = {family: {"status": "MISSING", "usable_priced_outcomes": 0,
                      "market_count": 0, "complete_market_count": 0,
                      "latest_changed_at_utc": None}
             for family in FAMILIES}
    if book is None:
        return {"status": "MISSING", "unsupported_metadata_markets": 0, "families": empty}
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
    unsupported = 0
    for market_id, market in sorted(markets.items()):
        definition = dictionary.get(market_id)
        if definition is None:
            unsupported += 1
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
                unsupported += 1
                continue
            if not isinstance(outcome, dict) or not isinstance(outcome.get("players"), dict):
                _fail("BATCH_RESPONSE_INVALID")
            outcome_states = {}
            for player in outcome["players"].values():
                actual_family = family
                if family == "MATCH_GOAL_TOTALS" and isinstance(player, dict):
                    actual_family = ("MATCH_GOAL_TOTALS" if player.get("mainLine") is True
                                     else "ALTERNATE_GOAL_TOTALS")
                families_seen.add(actual_family)
                usable, stale, latest = _priced(player, observed_at=observed_at)
                player_inactive = isinstance(player, dict) and player.get("active") is False
                player_incomplete = (not isinstance(player, dict)
                                     or player.get("active") not in (True, False))
                state = outcome_states.setdefault(actual_family, {
                    "usable": False, "stale": False, "inactive": False,
                    "incomplete": False, "latest": None,
                })
                state["stale"] |= stale or market_stale
                state["inactive"] |= market_inactive or player_inactive
                state["incomplete"] |= market_incomplete or player_incomplete
                state["usable"] |= (usable and not market_inactive
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
        for family in families_seen:
            accumulators[family]["markets"] += 1
            required_outcomes = len(outcome_family) if family == base_family else 2
            accumulators[family]["complete_markets"] += int(
                required_outcomes >= 2
                and len(usable_outcomes.get(family, ())) == required_outcomes)
    result = {}
    for family, value in accumulators.items():
        if value["markets"] == 0:
            status = "UNSUPPORTED_METADATA" if unsupported and not dictionary else "MISSING"
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
    return {"status": status, "unsupported_metadata_markets": unsupported, "families": result}


def execute(*, state: Path, authorization_path: Path, metadata_path: Path,
            clock=_now, client_type=OddsPapiTournamentResearchClient) -> dict:
    """Consume the reviewed one-shot authorization and capture its result."""
    now = clock().astimezone(timezone.utc)
    authorization, metadata = load_authorization(authorization_path, metadata_path, now=now)
    with _existing_runner_lock(Path(state)) as control_path:
        try:
            control = _load(control_path)
            if control["version"] != 2:
                _fail("CONTROL_INVALID")
            rollover_if_needed(control_path, control, now=now, save=_save)
        except (BudgetError, RunnerError, ValueError):
            _fail("CONTROL_INVALID")
        directory = _private_directory(Path(state))
        marker = directory / "attempt.json"
        if marker.exists() or (directory / "report.json").exists() or (directory / "response.json.gz").exists():
            _fail("EXPERIMENT_ALREADY_ATTEMPTED")
        summary = {"provider_requests": 0}
        guard = _RequestBudgetGuard(control_path, control, summary,
            allowed_kinds={RESEARCH_KIND}, invocation_limit=1)
        try:
            guard.reserve(1)
        except RunnerError:
            _fail("REQUEST_BUDGET")
        attempted_at = now.isoformat().replace("+00:00", "Z")
        write_new_record(marker, {
            "version": SCHEMA_VERSION, "experiment": EXPERIMENT,
            "attempted_at_utc": attempted_at, "request_limit": 1,
            "tournament_ids": list(TOURNAMENT_IDS), "bookmakers": list(BOOKMAKERS),
            "market_metadata_sha256": authorization["market_metadata_sha256"],
        })
        client = client_type(request_guard=guard, max_response_bytes=MAX_RESPONSE_BYTES)
        batch = None
        failure = None
        raw = b""
        try:
            batch = client.retrieve(tournament_ids=TOURNAMENT_IDS, bookmakers=BOOKMAKERS)
            raw = batch.raw
        except OddsPapiTournamentResearchError as error:
            failure = {"code": error.code, "http_status": error.http_status}
            raw = error.raw
        response_received_at = clock().astimezone(timezone.utc)
        if response_received_at < now:
            _fail("CLOCK_INVALID")
        compressed = gzip.compress(raw, compresslevel=9, mtime=0) if raw else b""
        if len(compressed) > MAX_COMPRESSED_BYTES:
            failure, compressed = {"code": "COMPRESSED_RESPONSE_TOO_LARGE", "http_status": None}, b""
        analysis = None
        if batch is not None and failure is None:
            try:
                analysis = analyze_batch(batch.payload, metadata, observed_at=response_received_at)
            except TournamentResearchError as error:
                failure = {"code": str(error), "http_status": batch.http_status}
        completed_at = clock().astimezone(timezone.utc)
        if completed_at < response_received_at:
            _fail("CLOCK_INVALID")
        if compressed:
            _write_bytes(directory / "response.json.gz", compressed)
        report = {
            "version": SCHEMA_VERSION, "experiment": EXPERIMENT,
            "status": "COMPLETE" if failure is None else "FAILED",
            "requested_at_utc": attempted_at,
            "response_received_at_utc": response_received_at.isoformat().replace("+00:00", "Z"),
            "completed_at_utc": completed_at.isoformat().replace("+00:00", "Z"),
            "raw_retention_until_utc": (now + timedelta(days=RAW_RETENTION_DAYS)).isoformat().replace("+00:00", "Z"),
            "request": {"endpoint": "/v4/odds-by-tournaments",
                        "tournament_ids": list(TOURNAMENT_IDS),
                        "bookmakers": list(BOOKMAKERS), "language": "en", "verbosity": 3},
            "provider_accounting": {"reserved": 1, "requests_attempted": summary["provider_requests"],
                                    "reservation_refunded": False,
                                    "provider_quota_preflight_sufficient": True},
            "http_result": failure if failure else {"code": "OK", "http_status": batch.http_status},
            "response_size_bytes": len(raw), "compressed_size_bytes": len(compressed),
            "raw_response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
            "analysis": analysis,
        }
        write_new_record(directory / "report.json", report)
        return report


def main(argv=None) -> int:
    """Fixed operator entrypoint.  No endpoint, request, path, or mode arguments."""
    if list(sys.argv[1:] if argv is None else argv):
        print("TOURNAMENT_RESEARCH_ARGUMENTS_REJECTED", file=sys.stderr)
        return 2
    try:
        credential_directory = Path(os.environ.get("CREDENTIALS_DIRECTORY", ""))
        if (not credential_directory.is_absolute() or credential_directory.is_symlink()
                or not credential_directory.is_dir()):
            _fail("AUTHORIZATION_INVALID")
        report = execute(state=STATE_PATH,
                         authorization_path=credential_directory / AUTHORIZATION_CREDENTIAL,
                         metadata_path=METADATA_PATH)
    except (TournamentResearchError, LedgerError, RunnerError) as error:
        code = str(error)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            code = "TOURNAMENT_RESEARCH_FAILED"
        print(code, file=sys.stderr)
        return 1
    print(json.dumps({"experiment": EXPERIMENT, "status": report["status"],
                      "provider_requests": report["provider_accounting"]["requests_attempted"]},
                     sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
