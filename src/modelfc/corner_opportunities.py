"""Immutable prospective prediction, market-observation and opportunity records.

The frozen prediction distribution is independent of sportsbook prices.  Later
market lines query that distribution without loading history or rerunning the
fixture model.  Market observations and qualifying opportunities are append-
only companions to the existing analysis capture and outcome evidence.
"""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any
import uuid

from modelfc.corner_analysis_store import _canonical_hash, load_analysis_capture
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corner_market_data import CornerMarketObservation
from modelfc.corner_markets import american_odds_terms
from modelfc.ledger_storage import (
    LedgerError, ensure_directory, ledger_lock, read_json_record, utc_timestamp,
    write_new_record,
)


SCHEMA_VERSION = 1
PREDICTION_RULE_VERSION = "frozen-team-count-distribution-v1"
QUALIFICATION_POLICY_VERSION = "team-total-no-vig-v1"
MINIMUM_AMERICAN_ODDS = -200
MINIMUM_NO_VIG_EDGE = 0.05
WATCHLIST_NO_VIG_EDGE = -0.05
EDGE_COMPARISON_TOLERANCE = 1e-12
# OddsPapi sometimes supplies decimal prices rounded to two places while its
# American integer maps to a repeating decimal.  Nearest-cent rounding can
# differ by at most 0.005; the recorded provider maximum is 0.003333... .
DECIMAL_AMERICAN_ODDS_TOLERANCE = 0.005
PRICE_INCONSISTENCY_REVIEW = "PRICE_INCONSISTENCY_REVIEW"


def _id(namespace: str, value: Any) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"modelfc:{namespace}:{_canonical_hash(value)}").hex


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
    if parsed.tzinfo is None:
        raise LedgerError("INVALID_PROSPECTIVE_RECORD")
    return parsed.astimezone(timezone.utc)


def _record_path(state: Path, kind: str, identity: str, parent: str | None = None) -> Path:
    try:
        normalized = uuid.UUID(hex=identity).hex
        normalized_parent = uuid.UUID(hex=parent).hex if parent is not None else None
    except (AttributeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_ID") from None
    directory = state / kind
    if normalized_parent is not None:
        directory /= normalized_parent
    return directory / f"{normalized}.json"


def _publish(state: Path, path: Path, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    record = json.loads(json.dumps(record, allow_nan=False))
    record["record_hash"] = _canonical_hash(record)
    ensure_directory(state, "Model FC state directory")
    ensure_directory(path.parent, "prospective evidence directory")
    with ledger_lock(state):
        if path.exists():
            existing = read_json_record(path, "prospective", "UNKNOWN_PROSPECTIVE_RECORD")
            if existing != record:
                raise LedgerError("IDEMPOTENCY_CONFLICT")
            return existing, False
        write_new_record(path, record)
    return record, True


def _load(path: Path, expected_kind: str, expected_id: str) -> dict[str, Any]:
    record = read_json_record(path, "prospective", "UNKNOWN_PROSPECTIVE_RECORD")
    try:
        payload = {key: value for key, value in record.items() if key != "record_hash"}
        if (record["schema_version"] != SCHEMA_VERSION
                or record["record_type"] != expected_kind
                or record[expected_kind + "_id"] != uuid.UUID(hex=expected_id).hex
                or record["record_hash"] != _canonical_hash(payload)):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None
    return record


def prediction_id_for_analysis(analysis_id: str) -> str:
    try:
        normalized = uuid.UUID(hex=analysis_id).hex
    except (AttributeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_ID") from None
    return uuid.uuid5(uuid.NAMESPACE_URL, f"modelfc:prediction:{normalized}").hex


def store_prediction_from_capture(state_dir: str | Path, analysis_id: str) -> tuple[dict[str, Any], bool]:
    """Extract an odds-free frozen distribution from a validated analysis."""
    state = Path(state_dir)
    capture = load_analysis_capture(state, analysis_id)
    try:
        request, response = capture["request"], capture["response"]
        prematch, fixture, forecast = request["prematch"], response["fixture"], response["forecast"]
        if request["competition"] != "E1" or prematch["provider"] != "oddspapi":
            raise ValueError
        raw = prematch["fixture"]
        if fixture["competition"] != "E1" or raw["fixtureId"] == "":
            raise ValueError
        created = _timestamp(response["created_at"])
        kickoff = _timestamp(fixture["kickoff_at"])
        if created >= kickoff:
            raise ValueError
        configuration = forecast["configuration"]
        settings = request["configuration"]
        if settings["model"] != forecast["model"]:
            raise ValueError
        distribution = {
            "home_expected_corners": forecast["home_expected_corners"],
            "away_expected_corners": forecast["away_expected_corners"],
            "dispersion_size": configuration["dispersion_size"],
            "match_total_method": configuration["match_total_method"],
        }
        for field in ("home_expected_corners", "away_expected_corners"):
            value = distribution[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError
        size = distribution["dispersion_size"]
        if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float))
                                 or not math.isfinite(size) or size <= 0):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_CAPTURE") from None
    prediction_id = prediction_id_for_analysis(analysis_id)
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "prediction",
        "prediction_id": prediction_id,
        "analysis_id": uuid.UUID(hex=analysis_id).hex,
        "forecast_id": response["forecast_id"],
        "created_at_utc": response["created_at"],
        "fixture": {
            **fixture,
            "provider": "oddspapi",
            "provider_fixture_id": raw["fixtureId"],
        },
        "model": {
            "name": forecast["model"],
            "version": forecast["model_version"],
            "configuration": {
                **settings,
                "dispersion_size": configuration["dispersion_size"],
                "match_total_method": configuration["match_total_method"],
            },
            "prediction_rule_version": PREDICTION_RULE_VERSION,
        },
        "history": {
            "latest_history_date": forecast["latest_history_date"],
            "source_data_hashes": forecast["source_data_hashes"],
        },
        "distribution": distribution,
        "capture_reference": {
            "request_hash": capture["request_hash"],
            "response_hash": capture["response_hash"],
        },
    }
    return _publish(state, _record_path(state, "predictions", prediction_id), record)


