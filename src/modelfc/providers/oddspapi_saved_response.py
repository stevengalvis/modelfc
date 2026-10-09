"""SHA-pinned, offline five-league OddsPapi replay. Never performs acquisition.

Output describes saved prices at retrieval, not currently actionable offers.
Only explicitly supplied regular files are read. No ledger or budget writes.
"""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

from modelfc.acquisition_planner import LEAGUES, Fixture, PlanningError, utc
from modelfc.corner_markets import american_odds_terms
from modelfc.oddspapi_market_inventory import (
    MarketInventoryError, MAX_FIXTURES, MAX_METADATA_BYTES,
    _analyze_book, _metadata_index, _metadata_parts, _metadata_exclusion, _read_regular, _utc,
)

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class ReplayError(ValueError):
    """Fixed-code offline rejection without raw data or paths."""


def _reject(code="INVALID_SAVED_RESPONSE"):
    raise ReplayError(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _constant(_value):
    _reject("INVALID_JSON_NUMBER")


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        _reject("INVALID_JSON")


def _number(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def process_saved_response(payload: object, metadata: object, *, retrieved_at: datetime) -> dict:
    """Preserve individual outcomes; shared inventory never pairs across books."""
    observed = utc(retrieved_at)
    fixtures = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else None
    if fixtures is None or len(fixtures) > MAX_FIXTURES:
        _reject()
    dictionary, known = _metadata_index(metadata)
    rows, excluded = _metadata_parts(metadata)
    excluded.update({str(row["marketId"]): _metadata_exclusion(row)
                     for row in rows if str(row["marketId"]) not in dictionary})
    leagues = {row.tournament_id: row for row in LEAGUES}
    result, seen = [], set()
    for raw in fixtures:
        if not isinstance(raw, dict) or type(raw.get("tournamentId")) is not int or type(raw.get("sportId")) is not int or raw.get("sportId") != 10:
            _reject()
        league = leagues.get(raw["tournamentId"])
        if league is None:
            _reject("UNSUPPORTED_COMPETITION")
        # Slug/country fields, when supplied, must agree with the fixed registry.
        if (raw.get("tournamentSlug", league.slug) != league.slug
                or raw.get("categorySlug", league.country.lower()) != league.country.lower()):
            _reject("INVALID_COMPETITION_IDENTITY")
        try:
            fixture = Fixture(league.competition, league.tournament_id, raw.get("fixtureId"),
                              _utc(raw.get("startTime")))
        except (PlanningError, MarketInventoryError):
            _reject("INVALID_FIXTURE_IDENTITY")
        if fixture.fixture_id in seen:
            _reject("DUPLICATE_FIXTURE")
        seen.add(fixture.fixture_id)
        if any(not isinstance(raw.get(key), str) or not raw[key].strip()
               for key in ("participant1Name", "participant2Name")):
            _reject("INVALID_FIXTURE_IDENTITY")
        if type(raw.get("statusId")) is not int or type(raw.get("hasOdds")) is not bool:
            _reject()
        updated = raw.get("updatedAt")
        updated_future = updated is not None and _utc(updated) > observed
        books = raw.get("bookmakerOdds")
        if not isinstance(books, dict) or set(books) - {"fanduel"}:
            _reject("UNSUPPORTED_BOOKMAKER")
        book = books.get("fanduel")
        inventory = _analyze_book(book, dictionary, observed_at=observed,
            fixture_stale=raw.get("staleOdds") is True, known_market_ids=known, excluded_ids=excluded)
        prices, diagnostics = [], list(inventory["metadata_diagnostics"])
        for mid, market in sorted((book or {}).get("markets", {}).items()):
            if mid not in dictionary:
                continue
            definition, family = dictionary[mid]
            one_book = {**book, "markets": {mid: market}}
            one_inventory = _analyze_book(one_book, dictionary, observed_at=observed,
                fixture_stale=raw.get("staleOdds") is True, known_market_ids=known, excluded_ids=excluded)
            outcomes = {str(item["outcomeId"]): item["outcomeName"] for item in definition["outcomes"]}
            for oid, outcome in sorted(market["outcomes"].items()):
                if oid not in outcomes:
                    diagnostics.append({"market_id": mid, "outcome_id": oid, "status": "MISSING_OUTCOME_METADATA"})
                    continue
                for pid, player in sorted(outcome["players"].items()):
                    if not isinstance(player, dict):
                        _reject()
                    price = player.get("price")
                    if price is not None and not _number(price):
                        _reject("INVALID_PRICE")
                    american = player.get("priceAmerican")
                    if american is not None and (not isinstance(american, str)
                            or not re.fullmatch(r"[+-]?[0-9]{1,8}", american)):
                        _reject("INVALID_PRICE")
                    if american is not None:
                        try:
                            american_odds_terms(int(american))
                        except ValueError:
                            _reject("INVALID_PRICE")
                    actual_family = ("ALTERNATE_GOAL_TOTALS" if family == "MATCH_GOAL_TOTALS"
                                     and player.get("mainLine") is False else family)
                    status = one_inventory["families"][actual_family]["status"]
                    changed, bookmaker_changed = player.get("changedAt"), player.get("bookmakerChangedAt")
                    if updated_future:
                        status = "FUTURE_TIMESTAMP"
                    for value in (changed, bookmaker_changed):
                        if value is not None and _utc(value) > observed:
                            status = "FUTURE_TIMESTAMP"
                    if raw["statusId"] != 0 or fixture.kickoff_utc <= observed:
                        status = "NOT_PREMATCH"
                    if not raw["hasOdds"] and status == "AVAILABLE":
                        status = "INCOMPLETE"
                    # An unavailable individual outcome cannot inherit availability from another player.
                    if status == "AVAILABLE":
                        if player.get("active") is False:
                            status = "INACTIVE"
                        elif player.get("staleOdds") is True:
                            status = "STALE"
                        elif player.get("active") is not True or price is None or price <= 1:
                            status = "INCOMPLETE"
                    prices.append({"bookmaker": "fanduel", "market_id": mid, "outcome_id": oid,
                        "bookmaker_market_id": market.get("bookmakerMarketId"),
                        "bookmaker_outcome_id": player.get("bookmakerOutcomeId"),
                        "player_id": pid, "family": actual_family, "market_type": definition["marketType"],
                        "market_name": definition.get("marketName"), "period": definition["period"],
                        "line": definition.get("handicap"), "outcome": outcomes[oid],
                        "decimal_odds": price, "american_odds": american,
                        "main_line": player.get("mainLine"), "status": status,
                        "active": player.get("active"), "stale_odds": player.get("staleOdds"),
                        "changed_at": changed, "bookmaker_changed_at": bookmaker_changed,
                        "retrieved_at_utc": observed.isoformat()})
        result.append({"competition": league.competition, "tournament_id": league.tournament_id,
            "fixture_id": fixture.fixture_id, "home_team": raw["participant1Name"],
            "away_team": raw["participant2Name"], "kickoff_utc": fixture.kickoff_utc.isoformat(),
            "fixture_status_id": raw["statusId"], "has_odds": raw["hasOdds"],
            "fixture_updated_at": updated, "bookmaker_fixture_id": (book or {}).get("bookmakerFixtureId"),
            "inventory": inventory,
            "metadata_diagnostics": diagnostics, "prices": prices})
    result.sort(key=lambda row: (row["kickoff_utc"], row["competition"], row["fixture_id"]))
    return {"schema_version": 1, "research_only": True, "saved_observation_only": True,
            "retrieved_at_utc": observed.isoformat(), "fixtures": result}


def replay_files(response: Path, metadata: Path, *, response_sha256: str,
                 metadata_sha256: str, retrieved_at: datetime) -> dict:
    """Caller authorizes exact supplied bytes with independently recorded hashes."""
    raw_files = []
    for path, expected, limit in ((response, response_sha256, MAX_RESPONSE_BYTES),
                                   (metadata, metadata_sha256, MAX_METADATA_BYTES)):
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            _reject("INVALID_SOURCE_HASH")
        raw = _read_regular(path, limit, "SAVED_FILE_REJECTED")
        if hashlib.sha256(raw).hexdigest() != expected:
            _reject("SOURCE_HASH_MISMATCH")
        raw_files.append(raw)
    result = process_saved_response(_json(raw_files[0]), _json(raw_files[1]), retrieved_at=retrieved_at)
    source_metadata = _json(raw_files[1])
    result["provenance"] = {"metadata_source_sha256": source_metadata.get("source_sha256")
                            if isinstance(source_metadata, dict) else None, "response_sha256": response_sha256, "metadata_sha256": metadata_sha256,
                            "response_bytes": len(raw_files[0]), "metadata_bytes": len(raw_files[1])}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--response-sha256", required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--metadata-sha256", required=True)
    parser.add_argument("--retrieved-at", required=True)
    args = parser.parse_args(argv)
    try:
        result = replay_files(args.response, args.metadata, response_sha256=args.response_sha256,
                              metadata_sha256=args.metadata_sha256, retrieved_at=_utc(args.retrieved_at))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (ReplayError, MarketInventoryError, PlanningError, TypeError, OverflowError):
        print('{"status":"REPLAY_REJECTED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
