"""Private prospective comparison of a frozen 180-day Championship candidate.

Missing records may be recovered only from identical prematch inputs. They never enter
opportunity qualification or replace the production prediction.
"""

import argparse
from datetime import date, datetime, timezone
import json
import math
import re
from pathlib import Path
from typing import Iterable

from modelfc.corner_analysis_store import _canonical_hash, load_analysis_capture
from modelfc.corner_data import (
    configured_history, configured_history_lock, configured_history_paths, load_data_config,
)
from modelfc.corner_opportunities import (
    _timestamp, _source_observation, load_prediction, prediction_id_for_analysis,
    prediction_records, prediction_target_records, target_id,
)
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.ledger_storage import (
    LedgerError, ensure_directory, git_commit_sha, ledger_lock, read_json_record,
    source_records, write_new_record, existing_read_lock, ledger_read_lock,
)
from modelfc.matches import TeamCornerObservation, Venue


MODEL_NAME = "venue-opponent-time-weighted-negative-binomial"
MODEL_VERSION = "deepfc-cff381c04b0b6b341b4845fded743c7aa2df6f4b-180-day-v1"
DEEPFC_SOURCE_COMMIT = "cff381c04b0b6b341b4845fded743c7aa2df6f4b"
HALF_LIFE_DAYS = 180
SMOOTHING_MATCHES = 5.0


def expected_corners(
    observations: Iterable[TeamCornerObservation], team: str, opponent: str,
    venue: Venue, prediction_date: date,
) -> float:
    """The frozen DeepFC experiment's venue attack × opposing concession rate."""
    history = tuple(observations)
    if not history or any(item.match_date >= prediction_date for item in history):
        raise ValueError("shadow history must contain only earlier matches")
    opposite = Venue.AWAY if venue is Venue.HOME else Venue.HOME

    def weighted(items: Iterable[TeamCornerObservation], field: str) -> tuple[float, float]:
        weighted_sum = weighted_count = 0.0
        for item in items:
            weight = 0.5 ** ((prediction_date - item.match_date).days / HALF_LIFE_DAYS)
            weighted_sum += weight * getattr(item, field)
            weighted_count += weight
        return weighted_sum, weighted_count

    league_sum, league_count = weighted(
        (item for item in history if item.venue is venue), "corners_for",
    )
    attack_sum, attack_count = weighted(
        (item for item in history if item.team == team and item.venue is venue),
        "corners_for",
    )
    allowed_sum, allowed_count = weighted(
        (item for item in history if item.team == opponent and item.venue is opposite),
        "corners_against",
    )
    league_rate = (league_sum + SMOOTHING_MATCHES) / (league_count + SMOOTHING_MATCHES)
    attack_rate = (attack_sum + SMOOTHING_MATCHES * league_rate) / (attack_count + SMOOTHING_MATCHES)
    allowed_rate = (allowed_sum + SMOOTHING_MATCHES * league_rate) / (allowed_count + SMOOTHING_MATCHES)
    return attack_rate * allowed_rate / league_rate


def _now():
    return datetime.now(timezone.utc)


def dispersion(history):
    """Frozen DeepFC NB2 alpha arithmetic; alpha <= 1e-12 uses Poisson."""
    values = [item.corners_for for item in history]
    if len(values) < 2:
        return 0.0
    total = sum(values)
    mean = total / len(values)
    if mean <= 0:
        return 0.0
    variance = (sum(value ** 2 for value in values) - total ** 2 / len(values)) / (len(values) - 1)
    return max(0.0, (variance - mean) / mean ** 2)


def _number(value, *, minimum=0, maximum=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < minimum or maximum is not None and value > maximum):
        raise LedgerError("INVALID_SHADOW_RECORD")
    return value


def _probabilities(target):
    win = _number(target["model_probability"], maximum=1)
    push = _number(target["push_probability"], maximum=1)
    decisive = _number(target["decisive_model_probability"], maximum=1)
    if (push >= 1 or win + push > 1 + 1e-12
            or not math.isclose(win, (1 - push) * decisive, rel_tol=0, abs_tol=1e-10)
            or not float(target["line"]).is_integer() and push != 0):
        raise LedgerError("INVALID_SHADOW_PROBABILITY")


