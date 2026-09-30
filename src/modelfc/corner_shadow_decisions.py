"""Private, immutable paired market decisions for the single E1 DeepFC shadow.

An observation policy stamp is written during quote collection. Replay only
uses that stamp: a historical observation without one has unknown policy.
"""

from datetime import datetime, timezone
import math
from pathlib import Path
import re

from modelfc.corner_analysis_store import _canonical_hash
from modelfc.corner_markets import american_odds_terms
from modelfc.corner_opportunities import (
    _id, _paired_selections, _source_observation, _timestamp, current_qualification_policy,
    load_prediction, prediction_observations, prediction_target_records,
    qualification_decision, target_id,
)
from modelfc.corner_shadow import MODEL_VERSION, read_shadow
from modelfc.ledger_storage import (
    LedgerError, ensure_directory, existing_read_lock, ledger_lock,
    ledger_read_lock, read_json_record, write_new_record,
)

HEX = re.compile(r"[0-9a-f]{32}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
V1_POLICY_FIELDS = frozenset(("version", "minimum_american_odds", "minimum_no_vig_edge",
                              "watchlist_no_vig_edge", "edge_comparison_tolerance",
                              "no_vig_price_source", "decimal_american_odds_tolerance"))


def _path(state, family, identity, parent=None):
    if not isinstance(identity, str) or not HEX.fullmatch(identity):
        raise LedgerError("INVALID_SHADOW_DECISION")
    if parent is not None and (not isinstance(parent, str) or not HEX.fullmatch(parent)):
        raise LedgerError("INVALID_SHADOW_DECISION")
    return state / family / (parent or "") / (identity + ".json")


def _hashed(payload):
    return {**payload, "record_hash": _canonical_hash(payload)}


def _read(path, kind):
    record = read_json_record(path, kind, "INVALID_SHADOW_DECISION")
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("record_type") != kind
            or record.get("record_hash") != _canonical_hash({k: v for k, v in record.items()
                                                              if k != "record_hash"})):
        raise LedgerError("INVALID_SHADOW_DECISION")
    return record


def _publish(state, path, record):
    ensure_directory(state, "state")
    ensure_directory(path.parent, "private shadow decisions")
    with ledger_lock(state):
        if path.is_symlink():
            raise LedgerError("INVALID_SHADOW_DECISION")
        if path.exists():
            if _read(path, record["record_type"]) != record:
                raise LedgerError("SHADOW_DECISION_CONFLICT")
            return record, False
        # Deliberately no evidence_state: modelfc-api receives no ACL.
        write_new_record(path, record)
    return record, True


def stamp_observation(state_dir, observation, release_sha, *, clock=None):
    """Freeze policy at quote publication, before champion assessment."""
    state = Path(state_dir)
    identity = observation["observation_id"]
    if not isinstance(release_sha, str) or not SHA.fullmatch(release_sha):
        raise LedgerError("INVALID_SHADOW_RELEASE")
    path = _path(state, "shadow-observation-policies", identity)
    if path.exists() or path.is_symlink():
        return _policy(state, observation), False
    stamped = (clock or (lambda: datetime.now(timezone.utc)))()
    if (stamped.tzinfo is None
            or _timestamp(observation["retrieved_at_utc"]) > stamped
            or stamped >= _timestamp(observation["fixture"]["kickoff_utc"])):
        raise LedgerError("INVALID_SHADOW_DECISION")
    payload = {"schema_version": 1, "record_type": "shadow_observation_policy",
               "observation_id": identity, "observation_hash": observation["record_hash"],
               "observed_at_utc": observation["retrieved_at_utc"],
               "stamped_at_utc": stamped.isoformat(),
               "release_sha": release_sha, "policy": current_qualification_policy()}
    record = _hashed(payload)
    return _publish(state, path, record)


