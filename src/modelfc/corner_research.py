"""Private deterministic E1 research snapshots. No network or public transport.

State is injected by trusted Python callers; the CLI uses MODELFC_STATE_DIR.
Snapshot IDs and reviewed segment enums are the only tool-facing selectors.
"""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, TypedDict

from modelfc.corner_analysis_store import _canonical_hash
from modelfc.corner_analysis_outcomes import _validated_chain
from modelfc.corner_opportunities import (
    _id, fixture_record_id, historical_context, target_id,
    opportunity_records, prediction_observations, prediction_records,
    prediction_target_records,
)
from modelfc.corner_prospective_read import (
    _contains_prospective_records, _locked_inventory, _target_settlement,
    _time, _validated_outcome, _profit,
)
from modelfc.corner_shadow import _probabilities, _reference, _validate, read_shadow, validated_context
from modelfc.corner_shadow_decisions import _assessment, _policy, assessment_from_inputs, validate_policy_stamp
from modelfc.corner_shadow_scoring import decision_report, forecast_report
from modelfc.ledger_storage import (
    LedgerError, existing_read_lock, git_commit_sha, ledger_read_lock, write_new_record,
)

MAX_PREDICTIONS = 1000
MAX_TARGETS = 256
MAX_OBSERVATIONS = 12
MAX_OPPORTUNITIES = 256
MAX_FILES = 20000
MAX_RECORD_BYTES = 2_000_000
MAX_MANIFEST_BYTES = 8_000_000
MAX_RESPONSE_BYTES = 65536
MAX_TOTAL_EVIDENCE_BYTES = 128_000_000
MAX_FIXTURE_TARGETS = 64
MAX_FIXTURE_DECISIONS = 64
LOW_SAMPLE_N = 30  # Descriptive warning only, never a significance threshold.
SEGMENTS = ("venue", "venue_history")
FAMILIES = {"analyses", "predictions", "prediction-targets", "market-observations", "opportunities",
            "analysis-outcomes", "shadow-predictions", "shadow-observation-policies", "shadow-decisions"}
