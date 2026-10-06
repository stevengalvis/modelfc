"""Validated read-only views over prospective corner evidence."""

from datetime import datetime, timezone
import math
import re
from pathlib import Path
from typing import Any

from modelfc.corner_analysis_outcomes import load_outcome_chain_readonly
from modelfc.corner_markets import american_odds_terms
from modelfc.corner_market_intelligence import (
    best_recommendations, market_intelligence_from_snapshot,
)
from modelfc.corner_opportunities import (
    historical_context,
    opportunity_records,
    prediction_observations,
    prediction_records,
    prediction_target_records,
)
from modelfc.ledger_storage import (
    LedgerError, LedgerStorageUnavailable, existing_read_lock, ledger_read_lock,
)


def _time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
    if parsed.tzinfo is None:
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
    if not finite:
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    return value


def _expected_count(value: Any) -> float:
    count = _number(value)
    if count < 0:
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    return count


def _state_directory(state: Path, name: str) -> Path:
    directory = state / name
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    return directory


def _child_directories(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    children = set()
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_dir():
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        children.add(path.name)
    return children


def _outcome_tip(state: Path, prediction: dict[str, Any]) -> dict[str, Any] | None:
    _, tip = load_outcome_chain_readonly(state, prediction["analysis_id"])
    if tip is None:
        return None
    return _validated_outcome(prediction, tip)


def _validated_outcome(prediction: dict[str, Any], tip: dict[str, Any]) -> dict[str, Any]:
    """Validate a selected immutable tip without consulting newer corrections."""
    try:
        fixture = prediction["fixture"]
        outcome_fixture = tip["fixture"]
        if (tip["status"] != "COMPLETED"
                or tip["capture"]["analysis_id"] != prediction["analysis_id"]
                or tip["capture"]["request_hash"] != prediction["capture_reference"]["request_hash"]
                or tip["capture"]["response_hash"] != prediction["capture_reference"]["response_hash"]
                or outcome_fixture["competition"] != fixture["competition"]
                or outcome_fixture["home_team"] != fixture["home_team"]
                or outcome_fixture["away_team"] != fixture["away_team"]
                or outcome_fixture["kickoff_at"] != fixture["kickoff_at"]
                or outcome_fixture["provider"] != fixture["provider"]
                or outcome_fixture["provider_fixture_id"] != fixture["provider_fixture_id"]):
            raise ValueError
        result = tip["result"]
        for field in ("home_corners", "away_corners"):
            if isinstance(result[field], bool) or not isinstance(result[field], int) or result[field] < 0:
                raise ValueError
            _number(result[field])
    except (KeyError, TypeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
    return tip


def _status(kickoff: datetime, outcome: dict[str, Any] | None, now: datetime) -> str:
    if outcome is not None:
        return "SETTLED"
    return "UPCOMING" if kickoff > now else "EXPIRED_UNSETTLED"


def _target_settlement(
    target: dict[str, Any], outcome: dict[str, Any] | None,
) -> tuple[str | None, int | None]:
    if outcome is None or target["status"] != "SUPPORTED":
        return None, None
    try:
        if target["market_type"] != "TEAM_TOTAL" or target["team_side"] not in ("HOME", "AWAY"):
            raise ValueError
        actual = outcome["result"][target["team_side"].lower() + "_corners"]
        line = _number(target["line"])
        direction = target["direction"]
        if direction not in ("OVER", "UNDER"):
            raise ValueError
        settled = (
            "PUSH" if actual == line else
            "WIN" if (actual > line if direction == "OVER" else actual < line) else
            "LOSS"
        )
    except (KeyError, TypeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
    return settled, actual


def _profit(outcome: str | None, american_odds: int) -> float | None:
    if outcome is None:
        return None
    profit, _ = american_odds_terms(american_odds)
    return profit if outcome == "WIN" else -1.0 if outcome == "LOSS" else 0.0


def _empty_inventory() -> dict[str, Any]:
    return {"predictions": [], "opportunities": [], "targets": {}, "detail": None,
            "recommendations": []}


def _movement_summary(snapshots: list[dict[str, Any]], decisive: float) -> dict[str, Any]:
    baseline = snapshots[0]["no_vig_market_probability"]
    later = [item for item in snapshots[1:] if item["no_vig_market_probability"] is not None]
    if not later:
        return {
            "status": "UNAVAILABLE" if len(snapshots) > 1 else "NO_LATER_OBSERVATION",
            "market_change_percentage_points": None,
            "latest_comparable_observation_id": None,
        }
    latest = later[-1]
    initial_distance = abs(decisive - baseline)
    latest_distance = abs(decisive - latest["no_vig_market_probability"])
    return {
        "status": ("TOWARD_ZENO" if latest_distance < initial_distance else
                   "AWAY_FROM_ZENO" if latest_distance > initial_distance else "UNCHANGED"),
        "market_change_percentage_points": (latest["no_vig_market_probability"] - baseline) * 100,
        "latest_comparable_observation_id": latest["observation_id"],
    }


def _retained_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(snapshots) <= 12:
        return snapshots
    # Keep the latest usable comparison even when many newer selected-side
    # prices lack a pair. The browser can then validate the summary against
    # the displayed evidence. Never invent or change a snapshot.
    latest_paired = next((index for index in range(len(snapshots) - 1, 0, -1)
                          if snapshots[index]["no_vig_market_probability"] is not None), None)
    indices = {0, *range(len(snapshots) - 11, len(snapshots))}
    if latest_paired is not None and latest_paired not in indices:
        indices.remove(len(snapshots) - 11)
        indices.add(latest_paired)
    return [snapshots[index] for index in sorted(indices)]


def _opportunity_detail(
    opportunity: dict[str, Any], prediction: dict[str, Any],
    target: dict[str, Any], observation: dict[str, Any],
    observations: list[dict[str, Any]], outcome: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project one qualified event from the already validated locked inventory."""
    offer = opportunity["offer"]
    same_market = lambda item: all(item[key] == offer[key] for key in (
        "bookmaker", "market_type", "team_side", "team", "line",
    ))
    pair = [item for item in observation["selections"] if same_market(item)]
    if (len(pair) != 2 or {item["direction"] for item in pair} != {"OVER", "UNDER"}
            or sum(item["selection_id"] == opportunity["selection_id"] for item in pair) != 1):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    try:
        implied = {item["direction"]: 1 / _number(item["decimal_odds"]) for item in pair}
        total = math.fsum(implied.values())
        if not math.isfinite(total) or total <= 0:
            raise ValueError
        paired = opportunity["paired_implied_probabilities"]
        if (set(paired) != set(implied) or any(
                not math.isclose(_number(paired[key]), implied[key], rel_tol=0, abs_tol=1e-12)
                for key in implied)):
            raise ValueError
        no_vig = {key: value / total for key, value in implied.items()}
        direction = offer["direction"]
        model_win = _number(target["model_probability"])
        push = _number(target["push_probability"])
        decisive = _number(target["decisive_model_probability"])
        policy = opportunity["policy"]
        min_odds = policy["minimum_american_odds"]
        min_edge = _number(policy["minimum_no_vig_edge"])
        edge = _number(opportunity["no_vig_probability_edge"])
        if (type(min_odds) is not int or min_edge < 0 or min_edge > 1
                or policy["no_vig_price_source"] != "decimal_odds"
                or any(not 0 <= value <= 1 for value in (model_win, push, decisive))
                or not math.isclose((1 - push) * decisive, model_win, rel_tol=0, abs_tol=1e-10)
                or not math.isclose(_number(opportunity["model_decisive_probability"]), decisive, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(_number(opportunity["no_vig_market_probability"]), no_vig[direction], rel_tol=0, abs_tol=1e-12)
                or not math.isclose(edge, decisive - no_vig[direction], rel_tol=0, abs_tol=1e-12)
                or offer["american_odds"] < min_odds or edge + 1e-12 < min_edge):
            raise ValueError
        expected_team = _expected_count(prediction["distribution"][offer["team_side"].lower() + "_expected_corners"])
        if not math.isclose(_expected_count(target["expected_corners"]), expected_team, rel_tol=0, abs_tol=1e-12):
            raise ValueError
        for item in pair:
            profit, _ = american_odds_terms(item["american_odds"])
            if not math.isclose(_number(item["decimal_odds"]), 1 + profit, rel_tol=0, abs_tol=0.005):
                raise ValueError
        snapshots = []
        for item in observations:
            if _time(item["retrieved_at_utc"]) >= _time(prediction["fixture"]["kickoff_at"]):
                raise ValueError
            if _time(item["retrieved_at_utc"]) < _time(observation["retrieved_at_utc"]):
                continue
            matches = [selection for selection in item["selections"]
                       if same_market(selection) and selection["direction"] == direction]
            if len(matches) > 1 or (item["observation_id"] == observation["observation_id"]
                                    and len(matches) != 1):
                raise ValueError
            if matches:
                selection = matches[0]
                profit, _ = american_odds_terms(selection["american_odds"])
                decimal_odds = _number(selection["decimal_odds"])
                if decimal_odds <= 1:
                    raise ValueError
                # A later selected quote can outlive its opposite side.  Keep
                # the recorded price, but only compare a complete frozen pair.
                later_pair = [candidate for candidate in item["selections"] if same_market(candidate)]
                comparable = None
                price_consistent = math.isclose(decimal_odds, 1 + profit, rel_tol=0, abs_tol=0.005)
                if len(later_pair) == 2 and {candidate["direction"] for candidate in later_pair} == {"OVER", "UNDER"}:
                    try:
                        prices = {candidate["direction"]: _number(candidate["decimal_odds"]) for candidate in later_pair}
                        if all(price > 1 for price in prices.values()):
                            later_implied = {side: 1 / price for side, price in prices.items()}
                            comparable = later_implied[direction] / math.fsum(later_implied.values())
                            price_consistent = all(math.isclose(
                                prices[candidate["direction"]], 1 + american_odds_terms(candidate["american_odds"])[0],
                                rel_tol=0, abs_tol=0.005,
                            ) for candidate in later_pair)
                    except (KeyError, TypeError, ValueError, ZeroDivisionError):
                        pass
                snapshots.append({
                    "observation_id": item["observation_id"],
                    "retrieved_at_utc": item["retrieved_at_utc"],
                    "bookmaker": selection["bookmaker"], "direction": direction,
                    "line": selection["line"], "american_odds": selection["american_odds"],
                    "decimal_odds": decimal_odds,
                    "no_vig_market_probability": comparable,
                    "price_consistent": price_consistent,
                    "qualifying_observation": item["observation_id"] == observation["observation_id"],
                })
        if not snapshots or not snapshots[0]["qualifying_observation"]:
            raise ValueError
        retained = _retained_snapshots(snapshots)
        if not math.isclose(retained[0]["no_vig_market_probability"], no_vig[direction], rel_tol=0, abs_tol=1e-12):
            raise ValueError
        market = [{
            "direction": item["direction"], "american_odds": item["american_odds"],
            "decimal_odds": _number(item["decimal_odds"]),
            "implied_probability": implied[item["direction"]],
            "no_vig_probability": no_vig[item["direction"]],
            "qualified": item["selection_id"] == opportunity["selection_id"],
        } for item in sorted(pair, key=lambda item: item["direction"])]
        result, actual = _target_settlement(target, outcome)
        if outcome is not None:
            _time(outcome["recorded_at_utc"])
        return {
            "forecast": {
                "expected_team_corners": _expected_count(target["expected_corners"]),
                "expected_home_corners": _expected_count(prediction["distribution"]["home_expected_corners"]),
                "expected_away_corners": _expected_count(prediction["distribution"]["away_expected_corners"]),
                "expected_match_corners": math.fsum((prediction["distribution"]["home_expected_corners"], prediction["distribution"]["away_expected_corners"])),
                "model_probability": model_win, "push_probability": push,
                "decisive_model_probability": decisive,
                "model_name": prediction["model"]["name"], "model_version": prediction["model"]["version"],
                "created_at_utc": prediction["created_at_utc"],
                "materialized_at_utc": target["materialized_at_utc"],
                "latest_history_date": prediction["history"]["latest_history_date"],
                "historical_context": historical_context(prediction),
            },
            "qualification": {
                "minimum_no_vig_edge": min_edge, "minimum_american_odds": min_odds,
                "edge_pass": True, "price_pass": True,
                "policy_version": opportunity["policy_version"],
                "market_type": offer["market_type"], "bookmaker": offer["bookmaker"],
            },
            "market_at_qualification": market,
            "recorded_market": retained,
            "recorded_market_count": len(snapshots),
            "market_movement": _movement_summary(snapshots, decisive),
            "source_observation_id": prediction["source_observation"]["observation_id"],
            "actual_home_corners": None if outcome is None else outcome["result"]["home_corners"],
            "actual_away_corners": None if outcome is None else outcome["result"]["away_corners"],
            "outcome_recorded_at_utc": None if outcome is None else outcome["recorded_at_utc"],
        }
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None


def _contains_prospective_records(state: Path) -> bool:
    # Outcomes also belong to the older analysis workflow.  Only the
    # prospective record families establish that this inventory exists.
    for name in ("predictions", "prediction-targets", "opportunities"):
        directory = _state_directory(state, name)
        if directory.exists() and any(directory.rglob("*.json")):
            return True
    return False


def _locked_inventory(
    state: Path, *, now: datetime, detail_id: str | None = None,
    include_recommendations: bool = False,
) -> dict[str, Any]:
    _state_directory(state, "predictions")
    targets_dir = _state_directory(state, "prediction-targets")
    opportunities_dir = _state_directory(state, "opportunities")
    _state_directory(state, "market-observations")
    _state_directory(state, "analysis-outcomes")

    predictions = prediction_records(state)
    prediction_ids = {item["prediction_id"] for item in predictions}
    if len(prediction_ids) != len(predictions):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    if (_child_directories(targets_dir) - prediction_ids
            or _child_directories(opportunities_dir) - prediction_ids):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    # Observation-only fixtures are valid evidence.  Observations become part
    # of this prediction-facing view only through a validated prediction or
    # opportunity reference below.

    prediction_views = []
    opportunity_views = []
    target_views: dict[str, tuple[dict[str, Any], str | None]] = {}
    detail = None
    seen_opportunities: set[str] = set()
    recommendation_offers = []
    recommendation_metadata = {}
    for prediction in predictions:
        try:
            prediction_id = prediction["prediction_id"]
            fixture = prediction["fixture"]
            kickoff = _time(fixture["kickoff_at"])
            created = _time(prediction["created_at_utc"])
            if created >= kickoff:
                raise ValueError
            source = prediction["source_observation"]
            observations = prediction_observations(state, prediction)
            observation_by_id = {item["observation_id"]: item for item in observations}
            if source["observation_id"] not in observation_by_id:
                raise ValueError
            targets = prediction_target_records(state, prediction_id)
            targets_by_id = {item["target_id"]: item for item in targets}
            if len(targets_by_id) != len(targets):
                raise ValueError
            outcome = _outcome_tip(state, prediction)
            settlement_status = _status(kickoff, outcome, now)
            opportunities = opportunity_records(state, prediction_id)
            if include_recommendations and settlement_status == "UPCOMING":
                intelligence = market_intelligence_from_snapshot(prediction, observations, as_of=now)
                recommendation_offers.extend(intelligence["recommendations"])
                recommendation_metadata[prediction_id] = {
                    "competition": fixture["competition"], "provider": fixture["provider"],
                    "provider_fixture_id": fixture["provider_fixture_id"],
                    "kickoff_utc": fixture["kickoff_at"], "home_team": fixture["home_team"],
                    "away_team": fixture["away_team"],
                    "policy_version": intelligence["qualification_policy"]["version"],
                }
            for target in targets:
                if (target["prediction_id"] != prediction_id
                        or _time(target["prediction_created_at_utc"]) != created
                        or _time(target["materialized_at_utc"]) >= kickoff
                        or target["status"] not in ("SUPPORTED", "UNSUPPORTED")
                        or (target["status"] == "SUPPORTED"
                            and (target["market_type"] != "TEAM_TOTAL"
                                 or target["team_side"] not in ("HOME", "AWAY")))):
                    raise ValueError
                target_outcome, _ = _target_settlement(target, outcome)
                if target["target_id"] in target_views:
                    raise ValueError
                target_views[target["target_id"]] = (target, target_outcome)

            prediction_views.append({
                "prediction_id": prediction_id,
                "source_observation_id": source["observation_id"],
                "created_at_utc": prediction["created_at_utc"],
                "competition": fixture["competition"],
                "provider": fixture["provider"],
                "provider_fixture_id": fixture["provider_fixture_id"],
                "kickoff_utc": fixture["kickoff_at"],
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "model_name": prediction["model"]["name"],
                "model_version": prediction["model"]["version"],
                "expected_home_corners": _expected_count(prediction["distribution"]["home_expected_corners"]),
                "expected_away_corners": _expected_count(prediction["distribution"]["away_expected_corners"]),
                "expected_match_corners": math.fsum((
                    prediction["distribution"]["home_expected_corners"],
                    prediction["distribution"]["away_expected_corners"],
                )),
                "dispersion_size": prediction["distribution"]["dispersion_size"],
                "latest_history_date": prediction["history"]["latest_history_date"],
                "source_data_hashes": prediction["history"]["source_data_hashes"],
                "target_count": len(targets),
                "opportunity_count": len(opportunities),
                "settlement_status": settlement_status,
                "actual_home_corners": None if outcome is None else outcome["result"]["home_corners"],
                "actual_away_corners": None if outcome is None else outcome["result"]["away_corners"],
            })

            for opportunity in opportunities:
                if opportunity["opportunity_id"] in seen_opportunities:
                    raise ValueError
                seen_opportunities.add(opportunity["opportunity_id"])
                target = targets_by_id.get(opportunity["target_id"])
                observation = observation_by_id.get(opportunity["observation_id"])
                if target is None or observation is None:
                    raise ValueError
                selections = [item for item in observation["selections"]
                              if item["selection_id"] == opportunity["selection_id"]]
                if len(selections) != 1:
                    raise ValueError
                selection = selections[0]
                offer_fields = (
                    "bookmaker", "market_type", "team_side", "team", "direction",
                    "line", "american_odds", "decimal_odds", "provider_market_id",
                    "provider_outcome_id",
                )
                if (opportunity["prediction_id"] != prediction_id
                        or opportunity["qualified_at_utc"] != observation["retrieved_at_utc"]
                        or opportunity["offer"] != {key: selection[key] for key in offer_fields}
                        or target["prediction_id"] != prediction_id
                        or target["status"] != "SUPPORTED"
                        or target["market_type"] != "TEAM_TOTAL"
                        or any(target[key] != opportunity["offer"][key]
                               for key in ("market_type", "team_side", "team", "direction", "line"))
                        or opportunity["model_decisive_probability"] != target["decisive_model_probability"]):
                    raise ValueError
                result, actual = _target_settlement(target, outcome)
                american = selection["american_odds"]
                american_odds_terms(american)
                opportunity_views.append({
                    "opportunity_id": opportunity["opportunity_id"],
                    "prediction_id": prediction_id,
                    "target_id": target["target_id"],
                    "observation_id": observation["observation_id"],
                    "provider": fixture["provider"],
                    "provider_fixture_id": fixture["provider_fixture_id"],
                    "competition": fixture["competition"],
                    "kickoff_utc": fixture["kickoff_at"],
                    "home_team": fixture["home_team"],
                    "away_team": fixture["away_team"],
                    "bookmaker": selection["bookmaker"],
                    "market_type": target["market_type"],
                    "team_side": target["team_side"],
                    "team": target["team"],
                    "direction": target["direction"],
                    "line": target["line"],
                    "american_odds": american,
                    "decimal_odds": _number(selection["decimal_odds"]),
                    "qualified_at_utc": opportunity["qualified_at_utc"],
                    "model_decisive_probability": _number(opportunity["model_decisive_probability"]),
                    "no_vig_market_probability": _number(opportunity["no_vig_market_probability"]),
                    "no_vig_probability_edge": _number(opportunity["no_vig_probability_edge"]),
                    "policy_version": opportunity["policy_version"],
                    "settlement_status": settlement_status,
                    "result": result,
                    "actual_team_corners": actual,
                    "realized_profit_units": _profit(result, american),
                })
                if opportunity["opportunity_id"] == detail_id:
                    detail = {**opportunity_views[-1], **_opportunity_detail(
                        opportunity, prediction, target, observation, observations, outcome,
                    )}
        except (KeyError, TypeError, ValueError, OverflowError):
            raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None

    prediction_views.sort(key=lambda item: (
        -_time(item["created_at_utc"]).timestamp(), item["prediction_id"],
    ))
    status_order = {"UPCOMING": 0, "SETTLED": 1, "EXPIRED_UNSETTLED": 2}
    opportunity_views.sort(key=lambda item: (
        status_order[item["settlement_status"]], _time(item["kickoff_utc"]),
        item["qualified_at_utc"], item["opportunity_id"],
    ))
    return {
        "predictions": prediction_views,
        "opportunities": opportunity_views,
        "targets": target_views,
        "detail": detail,
        "recommendations": [{
            **recommendation_metadata[offer["prediction_id"]],
            **{key: offer[key] for key in (
                "prediction_id", "target_id", "market_type", "team_side", "team",
                "direction", "line", "bookmaker", "american_odds", "decimal_odds",
                "retrieved_at_utc", "observation_age_seconds", "availability_checked_at_utc",
                "model_probability", "push_probability", "decisive_model_probability",
                "sportsbook_implied_probability", "no_vig_market_probability",
                "no_vig_probability_edge", "expected_profit", "qualified",
            )},
        } for offer in best_recommendations(recommendation_offers)],
    }


def _inventory(state_dir: str | Path, *, now: datetime | None = None,
               detail_id: str | None = None, include_recommendations: bool = False) -> dict[str, Any]:
    state = Path(state_dir)
    if not state.exists():
        return _empty_inventory()
    if state.is_symlink() or not state.is_dir():
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _contains_prospective_records(state):
        return _empty_inventory()
    runner_lock = state / "prospective" / "runner.lock"
    if not runner_lock.exists() or not (state / ".lock").exists():
        raise LedgerStorageUnavailable("prospective read boundary is unavailable")
    # Match the writer order: the runner owns its lock for the complete run and
    # individual immutable publications use the state lock beneath it.
    with existing_read_lock(runner_lock):
        with ledger_read_lock(state):
            return _locked_inventory(state, now=now, detail_id=detail_id,
                                     include_recommendations=include_recommendations)


def read_recommendations(state_dir: str | Path) -> list[dict[str, Any]]:
    """Current actionable offers from one validated runner/ledger snapshot.

    A single response-wide timestamp and the intelligence engine's default
    300-second retrieval limit apply. All evidence is validated under the same
    locks as the existing public prospective views; no targets are published.
    """
    return _inventory(state_dir, include_recommendations=True)["recommendations"]


def read_predictions(state_dir: str | Path) -> list[dict[str, Any]]:
    return _inventory(state_dir)["predictions"]


def read_opportunities(state_dir: str | Path) -> list[dict[str, Any]]:
    return _inventory(state_dir)["opportunities"]


def read_opportunity(state_dir: str | Path, opportunity_id: str) -> dict[str, Any]:
    if not isinstance(opportunity_id, str) or not re.fullmatch(r"[0-9a-f]{32}", opportunity_id):
        raise LedgerError("UNKNOWN_OPPORTUNITY")
    detail = _inventory(state_dir, detail_id=opportunity_id)["detail"]
    if detail is None:
        raise LedgerError("UNKNOWN_OPPORTUNITY")
    return detail


def read_performance(state_dir: str | Path) -> dict[str, Any]:
    inventory = _inventory(state_dir)
    predictions = inventory["predictions"]
    settled_predictions = [item for item in predictions if item["settlement_status"] == "SETTLED"]
    team_errors, total_errors = [], []
    for item in settled_predictions:
        try:
            for side in ("home", "away"):
                team_errors.append(_number(item[f"actual_{side}_corners"]
                                           - item[f"expected_{side}_corners"]))
            actual_total = _number(item["actual_home_corners"] + item["actual_away_corners"])
            total_errors.append(_number(actual_total - item["expected_match_corners"]))
        except OverflowError:
            raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None

    def errors_summary(errors: list[float]) -> tuple[float | None, float | None, float | None]:
        if not errors:
            return None, None, None
        n = len(errors)
        try:
            return (_number(math.fsum(abs(error) for error in errors) / n),
                    _number(math.sqrt(math.fsum(_number(error * error) for error in errors) / n)),
                    _number(math.fsum(errors) / n))
        except OverflowError:
            raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None

    team_mae, team_rmse, team_bias = errors_summary(team_errors)
    total_mae, total_rmse, total_bias = errors_summary(total_errors)
    targets = list(inventory["targets"].values())
    supported = [item for item in targets
                 if item[0]["status"] == "SUPPORTED"
                 and item[0]["market_type"] == "TEAM_TOTAL"]
    settled_targets = [item for item in supported if item[1] is not None]
    scored = []
    for target, result in supported:
        # These values were materialized before kickoff. Never regenerate them
        # using today's model, including for pushed selections.
        try:
            probability = _number(target["decisive_model_probability"])
            win = _number(target["model_probability"])
            push = _number(target["push_probability"])
        except (KeyError, TypeError, ValueError):
            raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
        if (not all(0 <= value <= 1 for value in (probability, win, push))
                or win + push > 1 + 1e-12
                or 1 - push <= 0
                or not math.isclose(probability, win / (1 - push), rel_tol=0, abs_tol=1e-8)):
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        if result is None:
            continue
        if result in ("WIN", "LOSS"):
            scored.append((probability, int(result == "WIN")))
        elif result != "PUSH":
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    # Half-open buckets except the final bucket, which includes p=1.
    boundaries = (0.0, 0.5, 0.6, 0.7, 0.8, 1.0)
    calibration = []
    for index, (lower, upper) in enumerate(zip(boundaries, boundaries[1:])):
        members = [(p, y) for p, y in scored
                   if lower <= p and (p < upper or index == len(boundaries) - 2 and p <= upper)]
        calibration.append({
            "lower_bound": lower, "upper_bound": upper, "sample_count": len(members),
            "mean_predicted_probability": None if not members else math.fsum(p for p, _ in members) / len(members),
            "observed_win_rate": None if not members else sum(y for _, y in members) / len(members),
        })
    # Evaluation-only clipping at exact boundaries; persisted probabilities
    # remain untouched. log1p avoids cancellation for probabilities near 0.
    log_floor = 1e-15
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    for item in predictions:
        key = (item["model_name"], item["model_version"])
        if not all(isinstance(part, str) and part.strip() for part in key):
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        row = versions.setdefault(key, {"model_name": key[0], "model_version": key[1],
                                        "total_prediction_runs": 0, "settled_prediction_runs": 0})
        row["total_prediction_runs"] += 1
        row["settled_prediction_runs"] += item["settlement_status"] == "SETTLED"
    opportunities = inventory["opportunities"]
    settled = [item for item in opportunities if item["result"] is not None]
    decisive = [item for item in settled if item["result"] in ("WIN", "LOSS")]
    wins = sum(item["result"] == "WIN" for item in settled)
    losses = sum(item["result"] == "LOSS" for item in settled)
    pushes = sum(item["result"] == "PUSH" for item in settled)
    return {
        "model_performance": {
            "total_prediction_runs": len(predictions),
            "settled_prediction_runs": len(settled_predictions),
            "settled_team_forecasts": len(team_errors),
            "team_corner_mae": team_mae,
            "team_corner_rmse": team_rmse,
            "team_corner_mean_error": team_bias,
            "match_total_mae": total_mae,
            "match_total_rmse": total_rmse,
            "match_total_mean_error": total_bias,
            "model_versions": [versions[key] for key in sorted(versions)],
            "total_unique_prediction_targets": len(targets),
            "supported_prediction_targets": len(supported),
            "settled_prediction_targets": len(settled_targets),
            "unsettled_supported_prediction_targets": len(supported) - len(settled_targets),
            "probability_targets_scored": len(settled_targets),
            "decisive_probability_targets_scored": len(scored),
            "pushes_excluded_from_decisive_scoring": len(settled_targets) - len(scored),
            "brier_score": None if not scored else math.fsum((p - y) ** 2 for p, y in scored) / len(scored),
            "log_loss": None if not scored else math.fsum(
                -math.log(max(p, log_floor)) if y else
                -math.log1p(-p) if 1 - p >= log_floor else -math.log(log_floor)
                for p, y in scored
            ) / len(scored),
            "calibration": calibration,
        },
        "opportunity_performance": {
            "total_opportunity_events": len(opportunities),
            "settled_opportunities": len(settled),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_rate_excluding_pushes": None if not decisive else wins / len(decisive),
            "realized_profit_units": math.fsum(
                item["realized_profit_units"] for item in settled
            ),
            "roi_on_settled_opportunities": None if not settled else math.fsum(
                item["realized_profit_units"] for item in settled
            ) / len(settled),
            "unresolved_open_opportunities": len(opportunities) - len(settled),
        },
    }
