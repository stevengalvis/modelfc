"""Bounded, hash-pinned team/history diagnostic. No aliases, forecasts, HTTP or writes.

Private operator export only. Saved odds are retrospective diagnostic inputs,
never proof of a prospective model freeze. This module does not authorize joins.
"""
import argparse
import csv
from inspect import signature
from datetime import date, datetime
import hashlib
from io import StringIO
import json
from pathlib import Path

from modelfc.acquisition_planner import LEAGUES, Fixture, plan_acquisition, utc
from modelfc.btts_model import HistorySource
from modelfc.offline_forecasts import history_bytes_from_config
from modelfc.corner_data import parse_data_config
from modelfc.corner_forecasts import predict_corner_fixture
from modelfc.matches import Venue
from modelfc.providers.football_data import _read_corner_observations
from modelfc.providers.oddspapi import COMPETITIONS, OddsPapiError, normalize_team
from modelfc.providers.oddspapi_saved_response import MAX_RESPONSE_BYTES, _json
from modelfc.oddspapi_market_inventory import MAX_FIXTURES, _read_regular


class IdentityAuditError(ValueError):
    """Fixed rejection code; never echoes source text or filesystem paths."""


def _reject():
    raise IdentityAuditError("IDENTITY_AUDIT_REJECTED")


def _text(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 120 or value != value.strip()
            or not value.isprintable()):
        _reject()
    return value


def _join(name, names, competition):
    # Export ambiguities, do not silently choose exact identity over a colliding alias.
    config = COMPETITIONS.get(competition)
    if config is None:
        return None, "UNSUPPORTED_MODEL"
    alias = dict(config.aliases).get(name)
    if name in names and alias in names and alias != name:
        return None, "AMBIGUOUS_HISTORY_IDENTITY"
    try:
        return normalize_team(name, names, competition), "VERIFIED_EXISTING_JOIN"
    except OddsPapiError:
        return None, "UNVERIFIED_IDENTITY"