def _policy(state, observation):
    path = _path(state, "shadow-observation-policies", observation["observation_id"])
    if path.is_symlink():
        raise LedgerError("INVALID_SHADOW_DECISION")
    record = _read(path,
                   "shadow_observation_policy")
    if (set(record) != {"schema_version", "record_type", "observation_id", "observation_hash",
                        "observed_at_utc", "stamped_at_utc", "release_sha", "policy", "record_hash"}
            or record["observation_id"] != observation["observation_id"]
            or record["observation_hash"] != observation["record_hash"]
            or record["observed_at_utc"] != observation["retrieved_at_utc"]
            or not _timestamp(record["observed_at_utc"]) <= _timestamp(record["stamped_at_utc"])
            < _timestamp(observation["fixture"]["kickoff_utc"])
            or not isinstance(record["release_sha"], str) or not SHA.fullmatch(record["release_sha"])):
        raise LedgerError("INVALID_SHADOW_DECISION")
    policy = record["policy"]
    if (not isinstance(policy, dict) or set(policy) != V1_POLICY_FIELDS
            or policy["version"] != "team-total-no-vig-v1"
            or policy["no_vig_price_source"] != "decimal_odds"
            or type(policy["minimum_american_odds"]) is not int
            or any(type(policy[k]) not in (int, float) or not math.isfinite(policy[k])
                   or policy[k] < 0 for k in ("minimum_no_vig_edge", "edge_comparison_tolerance",
                                               "decimal_american_odds_tolerance"))
            or type(policy["watchlist_no_vig_edge"]) not in (int, float)
            or not math.isfinite(policy["watchlist_no_vig_edge"])):
        raise LedgerError("INVALID_SHADOW_DECISION")
    return record


