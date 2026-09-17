"""Truthful V1 corner API capabilities from configured, validated history."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from modelfc.corner_data import (
    CornerDataConfig, configured_history, configured_history_lock,
    configured_history_paths, load_data_config,
)
from modelfc.corner_forecasts import CORNER_FIXTURE_MODELS
from modelfc.matches import Venue


COMPETITION_NAMES = {
    "E0": "Premier League",
    "E1": "Championship",
    "SP1": "La Liga",
    "SP2": "La Liga 2",
    "I1": "Serie A",
    "I2": "Serie B",
    "D1": "Bundesliga",
    "D2": "2. Bundesliga",
    "F1": "Ligue 1",
    "F2": "Ligue 2",
    "P1": "Primeira Liga",
    "T1": "Super Lig",
}

# Promotion to this set requires a documented, leakage-safe historical report
# at 8.5, 9.5, 10.5, and 11.5. The distribution implementation and synthetic
# tests alone are deliberately not treated as production validation.
MATCH_TOTAL_VALIDATED_COMPETITIONS: frozenset[str] = frozenset()
PRODUCTION_MARKETS = ("TEAM_TOTAL",)
PRIORITY_UNCONFIGURED_COMPETITIONS = ("SP2",)


def supported_markets_for(competition: str) -> tuple[str, ...]:
    markets = list(PRODUCTION_MARKETS)
    if competition in MATCH_TOTAL_VALIDATED_COMPETITIONS:
        markets.append("MATCH_TOTAL")
    return tuple(markets)


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _refresh_report(config: CornerDataConfig) -> dict[str, Any] | None:
    path = config.directory / "data" / "corner-refresh" / "status.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        return None
    return value


def _refresh_result(report: dict[str, Any] | None, league: str) -> dict[str, Any] | None:
    if report is None:
        return None
    matches = [
        item for item in report["results"]
        if isinstance(item, dict) and item.get("league") == league
    ]
    return matches[0] if len(matches) == 1 else None


def _base_competition(code: str, configured: bool) -> dict[str, Any]:
    return {
        "code": code,
        "name": COMPETITION_NAMES.get(code, code),
        "provider": "football-data",
        "analysis": False,
        "markets": [],
        "teams": [],
        "teams_by_side": {"HOME": [], "AWAY": []},
        "automatic_refresh": configured,
        "refresh_job_status": "UNVERIFIED",
        "last_refresh_status": None,
        "automatic_settlement": "MANUAL_ONLY",
        "trusted_kickoff_source": None,
        "latest_result_date": None,
        "last_refresh_at": None,
        "stale": True,
        "warnings": [],
    }


def _competition_capability(
    config: CornerDataConfig, code: str, today: date,
    report: dict[str, Any] | None, min_history: int, min_venue_history: int,
) -> dict[str, Any]:
    configured = code in config.leagues
    result = _base_competition(code, configured)
    if not configured:
        result["warnings"].append(_warning(
            "UNCONFIGURED_COMPETITION",
            f"{code} is not enabled in the corner data configuration.",
        ))
        if code == "SP2":
            result["warnings"].append(_warning(
                "UNVERIFIED_CORNER_COVERAGE",
                "La Liga 2 corner history and its refresh path have not been verified.",
            ))
        return result

    refresh = _refresh_result(report, code)
    if refresh is not None and isinstance(report.get("checked_at"), str):
        result["last_refresh_at"] = report["checked_at"]
    if refresh is None:
        result["warnings"].append(_warning(
            "REFRESH_STATUS_UNAVAILABLE",
            "No validated refresh attempt is recorded for this competition.",
        ))
    elif refresh.get("status") == "failed":
        result["last_refresh_status"] = "FAILED"
        result["warnings"].append(_warning(
            "REFRESH_FAILED", str(refresh.get("error") or "The last refresh attempt failed."),
        ))
    elif refresh.get("status") in {"updated", "unchanged"}:
        result["last_refresh_status"] = "SUCCEEDED"

    try:
        with configured_history_lock(config):
            configured_history_paths(config, code)
            observations = configured_history(config, code)
    except ValueError as error:
        result["warnings"].append(_warning("DATA_SOURCE_UNAVAILABLE", str(error)))
        return result
    if not observations:
        result["warnings"].append(_warning(
            "DATA_SOURCE_UNAVAILABLE", "Validated history contains no corner observations.",
        ))
        return result

    latest = max(item.match_date for item in observations)
    teams = sorted({item.team for item in observations})
    result.update({
        "latest_result_date": latest.isoformat(),
        "stale": (today - latest).days > config.max_age_days,
    })
    venue_counts = {
        (team, venue): sum(
            item.team == team and item.venue is venue for item in observations
        )
        for team in teams for venue in (Venue.HOME, Venue.AWAY)
    }
    home_ready = {
        team for team in teams if venue_counts[(team, Venue.HOME)] >= min_venue_history
    }
    away_ready = {
        team for team in teams if venue_counts[(team, Venue.AWAY)] >= min_venue_history
    }
    has_fixture_pair = any(home != away for home in home_ready for away in away_ready)
    if len(observations) >= min_history and has_fixture_pair:
        result["analysis"] = True
        result["markets"] = list(supported_markets_for(code))
        result["teams"] = sorted(home_ready & away_ready)
        result["teams_by_side"] = {
            "HOME": sorted(home_ready), "AWAY": sorted(away_ready),
        }
    else:
        result["warnings"].append(_warning(
            "INSUFFICIENT_HISTORY",
            f"Validated history does not meet the configured gates of {min_history} "
            f"team observations and {min_venue_history} venue matches per team.",
        ))
    if result["stale"]:
        result["warnings"].append(_warning(
            "STALE_DATA",
            f"Latest usable {code} history is {(today - latest).days} days old; "
            f"the configured limit is {config.max_age_days} days.",
        ))
    if code not in MATCH_TOTAL_VALIDATED_COMPETITIONS:
        result["warnings"].append(_warning(
            "MATCH_TOTAL_NOT_VALIDATED",
            "Match-total probabilities require a recorded leakage-safe historical "
            "evaluation at lines 8.5, 9.5, 10.5, and 11.5.",
        ))
    return result


def api_capabilities(
    data_config_path: str | Path, *, today: date | None = None,
    min_history: int = 100, min_venue_history: int = 5,
) -> dict[str, Any]:
    """Build capabilities without treating deploy-time files as code constants."""
    config = load_data_config(Path(data_config_path))
    today = today or datetime.now(timezone.utc).date()
    report = _refresh_report(config)
    codes = [*config.leagues]
    codes.extend(code for code in PRIORITY_UNCONFIGURED_COMPETITIONS if code not in codes)
    competitions = [
        _competition_capability(
            config, code, today, report, min_history, min_venue_history,
        )
        for code in codes
    ]
    match_total_enabled = any(
        item["analysis"] and "MATCH_TOTAL" in item["markets"]
        for item in competitions
    )
    markets = [*PRODUCTION_MARKETS]
    if match_total_enabled:
        markets.append("MATCH_TOTAL")
    return {
        "api_version": "v1",
        "stake": 1.0,
        "models": list(CORNER_FIXTURE_MODELS),
        "markets": markets,
        "market_capabilities": [
            {"market_type": "TEAM_TOTAL", "status": "SUPPORTED", "reason": None},
            {
                "market_type": "MATCH_TOTAL",
                "status": "SUPPORTED" if match_total_enabled else "UNAVAILABLE",
                "reason": None if match_total_enabled else "HISTORICAL_EVALUATION_REQUIRED",
            },
        ],
        "competitions": competitions,
    }