def load_prediction(state_dir: str | Path, prediction_id: str) -> dict[str, Any]:
    return _load(_record_path(Path(state_dir), "predictions", prediction_id),
                 "prediction", prediction_id)


def _observation_payload(observation: CornerMarketObservation) -> dict[str, Any]:
    fixture = observation.fixture
    if (fixture.competition != "E1" or fixture.provider != "oddspapi"
            or not fixture.provider_fixture_id):
        raise LedgerError("INVALID_MARKET_OBSERVATION")
    selections = []
    retrieved = set()
    for item in observation.selections:
        request = item.request
        retrieved.add(item.retrieved_at)
        if item.bookmaker not in ("draftkings", "fanduel"):
            raise LedgerError("INVALID_MARKET_OBSERVATION")
        american_odds_terms(request.american_odds)
        if (isinstance(item.decimal_odds, bool)
                or not isinstance(item.decimal_odds, (int, float))
                or not math.isfinite(item.decimal_odds) or item.decimal_odds <= 1):
            raise LedgerError("INVALID_MARKET_OBSERVATION")
        selections.append({
            "selection_id": _id("selection", {
                "bookmaker": item.bookmaker, "market_id": item.market_id,
                "outcome_id": item.outcome_id, "request": asdict(request),
            }),
            "client_market_id": request.client_market_id,
            "bookmaker": item.bookmaker,
            "market_type": request.market_type,
            "team_side": request.team_side,
            "team": observation.team_for(item),
            "direction": request.side,
            "line": request.line,
            "american_odds": request.american_odds,
            "decimal_odds": item.decimal_odds,
            "provider_market_id": item.market_id,
            "provider_outcome_id": item.outcome_id,
            "market_name": item.market_name,
            "main_line": item.main_line,
            "provider_changed_at": item.changed_at,
            "bookmaker_changed_at": item.bookmaker_changed_at,
        })
    if len(retrieved) > 1:
        raise LedgerError("INVALID_MARKET_OBSERVATION")
    retrieved_at = (next(iter(retrieved)) if retrieved else
                    getattr(observation.provenance, "retrieved_at", None))
    if not isinstance(retrieved_at, str):
        raise LedgerError("INVALID_MARKET_OBSERVATION")
    if _timestamp(retrieved_at) >= fixture.kickoff_utc.astimezone(timezone.utc):
        raise LedgerError("INVALID_MARKET_OBSERVATION")
    if len({item["selection_id"] for item in selections}) != len(selections):
        raise LedgerError("INVALID_MARKET_OBSERVATION")
    return {
        "fixture": {
            "competition": fixture.competition,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "kickoff_utc": fixture.kickoff_utc.astimezone(timezone.utc).isoformat(),
            "provider": fixture.provider,
            "provider_fixture_id": fixture.provider_fixture_id,
        },
        "retrieved_at_utc": retrieved_at,
        "availability": observation.availability,
        "selections": selections,
    }


