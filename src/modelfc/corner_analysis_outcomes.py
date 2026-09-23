"""Immutable Football-Data outcome companions. Never rerun a captured prediction.

Settle: python -m modelfc.corner_analysis_outcomes settle --state-dir STATE
  --analysis-id ID --data-config corner_data.json --key OBSERVATION_KEY
Read: replace settle with show and supply --outcome-id ID (no CSV required).
Corrections require both --supersedes CURRENT_OUTCOME_ID and --reason TEXT.
"""

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from modelfc.corner_analysis_store import load_analysis_capture, _canonical_hash
from modelfc.corner_data import load_data_config, configured_history_lock, configured_history_paths
from modelfc.corner_refresh import current_season
from modelfc.ledger_storage import LedgerError, ensure_directory, ledger_lock, read_json_record, write_new_record, utc_timestamp
from modelfc.providers.football_data import load_matches, load_corner_observations
from modelfc.providers.oddspapi import COMPETITIONS

MATCHING_VERSION = "football-data-exact-v1"
SETTLEMENT_VERSION = "team-total-comparison-v1"
ROW_FIELDS = ("Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HC", "AC", "Time")
TIMEZONES = {"E1": "Europe/London", "SP1": "Europe/Madrid"}


class OutcomeError(LedgerError):
    """Safe abstention, conflict or invalid immutable evidence."""


def _require(condition, reason="REVIEW_REQUIRED"):
    if not condition:
        raise OutcomeError(reason)


def _id(value):
    try:
        return uuid.UUID(hex=value).hex
    except (ValueError, TypeError, AttributeError):
        raise OutcomeError("INVALID_ID") from None


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None)
    return parsed.astimezone(timezone.utc)


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _capture(state, analysis_id):
    path = state / "analyses" / f"{_id(analysis_id)}.json"
    original = path.read_bytes()
    record = load_analysis_capture(state, analysis_id)
    _require(path.read_bytes() == original, "CAPTURE_CHANGED")
    try:
        response, request = record["response"], record["request"]
        fixture, prematch = response["fixture"], request["prematch"]
        raw = prematch["fixture"]
        code = fixture["competition"]
        config = COMPETITIONS[code]
        kickoff = _time(fixture["kickoff_at"])
        _require(prematch["provider"] == "oddspapi" and request["competition"] == code)
        _require(raw["sportId"] == 10 and raw["tournamentId"] == config.tournament_id
                 and raw["tournamentSlug"] == config.tournament_slug and raw["categorySlug"] == config.category)
        _require(raw["statusId"] == 0 and _time(raw["startTime"]) == kickoff)
        _require(_time(response["created_at"]) < kickoff)
        _require(date.fromisoformat(fixture["date"]) == kickoff.date())
        # No implicit local-date conversion or adjacent-day search.
        _require(kickoff.astimezone(ZoneInfo(TIMEZONES[code])).date() == kickoff.date())
        _require(fixture["home_team"] != fixture["away_team"])
        for field in ("fixtureId", "participant1Name", "participant2Name"):
            _require(isinstance(raw[field], str) and bool(raw[field].strip()))
        for field in ("participant1Id", "participant2Id"):
            _require(type(raw[field]) is int and raw[field] > 0)
        _require(raw["participant1Id"] != raw["participant2Id"])
        selections = {}
        for selection in prematch["selections"]:
            identity = selection["request"]["client_market_id"]
            _require(isinstance(identity, str) and bool(identity) and identity not in selections)
            _require(selection["fixture_id"] == raw["fixtureId"])
            selections[identity] = selection
        markets = response["markets"]
        _require(len(markets) == len(selections))
        _require(len({m["client_market_id"] for m in markets}) == len(markets))
        for market in markets:
            selection = selections[market["client_market_id"]]
            _require(all(market[k] == selection["request"][k]
                         for k in ("market_type", "team_side", "side", "line", "american_odds")))
            _require(_time(selection["retrieved_at"]) <= _time(response["created_at"]))
        return record, original, path, kickoff, selections
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, OutcomeError):
            raise
        raise OutcomeError("REVIEW_REQUIRED") from None


