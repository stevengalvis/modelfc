"""First-publication corner and Championship BTTS picks. No acquisition, model fitting or historical backfill.

The existing prospective runner owns runner.lock before calling publish_locked.
GET readers only acquire existing shared locks. Publication makes the immutable
pick available through the public track-record feed; it does not attest a page view.
"""
from datetime import datetime, timezone
import math
from pathlib import Path
import re

from modelfc import published_btts as btts
from modelfc.corner_analysis_store import _canonical_hash
from modelfc.corner_market_intelligence import get_market_intelligence
from modelfc.corner_opportunities import current_qualification_policy
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
    return _canonical_hash([*_fixture(offer), offer["market_type"], offer.get("team_side")])


def select(inventory, now, *, strategy="best_value"):
    """Best book per exact selection, then existing value ranking per team market."""
    selected = {}
    for offer in project_shortlist(inventory, now=now, limit=100000)["offers"]:
        if strategy != "higher_probability" or offer["model_probability"] >= .65:
            selected.setdefault(_identity(offer), offer)
    return selected


def _records(state, *, directory_name=DIRECTORY):
    directory = _state_directory(state, directory_name)
    if directory.is_symlink():
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    if not directory.exists():
        return []
    result = []
    from modelfc.pick_strategies import POLICY as STRATEGY_POLICY, publication_identity, selection_identity, effective_time
    from itertools import islice
    paths = list(islice(directory.iterdir(), 2001))
    if len(paths) > 2000:
        raise LedgerError("PUBLICATION_CAPACITY_EXCEEDED")
    activation_time = effective_time(state, _now()) if directory_name != DIRECTORY and paths else None
    for path in sorted(paths):
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536
                or re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None):
            raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
        record = read_json_record(path, "published recommendation", "UNKNOWN_PUBLISHED_RECOMMENDATION")
        try:
            independent = record.get("schema_version") == 2
            extra = {"strategy", "strategy_policy", "policy_effective_at_utc", "selection_id"} if independent else set()
            payload = {k: v for k, v in record.items() if k != "record_hash"}
            offer = record["offer"]
            published = _time(record["published_at_utc"])
            if (set(record) != {"schema_version", "policy_version", "publication_id",
                                "published_at_utc", "forecast_created_at_utc", "offer",
                                "ranking_views", "qualification_policy", "record_hash"} | extra
                    or record["schema_version"] != (1 if directory_name == DIRECTORY else 2) or record["policy_version"] != ("first-btts-value-v1" if offer["market_type"] == "BTTS" else POLICY)
                    or (offer["market_type"] != "BTTS" and set(offer) != set(OFFER_FIELDS))
                    or record["record_hash"] != _canonical_hash(payload)
                    or record["publication_id"] != (publication_identity(offer, record["strategy"]) if independent else _identity(offer))
                    or (independent and (record["strategy"] not in VIEWS
                        or record["strategy_policy"] != STRATEGY_POLICY
                        or record["selection_id"] != selection_identity(offer)
                        or record["policy_effective_at_utc"] != activation_time
                        or _time(record["policy_effective_at_utc"]) > published
                        or record["strategy"] == "higher_probability" and offer["model_probability"] < .65))
                    or record["qualification_policy"] != (btts.QUALIFICATION_POLICY if offer["market_type"] == "BTTS" else QUALIFICATION_POLICY)
                    or offer["policy_version"] != record["qualification_policy"]["version"]
                    or path.stem != record["publication_id"]
                    or not (offer["market_type"] == "BTTS" and offer.get("side") in ("YES", "NO")
                            or offer["market_type"] == "TEAM_TOTAL" and offer["team_side"] in ("HOME", "AWAY"))
                    or not (_time(record["forecast_created_at_utc"]) <= published < _time(offer["kickoff_utc"]))
                    or not (_time(offer["retrieved_at_utc"]) <= _time(offer["availability_checked_at_utc"]) <= published)
                    or offer["american_odds"] < -200 or offer["expected_profit"] <= 0
                    or offer["no_vig_probability_edge"] < .05 - 1e-12
                    or record["ranking_views"] != ([record["strategy"]] if independent else _views(offer))):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION") from None
        result.append(record)
    return result


def _views(offer):
    return ["best_value"] + (["higher_probability"] if offer["model_probability"] >= .65 else [])


