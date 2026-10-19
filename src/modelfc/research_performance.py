"""Read-only quality/history projection of existing frozen E1 evidence. No fitting or acquisition."""
from datetime import datetime, timezone
import math
from pathlib import Path

from modelfc import btts_outcomes as btts
from modelfc.btts_market_data import timestamp
from modelfc.btts_research import digest
from modelfc.corner_prospective_read import _inventory
from modelfc.ledger_storage import LedgerError
from modelfc.research_predictions import records as research_records, load_prepared, _id
from modelfc.research_outcomes import corner_results

POLICY = "earliest-frozen-per-exact-fixture-and-market-v1"
LIMITATIONS = [
    "Historical store membership and site visibility were not captured; current store origins are not historical membership.",
    "Missing original training-file retrieval times remain unavailable; hashes do not establish retrieval time.",
    "Odds-free corner forecasts have no saved line probabilities; none are generated for reporting.",
    "Research corner outcome corrections are unsupported; bookmaker corner and BTTS revision chains are retained.",
    "E0 evidence is unsupported. Overall coverage is partial E1 coverage, not zero EPL performance.",
]


def _fixture(row):
    return {k: row[k] for k in ("competition", "home_team", "away_team", "kickoff_utc")}


def _reference(kind, sha, retrieved=None):
    return dict(kind=kind, sha256=sha, retrieved_at_utc=retrieved)


def _source_hash(source):
    return source.get("file_sha256") or source.get("response_sha256")


def _revision(record, *, btts_revision=False):
    return dict(record_id=record.get("record_hash") if btts_revision else record["outcome_id"],
        recorded_at_utc=record["recorded_at_utc"],
        supersedes_id=record.get("supersedes_record_hash") if btts_revision else record.get("supersedes_outcome_id"),
        reason=record.get("reason") if btts_revision else record.get("correction_reason"),
        source_sha256=_source_hash(record["source"]), result=record["result"])


def _base(row, market, identity, origins):
    fixture = _fixture(row)
    fixture["kickoff_utc"] = timestamp(fixture["kickoff_utc"]).isoformat()
    return dict(**fixture, fixture_id=digest(fixture), forecast_id=identity, market=market,
        origins=origins, provider=row["provider"], provider_fixture_id=row["provider_fixture_id"],
        frozen_at_utc=row.get("frozen_at_utc", row.get("created_at_utc")), model_version=row["model_version"],
        source_forecast_id=identity, effective_record_hash=None, result_provider=None, result_retrieved_at_utc=None,
        selected_for_metrics=False, source_references=[_reference(s["filename"], s["sha256"])
            for s in row["source_data_hashes"]], probabilities=[], expected_home_corners=None,
        expected_away_corners=None, status="UNRESOLVED", result=None, outcome_id=None,
        outcome_recorded_at_utc=None, result_source_sha256=None, corrections=[], correction_support="SUPPORTED")


