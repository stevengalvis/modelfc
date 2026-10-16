"""First-publication corner picks. No acquisition, model fitting or historical backfill.

The existing prospective runner owns runner.lock before calling publish_locked.
GET readers only acquire existing shared locks. Publication makes the immutable
pick available through the public track-record feed; it does not attest a page view.
"""
from datetime import datetime, timezone
import math
from pathlib import Path
import re

from modelfc.corner_analysis_store import _canonical_hash
from modelfc.corner_market_intelligence import get_market_intelligence
from modelfc.corner_prospective_read import (
    _locked_inventory, _state_directory, _time,
)
from modelfc.ledger_storage import (
    LedgerError, existing_read_lock, ledger_read_lock, ledger_lock,
    read_json_record, write_new_record,
)
from modelfc.recorded_offers import project_shortlist
from modelfc.research_predictions import _sync_directories

POLICY = "first-team-total-value-v1"
# Immutable v1 semantics, deliberately independent of the live policy factory.
QUALIFICATION_POLICY = {
    "version": "team-total-no-vig-v1", "minimum_american_odds": -200,
    "minimum_no_vig_edge": .05, "watchlist_no_vig_edge": -.05,
    "edge_comparison_tolerance": 1e-12, "no_vig_price_source": "decimal_odds",
    "decimal_american_odds_tolerance": .005,
}
DIRECTORY = "published-recommendations"
VIEWS = ("best_value", "higher_probability")
OFFER_FIELDS = (
    "prediction_id", "target_id", "competition", "provider", "provider_fixture_id",
    "kickoff_utc", "home_team", "away_team", "market_type", "team_side", "team",
    "direction", "line", "bookmaker", "american_odds", "decimal_odds",
    "retrieved_at_utc", "availability_checked_at_utc", "model_probability",
    "push_probability", "decisive_model_probability", "no_vig_market_probability",
    "no_vig_probability_edge", "expected_profit", "opportunity_id",
    "observation_age_seconds", "sportsbook_implied_probability", "qualified", "policy_version",
)


def _now():
    return datetime.now(timezone.utc)


def _fixture(offer):
    return [offer[k] for k in ("competition", "provider", "provider_fixture_id")] + [
        _time(offer["kickoff_utc"]).isoformat()]


def _identity(offer):
    # No line, direction, bookmaker, prediction, retrieval or policy in identity:
    # all revisions compete for the same first-publication slot.
    return _canonical_hash([*_fixture(offer), offer["market_type"], offer["team_side"]])


def select(inventory, now):
    """Best book per exact selection, then existing value ranking per team market."""
    selected = {}
    for offer in project_shortlist(inventory, now=now, limit=100000)["offers"]:
        selected.setdefault(_identity(offer), offer)
    return selected