def _path(state, analysis_id):
    if not isinstance(analysis_id, str) or re.fullmatch(r"[0-9a-f]{32}", analysis_id) is None:
        raise LedgerError("INVALID_SHADOW_ID")
    directory = state / "shadow-predictions"
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise LedgerError("INVALID_SHADOW_RECORD")
    return directory / (analysis_id + ".json")


def _context_unchecked(state, analysis_id):
    """Prove capture, odds-free prediction, source observation and target links."""
    capture = load_analysis_capture(state, analysis_id)
    production = load_prediction(state, prediction_id_for_analysis(analysis_id))
    request, response = capture["request"], capture["response"]
    fixture, forecast = response["fixture"], response["forecast"]
    pm = request["prematch"]
    expected_fixture = {**fixture, "provider": pm["provider"],
                        "provider_fixture_id": pm["fixture"]["fixtureId"]}
    if (fixture["competition"] != "E1" or request["competition"] != "E1"
            or pm["provider"] != "oddspapi" or production["fixture"] != expected_fixture
            or production["analysis_id"] != analysis_id
            or production["forecast_id"] != response["forecast_id"]
            or production["created_at_utc"] != response["created_at"]
            or production["capture_reference"] != {
                "request_hash": capture["request_hash"], "response_hash": capture["response_hash"]}
            or production["model"]["name"] != forecast["model"]
            or production["model"]["version"] != forecast["model_version"]
            or request["configuration"]["model"] != forecast["model"]
            or any(production["model"]["configuration"][key] != value
                   for key, value in request["configuration"].items())
            or any(production["distribution"][key] != forecast[key]
                   for key in ("home_expected_corners", "away_expected_corners"))
            or any(production["distribution"][key] != forecast["configuration"][key]
                   for key in ("dispersion_size", "match_total_method"))
            or production["history"] != {"latest_history_date": forecast["latest_history_date"],
                                        "source_data_hashes": forecast["source_data_hashes"]}
            or _timestamp(response["created_at"]) >= _timestamp(fixture["kickoff_at"])
            or date.fromisoformat(fixture["date"]) != _timestamp(fixture["kickoff_at"]).date()):
        raise LedgerError("INVALID_SHADOW_REFERENCE")
    source = _source_observation(state, production)
    targets = prediction_target_records(state, production["prediction_id"])
    by_id = {target["target_id"]: target for target in targets}
    initial = {}
    for selection in source["selections"]:
        if selection["market_type"] != "TEAM_TOTAL" or selection["team_side"] not in ("HOME", "AWAY"):
            continue
        identity = target_id(production["prediction_id"], "TEAM_TOTAL", selection["team_side"],
                             selection["direction"], selection["line"])
        target = by_id.get(identity)
        if target is None:
            raise LedgerError("SHADOW_TARGET_MISSING")
        if target["status"] != "SUPPORTED":
            continue
        if (target["prediction_id"] != production["prediction_id"]
                or target["prediction_created_at_utc"] != production["created_at_utc"]
                or target["materialized_at_utc"] != source["retrieved_at_utc"]
                or any(target[key] != selection[key] for key in
                       ("market_type", "team_side", "team", "direction", "line"))
                or target["expected_corners"] != production["distribution"][
                    target["team_side"].lower() + "_expected_corners"]):
            raise LedgerError("INVALID_SHADOW_REFERENCE")
        _probabilities(target)
        initial[identity] = target
    return capture, production, [initial[key] for key in sorted(initial)]


def _context(state, analysis_id):
    try:
        return _context_unchecked(state, analysis_id)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise LedgerError("INVALID_SHADOW_REFERENCE") from None