def store_market_observation(
    state_dir: str | Path, observation: CornerMarketObservation,
) -> tuple[dict[str, Any], bool]:
    state = Path(state_dir)
    payload = _observation_payload(observation)
    observation_id = _id("market-observation", payload)
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "observation",
        "observation_id": observation_id,
        **payload,
    }
    path = _record_path(
        state, "market-observations", observation_id,
        _id("fixture", {"provider": observation.fixture.provider,
                         "competition": observation.fixture.competition,
                         "provider_fixture_id": observation.fixture.provider_fixture_id}),
    )
    return _publish(state, path, record)


def store_observation_from_capture(
    state_dir: str | Path, analysis_id: str,
) -> tuple[dict[str, Any], bool]:
    """Create the observation companion for an existing trusted capture."""
    state = Path(state_dir)
    capture = load_analysis_capture(state, analysis_id)
    try:
        request, response = capture["request"], capture["response"]
        prematch, fixture = request["prematch"], response["fixture"]
        raw_fixture = prematch["fixture"]
        if (request["competition"] != "E1" or prematch["provider"] != "oddspapi"
                or fixture["competition"] != "E1"):
            raise ValueError
        selections, retrieved = [], set()
        for raw in prematch["selections"]:
            item = raw["request"]
            retrieved.add(raw["retrieved_at"])
            if raw["bookmaker"] not in ("draftkings", "fanduel"):
                raise ValueError
            american_odds_terms(item["american_odds"])
            decimal_odds = raw["decimal_odds"]
            if (isinstance(decimal_odds, bool)
                    or not isinstance(decimal_odds, (int, float))
                    or not math.isfinite(decimal_odds) or decimal_odds <= 1):
                raise ValueError
            selection = {
                "selection_id": _id("selection", {
                    "bookmaker": raw["bookmaker"], "market_id": raw["market_id"],
                    "outcome_id": raw["outcome_id"], "request": item,
                }),
                "client_market_id": item["client_market_id"],
                "bookmaker": raw["bookmaker"], "market_type": item["market_type"],
                "team_side": item["team_side"],
                "team": (fixture["home_team"] if item["team_side"] == "HOME" else
                         fixture["away_team"] if item["team_side"] == "AWAY" else None),
                "direction": item["side"], "line": item["line"],
                "american_odds": item["american_odds"],
                "decimal_odds": decimal_odds,
                "provider_market_id": raw["market_id"],
                "provider_outcome_id": raw["outcome_id"],
                "market_name": raw["market_name"], "main_line": raw["main_line"],
                "provider_changed_at": raw["changed_at"],
                "bookmaker_changed_at": raw["bookmaker_changed_at"],
            }
            selections.append(selection)
        if len(retrieved) != 1:
            raise ValueError
        retrieved_at = next(iter(retrieved))
        if _timestamp(retrieved_at) >= _timestamp(fixture["kickoff_at"]):
            raise ValueError
        if len({item["selection_id"] for item in selections}) != len(selections):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise LedgerError("INVALID_PROSPECTIVE_CAPTURE") from None
    payload = {
        "fixture": {
            "competition": "E1", "home_team": fixture["home_team"],
            "away_team": fixture["away_team"], "kickoff_utc": fixture["kickoff_at"],
            "provider": "oddspapi", "provider_fixture_id": raw_fixture["fixtureId"],
        },
        "retrieved_at_utc": retrieved_at,
        "availability": prematch["availability"],
        "selections": selections,
    }
    observation_id = _id("market-observation", payload)
    record = {"schema_version": SCHEMA_VERSION, "record_type": "observation",
              "observation_id": observation_id, **payload}
    parent = fixture_record_id("oddspapi", "E1", raw_fixture["fixtureId"])
    return _publish(
        state, _record_path(state, "market-observations", observation_id, parent), record,
    )


