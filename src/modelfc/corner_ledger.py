"""Append-only corner forecasts, $1 picks, results, and profit summaries."""

import argparse
from datetime import date, datetime, timezone
import math
from pathlib import Path
from typing import Any, Iterable
import uuid

from modelfc.corner_forecasts import CornerFixturePrediction, CornerLineProbability
from modelfc.corner_markets import (
    american_odds_terms as _domain_american_odds_terms,
    price_corner_market,
)
from modelfc.ledger_storage import (
    LedgerError, ensure_directory, git_commit_sha, ledger_lock, read_json_record,
    source_records, utc_timestamp, write_new_record,
)


SCHEMA_VERSION = 1
STAKE = 1.0


def _valid_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} must be in UTC")


def _valid_number(value: Any, label: str, *, minimum: float = 0,
                  maximum: float | None = None) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (maximum is not None and value > maximum)):
        suffix = f" from {minimum} to {maximum}" if maximum is not None else f" at least {minimum}"
        raise ValueError(f"{label} must be a finite number{suffix}")


def _valid_count(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _validate_sources(values: Any) -> None:
    if not isinstance(values, list) or not values:
        raise ValueError("sources must be a non-empty list")
    for source in values:
        if not isinstance(source["filename"], str) or not source["filename"]:
            raise ValueError("source filename must be non-empty text")
        digest = source["sha256"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            raise ValueError("source SHA-256 must be lowercase hexadecimal")


def _forecast_path(ledger: Path, forecast_id: str) -> Path:
    try:
        normalized = uuid.UUID(hex=forecast_id).hex
    except (AttributeError, ValueError) as error:
        raise LedgerError(f"unknown corner forecast ID: {forecast_id}") from error
    return ledger / "forecasts" / f"{normalized}.json"


def _validate_line_records(lines: Any) -> None:
    if not isinstance(lines, list):
        raise ValueError("corner lines must be a list")
    seen = set()
    for item in lines:
        line = item["line"]
        _valid_number(line, "corner line", maximum=1000)
        if line % 0.5 != 0 or line in seen:
            raise ValueError("corner lines must be unique whole or half numbers")
        seen.add(line)
        probabilities = [item[key] for key in ("over", "under", "equal")]
        for value in probabilities:
            _valid_number(value, "line probability", maximum=1)
        if not math.isclose(sum(probabilities), 1, rel_tol=0, abs_tol=1e-12):
            raise ValueError("line probabilities must sum to 1")
        if line % 1 == 0.5 and item["equal"] != 0:
            raise ValueError("half-line equality probability must be zero")


def _validate_team_prediction(team: dict[str, Any], expected_name: str,
                              expected_venue: str) -> None:
    if team["team"] != expected_name or team["venue"] != expected_venue:
        raise ValueError("team prediction identity does not match fixture")
    _valid_number(team["expected_corners"], "expected corners")
    _valid_count(team["historical_match_count"], "team history count")
    _valid_count(team["venue_match_count"], "venue history count")
    date.fromisoformat(team["latest_match_date"])
    date.fromisoformat(team["latest_venue_match_date"])
    _validate_line_records(team["lines"])


def _validate_total_prediction(total: dict[str, Any], prediction: dict[str, Any],
                               model_name: str) -> None:
    _valid_number(total["expected_corners"], "expected match corners")
    expected = (
        prediction["home"]["expected_corners"]
        + prediction["away"]["expected_corners"]
    )
    if total["expected_corners"] != expected:
        raise ValueError("expected match corners must equal the two team means")
    method = ("independent-discrete-convolution"
              if model_name.endswith("negative-binomial") else "poisson-sum")
    if total["method"] != method or total["assumes_independence"] is not True:
        raise ValueError("invalid match-total distribution metadata")
    _validate_line_records(total["lines"])


def _validate_forecast(record: dict[str, Any], path: Path) -> None:
    try:
        if record["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        forecast_id = uuid.UUID(hex=record["forecast_id"]).hex
        if path.name != f"{forecast_id}.json":
            raise ValueError("filename does not match forecast ID")
        _valid_timestamp(record["created_at"], "creation timestamp")
        fixture = record["fixture"]
        date.fromisoformat(fixture["date"])
        for key in ("home_team", "away_team"):
            if not isinstance(fixture[key], str) or not fixture[key]:
                raise ValueError("fixture teams must be non-empty text")
        if fixture["home_team"] == fixture["away_team"]:
            raise ValueError("fixture teams must differ")
        provider = record["provider"]
        if not isinstance(provider["name"], str) or not provider["name"]:
            raise ValueError("provider name must be non-empty text")
        for key in ("country", "league", "competition"):
            if provider[key] is not None and (
                not isinstance(provider[key], str) or not provider[key]
            ):
                raise ValueError(f"provider {key} must be null or non-empty text")
        model = record["model"]
        if model["name"] not in (
            "venue-opponent-negative-binomial", "venue-opponent-poisson",
        ):
            raise ValueError("unsupported corner model")
        parameters = model["parameters"]
        _valid_count(parameters["min_history"], "minimum history")
        _valid_count(parameters["min_venue_history"], "minimum venue history")
        if parameters["min_history"] < 1 or parameters["min_venue_history"] < 1:
            raise ValueError("history gates must be positive")
        _valid_number(parameters["smoothing_matches"], "smoothing matches")
        if parameters["smoothing_matches"] <= 0:
            raise ValueError("smoothing matches must be positive")
        size = parameters["dispersion_size"]
        if model["name"].endswith("negative-binomial"):
            _valid_number(size, "dispersion size")
            if size <= 0:
                raise ValueError("dispersion size must be positive")
        elif size is not None:
            raise ValueError("Poisson forecast must have null dispersion size")
        history = record["history"]
        _valid_count(history["observation_count"], "history observation count")
        _valid_count(history["excluded_observation_count"], "excluded observation count")
        earliest = date.fromisoformat(history["earliest_date"])
        latest = date.fromisoformat(history["latest_date"])
        if earliest > latest or latest >= date.fromisoformat(fixture["date"]):
            raise ValueError("history dates must be ordered and before fixture")
        prediction = record["prediction"]
        _validate_team_prediction(prediction["home"], fixture["home_team"], "home")
        _validate_team_prediction(prediction["away"], fixture["away_team"], "away")
        if "total" in prediction:
            _validate_total_prediction(prediction["total"], prediction, model["name"])
        _validate_sources(record["sources"])
        if not isinstance(record["git_commit_sha"], str) or not record["git_commit_sha"]:
            raise ValueError("git commit SHA must be non-empty text")
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise LedgerError(f"invalid corner forecast record {path}: {error}") from error


def load_corner_forecast(ledger_dir: str | Path, forecast_id: str) -> dict[str, Any]:
    path = _forecast_path(Path(ledger_dir), forecast_id)
    record = read_json_record(path, "corner forecast",
                              f"unknown corner forecast ID: {forecast_id}")
    _validate_forecast(record, path)
    return record


def _all_forecasts(ledger: Path) -> list[dict[str, Any]]:
    directory = ledger / "forecasts"
    records = []
    fixtures = set()
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        record = read_json_record(path, "corner forecast", f"missing record: {path}")
        _validate_forecast(record, path)
        fixture = record["fixture"]
        key = (fixture["date"], fixture["home_team"], fixture["away_team"])
        if key in fixtures:
            raise LedgerError(f"duplicate corner forecast fixture: {key}")
        fixtures.add(key)
        records.append(record)
    return records


def _team_record(team: Any) -> dict[str, Any]:
    return {
        "team": team.team,
        "venue": team.venue.value,
        "expected_corners": team.expected_corners,
        "historical_match_count": team.historical_match_count,
        "venue_match_count": team.venue_match_count,
        "latest_match_date": team.latest_match_date.isoformat(),
        "latest_venue_match_date": team.latest_venue_match_date.isoformat(),
        "lines": [
            {"line": item.line, "over": item.over,
             "under": item.under, "equal": item.equal}
            for item in team.lines
        ],
    }


def _total_record(total: Any) -> dict[str, Any]:
    return {
        "expected_corners": total.expected_corners,
        "method": total.method,
        "assumes_independence": total.assumes_independence,
        "lines": [
            {"line": item.line, "over": item.over,
             "under": item.under, "equal": item.equal}
            for item in total.lines
        ],
    }


def save_corner_forecast(
    ledger_dir: str | Path, prediction: CornerFixturePrediction,
    history_dates: Iterable[date], source_paths: Iterable[str | Path], *,
    provider: str, country: str | None, league: str | None,
    competition: str | None, min_history: int, min_venue_history: int,
) -> tuple[dict[str, Any], Path]:
    """Save the exact model output without recording a bet."""
    ledger = Path(ledger_dir)
    for name in ("forecasts", "picks", "results"):
        ensure_directory(ledger / name, "corner ledger")
    dates = sorted(history_dates)
    fixture = prediction.fixture
    if (len(dates) != prediction.historical_observation_count
            or not dates or any(type(day) is not date or day >= fixture.match_date for day in dates)):
        raise LedgerError("invalid corner forecast history dates")
    forecast_id = uuid.uuid4().hex
    record = {
        "schema_version": SCHEMA_VERSION,
        "forecast_id": forecast_id,
        "created_at": utc_timestamp(),
        "fixture": {"date": fixture.match_date.isoformat(),
                    "home_team": fixture.home_team, "away_team": fixture.away_team},
        "provider": {"name": provider, "country": country,
                     "league": league, "competition": competition},
        "model": {"name": prediction.model, "parameters": {
            "min_history": min_history, "min_venue_history": min_venue_history,
            "smoothing_matches": prediction.smoothing_matches,
            "dispersion_size": prediction.dispersion_size,
        }},
        "git_commit_sha": git_commit_sha(),
        "history": {"observation_count": prediction.historical_observation_count,
                    "excluded_observation_count": prediction.excluded_observation_count,
                    "earliest_date": dates[0].isoformat(),
                    "latest_date": dates[-1].isoformat()},
        "prediction": {"home": _team_record(prediction.home),
                       "away": _team_record(prediction.away),
                       **({"total": _total_record(prediction.total)}
                          if prediction.total is not None else {})},
        "sources": source_records(Path(path) for path in source_paths),
    }
    path = ledger / "forecasts" / f"{forecast_id}.json"
    _validate_forecast(record, path)
    key = (fixture.match_date.isoformat(), fixture.home_team, fixture.away_team)
    with ledger_lock(ledger):
        for existing in _all_forecasts(ledger):
            other = existing["fixture"]
            if (other["date"], other["home_team"], other["away_team"]) == key:
                raise LedgerError(
                    "a corner forecast for this fixture already exists "
                    f"(forecast ID {existing['forecast_id']})"
                )
        write_new_record(path, record)
    return record, path


def american_odds_terms(odds: int) -> tuple[float, float]:
    """Backward-compatible ledger wrapper around shared market math."""
    try:
        return _domain_american_odds_terms(odds)
    except ValueError as error:
        raise LedgerError(str(error)) from error


def _line_for_pick(forecast: dict[str, Any], team_side: str,
                   line: float) -> dict[str, Any]:
    if team_side not in ("home", "away"):
        raise LedgerError("team must be home or away")
    for item in forecast["prediction"][team_side]["lines"]:
        if item["line"] == line:
            return item
    raise LedgerError(f"line {line:g} was not saved for the {team_side} team")


def _pick_values(line_record: dict[str, Any], side: str, odds: int) -> dict[str, float]:
    if side not in ("over", "under"):
        raise LedgerError("side must be over or under")
    try:
        value = price_corner_market(CornerLineProbability(
            float(line_record["line"]), line_record["over"],
            line_record["under"], line_record["equal"],
        ), side, odds)
    except ValueError as error:
        message = str(error)
        if message == "market has no decisive outcomes":
            message = "cannot price a market with 100% model push probability"
        raise LedgerError(message) from error
    return {
        "profit_if_win": value.profit_if_win,
        "implied_probability": value.implied_probability,
        "win_probability": value.model_probability,
        "loss_probability": value.loss_probability,
        "push_probability": value.push_probability,
        "decisive_win_probability": value.decisive_model_probability,
        "decisive_probability_edge": value.probability_edge,
        "expected_profit": value.expected_profit,
    }


def _validate_pick(record: dict[str, Any], forecast: dict[str, Any], path: Path) -> None:
    try:
        if record["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        pick_id = uuid.UUID(hex=record["pick_id"]).hex
        if path.name != f"{pick_id}.json":
            raise ValueError("filename does not match pick ID")
        if record["forecast_id"] != forecast["forecast_id"]:
            raise ValueError("forecast ID mismatch")
        _valid_timestamp(record["created_at"], "pick timestamp")
        market = record["market"]
        line_record = _line_for_pick(forecast, market["team_side"], market["line"])
        team = forecast["prediction"][market["team_side"]]["team"]
        if market["team"] != team:
            raise ValueError("pick team does not match forecast")
        values = _pick_values(line_record, market["side"], record["american_odds"])
        if record["stake"] != STAKE:
            raise ValueError("stake must be exactly $1")
        for key, value in values.items():
            if record["value"][key] != value:
                raise ValueError(f"saved {key} does not match forecast and odds")
    except (AttributeError, KeyError, TypeError, ValueError, LedgerError) as error:
        raise LedgerError(f"invalid corner pick record {path}: {error}") from error


def _all_picks(ledger: Path, forecasts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for path in sorted((ledger / "picks").glob("*.json")) if (ledger / "picks").exists() else ():
        record = read_json_record(path, "corner pick", f"missing record: {path}")
        forecast = forecasts.get(record.get("forecast_id"))
        if forecast is None:
            raise LedgerError(f"corner pick references unknown forecast: {path}")
        _validate_pick(record, forecast, path)
        records.append(record)
    return records


def record_corner_pick(
    ledger_dir: str | Path, forecast_id: str, team_side: str,
    line: float, side: str, american_odds: int,
) -> tuple[dict[str, Any], Path]:
    """Record one actual selection at a fixed $1 stake."""
    ledger = Path(ledger_dir)
    forecast = load_corner_forecast(ledger, forecast_id)
    if (ledger / "results" / f"{forecast['forecast_id']}.json").exists():
        raise LedgerError("cannot record a pick after the fixture result")
    line_record = _line_for_pick(forecast, team_side, line)
    values = _pick_values(line_record, side, american_odds)
    pick_id = uuid.uuid4().hex
    team = forecast["prediction"][team_side]["team"]
    record = {
        "schema_version": SCHEMA_VERSION, "pick_id": pick_id,
        "forecast_id": forecast["forecast_id"], "created_at": utc_timestamp(),
        "market": {"team_side": team_side, "team": team,
                   "line": float(line), "side": side},
        "american_odds": american_odds, "stake": STAKE, "value": values,
    }
    directory = ledger / "picks"
    ensure_directory(directory, "corner pick directory")
    path = directory / f"{pick_id}.json"
    _validate_pick(record, forecast, path)
    with ledger_lock(ledger):
        forecasts = {item["forecast_id"]: item for item in _all_forecasts(ledger)}
        for existing in _all_picks(ledger, forecasts):
            market = existing["market"]
            if (existing["forecast_id"] == forecast["forecast_id"]
                    and market["team_side"] == team_side and market["line"] == line):
                raise LedgerError("a pick for this forecast, team, and line already exists")
        if (ledger / "results" / f"{forecast['forecast_id']}.json").exists():
            raise LedgerError("cannot record a pick after the fixture result")
        write_new_record(path, record)
    return record, path


def _validate_result(record: dict[str, Any], forecast: dict[str, Any], path: Path) -> None:
    try:
        if record["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        if record["forecast_id"] != forecast["forecast_id"]:
            raise ValueError("forecast ID mismatch")
        _valid_timestamp(record["recorded_at"], "result timestamp")
        _valid_count(record["home_corners"], "home corners")
        _valid_count(record["away_corners"], "away corners")
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise LedgerError(f"invalid corner result record {path}: {error}") from error


def record_corner_result(
    ledger_dir: str | Path, forecast_id: str,
    home_corners: int, away_corners: int,
) -> tuple[dict[str, Any], bool]:
    ledger = Path(ledger_dir)
    forecast = load_corner_forecast(ledger, forecast_id)
    try:
        _valid_count(home_corners, "home corners")
        _valid_count(away_corners, "away corners")
    except ValueError as error:
        raise LedgerError(str(error)) from error
    path = ledger / "results" / f"{forecast['forecast_id']}.json"
    result = {"schema_version": SCHEMA_VERSION,
              "forecast_id": forecast["forecast_id"],
              "recorded_at": utc_timestamp(), "home_corners": home_corners,
              "away_corners": away_corners}
    ensure_directory(path.parent, "corner result directory")
    _validate_result(result, forecast, path)
    with ledger_lock(ledger):
        if path.exists():
            existing = read_json_record(path, "corner result", f"missing record: {path}")
            _validate_result(existing, forecast, path)
            if ((existing["home_corners"], existing["away_corners"])
                    == (home_corners, away_corners)):
                return existing, False
            raise LedgerError("a conflicting corner result is already recorded")
        write_new_record(path, result)
    return result, True


def _settle(pick: dict[str, Any], result: dict[str, Any]) -> tuple[str, float]:
    market = pick["market"]
    actual = result[f"{market['team_side']}_corners"]
    if actual == market["line"]:
        return "push", 0.0
    won = actual > market["line"] if market["side"] == "over" else actual < market["line"]
    return ("win", pick["value"]["profit_if_win"]) if won else ("loss", -STAKE)


def corner_ledger_summary(ledger_dir: str | Path) -> dict[str, Any]:
    ledger = Path(ledger_dir)
    forecasts_list = _all_forecasts(ledger)
    forecasts = {item["forecast_id"]: item for item in forecasts_list}
    picks = _all_picks(ledger, forecasts)
    results = {}
    for forecast_id, forecast in forecasts.items():
        path = ledger / "results" / f"{forecast_id}.json"
        if path.exists():
            result = read_json_record(path, "corner result", f"missing record: {path}")
            _validate_result(result, forecast, path)
            results[forecast_id] = result
    settled = []
    for pick in picks:
        result = results.get(pick["forecast_id"])
        if result is not None:
            outcome, profit = _settle(pick, result)
            settled.append({"pick": pick, "forecast": forecasts[pick["forecast_id"]],
                            "result": result, "outcome": outcome, "profit": profit})
    profit = math.fsum(item["profit"] for item in settled)
    settled_stake = len(settled) * STAKE
    return {
        "ledger": str(ledger), "saved_forecasts": len(forecasts),
        "completed_forecasts": len(results),
        "pending_forecasts": len(forecasts) - len(results),
        "picks": len(picks), "settled_picks": len(settled),
        "open_picks": len(picks) - len(settled),
        "wins": sum(item["outcome"] == "win" for item in settled),
        "losses": sum(item["outcome"] == "loss" for item in settled),
        "pushes": sum(item["outcome"] == "push" for item in settled),
        "settled_stake": settled_stake, "profit": profit,
        "roi": profit / settled_stake if settled_stake else None,
        "recorded_expected_profit": math.fsum(pick["value"]["expected_profit"] for pick in picks),
        "settlements": settled,
    }


def format_corner_ledger_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"Ledger: {summary['ledger']}",
        f"Forecasts: {summary['saved_forecasts']} saved; "
        f"{summary['completed_forecasts']} completed; {summary['pending_forecasts']} pending",
        f"Picks: {summary['picks']} total; {summary['settled_picks']} settled; "
        f"{summary['open_picks']} open",
        f"Record: {summary['wins']}-{summary['losses']}-{summary['pushes']} (W-L-P)",
        f"Settled stake: ${summary['settled_stake']:.2f}",
        f"Profit: ${summary['profit']:+.2f}",
        "ROI: " + (f"{summary['roi']:.2%}" if summary["roi"] is not None else "not available"),
        f"Recorded model expected profit across all picks: "
        f"${summary['recorded_expected_profit']:+.2f}",
    ]
    for item in summary["settlements"]:
        fixture, pick, result = item["forecast"]["fixture"], item["pick"], item["result"]
        market = pick["market"]
        lines.append(
            f"{fixture['date']} {fixture['home_team']} {result['home_corners']}-"
            f"{result['away_corners']} {fixture['away_team']} | {market['team']} "
            f"{market['side'].upper()} {market['line']:g} {pick['american_odds']:+d} | "
            f"{item['outcome'].upper()} ${item['profit']:+.2f}"
        )
    return "\n".join(lines)


def _american_odds(value: str) -> int:
    try:
        odds = int(value)
        american_odds_terms(odds)
        return odds
    except (ValueError, LedgerError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _non_negative_int(value: str) -> int:
    try:
        result = int(value)
        _valid_count(result, "corner count")
        return result
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("record-pick")
    pick.add_argument("--ledger-dir", type=Path, required=True)
    pick.add_argument("--forecast-id", required=True)
    pick.add_argument("--team", choices=("home", "away"), required=True)
    pick.add_argument("--line", type=float, required=True)
    pick.add_argument("--side", choices=("over", "under"), required=True)
    pick.add_argument("--american-odds", type=_american_odds, required=True)
    result = commands.add_parser("record-result")
    result.add_argument("--ledger-dir", type=Path, required=True)
    result.add_argument("--forecast-id", required=True)
    result.add_argument("--home-corners", type=_non_negative_int, required=True)
    result.add_argument("--away-corners", type=_non_negative_int, required=True)
    summary = commands.add_parser("summary")
    summary.add_argument("--ledger-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "record-pick":
            record, path = record_corner_pick(
                args.ledger_dir, args.forecast_id, args.team, args.line,
                args.side, args.american_odds,
            )
            print(f"Recorded $1 pick {record['pick_id']} at {path}")
            print(f"Model expected profit: ${record['value']['expected_profit']:+.4f}")
        elif args.command == "record-result":
            record, created = record_corner_result(
                args.ledger_dir, args.forecast_id,
                args.home_corners, args.away_corners,
            )
            print(("Recorded" if created else "Result already recorded")
                  + f" for forecast {record['forecast_id']}")
        else:
            print(format_corner_ledger_summary(corner_ledger_summary(args.ledger_dir)))
    except LedgerError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