def _validate(record, analysis_id):
    fields = {"schema_version", "record_type", "analysis_id", "production_prediction_id",
              "production_prediction_hash", "capture_reference", "source_observation",
              "prediction_created_at_utc", "created_at_utc", "fixture", "model", "history",
              "distribution", "targets", "record_hash"}
    try:
        if (not isinstance(record, dict) or set(record) != fields
                or type(record["schema_version"]) is not int or record["schema_version"] != 2
                or record["record_type"] != "shadow_prediction"
                or record["analysis_id"] != analysis_id
                or record["production_prediction_id"] != prediction_id_for_analysis(analysis_id)
                or record["record_hash"] != _canonical_hash({k: v for k, v in record.items() if k != "record_hash"})):
            raise ValueError
        model = record["model"]
        if (set(model) != {"family", "competition", "name", "version", "deepfc_source_commit",
                          "zeno_release_sha", "half_life_days", "smoothing_matches",
                          "min_history", "min_venue_history", "probability_rule"}
                or model != _model(model["zeno_release_sha"])
                or re.fullmatch(r"[0-9a-f]{40}", model["zeno_release_sha"]) is None):
            raise ValueError
        fixture = record["fixture"]
        if (fixture["competition"] != "E1" or fixture["provider"] != "oddspapi"
                or not _timestamp(record["prediction_created_at_utc"]) <= _timestamp(record["created_at_utc"]) < _timestamp(fixture["kickoff_at"])):
            raise ValueError
        history = record["history"]
        if (set(history) != {"source_data_hashes", "cutoff_date", "latest_history_date"}
                or history["cutoff_date"] != fixture["date"]
                or date.fromisoformat(history["latest_history_date"]) >= date.fromisoformat(fixture["date"])
                or not isinstance(history["source_data_hashes"], list) or not history["source_data_hashes"]):
            raise ValueError
        for source in history["source_data_hashes"]:
            if (set(source) != {"filename", "sha256"} or re.fullmatch(r"E1_[0-9]{4}\.csv", source["filename"]) is None
                    or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None):
                raise ValueError
        distribution = record["distribution"]
        if set(distribution) != {"home_expected_corners", "away_expected_corners", "dispersion", "dispersion_size"}:
            raise ValueError
        for side in ("home", "away"):
            if _number(distribution[side + "_expected_corners"]) <= 0:
                raise ValueError
        alpha = _number(distribution["dispersion"])
        size = distribution["dispersion_size"]
        if size != (None if math.isclose(alpha, 0, abs_tol=1e-12) else 1 / alpha):
            raise ValueError
        targets = record["targets"]
        if not isinstance(targets, list) or [t["production_target_id"] for t in targets] != sorted({t["production_target_id"] for t in targets}):
            raise ValueError
        for target in targets:
            if (set(target) != {"production_target_id", "production_target_hash", "market_type", "team_side",
                               "team", "direction", "line", "model_probability", "push_probability", "decisive_model_probability"}
                    or target["market_type"] != "TEAM_TOTAL" or target["team_side"] not in ("HOME", "AWAY")
                    or target["direction"] not in ("OVER", "UNDER")
                    or target["team"] != fixture[target["team_side"].lower() + "_team"]
                    or re.fullmatch(r"[0-9a-f]{64}", target["production_target_hash"]) is None):
                raise ValueError
            line = _number(target["line"], maximum=1000)
            if line * 2 != int(line * 2) or target["production_target_id"] != target_id(
                    record["production_prediction_id"], "TEAM_TOTAL", target["team_side"], target["direction"], line):
                raise ValueError
            _probabilities(target)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise LedgerError("INVALID_SHADOW_RECORD") from None
    return record


def _model(release_sha):
    return {"family": "team_corners", "competition": "E1", "name": MODEL_NAME, "version": MODEL_VERSION,
            "deepfc_source_commit": DEEPFC_SOURCE_COMMIT, "zeno_release_sha": release_sha,
            "half_life_days": HALF_LIFE_DAYS, "smoothing_matches": SMOOTHING_MATCHES,
            "min_history": 100, "min_venue_history": 5, "probability_rule": "zeno-full-count-lines-v1"}


