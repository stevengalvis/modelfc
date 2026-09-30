"""Private prospective comparison of a frozen 180-day Championship candidate.

Shadow records are created only for newly captured analyses. They never enter
opportunity qualification or replace the production prediction.
"""

import argparse
from datetime import date
import json
import math
from pathlib import Path
from typing import Iterable

from modelfc.corner_analysis_store import _canonical_hash, load_analysis_capture
from modelfc.corner_data import (
    configured_history, configured_history_lock, configured_history_paths, load_data_config,
)
from modelfc.corner_opportunities import (
    _timestamp, load_prediction, prediction_id_for_analysis, prediction_target_records,
)
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corners import estimate_negative_binomial_size
from modelfc.ledger_storage import (
    LedgerError, ensure_directory, git_commit_sha, ledger_lock, read_json_record,
    source_records, write_new_record,
)
from modelfc.matches import TeamCornerObservation, Venue


MODEL_NAME = "venue-opponent-time-weighted-negative-binomial"
MODEL_VERSION = "deepfc-cff381c04b0b6b341b4845fded743c7aa2df6f4b-180-day-v1"
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


def store_shadow_from_new_capture(
    state_dir: str | Path, data_config_path: str | Path, analysis_id: str,
) -> tuple[dict, bool]:
    """Freeze the candidate only when the original capture's CSV bytes still match."""
    state = Path(state_dir)
    capture = load_analysis_capture(state, analysis_id)
    production = load_prediction(state, prediction_id_for_analysis(analysis_id))
    response = capture["response"]
    fixture = response["fixture"]
    created = _timestamp(response["created_at"])
    if (fixture["competition"] != "E1" or capture["request"]["competition"] != "E1"
            or created >= _timestamp(fixture["kickoff_at"])
            or production["analysis_id"] != analysis_id
            or production["created_at_utc"] != response["created_at"]):
        raise LedgerError("INVALID_SHADOW_CAPTURE")
    fixture_date = date.fromisoformat(fixture["date"])
    config = load_data_config(Path(data_config_path))
    with configured_history_lock(config):
        paths = configured_history_paths(config, "E1")
        if source_records(paths) != response["forecast"]["source_data_hashes"]:
            raise LedgerError("SHADOW_HISTORY_CHANGED")
        observations = configured_history(config, "E1")
        if source_records(paths) != response["forecast"]["source_data_hashes"]:
            raise LedgerError("SHADOW_HISTORY_CHANGED")
        history = [item for item in observations if item.match_date < fixture_date]
        if (len(history) < 100 or any(sum(
                item.team == team and item.venue is venue for item in history
            ) < 5 for team, venue in ((fixture["home_team"], Venue.HOME),
                                    (fixture["away_team"], Venue.AWAY)))):
            raise LedgerError("SHADOW_HISTORY_INSUFFICIENT")
        home = expected_corners(history, fixture["home_team"], fixture["away_team"],
                                Venue.HOME, fixture_date)
        away = expected_corners(history, fixture["away_team"], fixture["home_team"],
                                Venue.AWAY, fixture_date)
        size = estimate_negative_binomial_size(history)
    if not all(math.isfinite(value) and value > 0 for value in (home, away, size)):
        raise LedgerError("INVALID_SHADOW_DISTRIBUTION")
    payload = {
        "schema_version": 1, "record_type": "shadow_prediction",
        "analysis_id": analysis_id,
        "production_prediction_id": production["prediction_id"],
        "production_prediction_hash": production["record_hash"],
        "created_at_utc": response["created_at"],
        "fixture": production["fixture"],
        "model": {"name": MODEL_NAME, "version": MODEL_VERSION,
                  "zeno_release_sha": git_commit_sha(), "half_life_days": HALF_LIFE_DAYS,
                  "smoothing_matches": SMOOTHING_MATCHES},
        "history": {"source_data_hashes": response["forecast"]["source_data_hashes"],
                    "cutoff_date": fixture["date"]},
        "distribution": {"home_expected_corners": home, "away_expected_corners": away,
                         "dispersion_size": size},
    }
    record = {**payload, "record_hash": _canonical_hash(payload)}
    path = state / "shadow-predictions" / f"{analysis_id}.json"
    ensure_directory(state, "Model FC state directory")
    ensure_directory(path.parent, "shadow prediction directory")
    with ledger_lock(state):
        if path.exists():
            existing = read_json_record(path, "shadow prediction", "INVALID_SHADOW_RECORD")
            if existing != record:
                raise LedgerError("SHADOW_IDEMPOTENCY_CONFLICT")
            return existing, False
        write_new_record(path, record)
    return record, True