def _audit_identities(payload: bytes, sources: tuple[tuple[str, bytes], ...], *,
                     cutoff: date, as_of: datetime, max_age_days: int = 14) -> dict:
    """Inventory every distinct E1 identity and diagnose only planner-eligible fixtures.

    Stable IDs check cross-fixture consistency; they do not themselves establish
    that a provider name denotes a particular historical team. Unknown mappings
    remain blocked for explicit operator verification.
    """
    observed = utc(as_of)
    if (type(cutoff) is not date or cutoff != observed.date()
            or type(max_age_days) is not int or max_age_days < 1
            or not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RESPONSE_BYTES
            or not sources or len(sources) > 32):
        _reject()
    provenance = []
    rows = []
    for filename, content in sorted(sources):
        if not isinstance(content, bytes) or not 0 < len(content) <= 2_000_000:
            _reject()
        provenance.append(HistorySource(competition="E1", filename=filename,
            sha256=hashlib.sha256(content).hexdigest()).model_dump(mode="json"))
        rows.extend(_read_corner_observations(StringIO(content.decode("utf-8-sig")), "E1"))
    if len({s["filename"] for s in provenance}) != len(provenance):
        _reject()
    keys = [(r.match_date, r.team, r.opponent, r.venue) for r in rows]
    if len(keys) != len(set(keys)):
        _reject()
    prior = [r for r in rows if r.match_date < cutoff]
    names = {_text(r.team) for r in rows}
    minimum_history = signature(predict_corner_fixture).parameters["min_history"].default
    minimum_venue = signature(predict_corner_fixture).parameters["min_venue_history"].default
    if len(names) > 1000:
        _reject()
    raw = _json(payload)
    fixtures = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else None
    if fixtures is None or len(fixtures) > MAX_FIXTURES:
        _reject()
    leagues = {l.tournament_id: l for l in LEAGUES}
    known, identities, seen = [], {}, set()
    for fixture in fixtures:
        if (not isinstance(fixture, dict) or type(fixture.get("tournamentId")) is not int
                or type(fixture.get("sportId")) is not int or fixture["sportId"] != 10):
            _reject()
        league = leagues.get(fixture["tournamentId"])
        if league is None:
            _reject()
        if (fixture.get("tournamentSlug", league.slug) != league.slug
                or fixture.get("categorySlug", league.country.lower()) != league.country.lower()):
            _reject()
        identity = Fixture(league.competition, league.tournament_id, _text(fixture.get("fixtureId")),
                           datetime.fromisoformat(fixture["startTime"].replace("Z", "+00:00")))
        if identity.fixture_id in seen:
            _reject()
        seen.add(identity.fixture_id)
        team_names = tuple(_text(fixture.get(key)) for key in ("participant1Name", "participant2Name"))
        team_ids = tuple(fixture.get(key) for key in ("participant1Id", "participant2Id"))
        if (team_names[0] == team_names[1] or any(type(i) is not int or i <= 0 for i in team_ids)
                or team_ids[0] == team_ids[1]):
            _reject()
        if league.competition != "E1":
            continue  # Other fixed leagues retain identity but have no enabled models/history.
        if type(fixture.get("statusId")) is not int:
            _reject()
        joined = []
        for pid, name in zip(team_ids, team_names):
            canonical, status = _join(name, names, "E1")
            identities.setdefault(pid, {})[name] = (canonical, status)
            joined.append(canonical)
        known.append((identity, team_ids, team_names, joined, fixture["statusId"]))
    # Same ID with inconsistent names, or one canonical team assigned multiple IDs,
    # is a review blocker. Never derive an alias from either collision.
    collisions = set()
    canonical_ids = {}
    for pid, variants in identities.items():
        if len(variants) != 1:
            collisions.add(pid)
        for canonical, _ in variants.values():
            if canonical is not None:
                canonical_ids.setdefault(canonical, set()).add(pid)
    for ids in canonical_ids.values():
        if len(ids) > 1:
            collisions.update(ids)
    exported = []
    for pid, variants in sorted(identities.items()):
        for name, (canonical, status) in sorted(variants.items()):
            exported.append({"competition": "E1", "provider": "oddspapi", "provider_team_id": pid,
                "provider_name": name, "historical_name": canonical,
                "status": "AMBIGUOUS_PROVIDER_IDENTITY" if pid in collisions else status})
    plan = plan_acquisition({"E1": [f for f, _, _, _, status in known if status == 0]}, as_of=observed)
    eligible = {o.fixture for batch in plan.batches for o in batch.obligations}
    diagnostics = []
    for fixture, ids, display, joined, _ in sorted(known, key=lambda v: (v[0].kickoff_utc, v[0].fixture_id)):
        if fixture not in eligible:
            continue
        failures, teams = [], []
        if len(prior) < minimum_history:
            failures.append("INSUFFICIENT_LEAGUE_OBSERVATIONS")
        for pid, original, canonical, venue in zip(ids, display, joined, (Venue.HOME, Venue.AWAY)):
            results = [r for r in prior if canonical is not None and r.team == canonical]
            venue_results = [r for r in results if r.venue == venue]
            latest = max((r.match_date for r in results), default=None)
            if pid in collisions:
                failures.append("AMBIGUOUS_PROVIDER_IDENTITY")
            elif canonical is None:
                failures.append("IDENTITY_UNVERIFIED")
            elif not results:
                failures.append("MISSING_PRE_CUTOFF_HISTORICAL_RECORDS")
            elif len(venue_results) < minimum_venue:
                failures.append("INSUFFICIENT_TEAM_VENUE_OBSERVATIONS")
            teams.append({"provider_team_id": pid, "provider_name": original, "historical_name": canonical,
                "venue": venue.value, "team_observations": len(results), "venue_observations": len(venue_results),
                "latest_history_date": None if latest is None else latest.isoformat(),
                "history_age_days": None if latest is None else (fixture.kickoff_utc.date() - latest).days,
                "stale_history_warning": latest is not None and (fixture.kickoff_utc.date()-latest).days > max_age_days,
                "promotion_status": "NOT_VERIFIED"})
        diagnostics.append({"competition": "E1", "fixture_id": fixture.fixture_id,
            "kickoff_utc": fixture.kickoff_utc.isoformat(), "teams": teams,
            "league_team_observations": len(prior), "minimum_league_team_observations": minimum_history,
            "minimum_team_venue_observations": minimum_venue, "failures": sorted(set(failures)),
            "status": "REVIEW" if failures else "COUNT_GATES_SATISFIED"})
    return {"schema_version": 1, "retrospective_diagnostic_only": True,
        "provider_requests": 0, "source_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "as_of_utc": observed.isoformat(), "cutoff_date": cutoff.isoformat(), "history_sources": provenance,
        "historical_names": sorted(names), "excluded_same_day_future_observations": len(rows)-len(prior),
        "identities": exported, "unmatched_identities": [r for r in exported if r["status"] != "VERIFIED_EXISTING_JOIN"],
        "eligible_fixtures": diagnostics, "unsupported_model_competitions": sorted({leagues[f["tournamentId"]].competition for f in fixtures if f["tournamentId"] != 18})}


def audit_identities(payload: bytes, sources: tuple[tuple[str, bytes], ...], *,
                     cutoff: date, as_of: datetime, max_age_days: int = 14) -> dict:
    """Sanitized bounded diagnostic; all source names, only pre-cutoff model counts."""
    try:
        return _audit_identities(payload, sources, cutoff=cutoff, as_of=as_of, max_age_days=max_age_days)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError, csv.Error):
        raise IdentityAuditError("IDENTITY_AUDIT_REJECTED") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--response-sha256", required=True)
    parser.add_argument("--data-config", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args(argv)
    try:
        import re
        if not re.fullmatch(r"[0-9a-f]{64}", args.response_sha256):
            _reject()
        raw = _read_regular(args.response, MAX_RESPONSE_BYTES, "IDENTITY_AUDIT_REJECTED")
        if hashlib.sha256(raw).hexdigest() != args.response_sha256:
            _reject()
        config_raw = _read_regular(args.data_config, 16_384, "IDENTITY_AUDIT_REJECTED")
        config = parse_data_config(_json(config_raw), args.data_config, resolve_directory=False)
        report = audit_identities(raw, history_bytes_from_config(config, "E1"),
            as_of=datetime.fromisoformat(args.as_of.replace("Z", "+00:00")), cutoff=date.fromisoformat(args.cutoff),
            max_age_days=config.max_age_days)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError, csv.Error):
        print('{"status":"IDENTITY_AUDIT_REJECTED"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