def fixture_record_id(provider: str, competition: str, provider_fixture_id: str) -> str:
    return _id("fixture", {"provider": provider, "competition": competition,
                           "provider_fixture_id": provider_fixture_id})


def load_market_observation(
    state_dir: str | Path, fixture_record_id: str, observation_id: str,
) -> dict[str, Any]:
    return _load(_record_path(Path(state_dir), "market-observations", observation_id,
                              fixture_record_id), "observation", observation_id)


def fixture_observations(
    state_dir: str | Path, provider: str, competition: str, provider_fixture_id: str,
) -> list[dict[str, Any]]:
    state = Path(state_dir)
    parent = fixture_record_id(provider, competition, provider_fixture_id)
    directory = state / "market-observations" / parent
    records = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        if path.is_symlink():
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        record = load_market_observation(state, parent, path.stem)
        fixture = record["fixture"]
        if (fixture["provider"] != provider or fixture["competition"] != competition
                or fixture["provider_fixture_id"] != provider_fixture_id):
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        records.append(record)
    return sorted(records, key=lambda item: (item["retrieved_at_utc"], item["observation_id"]))


def target_id(prediction_id: str, market_type: str, team_side: str | None,
              direction: str, line: float) -> str:
    return _id("prediction-target", {
        "prediction_id": uuid.UUID(hex=prediction_id).hex,
        "market_type": market_type,
        "team_side": team_side,
        "direction": direction,
        "line": float(line),
    })


def _target_record(prediction: dict[str, Any], selection: dict[str, Any],
                   materialized_at: str) -> dict[str, Any]:
    prediction_id = prediction["prediction_id"]
    market_type, side = selection["market_type"], selection["team_side"]
    direction, line = selection["direction"], selection["line"]
    identity = target_id(prediction_id, market_type, side, direction, line)
    base = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "target",
        "target_id": identity,
        "prediction_id": prediction_id,
        "market_type": market_type,
        "team_side": side,
        "team": selection["team"],
        "direction": direction,
        "line": line,
        "prediction_created_at_utc": prediction["created_at_utc"],
        "materialized_at_utc": materialized_at,
        "prediction_rule_version": PREDICTION_RULE_VERSION,
    }
    if market_type != "TEAM_TOTAL" or side not in ("HOME", "AWAY"):
        return {**base, "status": "UNSUPPORTED",
                "unsupported_reason": "HISTORICAL_EVALUATION_REQUIRED",
                "model_probability": None, "push_probability": None,
                "decisive_model_probability": None, "expected_corners": None}
    distribution = prediction["distribution"]
    expected = distribution[side.lower() + "_expected_corners"]
    probability = corner_line_probabilities(expected, line, distribution["dispersion_size"])
    win = probability.over if direction == "OVER" else probability.under
    loss = probability.under if direction == "OVER" else probability.over
    decisive_mass = math.fsum((win, loss))
    if decisive_mass == 0:
        return {**base, "status": "UNSUPPORTED",
                "unsupported_reason": "NO_DECISIVE_OUTCOMES",
                "model_probability": win, "push_probability": probability.equal,
                "decisive_model_probability": None, "expected_corners": expected}
    decisive = win / decisive_mass
    return {**base, "status": "SUPPORTED", "unsupported_reason": None,
            "model_probability": win, "push_probability": probability.equal,
            "decisive_model_probability": decisive, "expected_corners": expected}