def _corners(state, now, cohort):
    rows = []
    if cohort in {"ALL", "BOOKMAKER_RESEARCH"}:
        # Reuse the established locked validation, target settlement and cutoff logic.
        inv = _inventory(state, now=now, include_research=False, outcome_as_of=now, include_outcome_history=True)
        for prediction in inv["predictions"]:
            if timestamp(prediction["created_at_utc"]) > now:
                continue
            evidence = inv["outcome_history"][prediction["prediction_id"]]
            original = evidence["prediction"]
            row = _base(prediction, "CORNER", prediction["prediction_id"], ["BOOKMAKER_RESEARCH"])
            row["model_name"] = prediction["model_name"]
            row["source_forecast_id"] = original["forecast_id"]
            row["source_references"].extend([
                _reference("capture-request", original["capture_reference"]["request_hash"]),
                _reference("capture-response", original["capture_reference"]["response_hash"]),
                _reference("source-observation", original["source_observation"]["record_hash"],
                           original["source_observation"]["retrieved_at_utc"])])
            for target, result in inv["targets"].values():
                if (target["prediction_id"] != prediction["prediction_id"] or target["status"] != "SUPPORTED"
                        or timestamp(target["materialized_at_utc"]) > now):
                    continue
                p, push, decisive = (target[k] for k in ("model_probability", "push_probability", "decisive_model_probability"))
                if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1
                            for v in (p, push, decisive)) or push >= 1 or p + push > 1+1e-12
                        or not math.isclose(decisive, p/(1-push), rel_tol=0, abs_tol=1e-8)):
                    raise LedgerError("INVALID_RESEARCH_PROBABILITY")
                row["probabilities"].append(dict(target_id=target["target_id"], team_side=target["team_side"],
                    direction=target["direction"], line=target["line"], probability=p, push_probability=push,
                    decisive_probability=decisive, recorded_at_utc=target["materialized_at_utc"], result=result))
            visible = [r for r in evidence["records"] if timestamp(r["recorded_at_utc"]) <= now]
            row["corrections"] = sorted([_revision(r) for r in visible], key=lambda r:(timestamp(r["recorded_at_utc"]),r["record_id"]))
            # Same validated, chronology-checked tip that graded the saved targets.
            _corner_values(row, prediction, evidence["effective"], now)
            rows.append(row)
    if cohort in {"ALL", "ODDS_FREE"}:
        outcomes = corner_results(state, as_of=now)
        identities = {_id(p.forecast_id, m): p.forecast_id for p, m in load_prepared(state)}
        for prediction in research_records(state, now=now, include_outcomes=False):
            if prediction["prediction_type"] != "CORNER" or timestamp(prediction["created_at_utc"]) > now:
                continue
            row = _base(prediction, "CORNER", prediction["prediction_id"], ["ODDS_FREE"])
            row["model_name"] = prediction["model_name"]
            row["source_forecast_id"] = identities[prediction["prediction_id"]]
            row["correction_support"] = "UNSUPPORTED"
            row["source_references"].append(_reference("calendar", prediction["calendar_source_sha256"], prediction["calendar_retrieved_at_utc"]))
            outcome = outcomes.get(row["fixture_id"])
            _corner_values(row, prediction, outcome, now)
            rows.append(row)
    return rows


def _corner_values(row, prediction, outcome, now):
    row.update(expected_home_corners=prediction["expected_home_corners"], expected_away_corners=prediction["expected_away_corners"])
    row["status"] = "SETTLED" if outcome else "UPCOMING" if timestamp(row["kickoff_utc"]) > now else "UNRESOLVED"
    if outcome:
        row.update(result=outcome["result"], outcome_id=outcome["outcome_id"],
            outcome_recorded_at_utc=outcome["recorded_at_utc"], effective_record_hash=outcome["record_hash"],
            result_provider=outcome["source"]["provider"], result_retrieved_at_utc=outcome["source"].get("retrieved_at_utc"), result_source_sha256=_source_hash(outcome["source"]))


def _btts(state, now, cohort):
    result = []
    originals = btts.inventory(state)
    original_outcomes = btts._originals(state, originals)
    research = {r["prediction_id"]: r for r in research_records(state, now=now, include_outcomes=False)}
    prepared_btts = {digest(p.btts): p for p, m in load_prepared(state) if m == "BTTS"}
    for forecast in btts.performance(state, as_of=now)["forecasts"]:
        if cohort != "ALL" and cohort not in forecast["origins"]:
            continue
        row = _base(forecast, "BTTS", forecast["forecast_id"], forecast["origins"])
        if forecast["forecast_id"] in prepared_btts:
            prepared = prepared_btts[forecast["forecast_id"]]
            row["source_forecast_id"] = prepared.forecast_id
            evidence = research[_id(prepared.forecast_id, "BTTS")]
            row["source_references"].extend([
                _reference("calendar", evidence["calendar_source_sha256"], evidence["calendar_retrieved_at_utc"]),
                _reference("research-prepared-forecast", prepared.forecast_id),
                _reference("research-prediction", evidence["prediction_id"])])
        revisions = ([original_outcomes[forecast["outcome_id"]]] + forecast["corrections"]) if forecast["outcome_id"] else []
        if revisions:
            row["result_retrieved_at_utc"] = revisions[-1]["source"].get("retrieved_at_utc")
        row.update(model_name=originals[forecast["forecast_id"]][0].model_name,
            effective_record_hash=forecast["effective_record_hash"], result_provider=forecast["result_provider"], status=forecast["status"], outcome_id=forecast["outcome_id"],
            outcome_recorded_at_utc=forecast["recorded_at_utc"], result_source_sha256=forecast["result_source_sha256"],
            result=None if forecast["actual_btts"] is None else dict(actual_btts=forecast["actual_btts"],
                home_goals=forecast["home_goals"], away_goals=forecast["away_goals"]),
            probabilities=[dict(target_id=forecast["forecast_id"], team_side=None, direction="YES", line=None,
                probability=forecast["yes_probability"], push_probability=0.0,
                decisive_probability=forecast["yes_probability"], recorded_at_utc=forecast["frozen_at_utc"],
                result=None if forecast["actual_btts"] is None else "WIN" if forecast["actual_btts"] == "YES" else "LOSS"),
                dict(target_id=forecast["forecast_id"]+":NO", team_side=None, direction="NO", line=None,
                probability=forecast["no_probability"], push_probability=0.0,
                decisive_probability=forecast["no_probability"], recorded_at_utc=forecast["frozen_at_utc"],
                result=None if forecast["actual_btts"] is None else "WIN" if forecast["actual_btts"] == "NO" else "LOSS")],
            corrections=[_revision(r, btts_revision=True) for r in revisions])
        result.append(row)
    return result