def read_shadow(state_dir: str | Path, analysis_id: str) -> dict:
    path = Path(state_dir) / "shadow-predictions" / f"{analysis_id}.json"
    record = read_json_record(path, "shadow prediction", "INVALID_SHADOW_RECORD")
    payload = {key: value for key, value in record.items() if key != "record_hash"}
    if (record.get("schema_version") != 1 or record.get("record_type") != "shadow_prediction"
            or record.get("analysis_id") != analysis_id
            or record.get("record_hash") != _canonical_hash(payload)):
        raise LedgerError("INVALID_SHADOW_RECORD")
    return record


def compare_settled(state_dir: str | Path) -> dict:
    """Score both frozen forecasts on identical observed team-total lines."""
    from modelfc.corner_prospective_read import _outcome_tip

    state = Path(state_dir)
    errors = {"production": [], "shadow": []}
    brier = {"production": [], "shadow": []}
    settled = 0
    directory = state / "shadow-predictions"
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        if path.is_symlink() or not path.is_file():
            raise LedgerError("INVALID_SHADOW_RECORD")
        shadow = read_shadow(state, path.stem)
        production = load_prediction(state, shadow["production_prediction_id"])
        if (production["record_hash"] != shadow["production_prediction_hash"]
                or production["analysis_id"] != shadow["analysis_id"]
                or production["fixture"] != shadow["fixture"]):
            raise LedgerError("INVALID_SHADOW_REFERENCE")
        outcome = _outcome_tip(state, production)
        if outcome is None:
            continue
        settled += 1
        for venue in ("home", "away"):
            actual = outcome["result"][venue + "_corners"]
            for name, record in (("production", production), ("shadow", shadow)):
                expected = record["distribution"][venue + "_expected_corners"]
                errors[name].append(abs(actual - expected))
        for target in prediction_target_records(state, production["prediction_id"]):
            if target["status"] != "SUPPORTED" or target["market_type"] != "TEAM_TOTAL":
                continue
            actual = outcome["result"][target["team_side"].lower() + "_corners"]
            line = target["line"]
            if actual == line:
                continue
            direction = target["direction"]
            observed_win = int(actual > line if direction == "OVER" else actual < line)
            brier["production"].append((target["decisive_model_probability"] - observed_win) ** 2)
            distribution = shadow["distribution"]
            probability = corner_line_probabilities(
                distribution[target["team_side"].lower() + "_expected_corners"], line,
                distribution["dispersion_size"],
            )
            win = probability.over if direction == "OVER" else probability.under
            if probability.equal >= 1:
                raise LedgerError("INVALID_SHADOW_DISTRIBUTION")
            decisive = win / (1 - probability.equal)
            brier["shadow"].append((decisive - observed_win) ** 2)

    return {
        "settled_fixtures": settled,
        "team_forecasts": len(errors["production"]),
        "market_line_targets": len(brier["production"]),
        "production": {
            "team_mae": math.fsum(errors["production"]) / len(errors["production"])
            if errors["production"] else None,
            "mean_brier": math.fsum(brier["production"]) / len(brier["production"])
            if brier["production"] else None,
        },
        "shadow": {
            "team_mae": math.fsum(errors["shadow"]) / len(errors["shadow"])
            if errors["shadow"] else None,
            "mean_brier": math.fsum(brier["shadow"]) / len(brier["shadow"])
            if brier["shadow"] else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compare_settled(args.state_dir), indent=2))


if __name__ == "__main__":
    main()