def _records(state):
    directory = _state_directory(state, DIRECTORY)
    if directory.is_symlink():
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.iterdir()):
        if (path.is_symlink() or not path.is_file()
                or re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None):
            raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
        record = read_json_record(path, "published recommendation", "UNKNOWN_PUBLISHED_RECOMMENDATION")
        try:
            payload = {k: v for k, v in record.items() if k != "record_hash"}
            offer = record["offer"]
            published = _time(record["published_at_utc"])
            if (set(record) != {"schema_version", "policy_version", "publication_id",
                                "published_at_utc", "forecast_created_at_utc", "offer",
                                "ranking_views", "qualification_policy", "record_hash"}
                    or record["schema_version"] != 1 or record["policy_version"] != POLICY
                    or set(offer) != set(OFFER_FIELDS)
                    or record["record_hash"] != _canonical_hash(payload)
                    or record["publication_id"] != _identity(offer)
                    or record["qualification_policy"] != QUALIFICATION_POLICY
                    or offer["policy_version"] != record["qualification_policy"]["version"]
                    or path.stem != record["publication_id"]
                    or offer["market_type"] != "TEAM_TOTAL" or offer["team_side"] not in ("HOME", "AWAY")
                    or not (_time(record["forecast_created_at_utc"]) <= published < _time(offer["kickoff_utc"]))
                    or not (_time(offer["retrieved_at_utc"]) <= _time(offer["availability_checked_at_utc"]) <= published)
                    or offer["american_odds"] < -200 or offer["expected_profit"] <= 0
                    or offer["no_vig_probability_edge"] < .05 - 1e-12
                    or record["ranking_views"] != _views(offer)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION") from None
        result.append(record)
    return result


def _views(offer):
    return ["best_value"] + (["higher_probability"] if offer["model_probability"] >= .65 else [])


def publish_locked(state_dir):
    """Called only by the live writer under runner.lock, never by reads/replay.

    No caller-supplied time. Clock sampled after source validation and checked
    again immediately before atomic linking. A crossed kickoff aborts publication.
    """
    state = Path(state_dir)
    with ledger_lock(state) as lock_fd:
        existing = {r["publication_id"] for r in _records(state)}
        inventory = _locked_inventory(state, now=_now(), include_recorded=True, upcoming_only=True)
        chosen = select(inventory, _now())
        predictions = {p["prediction_id"]: p for p in inventory["predictions"]}
        events = {e["opportunity_id"] for e in inventory["opportunities"]}
        created = 0
        for identity, offer in chosen.items():
            if identity in existing:
                continue
            if inventory["recorded"][offer["prediction_id"]]["qualification_policy"] != QUALIFICATION_POLICY:
                raise LedgerError("UNSUPPORTED_PUBLICATION_POLICY")
            now = _now()
            forecast_at = predictions[offer["prediction_id"]]["created_at_utc"]
            kickoff = _time(offer["kickoff_utc"])
            if now >= kickoff:
                continue
            if (offer["opportunity_id"] not in events
                    or _time(forecast_at) > now or _time(offer["retrieved_at_utc"]) > now
                    or _time(offer["availability_checked_at_utc"]) > now):
                raise LedgerError("INVALID_PUBLICATION_CHRONOLOGY")
            record = {"schema_version": 1, "policy_version": POLICY,
                      "publication_id": identity, "published_at_utc": now.isoformat(),
                      "forecast_created_at_utc": forecast_at,
                      "offer": {k: offer[k] for k in OFFER_FIELDS}, "ranking_views": _views(offer),
                      "qualification_policy": dict(QUALIFICATION_POLICY)}
            record["offer"]["observation_age_seconds"] = (now - _time(offer["retrieved_at_utc"])).total_seconds()
            record["record_hash"] = _canonical_hash(record)
            directory = _state_directory(state, DIRECTORY)
            directory.mkdir(exist_ok=True)

            def before_publish():
                if not now <= _now() < kickoff:
                    raise LedgerError("PUBLICATION_WINDOW_CLOSED")

            write_new_record(directory / f"{identity}.json", record,
                             before_publish=before_publish,
                             evidence_state=state, evidence_lock_fd=lock_fd)
            _sync_directories(directory, state)
            created += 1
        return created


def _summary(rows):
    settled = [r for r in rows if r["result"] in ("WIN", "LOSS", "PUSH")]
    wins = sum(r["result"] == "WIN" for r in rows)
    losses = sum(r["result"] == "LOSS" for r in rows)
    profit = math.fsum(r["profit_units"] for r in settled)
    return {"picks": len(rows), "unique_fixtures": len({_canonical_hash(_fixture(r["offer"])) for r in rows}),
            "settled": len(settled), "wins": wins, "losses": losses,
            "pushes": sum(r["result"] == "PUSH" for r in rows), "voids": 0,
            "pending": len(rows) - len(settled), "profit_units": profit,
            "win_rate": wins / (wins + losses) if wins + losses else None,
            "roi": profit / len(settled) if settled else None}


def read(state_dir):
    state = Path(state_dir)
    rows = []
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    directory = _state_directory(state, DIRECTORY)
    if directory.is_symlink():
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    if directory.exists():
        with existing_read_lock(state / "prospective" / "runner.lock"), ledger_read_lock(state):
            records = _records(state)
            inventory = _locked_inventory(state, now=_now())
            events = {e["opportunity_id"]: e for e in inventory["opportunities"]}
            predictions = {p["prediction_id"]: p for p in inventory["predictions"]}
            targets = inventory["targets"]
            for record in records:
                offer = record["offer"]
                try:
                    event = events[offer["opportunity_id"]]
                    prediction = predictions[offer["prediction_id"]]
                    target, _ = targets[offer["target_id"]]
                    # Bind saved price/selection/fixture to validated immutable
                    # quote and forecast evidence; use only existing outcome tips.
                    common = set(OFFER_FIELDS) & set(event)
                    if (any(offer[k] != event[k] for k in common)
                            or offer["retrieved_at_utc"] != event["qualified_at_utc"]
                            or record["forecast_created_at_utc"] != prediction["created_at_utc"]
                            or offer["model_probability"] != target["model_probability"]
                            or offer["push_probability"] != target["push_probability"]
                            or _time(record["published_at_utc"]) > _now()):
                        raise ValueError
                    historical = get_market_intelligence(state, offer["prediction_id"],
                                                         as_of=_time(record["published_at_utc"]),
                                                         qualification_policy=record["qualification_policy"])
                    source_offers = [o for t in historical["targets"] for o in t["offers"]
                                     if o["target_id"] == offer["target_id"] and o["bookmaker"] == offer["bookmaker"]]
                    if len(source_offers) != 1:
                        raise ValueError
                    source_offer = source_offers[0]
                    if (set(source_offer["recommendation_ineligible_reasons"]) - {"STALE_RETRIEVAL"}
                            or any(offer[k] != source_offer[k] for k in set(OFFER_FIELDS) & set(source_offer))):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION") from None
                rows.append({**record, "result": event["result"] or "PENDING",
                             "profit_units": event["realized_profit_units"]})
    rows.sort(key=lambda r: (r["published_at_utc"], r["publication_id"]), reverse=True)
    groups = []
    for competition, market in sorted({(r["offer"]["competition"], r["offer"]["market_type"]) for r in rows}):
        groups.append({"competition": competition, "market_type": market,
                       "summary": _summary([r for r in rows if r["offer"]["competition"] == competition
                                            and r["offer"]["market_type"] == market])})
    return {"policy_version": POLICY, "void_settlement_supported": False,
            "summary": _summary(rows), "by_view": {view: _summary([r for r in rows if view in r["ranking_views"]]) for view in VIEWS},
            "by_league_market": groups, "publications": rows}
