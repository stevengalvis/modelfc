"""Local JSON ledger storage and scoring for live fixture forecasts."""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Iterable
import uuid

from modelfc.evaluation import multiclass_brier_score
from modelfc.forecasts import FixturePrediction, Forecast
from modelfc.matches import Match, MatchResult


SCHEMA_VERSION = 1


class LedgerError(ValueError):
    """Raised when a ledger operation or record is invalid."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_commit_sha() -> str:
    try:
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    sha = result.stdout.strip()
    return sha if sha else "unknown"


def _source_records(paths: Iterable[Path]) -> list[dict[str, str]]:
    records = []
    for path in paths:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise LedgerError(f"could not hash source CSV {path}: {error}") from error
        records.append({"filename": path.name, "sha256": digest})
    return records


def _write_new(path: Path, record: dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(record, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
    except FileExistsError as error:
        raise LedgerError(f"refusing to overwrite existing record: {path}") from error
    except (OSError, ValueError) as error:
        raise LedgerError(f"could not write ledger record {path}: {error}") from error


def _read_json(path: Path, kind: str) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LedgerError(f"unknown forecast ID: {path.stem}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise LedgerError(f"invalid {kind} record {path}: {error}") from error
    if not isinstance(record, dict):
        raise LedgerError(f"invalid {kind} record {path}: expected a JSON object")
    return record


def _validate_forecast(record: dict[str, Any], path: Path) -> None:
    try:
        if record["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        forecast_id = record["forecast_id"]
        uuid.UUID(hex=forecast_id)
        created_at = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            raise ValueError("creation timestamp must include a UTC offset")
        date.fromisoformat(record["fixture"]["date"])
        if not all(
            isinstance(record["fixture"][key], str) and record["fixture"][key]
            for key in ("home_team", "away_team")
        ):
            raise ValueError("fixture team names must be non-empty strings")
        if record["model"]["name"] != "plain Poisson":
            raise ValueError("unexpected model name")
        parameters = record["model"]["parameters"]
        maximum_goals = parameters["maximum_goals"]
        smoothing_matches = parameters["smoothing_matches"]
        if (
            isinstance(maximum_goals, bool)
            or not isinstance(maximum_goals, int)
            or maximum_goals < 0
        ):
            raise ValueError("maximum goals must be a non-negative integer")
        if (
            isinstance(smoothing_matches, bool)
            or not isinstance(smoothing_matches, (int, float))
            or not math.isfinite(smoothing_matches)
            or smoothing_matches <= 0
        ):
            raise ValueError("smoothing matches must be a finite positive number")
        for key in ("expected_home_goals", "expected_away_goals"):
            value = record["prediction"][key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("expected goals must be finite non-negative numbers")
        probabilities = record["prediction"]["probabilities"]
        values = [probabilities[key] for key in ("home", "draw", "away")]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError("probabilities must be numbers")
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise ValueError("probabilities must be finite values between 0 and 1")
        if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("probabilities must sum to 1")
        match_count = record["history"]["match_count"]
        if isinstance(match_count, bool) or not isinstance(match_count, int) or match_count < 0:
            raise ValueError("historical match count must be non-negative")
        if not isinstance(record["sources"], list):
            raise ValueError("sources must be a list")
        for source in record["sources"]:
            if not isinstance(source["filename"], str) or not source["filename"]:
                raise ValueError("source filename must be a non-empty string")
            if (
                not isinstance(source["sha256"], str)
                or len(source["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in source["sha256"])
            ):
                raise ValueError("source SHA-256 must be a lowercase hexadecimal digest")
    except (KeyError, TypeError, ValueError) as error:
        raise LedgerError(f"invalid forecast record {path}: {error}") from error


def load_forecast(ledger_dir: str | Path, forecast_id: str) -> dict[str, Any]:
    """Load and validate one saved forecast by its safe UUID identifier."""

    try:
        normalized_id = uuid.UUID(hex=forecast_id).hex
    except (ValueError, AttributeError) as error:
        raise LedgerError(f"unknown forecast ID: {forecast_id}") from error
    path = Path(ledger_dir) / "forecasts" / f"{normalized_id}.json"
    record = _read_json(path, "forecast")
    _validate_forecast(record, path)
    if record["forecast_id"] != normalized_id:
        raise LedgerError(f"invalid forecast record {path}: forecast ID does not match filename")
    return record


def save_forecast(
    ledger_dir: str | Path,
    prediction: FixturePrediction,
    history_dates: Iterable[Any],
    source_paths: Iterable[str | Path],
    max_goals: int,
    smoothing_matches: float,
) -> tuple[dict[str, Any], Path]:
    """Save an already-calculated prediction without replacing ledger data."""

    ledger = Path(ledger_dir)
    forecast_dir = ledger / "forecasts"
    result_dir = ledger / "results"
    forecast_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    fixture = prediction.fixture
    fixture_key = (fixture.match_date.isoformat(), fixture.home_team, fixture.away_team)
    for path in forecast_dir.glob("*.json"):
        existing = _read_json(path, "forecast")
        _validate_forecast(existing, path)
        other = existing["fixture"]
        if (other["date"], other["home_team"], other["away_team"]) == fixture_key:
            raise LedgerError(
                "a forecast for this fixture already exists "
                f"(forecast ID {existing['forecast_id']}); original was not overwritten"
            )

    dates = sorted(history_dates)
    forecast_id = uuid.uuid4().hex
    record = {
        "schema_version": SCHEMA_VERSION,
        "forecast_id": forecast_id,
        "created_at": _timestamp(),
        "fixture": {
            "date": fixture.match_date.isoformat(),
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
        },
        "model": {
            "name": "plain Poisson",
            "parameters": {
                "smoothing_matches": smoothing_matches,
                "maximum_goals": max_goals,
            },
        },
        "git_commit_sha": _git_commit_sha(),
        "prediction": {
            "expected_home_goals": prediction.expected_home_goals,
            "expected_away_goals": prediction.expected_away_goals,
            "probabilities": {
                "home": prediction.home_win_probability,
                "draw": prediction.draw_probability,
                "away": prediction.away_win_probability,
            },
        },
        "history": {
            "match_count": prediction.historical_match_count,
            "earliest_date": dates[0].isoformat() if dates else None,
            "latest_date": dates[-1].isoformat() if dates else None,
        },
        "sources": _source_records(Path(path) for path in source_paths),
    }
    path = forecast_dir / f"{forecast_id}.json"
    _write_new(path, record)
    return record, path


def _validate_goals(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LedgerError(f"{name} must be a non-negative integer")


def _score_forecast(
    forecast: dict[str, Any], home_goals: int, away_goals: int
) -> tuple[MatchResult, float]:
    fixture = forecast["fixture"]
    outcome = (
        MatchResult.HOME_WIN
        if home_goals > away_goals
        else MatchResult.AWAY_WIN
        if home_goals < away_goals
        else MatchResult.DRAW
    )
    match = Match(
        date.fromisoformat(fixture["date"]),
        fixture["home_team"],
        fixture["away_team"],
        home_goals,
        away_goals,
        outcome,
    )
    probabilities = forecast["prediction"]["probabilities"]
    brier = multiclass_brier_score(
        Forecast(
            match,
            probabilities["home"],
            probabilities["draw"],
            probabilities["away"],
        )
    )
    return outcome, brier


def _validate_result(record: dict[str, Any], forecast: dict[str, Any], path: Path) -> None:
    try:
        if record["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        if record["forecast_id"] != forecast["forecast_id"]:
            raise ValueError("forecast ID mismatch")
        recorded_at = datetime.fromisoformat(record["recorded_at"].replace("Z", "+00:00"))
        if recorded_at.tzinfo is None:
            raise ValueError("recording timestamp must include a UTC offset")
        score = record["final_score"]
        _validate_goals(score["home_goals"], "final home goals")
        _validate_goals(score["away_goals"], "final away goals")
        outcome, brier = _score_forecast(
            forecast, score["home_goals"], score["away_goals"]
        )
        if record["outcome"] != outcome.value:
            raise ValueError("outcome does not agree with final score")
        if record["brier_score"] != brier:
            raise ValueError("Brier score does not agree with saved forecast and result")
    except (KeyError, TypeError, ValueError, LedgerError) as error:
        raise LedgerError(f"invalid result record {path}: {error}") from error


def record_result(
    ledger_dir: str | Path,
    forecast_id: str,
    home_goals: int,
    away_goals: int,
) -> tuple[dict[str, Any], bool]:
    """Record and score a final result; return the record and whether it is new."""

    _validate_goals(home_goals, "final home goals")
    _validate_goals(away_goals, "final away goals")
    forecast = load_forecast(ledger_dir, forecast_id)
    forecast_id = forecast["forecast_id"]
    result_path = Path(ledger_dir) / "results" / f"{forecast_id}.json"
    if result_path.exists():
        existing = _read_json(result_path, "result")
        _validate_result(existing, forecast, result_path)
        score = existing.get("final_score", {})
        if score.get("home_goals") == home_goals and score.get("away_goals") == away_goals:
            return existing, False
        raise LedgerError(
            "a conflicting result is already recorded for forecast ID " + forecast_id
        )

    outcome, brier = _score_forecast(forecast, home_goals, away_goals)
    result = {
        "schema_version": SCHEMA_VERSION,
        "forecast_id": forecast_id,
        "recorded_at": _timestamp(),
        "final_score": {"home_goals": home_goals, "away_goals": away_goals},
        "outcome": outcome.value,
        "brier_score": brier,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    _write_new(result_path, result)
    return result, True


def ledger_summary(ledger_dir: str | Path) -> dict[str, Any]:
    """Return validated forecast/result counts and completed fixture details."""

    ledger = Path(ledger_dir)
    forecasts = []
    forecast_dir = ledger / "forecasts"
    for path in sorted(forecast_dir.glob("*.json")) if forecast_dir.exists() else ():
        record = _read_json(path, "forecast")
        _validate_forecast(record, path)
        forecasts.append(record)

    completed = []
    for forecast in forecasts:
        result_path = ledger / "results" / f"{forecast['forecast_id']}.json"
        if not result_path.exists():
            continue
        result = _read_json(result_path, "result")
        _validate_result(result, forecast, result_path)
        completed.append({"forecast": forecast, "result": result})
    scores = [item["result"]["brier_score"] for item in completed]
    return {
        "ledger": str(ledger),
        "saved": len(forecasts),
        "completed": len(completed),
        "pending": len(forecasts) - len(completed),
        "completed_fixtures": completed,
        "average_brier_score": sum(scores) / len(scores) if scores else None,
    }


def format_summary(summary: dict[str, Any]) -> str:
    """Format a local-ledger summary for terminal output."""

    lines = [
        f"Ledger: {summary['ledger']}",
        f"Saved forecasts: {summary['saved']}",
        f"Completed forecasts: {summary['completed']}",
        f"Pending forecasts: {summary['pending']}",
    ]
    for item in summary["completed_fixtures"]:
        fixture = item["forecast"]["fixture"]
        result = item["result"]
        score = result["final_score"]
        lines.append(
            f"{fixture['date']} {fixture['home_team']} {score['home_goals']}-"
            f"{score['away_goals']} {fixture['away_team']} — Brier {result['brier_score']:.6f}"
        )
    average = summary["average_brier_score"]
    lines.append(
        "Average Brier score (completed forecasts only): "
        + (f"{average:.6f}" if average is not None else "not available")
    )
    return "\n".join(lines)


def _non_negative_int(value: str) -> int:
    try:
        goals = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("goals must be non-negative integers") from error
    if goals < 0:
        raise argparse.ArgumentTypeError("goals must be non-negative integers")
    return goals


def main() -> None:
    """Record results in, or summarize, a local live-forecast ledger."""

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    result_parser = commands.add_parser("record-result", help="record and score a result")
    result_parser.add_argument("--ledger-dir", type=Path, required=True)
    result_parser.add_argument("--forecast-id", required=True)
    result_parser.add_argument("--home-goals", type=_non_negative_int, required=True)
    result_parser.add_argument("--away-goals", type=_non_negative_int, required=True)
    summary_parser = commands.add_parser("summary", help="summarize one local ledger")
    summary_parser.add_argument("--ledger-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "record-result":
            result, created = record_result(
                args.ledger_dir, args.forecast_id, args.home_goals, args.away_goals
            )
            status = "Recorded" if created else "Result already recorded"
            print(f"{status} for forecast {result['forecast_id']}")
            print(f"Brier score: {result['brier_score']:.6f}")
        else:
            print(format_summary(ledger_summary(args.ledger_dir)))
    except LedgerError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