def _reference(record, capture, production, targets):
    if (record["production_prediction_hash"] != production["record_hash"]
            or record["fixture"] != production["fixture"]
            or record["capture_reference"] != production["capture_reference"]
            or record["source_observation"] != production["source_observation"]
            or record["prediction_created_at_utc"] != production["created_at_utc"]
            or record["model"]["zeno_release_sha"] != production["model"]["version"]
            or record["history"] != {**production["history"], "cutoff_date": production["fixture"]["date"]}
            or len(record["targets"]) != len(targets)):
        raise LedgerError("INVALID_SHADOW_REFERENCE")
    for frozen, target in zip(record["targets"], targets):
        if (frozen["production_target_id"] != target["target_id"]
                or frozen["production_target_hash"] != target["record_hash"]
                or any(frozen[key] != target[key] for key in ("market_type", "team_side", "team", "direction", "line"))):
            raise LedgerError("INVALID_SHADOW_REFERENCE")


def store_shadow_from_capture(state_dir, data_config_path, analysis_id, *, clock=None):
    """Create or recover only with identical sources, release and prematch timing.

    Source-observation targets define a fixed paired cohort. Later-only targets
    are counted separately, never retrospectively generated during evaluation.
    """
    clock = clock or _now
    state = Path(state_dir)
    path = _path(state, analysis_id)
    capture, production, targets = _context(state, analysis_id)
    if path.exists() or path.is_symlink():
        return read_shadow(state, analysis_id), False
    release_sha = git_commit_sha()
    if release_sha != production["model"]["version"] or re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise LedgerError("SHADOW_RELEASE_CHANGED")
    kickoff = _timestamp(production["fixture"]["kickoff_at"])
    def before_publish():
        if clock() >= kickoff:
            raise LedgerError("SHADOW_WINDOW_CLOSED")
    before_publish()
    fixture = production["fixture"]
    fixture_date = date.fromisoformat(fixture["date"])
    config = load_data_config(Path(data_config_path))
    with configured_history_lock(config):
        paths = configured_history_paths(config, "E1")
        sources = capture["response"]["forecast"]["source_data_hashes"]
        if source_records(paths) != sources:
            raise LedgerError("SHADOW_HISTORY_CHANGED")
        observations = configured_history(config, "E1")
        if source_records(paths) != sources:
            raise LedgerError("SHADOW_HISTORY_CHANGED")
        history = [item for item in observations if item.match_date < fixture_date]
        if (len(history) < 100 or any(sum(item.team == team and item.venue is venue for item in history) < 5
                for team, venue in ((fixture["home_team"], Venue.HOME), (fixture["away_team"], Venue.AWAY)))):
            raise LedgerError("SHADOW_HISTORY_INSUFFICIENT")
        home = expected_corners(history, fixture["home_team"], fixture["away_team"], Venue.HOME, fixture_date)
        away = expected_corners(history, fixture["away_team"], fixture["home_team"], Venue.AWAY, fixture_date)
        alpha = dispersion(history)
        size = None if math.isclose(alpha, 0, abs_tol=1e-12) else 1 / alpha
    frozen_targets = []
    for target in targets:
        probability = corner_line_probabilities(home if target["team_side"] == "HOME" else away,
                                                target["line"], size)
        win = probability.over if target["direction"] == "OVER" else probability.under
        loss = probability.under if target["direction"] == "OVER" else probability.over
        frozen_targets.append({
            "production_target_id": target["target_id"], "production_target_hash": target["record_hash"],
            **{key: target[key] for key in ("market_type", "team_side", "team", "direction", "line")},
            "model_probability": win, "push_probability": probability.equal,
            "decisive_model_probability": win / math.fsum((win, loss)),
        })
    payload = {
        "schema_version": 2, "record_type": "shadow_prediction", "analysis_id": analysis_id,
        "production_prediction_id": production["prediction_id"], "production_prediction_hash": production["record_hash"],
        "capture_reference": production["capture_reference"], "source_observation": production["source_observation"],
        "prediction_created_at_utc": production["created_at_utc"], "created_at_utc": clock().isoformat(),
        "fixture": fixture, "model": _model(release_sha),
        "history": {**production["history"], "cutoff_date": fixture["date"]},
        "distribution": {"home_expected_corners": home, "away_expected_corners": away,
                         "dispersion": alpha, "dispersion_size": size}, "targets": frozen_targets,
    }
    record = _validate({**payload, "record_hash": _canonical_hash(payload)}, analysis_id)
    ensure_directory(state, "Model FC state directory")
    ensure_directory(path.parent, "private shadow prediction directory")
    with ledger_lock(state):
        if path.exists() or path.is_symlink():
            return read_shadow(state, analysis_id), False
        # No evidence_state: private records never receive modelfc-api ACLs.
        write_new_record(path, record, before_publish=before_publish)
    return record, True