def materialize_target(
    state_dir: str | Path, prediction: dict[str, Any], selection: dict[str, Any],
    *, materialized_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    state = Path(state_dir)
    materialized_at = materialized_at or utc_timestamp()
    _timestamp(materialized_at)
    record = _target_record(prediction, selection, materialized_at)
    identity = record["target_id"]
    path = _record_path(state, "prediction-targets", identity, prediction["prediction_id"])
    # Materialization time is evidence of when a later line first appeared, but
    # an exact replay must return the original rather than conflict on the clock.
    with ledger_lock(state):
        if path.exists():
            return _load(path, "target", identity), False
    return _publish(state, path, record)


def _paired_selections(observation: dict[str, Any]):
    groups: dict[tuple, dict[str, dict[str, Any]]] = {}
    ambiguous = set()
    for selection in observation["selections"]:
        key = (selection["bookmaker"], selection["market_type"],
               selection["team_side"], selection["line"])
        direction = selection["direction"]
        if direction in groups.setdefault(key, {}):
            ambiguous.add(key)
        groups[key][direction] = selection
    for key, sides in groups.items():
        if key not in ambiguous and set(sides) == {"OVER", "UNDER"}:
            yield key, sides


def _meets_edge_threshold(edge: float, threshold: float) -> bool:
    return edge > threshold or math.isclose(
        edge, threshold, rel_tol=0.0, abs_tol=EDGE_COMPARISON_TOLERANCE,
    )


def _prices_are_consistent(selection: dict[str, Any]) -> bool:
    profit, _ = american_odds_terms(selection["american_odds"])
    american_decimal = 1 + profit
    return math.isclose(
        selection["decimal_odds"], american_decimal, rel_tol=0.0,
        abs_tol=DECIMAL_AMERICAN_ODDS_TOLERANCE,
    )


def assess_observation(
    state_dir: str | Path, prediction: dict[str, Any], observation: dict[str, Any],
) -> dict[str, Any]:
    """Materialize all targets and append only qualifying opportunity events."""
    state = Path(state_dir)
    predicted_fixture, observed_fixture = prediction["fixture"], observation["fixture"]
    if ({"competition": predicted_fixture["competition"],
         "home_team": predicted_fixture["home_team"],
         "away_team": predicted_fixture["away_team"],
         "kickoff_utc": predicted_fixture["kickoff_at"],
         "provider": predicted_fixture["provider"],
         "provider_fixture_id": predicted_fixture["provider_fixture_id"]} != observed_fixture
            or _timestamp(observation["retrieved_at_utc"]) >= _timestamp(predicted_fixture["kickoff_at"])):
        raise LedgerError("PREDICTION_OBSERVATION_MISMATCH")
    targets = {}
    for selection in observation["selections"]:
        target, _ = materialize_target(
            state, prediction, selection,
            materialized_at=observation["retrieved_at_utc"],
        )
        targets[selection["selection_id"]] = target
    inconsistent = {
        selection["selection_id"] for selection in observation["selections"]
        if not _prices_are_consistent(selection)
    }
    created, watchlisted = 0, False
    for _, sides in _paired_selections(observation):
        if any(selection["selection_id"] in inconsistent
               for selection in sides.values()):
            continue
        try:
            implied = {direction: 1 / selection["decimal_odds"]
                       for direction, selection in sides.items()}
            total = math.fsum(implied.values())
            if (not all(math.isfinite(value) and value > 0 for value in implied.values())
                    or not math.isfinite(total) or total <= 0):
                continue
        except (KeyError, TypeError, ZeroDivisionError):
            continue
        for direction, selection in sides.items():
            target = targets[selection["selection_id"]]
            if target["status"] != "SUPPORTED":
                continue
            no_vig = implied[direction] / total
            edge = target["decisive_model_probability"] - no_vig
            eligible_price = selection["american_odds"] >= MINIMUM_AMERICAN_ODDS
            watchlisted |= eligible_price and _meets_edge_threshold(
                edge, WATCHLIST_NO_VIG_EDGE,
            )
            if not eligible_price or not _meets_edge_threshold(
                    edge, MINIMUM_NO_VIG_EDGE):
                continue
            payload = {
                "prediction_id": prediction["prediction_id"],
                "target_id": target["target_id"],
                "observation_id": observation["observation_id"],
                "selection_id": selection["selection_id"],
                "policy_version": QUALIFICATION_POLICY_VERSION,
            }
            opportunity_id = _id("opportunity", payload)
            record = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "opportunity",
                "opportunity_id": opportunity_id,
                "qualified_at_utc": observation["retrieved_at_utc"],
                **payload,
                "policy": {
                    "minimum_american_odds": MINIMUM_AMERICAN_ODDS,
                    "minimum_no_vig_edge": MINIMUM_NO_VIG_EDGE,
                    "no_vig_price_source": "decimal_odds",
                },
                "offer": {key: selection[key] for key in (
                    "bookmaker", "market_type", "team_side", "team", "direction",
                    "line", "american_odds", "decimal_odds", "provider_market_id",
                    "provider_outcome_id",
                )},
                "model_decisive_probability": target["decisive_model_probability"],
                "paired_implied_probabilities": implied,
                "no_vig_market_probability": no_vig,
                "no_vig_probability_edge": edge,
            }
            _, was_created = _publish(
                state, _record_path(state, "opportunities", opportunity_id,
                                    prediction["prediction_id"]), record,
            )
            created += int(was_created)
    return {"opportunities_created": created, "watchlisted": watchlisted,
            "targets": len(targets),
            "review_required_reasons": (
                [PRICE_INCONSISTENCY_REVIEW] if inconsistent else []
            )}