def _summary(rows, market):
    primary = [r for r in rows if r["selected_for_metrics"]]
    settled = [r for r in primary if r["status"] == "SETTLED"]
    targets = [p for r in primary for p in r["probabilities"] if market != "BTTS" or p["direction"] == "YES"]
    scored = [p for p in targets if p["result"] in {"WIN", "LOSS"}]
    scores = btts.scores([dict(yes_probability=p["decisive_probability"], actual_btts="YES" if p["result"] == "WIN" else "NO") for p in scored])
    errors = [r["result"][f"{side}_corners"]-r[f"expected_{side}_corners"] for r in settled for side in ("home", "away")] if market == "CORNER" else []
    return dict(market=market, unique_fixtures=len(primary), forecast_cohorts=len(rows),
        settled_fixtures=len(settled), unresolved_fixtures=sum(r["status"]=="UNRESOLVED" for r in primary),
        upcoming_fixtures=sum(r["status"]=="UPCOMING" for r in primary),
        probability_unit="fixture-yes" if market=="BTTS" else "saved-decisive-team-total-target",
        saved_probability_targets=len(targets), pushes_excluded=sum(p["result"]=="PUSH" for p in targets),
        probability_scores=scores, team_count_sample_count=len(errors),
        team_corner_mae=math.fsum(abs(e) for e in errors)/len(errors) if errors else None,
        team_corner_rmse=math.sqrt(math.fsum(e*e for e in errors)/len(errors)) if errors else None)


def read(state_dir, *, competition="ALL", market="ALL", cohort="ALL", as_of=None):
    now = as_of or datetime.now(timezone.utc)
    if (competition not in {"ALL", "E1", "E0"} or market not in {"ALL", "CORNER", "BTTS"}
            or cohort not in {"ALL", "ODDS_FREE", "BOOKMAKER_RESEARCH"} or now.tzinfo is None
            or now > datetime.now(timezone.utc)):
        raise ValueError("INVALID_RESEARCH_QUERY")
    state = Path(state_dir)
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise LedgerError("INVALID_RESEARCH_STATE")
    rows = []
    if competition != "E0":
        if market in {"ALL", "CORNER"}: rows.extend(_corners(state, now, cohort))
        if market in {"ALL", "BTTS"}: rows.extend(_btts(state, now, cohort))
    rows.sort(key=lambda r:(timestamp(r["frozen_at_utc"]), r["market"], r["forecast_id"]))
    seen = set()
    for row in rows:
        key = (row["market"], row["fixture_id"])
        row["selected_for_metrics"] = key not in seen
        seen.add(key)
    return dict(selection=dict(competition=competition, market=market, cohort=cohort, as_of_utc=now.isoformat()),
        policy=POLICY, coverage="UNSUPPORTED" if competition=="E0" else "PARTIAL" if competition=="ALL" else "SUPPORTED",
        supported_competitions=["E1"], unavailable_competitions=["E0"], historical_store_membership="UNVERIFIED",
        unique_fixtures=len({r["fixture_id"] for r in rows}), forecast_cohorts=len(rows),
        markets=[_summary([r for r in rows if r["market"]==m],m) for m in ("CORNER","BTTS") if market in {"ALL",m}],
        limitations=LIMITATIONS, forecasts=rows)
