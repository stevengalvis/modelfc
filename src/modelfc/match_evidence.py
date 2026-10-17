"""Compact saved E1 evidence for trusted callers; no transport or refresh side effects."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from modelfc.btts_market_data import BttsFixture, timestamp
from modelfc.btts_research import read_btts_research
from modelfc.corner_research import inspect_fixture
from modelfc.team_intelligence import BY_SOURCE, MAX_SOURCE_BYTES, parse_source, sample

MAX_OUTPUT_BYTES = 16384
MAX_ROWS = 4


def _age(observed, as_of):
    seconds = (as_of - timestamp(observed)).total_seconds()
    return {"age_seconds": max(0, seconds) if seconds >= 0 else None,
            "state": "FUTURE" if seconds < 0 else "STALE" if seconds > 300 else "RECENT_RECORDED"}


def _same_fixture(saved, fixture):
    return all(saved.get(k) == getattr(fixture, k) for k in
               ("competition", "provider", "provider_fixture_id", "home_team", "away_team")) and timestamp(
                   saved.get("kickoff_utc", saved.get("kickoff_at", ""))) == timestamp(fixture.kickoff_utc)


def match_evidence(state_dir: str | Path, fixture: BttsFixture, *, as_of: datetime,
                   snapshot_id: str | None = None, prediction_id: str | None = None,
                   history_bytes: bytes | None = None) -> dict:
    """Read saved research and explicitly supplied saved season CSV bytes.

    Trusted application code binds state and supplies canonical source team names.
    No fuzzy/provider-name mapping, snapshot creation, pick selection or live odds.
    Integrity/storage failures propagate; absence is never inferred from an error.
    """
    fixture = BttsFixture.model_validate_json(fixture.model_dump_json())
    if as_of.tzinfo is None or (snapshot_id is None) != (prediction_id is None):
        raise ValueError("INVALID_MATCH_EVIDENCE_ARGUMENTS")
    as_of = as_of.astimezone(timezone.utc)
    result = {"schema_version": 1, "fixture": fixture.model_dump(mode="json"),
              "as_of_utc": as_of.isoformat(), "published_pick": {"state": "NOT_READ"},
              "limitations": ["SAVED_RESEARCH_NOT_PUBLISHED_PICKS_OR_CURRENT_ODDS",
                              "NO_BETTING_RECOMMENDATION", "E1_ONLY", "OTHER_MARKETS_UNSUPPORTED"],
              "frozen_corners": {"state": "UNAVAILABLE"},
              "saved_btts": {"state": "UNAVAILABLE", "comparisons": []},
              "descriptive_team_corners": {"state": "UNAVAILABLE"}}
    if snapshot_id is not None:
        detail = inspect_fixture(state_dir, snapshot_id, prediction_id)
        if not _same_fixture(detail["fixture"], fixture):
            result["frozen_corners"] = {"state": "CONFLICTING_FIXTURE"}
        else:
            observations = []
            for obs in detail["observations"][-2:]:
                observations.append({k: obs[k] for k in ("observation_id", "record_hash", "observed_at_utc",
                    "assessment_id", "assessment_hash", "assessment_state", "policy")} | {
                    "freshness": _age(obs["observed_at_utc"], as_of),
                    "recorded_selections": obs["selections"][:MAX_ROWS],
                    "frozen_decisions": obs["decisions"][:MAX_ROWS],
                    "selections_omitted": obs["selections_omitted"] + max(0, len(obs["selections"]) - MAX_ROWS),
                    "decisions_omitted": obs["decisions_omitted"] + max(0, len(obs["decisions"]) - MAX_ROWS)})
            result["frozen_corners"] = {k: detail[k] for k in ("snapshot_id", "prediction_id", "prediction_hash",
                "prediction_created_at_utc", "history", "historical_context", "release_sha")} | {
                "state": "FROZEN_RESEARCH",
                "prediction_is_future": timestamp(detail["prediction_created_at_utc"]) > as_of, "model": detail["champion"]["model"],
                "targets": detail["champion"]["targets"][:MAX_ROWS],
                "targets_omitted": detail["targets_omitted"] + max(0, len(detail["champion"]["targets"]) - MAX_ROWS),
                "observations": observations, "observations_omitted": max(0, len(detail["observations"]) - 2)}
    views = read_btts_research(Path(state_dir), fixture.competition, as_of=as_of)
    same_id = [v for v in views if (v.fixture.provider, v.fixture.provider_fixture_id) ==
               (fixture.provider, fixture.provider_fixture_id)]
    exact = [v for v in same_id if _same_fixture(v.fixture.model_dump(), fixture)]
    result["saved_btts"] = {"state": "CONFLICTING_FIXTURE" if len(exact) != len(same_id) else
        "SAVED_RESEARCH" if exact else "UNAVAILABLE", "comparisons": [v.model_dump(mode="json") for v in exact[:MAX_ROWS]],
        "comparisons_omitted": max(0, len(exact) - MAX_ROWS),
        "scope": "SAVED_INVENTORY_AT_READ_NOT_PART_OF_CORNER_SNAPSHOT"}
    if history_bytes is not None:
        if len(history_bytes) > MAX_SOURCE_BYTES:
            raise ValueError("MATCH_HISTORY_LIMIT")
        # Exclude kickoff day: saved CSV dates cannot establish pre-kickoff timing.
        cutoff = min(as_of.date(), timestamp(fixture.kickoff_utc).date() - timedelta(days=1))
        first_year = as_of.year - (as_of.month < 7)
        season = f"{first_year}/{first_year + 1}"
        kickoff = timestamp(fixture.kickoff_utc)
        fixture_year = kickoff.year - (kickoff.month < 7)
        population = parse_source(history_bytes, as_of.date()) if fixture_year == first_year else {}
        teams = []
        for name, venue in ((fixture.home_team, "HOME"), (fixture.away_team, "AWAY")):
            identity = BY_SOURCE.get(name)
            rows = [m for m in population.get(identity.team_id, []) if m.day <= cutoff] if identity else []
            all_sample = sample(rows).model_dump(mode="json")
            venue_sample = sample([m for m in rows if m.venue == venue]).model_dump(mode="json")
            teams.append({"team": name, "team_id": identity.team_id if identity else None,
                "state": "UNMAPPED_TEAM" if identity is None else "UNAVAILABLE" if not all_sample["n"] else "DESCRIPTIVE",
                "season": all_sample, "venue": venue, "venue_sample": venue_sample,
                "small_sample": venue_sample["n"] < 5,
                "missing_corner_rows": all_sample["completed"] - all_sample["n"],
                "days_since_latest_result": (as_of.date() - rows[-1].day).days if rows else None})
        result["descriptive_team_corners"] = {"state": "DESCRIPTIVE" if fixture_year == first_year else "UNSUPPORTED_SEASON",
            "season": season, "source_observed_at_utc": None, "source_freshness": "UNKNOWN",
            "source_sha256": hashlib.sha256(history_bytes).hexdigest(),
            "cutoff_date": cutoff.isoformat(), "scope": "SUPPLIED_SAVED_CSV_NOT_FROZEN_FORECAST_INPUT",
            "teams": teams if fixture_year == first_year else []}
    encoded = json.dumps(result, allow_nan=False, separators=(",", ":"), ensure_ascii=True).encode()
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("MATCH_EVIDENCE_OUTPUT_LIMIT")
    return json.loads(encoded)