HEX = re.compile(r"[0-9a-f]{32}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
COHORT = "E1-team-corners-all-predictions-paired-frozen-source-targets-v1"
FIELDS = {"schema_version", "record_type", "snapshot_id", "created_at_utc", "release_sha",
          "competition", "family", "cohort", "evidence_cutoff_utc", "entries", "record_hash"}

# Snapshot schema 1 deliberately pins this existing candidate's validation
# contract. A future live candidate must not reinterpret these frozen records.
SHADOW_CONTRACT_V1 = {
    "family": "team_corners", "competition": "E1",
    "name": "venue-opponent-time-weighted-negative-binomial",
    "version": "deepfc-cff381c04b0b6b341b4845fded743c7aa2df6f4b-180-day-v1",
    "deepfc_source_commit": "cff381c04b0b6b341b4845fded743c7aa2df6f4b",
    "half_life_days": 180, "smoothing_matches": 5.0, "min_history": 100,
    "min_venue_history": 5, "probability_rule": "zeno-full-count-lines-v1",
}


class SnapshotReference(TypedDict):
    family: str
    id: str
    parent: str | None
    hash: str


class SnapshotEntry(TypedDict):
    capture: SnapshotReference
    prediction: SnapshotReference
    shadow: SnapshotReference | None
    targets: list[SnapshotReference]
    observations: list[dict[str, SnapshotReference | None]]
    opportunities: list[SnapshotReference]
    outcome_chain: list[SnapshotReference]


class SnapshotInfo(TypedDict):
    snapshot_id: str
    record_hash: str
    created_at_utc: str
    predictions_included: int


class ResearchSnapshot(TypedDict):
    schema_version: int
    record_type: str
    snapshot_id: str
    created_at_utc: str
    release_sha: str
    competition: str
    family: str
    cohort: str
    evidence_cutoff_utc: str | None
    entries: list[SnapshotEntry]
    record_hash: str


class ResearchError(LedgerError):
    """Fixed safe research error; adapters never expose internal exceptions."""


def _identity(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ResearchError("INVALID_RESEARCH_ID")
    return value


def _safe(path):
    # All ancestors matter, including a dangling symlink or FIFO at the leaf.
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink() or parent.exists() and parent != path and not parent.is_dir():
            raise ResearchError("INVALID_RESEARCH_STORAGE")
    return path


def _read(path, maximum=MAX_RECORD_BYTES):
    _safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ResearchError("RESEARCH_LIMIT_EXCEEDED")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw.encode("utf-8")) > maximum:
            raise ResearchError("RESEARCH_LIMIT_EXCEEDED")
        value = json.loads(raw)
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise ResearchError("INVALID_RESEARCH_EVIDENCE")
    return value


def _ref(record, family, identity, parent=None):
    return {"family": family, "id": identity, "parent": parent, "hash": record["record_hash"] if family != "analyses" else _canonical_hash(record)}


def _reference_path(state, ref):
    if not isinstance(ref, dict) or set(ref) != {"family", "id", "parent", "hash"}:
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    family = ref["family"]
    if family not in FAMILIES or not isinstance(ref["hash"], str) or not HASH.fullmatch(ref["hash"]):
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    identity = _identity(ref["id"])
    nested = family in {"prediction-targets", "market-observations", "opportunities", "analysis-outcomes", "shadow-decisions"}
    if nested != (ref["parent"] is not None):
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    return state / family / (_identity(ref["parent"]) if nested else "") / (identity + ".json")


def _resolve(state, ref, family, budget=None):
    if ref["family"] != family:
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    path = _safe(_reference_path(state, ref))
    if budget is not None:
        budget["records"] += 1
        budget["bytes"] += path.stat().st_size
        if budget["records"] > MAX_FILES or budget["bytes"] > MAX_TOTAL_EVIDENCE_BYTES:
            raise ResearchError("RESEARCH_LIMIT_EXCEEDED")
    record = _read(path)
    identity_field = {"analyses": "analysis_id", "shadow-predictions": "analysis_id",
                      "predictions": "prediction_id", "prediction-targets": "target_id",
                      "market-observations": "observation_id", "opportunities": "opportunity_id",
                      "analysis-outcomes": "outcome_id", "shadow-observation-policies": "observation_id",
                      "shadow-decisions": "decision_id"}[family]
    if record[identity_field] != ref["id"]:
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    if family in {"prediction-targets", "opportunities", "shadow-decisions"} and record["prediction_id"] != ref["parent"]:
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    if family == "analysis-outcomes" and record["capture"]["analysis_id"] != ref["parent"]:
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    if family == "market-observations" and fixture_record_id(record["fixture"]["provider"], record["fixture"]["competition"], record["fixture"]["provider_fixture_id"]) != ref["parent"]:
        raise ResearchError("INVALID_RESEARCH_REFERENCE")
    if family == "analyses":
        if _canonical_hash(record) != ref["hash"] or record["request_hash"] != _canonical_hash(record["request"]) or record["response_hash"] != _canonical_hash(record["response"]):
            raise ResearchError("INVALID_RESEARCH_EVIDENCE")
        return record
    if (record.get("record_hash") != ref["hash"] or record["record_hash"] !=
            _canonical_hash({k: v for k, v in record.items() if k != "record_hash"})):
        raise ResearchError("INVALID_RESEARCH_EVIDENCE")
    return record


def _preflight(state):
    # Fail on oversized populations before existing inventory readers enumerate them.
    count = total_bytes = 0
    for family in sorted(FAMILIES | {"analyses"}):
        directory = _safe(state / family)
        if not directory.exists():
            continue
        stack = [(directory, 0)]
        while stack:
            path, depth = stack.pop()
            for child in path.iterdir():
                count += 1
                if count > MAX_FILES:
                    raise ResearchError("RESEARCH_LIMIT_EXCEEDED")
                _safe(child)
                if child.is_dir() and depth < 1:
                    stack.append((child, depth + 1))
                elif not child.is_file() or child.suffix != ".json" or child.stat().st_size > MAX_RECORD_BYTES:
                    raise ResearchError("INVALID_RESEARCH_STORAGE")
                else:
                    total_bytes += child.stat().st_size
                    if total_bytes > MAX_TOTAL_EVIDENCE_BYTES:
                        raise ResearchError("RESEARCH_LIMIT_EXCEEDED")


def _bounded(items, maximum):
    if not isinstance(items, list) or len(items) > maximum:
        raise ResearchError("RESEARCH_LIMIT_EXCEEDED")
    return items


def _collect(state):
    rows, entries, seen_decisions, seen_shadows = [], [], set(), set()
    predictions = _bounded(prediction_records(state), MAX_PREDICTIONS)
    for prediction in predictions:
        pid, aid = prediction["prediction_id"], prediction["analysis_id"]
        if prediction["fixture"]["competition"] != "E1":
            raise ResearchError("UNSUPPORTED_RESEARCH_COHORT")
        targets = _bounded(prediction_target_records(state, pid), MAX_TARGETS)
        shadow_path = state / "shadow-predictions" / (aid + ".json")
        shadow = read_shadow(state, aid) if shadow_path.exists() else None
        if shadow is not None:
            seen_shadows.add(shadow_path)
        from modelfc.corner_analysis_outcomes import load_outcome_chain_readonly
        chain, tip = load_outcome_chain_readonly(state, aid)
        if tip is not None:
            _validated_outcome(prediction, tip)
        # Store a prefix in chain order, not filename or directory order.
        ordered = []
        previous = None
        for _ in chain:
            item = next(r for r in chain if r["supersedes_outcome_id"] == previous)
            ordered.append(item)
            previous = item["outcome_id"]
        observations, refs = [], []
        for obs in _bounded(prediction_observations(state, prediction), MAX_OBSERVATIONS):
            oid = obs["observation_id"]
            parent = fixture_record_id("oddspapi", "E1", prediction["fixture"]["provider_fixture_id"])
            did = _id("shadow-decision", {"prediction_id": pid, "observation_id": oid})
            path = state / "shadow-decisions" / pid / (did + ".json")
            seen_decisions.add(path)
            assessment = _read(path) if path.exists() else None
            stamp = None
            stamp_path = state / "shadow-observation-policies" / (oid + ".json")
            if stamp_path.exists():
                stamp = _policy(state, obs)
            if assessment is not None:
                if shadow is None or stamp is None or assessment != _assessment(state, prediction, obs, shadow, stamp):
                    raise ResearchError("INVALID_RESEARCH_EVIDENCE")
            observations.append({"observation": obs, "assessment": assessment, "policy": stamp})
            refs.append({"observation": _ref(obs, "market-observations", oid, parent),
                         "policy": _ref(stamp, "shadow-observation-policies", oid) if stamp else None,
                         "assessment": _ref(assessment, "shadow-decisions", did, pid) if assessment else None})
        opportunities = _bounded(opportunity_records(state, pid), MAX_OPPORTUNITIES)
        rows.append({"prediction": prediction, "shadow": shadow, "outcome": tip,
                     "targets": targets, "observations": observations, "outcome_chain": ordered, "opportunities": opportunities})
        from modelfc.corner_analysis_store import load_analysis_capture
        capture = load_analysis_capture(state, aid)
        entries.append({"capture": _ref(capture, "analyses", aid), "prediction": _ref(prediction, "predictions", pid),
                        "shadow": _ref(shadow, "shadow-predictions", aid) if shadow else None,
                        "targets": [_ref(t, "prediction-targets", t["target_id"], pid) for t in targets],
                        "observations": refs,
                        "opportunities": [_ref(o, "opportunities", o["opportunity_id"], pid) for o in opportunities],
                        "outcome_chain": [_ref(o, "analysis-outcomes", o["outcome_id"], aid) for o in ordered]})
    for family, seen in (("shadow-decisions", seen_decisions), ("shadow-predictions", seen_shadows)):
        directory = state / family
        if directory.exists() and set(directory.rglob("*.json")) - seen:
            raise ResearchError("INVALID_RESEARCH_EVIDENCE")
    return entries, rows


def _cutoff(rows):
    times = []
    for row in rows:
        times.append(_time(row["prediction"]["created_at_utc"]))
        times.extend(_time(t["materialized_at_utc"]) for t in row["targets"])
        if row["shadow"]:
            times.append(_time(row["shadow"]["created_at_utc"]))
        times.extend(_time(o["recorded_at_utc"]) for o in row["outcome_chain"])
        times.extend(_time(o["qualified_at_utc"]) for o in row["opportunities"])
        for item in row["observations"]:
            times.append(_time(item["observation"]["retrieved_at_utc"]))
            if item["policy"]:
                times.append(_time(item["policy"]["stamped_at_utc"]))
    return max(times).isoformat() if times else None


def create_snapshot(state_dir: str | Path) -> SnapshotInfo:
    """Trusted operator operation; appends only a private reference manifest."""
    return _guard(lambda: _create(Path(state_dir)))


def _create(state):
    _safe(state)
    with ExitStack() as locks:
        runner_lock = _safe(state / "prospective" / "runner.lock")
        state_lock = _safe(state / ".lock")
        if runner_lock.exists():
            locks.enter_context(existing_read_lock(runner_lock))
            if state_lock.exists():
                locks.enter_context(ledger_read_lock(state))
            elif _contains_prospective_records(state):
                raise ResearchError("RESEARCH_EVIDENCE_UNAVAILABLE")
            _preflight(state)
            _locked_inventory(state, now=datetime.now(timezone.utc))
            entries, rows = _collect(state)
        else:
            # Uninitialized empty state: freeze the empty population, never
            # enumerate a concurrently initialized first capture piecemeal.
            _preflight(state)
            if state_lock.exists() or any((state / family).exists() and any((state / family).rglob("*.json")) for family in FAMILIES):
                raise ResearchError("RESEARCH_EVIDENCE_UNAVAILABLE")
            entries, rows = [], []
        created = datetime.now(timezone.utc).isoformat()
        release = git_commit_sha()
        if not re.fullmatch(r"[0-9a-f]{40}", release):
            raise ResearchError("INVALID_RESEARCH_RELEASE")
        payload = {"schema_version": 1, "record_type": "research_snapshot", "created_at_utc": created,
                   "release_sha": release, "competition": "E1", "family": "team_corners", "cohort": COHORT,
                   "evidence_cutoff_utc": _cutoff(rows), "entries": entries}
        if payload["evidence_cutoff_utc"] and _time(payload["evidence_cutoff_utc"]) > _time(created):
            raise ResearchError("INVALID_RESEARCH_TIMESTAMP")
        payload["snapshot_id"] = _id("research-snapshot", payload)
        record = {**payload, "record_hash": _canonical_hash(payload)}
        directory = _safe(state / "research-snapshots")
        if not state.exists():
            state.mkdir(mode=0o700, parents=True)
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.stat().st_uid != os.geteuid() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise ResearchError("INVALID_RESEARCH_STORAGE")
        if len(json.dumps(record).encode()) > MAX_MANIFEST_BYTES:
            raise ResearchError("RESEARCH_LIMIT_EXCEEDED")
        # Private publication deliberately has no evidence_state/API ACL.
        write_new_record(_safe(directory / (payload["snapshot_id"] + ".json")), record)
    return {"snapshot_id": record["snapshot_id"], "record_hash": record["record_hash"],
            "created_at_utc": created, "predictions_included": len(entries)}


def _load(state, identity):
    record = _read(state / "research-snapshots" / (_identity(identity) + ".json"), MAX_MANIFEST_BYTES)
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    if (set(record) != FIELDS or record["schema_version"] != 1 or type(record["schema_version"]) is not int
            or record["record_type"] != "research_snapshot" or record["snapshot_id"] != identity
            or record["competition"] != "E1" or record["family"] != "team_corners" or record["cohort"] != COHORT
            or not re.fullmatch(r"[0-9a-f]{40}", record["release_sha"])
            or record["record_hash"] != _canonical_hash(payload)
            or identity != _id("research-snapshot", {k: v for k, v in payload.items() if k != "snapshot_id"})):
        raise ResearchError("INVALID_RESEARCH_SNAPSHOT")
    created = _time(record["created_at_utc"])
    if created.utcoffset().total_seconds() != 0:
        raise ResearchError("INVALID_RESEARCH_TIMESTAMP")
    rows = []
    budget = {"records": 0, "bytes": 0}
    seen = set()
    for entry in _bounded(record["entries"], MAX_PREDICTIONS):
        if set(entry) != {"capture", "prediction", "shadow", "targets", "observations", "opportunities", "outcome_chain"}:
            raise ResearchError("INVALID_RESEARCH_SNAPSHOT")
        prediction = _resolve(state, entry["prediction"], "predictions", budget)
        pid = prediction["prediction_id"]
        if pid in seen or pid != entry["prediction"]["id"]:
            raise ResearchError("INVALID_RESEARCH_REFERENCE")
        seen.add(pid)
        # Validate frozen source capture/targets with existing domain rules.
        historical_context(prediction)
        shadow = _resolve(state, entry["shadow"], "shadow-predictions", budget) if entry["shadow"] else None
        targets = [_resolve(state, ref, "prediction-targets", budget) for ref in _bounded(entry["targets"], MAX_TARGETS)]
        if len({t["target_id"] for t in targets}) != len(targets):
            raise ResearchError("INVALID_RESEARCH_REFERENCE")
        for target in targets:
            if target["target_id"] != target_id(pid, target["market_type"], target["team_side"], target["direction"], target["line"]):
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
            if target["status"] not in ("SUPPORTED", "UNSUPPORTED"):
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
            if target["prediction_id"] != pid or _time(target["materialized_at_utc"]) >= _time(prediction["fixture"]["kickoff_at"]):
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
            if target["status"] == "SUPPORTED":
                if target["market_type"] != "TEAM_TOTAL" or target["team_side"] not in ("HOME", "AWAY") or target["team"] != prediction["fixture"][target["team_side"].lower() + "_team"]:
                    raise ResearchError("INVALID_RESEARCH_REFERENCE")
                _probabilities(target)
        chain = [_resolve(state, ref, "analysis-outcomes", budget) for ref in _bounded(entry["outcome_chain"], MAX_TARGETS)]
        _, tip = _validated_chain(chain)
        if tip is not None:
            _validated_outcome(prediction, tip)
        observations = []
        for refs in _bounded(entry["observations"], MAX_OBSERVATIONS):
            if set(refs) != {"observation", "policy", "assessment"}:
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
            obs = _resolve(state, refs["observation"], "market-observations", budget)
            expected_fixture = {k: prediction["fixture"][k] for k in ("competition", "home_team", "away_team", "provider", "provider_fixture_id")}
            expected_fixture["kickoff_utc"] = prediction["fixture"]["kickoff_at"]
            if obs["fixture"] != expected_fixture or _time(obs["retrieved_at_utc"]) >= _time(expected_fixture["kickoff_utc"]):
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
            stamp = _resolve(state, refs["policy"], "shadow-observation-policies", budget) if refs["policy"] else None
            assessment = _resolve(state, refs["assessment"], "shadow-decisions", budget) if refs["assessment"] else None
            if stamp is not None:
                validate_policy_stamp(stamp, obs)
            observations.append({"observation": obs, "assessment": assessment, "policy": stamp})
        if len({i["observation"]["observation_id"] for i in observations}) != len(observations):
            raise ResearchError("INVALID_RESEARCH_REFERENCE")
        observations.sort(key=lambda i: (_time(i["observation"]["retrieved_at_utc"]), i["observation"]["observation_id"]))
        source = next(i["observation"] for i in observations if i["observation"]["observation_id"] == prediction["source_observation"]["observation_id"])
        if source["record_hash"] != prediction["source_observation"]["record_hash"]:
            raise ResearchError("INVALID_RESEARCH_REFERENCE")
        capture = _resolve(state, entry["capture"], "analyses", budget)
        _, _, initial = validated_context(capture, prediction, source, targets)
        if shadow is not None:
            _validate(shadow, prediction["analysis_id"], model_contract=SHADOW_CONTRACT_V1)
            _reference(shadow, capture, prediction, initial)
        for item in observations:
            if item["assessment"] is not None and (shadow is None or item["policy"] is None or item["assessment"] != assessment_from_inputs(
                    prediction, item["observation"], shadow, item["policy"], targets, source,
                    [i["observation"] for i in observations], shadow_version=SHADOW_CONTRACT_V1["version"])):
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
        opportunities = []
        for ref in _bounded(entry["opportunities"], MAX_OPPORTUNITIES):
            opportunity = _resolve(state, ref, "opportunities", budget)
            opportunities.append(opportunity)
            if opportunity["prediction_id"] != pid or opportunity["target_id"] not in {t["target_id"] for t in targets}:
                raise ResearchError("INVALID_RESEARCH_REFERENCE")
        rows.append({"prediction": prediction, "shadow": shadow, "targets": targets,
                     "outcome": tip, "observations": observations, "outcome_chain": chain, "opportunities": opportunities})
    if _cutoff(rows) != record["evidence_cutoff_utc"] or record["evidence_cutoff_utc"] and _time(record["evidence_cutoff_utc"]) > created:
        raise ResearchError("INVALID_RESEARCH_TIMESTAMP")
    return record, rows


def _guard(operation):
    try:
        return operation()
    except ResearchError:
        raise
    except (LedgerError, OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, OverflowError):
        raise ResearchError("RESEARCH_EVIDENCE_UNAVAILABLE") from None


def _output(value):
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        raise ResearchError("RESEARCH_OUTPUT_LIMIT")
    return value


def _warnings(n):
    return ["NO_SETTLED_PAIRED_EVIDENCE"] if n == 0 else ["LOW_SAMPLE_DESCRIPTIVE_ONLY"] if n < LOW_SAMPLE_N else []


def read_snapshot(state_dir: str | Path, snapshot_id: str) -> SnapshotInfo:
    """Read bounded snapshot metadata after validating its frozen evidence."""
    def operation():
        manifest, rows = _load(Path(state_dir), snapshot_id)
        return {"snapshot_id": snapshot_id, "record_hash": manifest["record_hash"],
                "created_at_utc": manifest["created_at_utc"], "predictions_included": len(rows)}
    return _guard(operation)


def research_summary(state_dir: str | Path, snapshot_id: str) -> dict[str, Any]:
    def operation():
        manifest, rows = _load(Path(state_dir), snapshot_id)
        forecast = forecast_report(rows)
        decisions = decision_report(rows)
        versions = sorted({(r["prediction"]["model"]["name"], r["prediction"]["model"]["version"]) for r in rows})
        return _output({"snapshot_id": snapshot_id, "created_at_utc": manifest["created_at_utc"],
                        "evidence_cutoff_utc": manifest["evidence_cutoff_utc"], "release_sha": manifest["release_sha"],
                        "competition": "E1", "family": "team_corners", "cohort": COHORT,
                        "coverage": {"predictions_included": len(rows),
                                     "settled_predictions": sum(r["outcome"] is not None for r in rows),
                                     "unsupported_targets": sum(t["status"] != "SUPPORTED" for r in rows for t in r["targets"]),
                                     "legacy_context_unavailable": sum(historical_context(r["prediction"]) is None for r in rows)},
                        "forecast_quality": forecast, "market_decisions": decisions,
                        "champion_versions": [{"name": n, "version": v} for n, v in versions],
                        "shadow_version": SHADOW_CONTRACT_V1["version"],
                        "policy_versions": sorted({i["policy"]["policy"]["version"] for r in rows for i in r["observations"] if i["policy"]}),
                        "warnings": _warnings(forecast["settled_fixtures"]),
                        "limitations": ["PAIRED_FROZEN_SOURCE_TARGETS_ONLY", "SNAPSHOTS_NOT_PLACED_BETS",
                                        "HYPOTHETICAL_ONE_UNIT_EVENT_ROI", "CHAMPION_WATCHLIST_OBSERVATION_SELECTION"]})
    return _guard(operation)


def segment_comparison(state_dir: str | Path, snapshot_id: str, segment: str) -> dict[str, Any]:
    def operation():
        if segment not in SEGMENTS:
            raise ResearchError("UNSUPPORTED_RESEARCH_SEGMENT")
        _, rows = _load(Path(state_dir), snapshot_id)
        groups = {key: [] for key in (("HOME", "AWAY") if segment == "venue" else ("0-9", "10-19", "20+", "UNKNOWN"))}
        for row in rows:
            if row["shadow"] is None:
                continue
            context = historical_context(row["prediction"])
            for side in ("home", "away"):
                n = context[side + "_venue_observations"] if context else None
                key = side.upper() if segment == "venue" else "UNKNOWN" if n is None else "0-9" if n < 10 else "10-19" if n < 20 else "20+"
                groups[key].append({**row, "sides": (side,)})
        result = []
        for key, members in groups.items():
            metrics = forecast_report(members)
            differences = {metric: None if metrics["production"][metric] is None else
                           metrics["shadow"][metric] - metrics["production"][metric] for metric in ("team_mae", "mean_brier")}
            result.append({"segment": key, "paired_team_forecasts": metrics["team_forecasts"],
                           "forecast_quality": metrics, "shadow_minus_champion": differences,
                           "market_decisions": decision_report(members), "warnings": _warnings(metrics["team_forecasts"])})
        return _output({"snapshot_id": snapshot_id, "dimension": segment, "groups": result,
                        "limitations": ["VENUE_ROWS_ARE_NOT_INDEPENDENT_FIXTURES", "NO_SIGNIFICANCE_CLAIM"]})
    return _guard(operation)


def inspect_fixture(state_dir: str | Path, snapshot_id: str, prediction_id: str) -> dict[str, Any]:
    def operation():
        _identity(prediction_id)
        manifest, rows = _load(Path(state_dir), snapshot_id)
        row = next((r for r in rows if r["prediction"]["prediction_id"] == prediction_id), None)
        if row is None:
            raise ResearchError("PREDICTION_NOT_IN_SNAPSHOT")
        p, shadow, outcome = row["prediction"], row["shadow"], row["outcome"]
        targets = []
        for target in sorted(row["targets"], key=lambda t: t["target_id"])[:MAX_FIXTURE_TARGETS]:
            result, actual = _target_settlement(target, outcome)
            targets.append({key: target[key] for key in ("target_id", "market_type", "team_side", "team", "direction", "line", "status", "model_probability", "push_probability", "decisive_model_probability")} | {"result": result, "actual_corners": actual})
        observations = []
        for item in row["observations"]:
            obs, assessment = item["observation"], item["assessment"]
            decisions = assessment["decisions"] if assessment else []
            rendered = []
            for decision in decisions[:MAX_FIXTURE_DECISIONS]:
                result, _ = _target_settlement({"status": "SUPPORTED", "market_type": "TEAM_TOTAL",
                                                "team_side": decision["team_side"], "direction": decision["direction"],
                                                "line": decision["line"]}, outcome)
                rendered.append({**decision, "result": result,
                                 "hypothetical_units": {name: _profit(result, decision["american_odds"]) if decision[name]["qualified"] else None
                                                        for name in ("champion", "shadow")}})
            selections = [{key: selection[key] for key in ("selection_id", "bookmaker", "market_type", "team_side", "team", "direction", "line", "american_odds", "decimal_odds")}
                          for selection in obs["selections"][:MAX_FIXTURE_DECISIONS]]
            observations.append({"observation_id": obs["observation_id"], "record_hash": obs["record_hash"],
                                 "observed_at_utc": obs["retrieved_at_utc"],
                                 "policy": item["policy"]["policy"] if item["policy"] else None,
                                 "assessment_id": assessment["decision_id"] if assessment else None,
                                 "assessment_hash": assessment["record_hash"] if assessment else None,
                                 "assessment_state": "FROZEN" if assessment else "MISSING_AT_SNAPSHOT",
                                 "decisions": rendered, "selections": selections,
                                 "selections_omitted": max(0, len(obs["selections"]) - MAX_FIXTURE_DECISIONS),
                                 "decisions_omitted": max(0, len(decisions) - MAX_FIXTURE_DECISIONS)})
        return _output({"snapshot_id": snapshot_id, "fixture": p["fixture"], "prediction_id": prediction_id,
                        "prediction_hash": p["record_hash"], "prediction_created_at_utc": p["created_at_utc"],
                        "champion": {"model": p["model"], "distribution": p["distribution"], "targets": targets},
                        "shadow": {"model": shadow["model"], "distribution": shadow["distribution"],
                                   "targets": shadow["targets"][:MAX_FIXTURE_TARGETS], "record_hash": shadow["record_hash"]} if shadow else None,
                        "shadow_state": "FROZEN" if shadow else "MISSING_AT_SNAPSHOT",
                        "historical_context": historical_context(p), "history": p["history"],
                        "observations": observations,
                        "settlement": {"outcome_id": outcome["outcome_id"], "record_hash": outcome["record_hash"],
                                       "recorded_at_utc": outcome["recorded_at_utc"], "result": outcome["result"]} if outcome else None,
                        "targets_omitted": max(0, len(row["targets"]) - MAX_FIXTURE_TARGETS),
                        "shadow_targets_omitted": max(0, len(shadow["targets"]) - MAX_FIXTURE_TARGETS) if shadow else 0,
                        "release_sha": manifest["release_sha"], "limitations": ["RECORDED_PRE_KICKOFF_SNAPSHOTS_NOT_LIVE_ODDS"]})
    return _guard(operation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    for name in ("summary", "segment", "fixture"):
        child = sub.add_parser(name)
        child.add_argument("--snapshot-id", required=True)
        if name == "segment":
            child.add_argument("--by", choices=SEGMENTS, required=True)
        if name == "fixture":
            child.add_argument("--prediction-id", required=True)
    args = parser.parse_args(argv)
    state = os.environ.get("MODELFC_STATE_DIR", "/var/lib/modelfc/state")
    try:
        if args.command == "snapshot":
            result = create_snapshot(state)
        elif args.command == "summary":
            result = research_summary(state, args.snapshot_id)
        elif args.command == "segment":
            result = segment_comparison(state, args.snapshot_id, args.by)
        else:
            result = inspect_fixture(state, args.snapshot_id, args.prediction_id)
    except ResearchError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