def _assessment(state, prediction, observation, shadow, stamp):
    if (shadow["production_prediction_id"] != prediction["prediction_id"]
            or shadow["production_prediction_hash"] != prediction["record_hash"]
            or shadow["model"]["version"] != MODEL_VERSION
            or observation not in prediction_observations(state, prediction)):
        raise LedgerError("INVALID_SHADOW_DECISION")
    policy = stamp["policy"]
    targets = {t["target_id"]: t for t in prediction_target_records(state, prediction["prediction_id"])}
    frozen = {t["production_target_id"]: t for t in shadow["targets"]}
    source = prediction["source_observation"]["observation_id"] == observation["observation_id"]
    source_targets = {target_id(prediction["prediction_id"], "TEAM_TOTAL", s["team_side"],
                                s["direction"], s["line"])
                      for s in _source_observation(state, prediction)["selections"]
                      if s["market_type"] == "TEAM_TOTAL" and s["team_side"] in ("HOME", "AWAY")}
    later_only = set()
    decisions = []
    for selection in observation["selections"]:
        if selection["market_type"] == "TEAM_TOTAL" and selection["team_side"] in ("HOME", "AWAY"):
            identity = target_id(prediction["prediction_id"], "TEAM_TOTAL", selection["team_side"],
                                 selection["direction"], selection["line"])
            if not source and identity not in source_targets:
                later_only.add(identity)
    for key, sides in _paired_selections(observation):
        if key[1] != "TEAM_TOTAL" or key[2] not in ("HOME", "AWAY"):
            continue
        pair = {}
        for direction, selection in sides.items():
            identity = target_id(prediction["prediction_id"], "TEAM_TOTAL", selection["team_side"],
                                 direction, selection["line"])
            pair[direction] = (identity, selection)
        # Both prices and both frozen probabilities must belong to the source cohort.
        if any(identity not in frozen for identity, _ in pair.values()):
            continue
        if any(not math.isclose(selection["decimal_odds"], 1 + american_odds_terms(
                selection["american_odds"])[0], rel_tol=0,
                abs_tol=policy["decimal_american_odds_tolerance"])
               for _, selection in pair.values()):
            continue
        implied = {direction: 1 / selection["decimal_odds"] for direction, (_, selection) in pair.items()}
        total = math.fsum(implied.values())
        if not all(math.isfinite(x) and x > 0 for x in implied.values()) or not math.isfinite(total) or total <= 0:
            continue
        for direction, (identity, selection) in pair.items():
            target = targets.get(identity)
            if (target is None or target["record_hash"] != frozen[identity]["production_target_hash"]
                    or target["status"] != "SUPPORTED"
                    or target["prediction_id"] != prediction["prediction_id"]
                    or any(target[field] != selection[field] for field in
                           ("market_type", "team_side", "team", "direction", "line"))
                    or target["materialized_at_utc"] > observation["retrieved_at_utc"]):
                raise LedgerError("INVALID_SHADOW_DECISION")
            champion = qualification_decision(selection, implied, total,
                                               target["decisive_model_probability"], policy)
            challenger = qualification_decision(selection, implied, total,
                                                 frozen[identity]["decisive_model_probability"], policy)
            decisions.append({
                "target_id": identity, "target_hash": target["record_hash"],
                "selection_id": selection["selection_id"], "bookmaker": key[0],
                "team_side": key[2], "team": selection["team"], "direction": direction,
                "line": selection["line"], "american_odds": selection["american_odds"],
                "decimal_odds": selection["decimal_odds"],
                "opposite_american_odds": pair["UNDER" if direction == "OVER" else "OVER"][1]["american_odds"],
                "opposite_decimal_odds": pair["UNDER" if direction == "OVER" else "OVER"][1]["decimal_odds"],
                "paired_implied_probabilities": implied,
                "champion": {"decisive_probability": target["decisive_model_probability"], **champion},
                "shadow": {"decisive_probability": frozen[identity]["decisive_model_probability"], **challenger},
            })
    identity = _id("shadow-decision", {"prediction_id": prediction["prediction_id"],
                                        "observation_id": observation["observation_id"]})
    return _hashed({
        "schema_version": 1, "record_type": "shadow_decision", "decision_id": identity,
        "prediction_id": prediction["prediction_id"], "prediction_hash": prediction["record_hash"],
        "shadow_analysis_id": shadow["analysis_id"], "shadow_hash": shadow["record_hash"],
        "observation_id": observation["observation_id"], "observation_hash": observation["record_hash"],
        "policy_stamp_hash": stamp["record_hash"], "release_sha": stamp["release_sha"],
        "fixture": observation["fixture"], "observed_at_utc": observation["retrieved_at_utc"],
        "source_observation": source, "policy": policy,
        "later_only_targets_excluded": len(later_only), "decisions": decisions,
    })


def store_assessment(state_dir, prediction, observation):
    """Append or replay only when every frozen input and policy stamp validates."""
    state = Path(state_dir)
    stamp = _policy(state, observation)
    shadow = read_shadow(state, prediction["analysis_id"])
    record = _assessment(state, prediction, observation, shadow, stamp)
    path = _path(state, "shadow-decisions", record["decision_id"], prediction["prediction_id"])
    return _publish(state, path, record)