def read_shadow(state_dir, analysis_id):
    state = Path(state_dir)
    record = _validate(read_json_record(_path(state, analysis_id), "shadow prediction", "INVALID_SHADOW_RECORD"), analysis_id)
    _reference(record, *_context(state, analysis_id))
    return record


def compare_settled(state_dir):
    """Read-only paired scoring, consuming frozen probabilities, never history."""
    from modelfc.corner_prospective_read import _outcome_tip
    state = Path(state_dir)
    errors = {"production": [], "shadow": []}
    brier = {"production": [], "shadow": []}
    counts = {"production_predictions": 0, "shadow_predictions": 0, "missing_shadow_predictions": 0,
              "settled_fixtures": 0, "team_forecasts": 0, "market_line_targets": 0,
              "push_targets_excluded": 0, "later_targets_excluded": 0}
    if not state.exists():
        return _report(counts, errors, brier)
    if not (state / "predictions").exists():
        directory = state / "shadow-predictions"
        if directory.is_symlink() or directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise LedgerError("INVALID_SHADOW_REFERENCE")
        return _report(counts, errors, brier)
    with existing_read_lock(state / "prospective" / "runner.lock"), ledger_read_lock(state):
        predictions = prediction_records(state)
        directory = state / "shadow-predictions"
        if directory.is_symlink() or directory.exists() and not directory.is_dir():
            raise LedgerError("INVALID_SHADOW_RECORD")
        paths = list(directory.glob("*.json")) if directory.exists() else []
        by_analysis = {p["analysis_id"]: p for p in predictions}
        for path in paths:
            if path.stem not in by_analysis or path.is_symlink() or not path.is_file():
                raise LedgerError("INVALID_SHADOW_REFERENCE")
        for production in predictions:
            counts["production_predictions"] += 1
            path = _path(state, production["analysis_id"])
            if not path.exists():
                counts["missing_shadow_predictions"] += 1
                continue
            shadow = read_shadow(state, production["analysis_id"])
            counts["shadow_predictions"] += 1
            outcome = _outcome_tip(state, production)
            if outcome is None:
                continue
            counts["settled_fixtures"] += 1
            for side in ("home", "away"):
                actual = outcome["result"][side + "_corners"]
                for name, record in (("production", production), ("shadow", shadow)):
                    errors[name].append(abs(actual - _number(record["distribution"][side + "_expected_corners"])))
            _, _, initial = _context(state, production["analysis_id"])
            targets = {target["target_id"]: target for target in initial}
            counts["later_targets_excluded"] += sum(t["status"] == "SUPPORTED" and t["market_type"] == "TEAM_TOTAL"
                and t["target_id"] not in targets for t in prediction_target_records(state, production["prediction_id"]))
            for frozen in shadow["targets"]:
                target = targets[frozen["production_target_id"]]
                actual = outcome["result"][target["team_side"].lower() + "_corners"]
                if actual == target["line"]:
                    counts["push_targets_excluded"] += 1
                    continue
                win = int(actual > target["line"] if target["direction"] == "OVER" else actual < target["line"])
                brier["production"].append((target["decisive_model_probability"] - win) ** 2)
                brier["shadow"].append((frozen["decisive_model_probability"] - win) ** 2)
    counts["team_forecasts"] = len(errors["production"])
    counts["market_line_targets"] = len(brier["production"])
    return _report(counts, errors, brier)


def _report(counts, errors, brier):
    return {**counts, **{name: {
        "team_mae": math.fsum(errors[name]) / len(errors[name]) if errors[name] else None,
        "mean_brier": math.fsum(brier[name]) / len(brier[name]) if brier[name] else None,
    } for name in ("production", "shadow")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    from modelfc.corner_shadow_decisions import compare_decisions
    print(json.dumps({**compare_settled(args.state_dir),
                      "opportunity_decisions": compare_decisions(args.state_dir)}, indent=2))


if __name__ == "__main__":
    main()