def opportunity_records(state_dir: str | Path, prediction_id: str) -> list[dict[str, Any]]:
    state = Path(state_dir)
    directory = state / "opportunities" / uuid.UUID(hex=prediction_id).hex
    records = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        if path.is_symlink():
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        record = _load(path, "opportunity", path.stem)
        if record["prediction_id"] != uuid.UUID(hex=prediction_id).hex:
            raise LedgerError("INVALID_PROSPECTIVE_RECORD")
        records.append(record)
    return records


def opportunity_views(state_dir: str | Path, prediction_id: str) -> list[dict[str, Any]]:
    """Derive first, best and latest-observed views without rewriting evidence."""
    prediction = load_prediction(state_dir, prediction_id)
    fixture = prediction["fixture"]
    observations = fixture_observations(
        state_dir, fixture["provider"], fixture["competition"],
        fixture["provider_fixture_id"],
    )
    kickoff = _timestamp(fixture["kickoff_at"])
    offers: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for observation in observations:
        if _timestamp(observation["retrieved_at_utc"]) >= kickoff:
            continue
        for selection in observation["selections"]:
            identity = target_id(
                prediction_id, selection["market_type"], selection["team_side"],
                selection["direction"], selection["line"],
            )
            offers.setdefault((identity, selection["bookmaker"]), []).append(
                (observation, selection),
            )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for opportunity in opportunity_records(state_dir, prediction_id):
        key = (opportunity["target_id"], opportunity["offer"]["bookmaker"])
        grouped.setdefault(key, []).append(opportunity)
    views = []
    for key, events in sorted(grouped.items()):
        events.sort(key=lambda item: (item["qualified_at_utc"], item["opportunity_id"]))
        best = max(events, key=lambda item: (
            item["offer"]["decimal_odds"], item["qualified_at_utc"], item["opportunity_id"],
        ))
        observed = sorted(offers.get(key, ()), key=lambda item: (
            item[0]["retrieved_at_utc"], item[0]["observation_id"], item[1]["selection_id"],
        ))
        latest = None if not observed else {
            "observation_id": observed[-1][0]["observation_id"],
            "selection_id": observed[-1][1]["selection_id"],
            "retrieved_at_utc": observed[-1][0]["retrieved_at_utc"],
            "american_odds": observed[-1][1]["american_odds"],
            "decimal_odds": observed[-1][1]["decimal_odds"],
        }
        views.append({
            "prediction_id": prediction_id, "target_id": key[0], "bookmaker": key[1],
            "first_qualifying_opportunity_id": events[0]["opportunity_id"],
            "best_qualifying_opportunity_id": best["opportunity_id"],
            "latest_observed_pre_kickoff": latest,
        })
    return views