def publish_locked(state_dir, *, include_btts=True, independent=False):
    """Called only by the live writer under runner.lock, never by reads/replay.

    No caller-supplied time. Clock sampled after source validation and checked
    again immediately before atomic linking. A crossed kickoff aborts publication.
    """
    state = Path(state_dir)
    with ledger_lock(state) as lock_fd:
        if current_qualification_policy() != QUALIFICATION_POLICY:
            raise LedgerError("UNSUPPORTED_PUBLICATION_POLICY")
        from modelfc import pick_strategies as strategies
        directory_name = strategies.DIRECTORY if independent else DIRECTORY
        effective = strategies.activate(state, lock_fd, _now()) if independent else None
        existing = {r["publication_id"] for r in _records(state, directory_name=directory_name)}
        inventory = _locked_inventory(state, now=_now(), include_recorded=True, upcoming_only=True)
        if any(view["qualification_policy"] != QUALIFICATION_POLICY for view in inventory["recorded"].values()):
            raise LedgerError("UNSUPPORTED_PUBLICATION_POLICY")
        selection_time = _now()
        predictions = {p["prediction_id"]: p for p in inventory["predictions"]}
        chosen = {}
        source = btts.records(state) if include_btts else ()
        for strategy in VIEWS if independent else ("best_value",):
            candidates = {key: (value, predictions[value["prediction_id"]]["created_at_utc"])
                          for key, value in select(inventory, selection_time, strategy=strategy).items()}
            if include_btts:
                candidates.update(btts.select(source, selection_time, strategy=strategy))
            for key, (value, forecast_at) in candidates.items():
                identity = strategies.publication_identity(value, strategy) if independent else key
                chosen[identity] = (value, forecast_at, strategy)
        if independent and len(existing | set(chosen)) > 2000:
            raise LedgerError("PUBLICATION_CAPACITY_EXCEEDED")
        events = {e["opportunity_id"] for e in inventory["opportunities"]}
        created = 0
        for identity, (offer, forecast_at, strategy) in chosen.items():
            if identity in existing:
                continue
            now = _now()
            kickoff = _time(offer["kickoff_utc"])
            if now >= kickoff:
                continue
            if (independent and _time(effective) > now
                    or offer["market_type"] != "BTTS" and offer["opportunity_id"] not in events
                    or _time(forecast_at) > now or _time(offer["retrieved_at_utc"]) > now
                    or _time(offer["availability_checked_at_utc"]) > now):
                raise LedgerError("INVALID_PUBLICATION_CHRONOLOGY")
            record = {"schema_version": 1, "policy_version": btts.POLICY if offer["market_type"] == "BTTS" else POLICY,
                      "publication_id": identity, "published_at_utc": now.isoformat(),
                      "forecast_created_at_utc": forecast_at,
                      "offer": dict(offer) if offer["market_type"] == "BTTS" else {k: offer[k] for k in OFFER_FIELDS}, "ranking_views": _views(offer),
                      "qualification_policy": dict(btts.QUALIFICATION_POLICY if offer["market_type"] == "BTTS" else QUALIFICATION_POLICY)}
            if independent:
                record.update(schema_version=2, strategy=strategy, strategy_policy=strategies.POLICY,
                              policy_effective_at_utc=effective, selection_id=strategies.selection_identity(offer),
                              ranking_views=[strategy])
            record["offer"]["observation_age_seconds"] = (now - _time(offer["retrieved_at_utc"])).total_seconds()
            record["record_hash"] = _canonical_hash(record)
            directory = _state_directory(state, directory_name)
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
            "pushes": sum(r["result"] == "PUSH" for r in rows),
            "voids": sum(r["result"] == "SIMULATION_VOID" for r in rows),
            "pending": sum(r["result"] == "PENDING" for r in rows), "profit_units": profit,
            "win_rate": wins / (wins + losses) if wins + losses else None,
            "roi": profit / len(settled) if settled else None}


def read(state_dir, *, directory_name=DIRECTORY, record_filter=None):
    state = Path(state_dir)
    rows = []
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    directory = _state_directory(state, directory_name)
    if directory.is_symlink():
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    if directory.exists():
        with existing_read_lock(state / "prospective" / "runner.lock"), ledger_read_lock(state):
            records = _records(state, directory_name=directory_name)
            if record_filter is not None:
                records = record_filter(records)
            if not records:
                return _report([])
            # Published corners never reference research-only forecasts. BTTS outcomes
            # are validated separately so absent optional provider code stays pending.
            inventory = _locked_inventory(state, now=_now(), include_research=False)
            events = {e["opportunity_id"]: e for e in inventory["opportunities"]}
            predictions = {p["prediction_id"]: p for p in inventory["predictions"]}
            targets = inventory["targets"]
            btts_source = btts.records(state) if any(r["offer"]["market_type"] == "BTTS" for r in records) else ()
            for record in records:
                offer = record["offer"]
                if offer["market_type"] == "BTTS":
                    if _time(record["published_at_utc"]) > _now():
                        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
                    btts.validate(record, btts_source)
                    result, profit = btts.result(record, state, btts_source, _now())
                    rows.append({**record, "result": result, "profit_units": profit})
                    continue
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
            from modelfc.settlement_reviews import apply_voids
            rows = apply_voids(state, rows, directory_name)
    return _report(rows)


def _report(rows):
    rows.sort(key=lambda r: (r["published_at_utc"], r["publication_id"]), reverse=True)
    groups = []
    for competition, market in sorted({(r["offer"]["competition"], r["offer"]["market_type"]) for r in rows}):
        groups.append({"competition": competition, "market_type": market,
                       "summary": _summary([r for r in rows if r["offer"]["competition"] == competition
                                            and r["offer"]["market_type"] == market])})
    return {"policy_version": "first-published-value-v2", "void_settlement_supported": False, "simulation_void_supported": True,
            "summary": _summary(rows), "by_view": {view: _summary([r for r in rows if view in r["ranking_views"]]) for view in VIEWS},
            "by_league_market": groups, "publications": rows}