def _evidence(config_path, fixture, kickoff):
    config = load_data_config(Path(config_path))
    code, day = fixture["competition"], kickoff.date()
    _require(kickoff < datetime.now(timezone.utc), "RESULT_NOT_AVAILABLE")
    season = current_season(day)
    with configured_history_lock(config):
        _require(code in config.leagues)
        _require((config.directory / f"{code}_{season}.csv").is_file(), "RESULT_NOT_AVAILABLE")
        paths = configured_history_paths(config, code)
        paths = [p for p in paths if p.name == f"{code}_{season}.csv"]
        _require(len(paths) == 1, "RESULT_NOT_AVAILABLE")
        path = paths[0]
        before = path.read_bytes()
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(enumerate(csv.DictReader(source), 2))
        home, away = fixture["home_team"], fixture["away_team"]
        candidates = []
        other_date = reversed_venue = False
        names = set()
        for number, row in rows:
            _require((row.get("Div") or "").strip() == code)
            row_home, row_away = ((row.get(k) or "").strip() for k in ("HomeTeam", "AwayTeam"))
            names.update((row_home, row_away))
            try:
                row_day = datetime.strptime(row["Date"].strip(), "%d/%m/%Y").date()
            except (KeyError, TypeError, ValueError, AttributeError):
                raise OutcomeError("REVIEW_REQUIRED") from None
            if (row_home, row_away) == (home, away):
                if row_day == day:
                    candidates.append((number, row))
                else:
                    other_date = True
            reversed_venue |= row_day == day and (row_home, row_away) == (away, home)
        _require(len(candidates) <= 1)
        if not candidates:
            _require(not (other_date or reversed_venue or (rows and not {home, away} <= names)))
            raise OutcomeError("RESULT_NOT_AVAILABLE")
        number, row = candidates[0]
        try:
            matches = [m for m in load_matches(path)
                       if (m.match_date, m.home_team, m.away_team) == (day, home, away)]
            observations = load_corner_observations(path, competition=code)
        except ValueError:
            raise OutcomeError("REVIEW_REQUIRED") from None
        _require(len(matches) == 1)
        pair = [o for o in observations if o.match_date == day and
                (o.team, o.opponent) in ((home, away), (away, home))]
        _require(len(pair) == 2)
        home_obs = next((o for o in pair if o.venue.value == "home" and o.team == home), None)
        away_obs = next((o for o in pair if o.venue.value == "away" and o.team == away), None)
        _require(home_obs is not None and away_obs is not None)
        _require((home_obs.corners_for, home_obs.corners_against) == (away_obs.corners_against, away_obs.corners_for))
        _require(path.read_bytes() == before, "SOURCE_CHANGED")
        retained = {k: row.get(k) for k in ROW_FIELDS}
        return season, {"provider": "football-data.co.uk", "filename": path.name,
                        "file_sha256": _digest(before), "row_number": number,
                        "row": retained, "row_sha256": _canonical_hash(retained),
                        "observed_at_utc": utc_timestamp(), "retrieved_at_utc": None,
                        "completed_at_utc": None}, {
                            "home_corners": home_obs.corners_for, "away_corners": away_obs.corners_for}


def _settlements(capture, selections, result):
    fixture = capture["response"]["fixture"]
    settled = []
    for market in capture["response"]["markets"]:
        if market["status"] != "SUPPORTED" or market["market_type"] != "TEAM_TOTAL":
            continue
        side, direction, line = market["team_side"], market["side"], market["line"]
        _require(side in ("HOME", "AWAY") and direction in ("OVER", "UNDER"))
        _require(type(line) in (int, float) and math.isfinite(line) and line >= 0 and line * 2 == int(line * 2))
        team = fixture[side.lower() + "_team"]
        _require(market["team"] == team)
        actual = result[side.lower() + "_corners"]
        outcome = "PUSH" if actual == line else "WIN" if (actual > line if direction == "OVER" else actual < line) else "LOSS"
        selection = selections[market["client_market_id"]]
        settled.append({"client_market_id": market["client_market_id"],
                        "bookmaker": selection["bookmaker"], "provider_market_id": selection["market_id"],
                        "provider_outcome_id": selection["outcome_id"], "team_side": side,
                        "team": team, "direction": direction, "line": line,
                        "actual_corners": actual, "outcome": outcome})
    _require(bool(settled), "NO_SUPPORTED_TEAM_TOTALS")
    return settled


def _directory(state_dir, analysis_id):
    return Path(state_dir) / "analysis-outcomes" / _id(analysis_id)


def load_outcome(state_dir, analysis_id, outcome_id):
    """Read immutable evidence without consulting current CSVs or running models."""
    record = read_json_record(_directory(state_dir, analysis_id) / f"{_id(outcome_id)}.json",
                              "analysis outcome", "UNKNOWN_OUTCOME")
    try:
        payload = {k: v for k, v in record.items() if k != "record_hash"}
        _require(record["record_hash"] == _canonical_hash(payload), "INVALID_OUTCOME")
        _require(record["schema_version"] == 1 and record["outcome_id"] == _id(outcome_id)
                 and record["capture"]["analysis_id"] == _id(analysis_id), "INVALID_OUTCOME")
    except (KeyError, TypeError, ValueError):
        raise OutcomeError("INVALID_OUTCOME") from None
    return record


def _chain(state, analysis_id):
    records = [load_outcome(state, analysis_id, p.stem)
               for p in _directory(state, analysis_id).glob("*.json")]
    roots = [r for r in records if r["supersedes_outcome_id"] is None]
    if not records:
        return [], None
    _require(len(roots) == 1, "INVALID_REVISION_CHAIN")
    tip, visited = roots[0], set()
    while tip["outcome_id"] not in visited:
        visited.add(tip["outcome_id"])
        children = [r for r in records if r["supersedes_outcome_id"] == tip["outcome_id"]]
        _require(len(children) <= 1, "INVALID_REVISION_CHAIN")
        if not children:
            break
        tip = children[0]
    _require(len(visited) == len(records), "INVALID_REVISION_CHAIN")
    return records, tip


