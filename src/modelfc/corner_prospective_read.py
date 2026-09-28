"""Validated read-only views over prospective corner evidence."""

from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

from modelfc.corner_analysis_outcomes import load_outcome_chain_readonly
from modelfc.corner_markets import american_odds_terms
from modelfc.corner_opportunities import (
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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    return value


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
    return {"predictions": [], "opportunities": [], "targets": {}}


def _contains_prospective_records(state: Path) -> bool:
    # Outcomes also belong to the older analysis workflow.  Only the
    # prospective record families establish that this inventory exists.
    for name in ("predictions", "prediction-targets", "opportunities"):
        directory = _state_directory(state, name)
        if directory.exists() and any(directory.rglob("*.json")):
            return True
    return False


def _locked_inventory(
    state: Path, *, now: datetime,
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
                "expected_home_corners": _number(prediction["distribution"]["home_expected_corners"]),
                "expected_away_corners": _number(prediction["distribution"]["away_expected_corners"]),
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
                if (opportunity["qualified_at_utc"] != observation["retrieved_at_utc"]
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
        except (KeyError, TypeError, ValueError):
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
    }


def _inventory(state_dir: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
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
            return _locked_inventory(state, now=now)


def read_predictions(state_dir: str | Path) -> list[dict[str, Any]]:
    return _inventory(state_dir)["predictions"]


def read_opportunities(state_dir: str | Path) -> list[dict[str, Any]]:
    return _inventory(state_dir)["opportunities"]


def read_opportunity(state_dir: str | Path, opportunity_id: str) -> dict[str, Any]:
    matches = [item for item in read_opportunities(state_dir)
               if item["opportunity_id"] == opportunity_id]
    if len(matches) != 1:
        raise LedgerError("UNKNOWN_OPPORTUNITY")
    return matches[0]


def read_performance(state_dir: str | Path) -> dict[str, Any]:
    inventory = _inventory(state_dir)
    predictions = inventory["predictions"]
    settled_predictions = [item for item in predictions if item["settlement_status"] == "SETTLED"]
    team_errors = [actual - predicted for item in settled_predictions for actual, predicted in (
        (item["actual_home_corners"], item["expected_home_corners"]),
        (item["actual_away_corners"], item["expected_away_corners"]),
    )]
    total_errors = [item["actual_home_corners"] + item["actual_away_corners"]
                    - item["expected_match_corners"] for item in settled_predictions]

    def errors_summary(errors: list[float]) -> tuple[float | None, float | None, float | None]:
        if not errors:
            return None, None, None
        n = len(errors)
        return (math.fsum(abs(error) for error in errors) / n,
                math.sqrt(math.fsum(error * error for error in errors) / n),
                math.fsum(errors) / n)

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