def compare_decisions(state_dir):
    """Private snapshot and settled hypothetical-event accounting."""
    from modelfc.corner_prospective_read import _outcome_tip, _profit, _target_settlement
    state = Path(state_dir)
    counts = {"paired_settled_fixtures": 0, "paired_decision_snapshots": 0,
              "observation_snapshots": 0, "later_only_targets_excluded": 0,
              "missing_assessments": 0, "pushes": 0,
              "neither_qualifies": 0, "champion_only": 0, "shadow_only": 0, "both_qualify": 0}
    scores = {name: {"hypothetical_qualifying_events": 0, "wins": 0, "losses": 0,
                     "pushes": 0, "settled_hypothetical_qualifying_events": 0,
                     "standardized_realized_units": 0.0,
                     "roi_on_settled_hypothetical_events": None,
                     "unique_target_bookmaker_opportunities": 0}
              for name in ("champion", "shadow")}
    if not (state / "predictions").exists():
        directory = state / "shadow-decisions"
        if directory.is_symlink() or directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise LedgerError("INVALID_SHADOW_DECISION")
        return {**counts, **scores}
    from modelfc.corner_opportunities import prediction_records
    with existing_read_lock(state / "prospective" / "runner.lock"), ledger_read_lock(state):
        seen_paths = set()
        for prediction in prediction_records(state):
            shadow_path = _path(state, "shadow-predictions", prediction["analysis_id"])
            shadow = read_shadow(state, prediction["analysis_id"]) if shadow_path.exists() else None
            outcome = _outcome_tip(state, prediction)
            if shadow is not None and outcome is not None:
                counts["paired_settled_fixtures"] += 1
            unique = {"champion": set(), "shadow": set()}
            for observation in prediction_observations(state, prediction):
                counts["observation_snapshots"] += 1
                identity = _id("shadow-decision", {"prediction_id": prediction["prediction_id"],
                                                     "observation_id": observation["observation_id"]})
                path = _path(state, "shadow-decisions", identity, prediction["prediction_id"])
                seen_paths.add(path)
                if path.is_symlink():
                    raise LedgerError("INVALID_SHADOW_DECISION")
                if not path.exists():
                    counts["missing_assessments"] += 1
                    continue
                if shadow is None:
                    raise LedgerError("INVALID_SHADOW_DECISION")
                stamp = _policy(state, observation)
                actual = _read(path, "shadow_decision")
                if actual != _assessment(state, prediction, observation, shadow, stamp):
                    raise LedgerError("INVALID_SHADOW_DECISION")
                counts["later_only_targets_excluded"] += actual["later_only_targets_excluded"]
                for decision in actual["decisions"]:
                    counts["paired_decision_snapshots"] += 1
                    a, b = decision["champion"]["qualified"], decision["shadow"]["qualified"]
                    key = ("both_qualify" if a and b else "champion_only" if a else
                           "shadow_only" if b else "neither_qualifies")
                    counts[key] += 1
                    for name, qualified in (("champion", a), ("shadow", b)):
                        if qualified:
                            scores[name]["hypothetical_qualifying_events"] += 1
                            unique[name].add((decision["target_id"], decision["bookmaker"]))
                    if outcome is None:
                        continue
                    result, _ = _target_settlement({"status": "SUPPORTED", "market_type": "TEAM_TOTAL",
                                                    "team_side": decision["team_side"],
                                                    "direction": decision["direction"],
                                                    "line": decision["line"]}, outcome)
                    counts["pushes"] += int(result == "PUSH")
                    for name, qualified in (("champion", a), ("shadow", b)):
                        if qualified:
                            item = scores[name]
                            item["settled_hypothetical_qualifying_events"] += 1
                            item[{"WIN": "wins", "LOSS": "losses", "PUSH": "pushes"}[result]] += 1
                            item["standardized_realized_units"] += _profit(result, decision["american_odds"])
            for name in scores:
                scores[name]["unique_target_bookmaker_opportunities"] += len(unique[name])
        directory = state / "shadow-decisions"
        if directory.is_symlink() or directory.exists() and not directory.is_dir():
            raise LedgerError("INVALID_SHADOW_DECISION")
        if directory.exists():
            for parent in directory.iterdir():
                if parent.is_symlink() or not parent.is_dir() or not HEX.fullmatch(parent.name):
                    raise LedgerError("INVALID_SHADOW_DECISION")
                for path in parent.iterdir():
                    if path.is_symlink() or not path.is_file() or path.suffix != ".json" or path not in seen_paths:
                        raise LedgerError("INVALID_SHADOW_DECISION")
    for item in scores.values():
        n = item["settled_hypothetical_qualifying_events"]
        item["roi_on_settled_hypothetical_events"] = item["standardized_realized_units"] / n if n else None
    return {**counts, **scores}