def record_outcome(*, state_dir, analysis_id, data_config_path, idempotency_key,
                   supersedes_outcome_id=None, correction_reason=None):
    """Record all saved supported team selections atomically; corrections append."""
    _require(isinstance(idempotency_key, str) and bool(idempotency_key.strip()), "INVALID_KEY")
    key = idempotency_key.strip()
    _require(bool(supersedes_outcome_id) == bool(correction_reason), "EXPLICIT_CORRECTION_REQUIRED")
    if supersedes_outcome_id:
        supersedes_outcome_id = _id(supersedes_outcome_id)
        _require(isinstance(correction_reason, str) and bool(correction_reason.strip()), "EXPLICIT_CORRECTION_REQUIRED")
        correction_reason = correction_reason.strip()
    state = Path(state_dir)
    with ledger_lock(state):
        capture, original, capture_path, kickoff, selections = _capture(state, analysis_id)
    fixture = capture["response"]["fixture"]
    season, source, result = _evidence(data_config_path, fixture, kickoff)
    settlements = _settlements(capture, selections, result)
    raw = capture["request"]["prematch"]["fixture"]
    reference = {"analysis_id": _id(analysis_id), "file_sha256": _digest(original),
                 "request_hash": capture["request_hash"], "response_hash": capture["response_hash"]}
    # File-wide changes, row order and a later observation time are not corrections.
    evidence_hash = _canonical_hash({"capture": reference, "row": source["row"],
                                    "matching": MATCHING_VERSION, "settlement": SETTLEMENT_VERSION})
    outcome_id = uuid.uuid5(uuid.NAMESPACE_URL, f"modelfc:outcome:{_id(analysis_id)}:{key}").hex
    directory = _directory(state, analysis_id)
    with ledger_lock(state):
        _require(capture_path.read_bytes() == original, "CAPTURE_CHANGED")
        records, tip = _chain(state, analysis_id)
        existing = next((r for r in records if r["outcome_id"] == outcome_id), None)
        if existing:
            _require(existing["evidence_hash"] == evidence_hash and
                     existing["supersedes_outcome_id"] == supersedes_outcome_id and
                     existing["correction_reason"] == correction_reason, "IDEMPOTENCY_CONFLICT")
            return existing, False
        if tip:
            _require(tip["capture"] == reference, "CAPTURE_CHANGED")
            if not supersedes_outcome_id:
                _require(tip["evidence_hash"] == evidence_hash, "RESULT_CONFLICT")
                raise OutcomeError("OUTCOME_ALREADY_EXISTS: reuse the original idempotency key")
            _require(tip["outcome_id"] == supersedes_outcome_id, "REVISION_CONFLICT")
            _require(tip["evidence_hash"] != evidence_hash, "CORRECTION_NOT_REQUIRED")
        else:
            _require(supersedes_outcome_id is None, "REVISION_CONFLICT")
        record = {"schema_version": 1, "outcome_id": outcome_id, "idempotency_key": key,
                  "recorded_at_utc": utc_timestamp(), "capture": reference,
                  "fixture": {**fixture, "source_season": season, "provider": "oddspapi",
                              "provider_fixture_id": raw["fixtureId"], "provider_provenance": raw},
                  "source": source, "result": result, "status": "COMPLETED",
                  "matching_rule_version": MATCHING_VERSION, "settlement_rule_version": SETTLEMENT_VERSION,
                  "settlements": settlements, "evidence_hash": evidence_hash,
                  "supersedes_outcome_id": supersedes_outcome_id, "correction_reason": correction_reason}
        record["record_hash"] = _canonical_hash(record)
        ensure_directory(directory, "analysis outcome directory")
        write_new_record(directory / f"{outcome_id}.json", record)
    return record, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("settle", "show"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--analysis-id", required=True)
    parser.add_argument("--data-config")
    parser.add_argument("--key")
    parser.add_argument("--outcome-id")
    parser.add_argument("--supersedes")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            record = load_outcome(args.state_dir, args.analysis_id, args.outcome_id)
            output = {"outcome": record}
        else:
            if not args.data_config or not args.key:
                parser.error("settle requires --data-config and --key")
            record, created = record_outcome(state_dir=args.state_dir, analysis_id=args.analysis_id,
                data_config_path=args.data_config, idempotency_key=args.key,
                supersedes_outcome_id=args.supersedes, correction_reason=args.reason)
            output = {"created": created, "outcome": record}
        print(json.dumps(output, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 2 if str(error) in ("REVIEW_REQUIRED", "RESULT_NOT_AVAILABLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
