"""Offline, hash-pinned Sportmonks final-result evidence for immutable settlement.

The operator authorizes source hashes and exact provider bindings. Hashes prove
byte identity, not source authenticity. No network or model execution occurs.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from modelfc.corner_analysis_outcomes import OutcomeError, _require
from modelfc.corner_refresh import current_season
from modelfc.history_completeness import MAX_CALENDAR_BYTES, parse_calendar
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.providers.sportmonks_results import MAX_PAGE_BYTES, normalize_page
from modelfc.supplemental_history import SupplementalResult


def read_pinned(path, digest, limit):
    """Read exactly the reviewed regular inode; no links, mutation or enumeration."""
    _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
             "SAVED_RESULT_PROVENANCE_REJECTED")
    path = Path(path).absolute()
    descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            _require(part != "..", "SAVED_RESULT_PROVENANCE_REJECTED")
            following = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW |
                                os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor); descriptor = following
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                     os.O_CLOEXEC, dir_fd=descriptor)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                     and 0 < before.st_size <= limit, "SAVED_RESULT_PROVENANCE_REJECTED")
            raw = source.read(limit + 1)
            after = os.fstat(source.fileno())
            final = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                      before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                      after.st_mtime_ns, after.st_ctime_ns)
                     and (before.st_dev, before.st_ino) == (final.st_dev, final.st_ino)
                     and len(raw) <= limit and hashlib.sha256(raw).hexdigest() == digest,
                     "SAVED_RESULT_PROVENANCE_REJECTED")
            return raw
    except OSError:
        raise OutcomeError("SAVED_RESULT_PROVENANCE_REJECTED") from None
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class SavedResultEvidence:
    result: SupplementalResult
    capture_reference: str
    calendar_sha256: str
    calendar_source_sha256: str
    calendar_fixture_id: int
    calendar_retrieved_at_utc: str

    def __post_init__(self):
        _require(isinstance(self.result, SupplementalResult)
                 and isinstance(self.capture_reference, str)
                 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.capture_reference)
                 and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                         for value in (self.calendar_sha256, self.calendar_source_sha256))
                 and type(self.calendar_fixture_id) is int and self.calendar_fixture_id > 0,
                 "SAVED_RESULT_PROVENANCE_REJECTED")

        _require(self.result.provider_league_id == 9,
                 "SAVED_RESULT_PROVENANCE_REJECTED")
        try:
            timestamp = datetime.fromisoformat(self.calendar_retrieved_at_utc.replace("Z", "+00:00"))
            _require(timestamp.tzinfo is not None,
                     "SAVED_RESULT_PROVENANCE_REJECTED")
        except (ValueError, AttributeError):
            raise OutcomeError("SAVED_RESULT_PROVENANCE_REJECTED") from None

    def for_settlement(self, fixture, kickoff):
        """Use the live clock only when accepting a new settlement."""
        return self.for_fixture(fixture, kickoff, as_of=datetime.now(timezone.utc))

    def for_fixture(self, fixture, kickoff, *, as_of):
        result = self.result
        _require(fixture["competition"] == result.competition == "E1"
                 and (fixture["home_team"], fixture["away_team"]) ==
                     (result.home_team, result.away_team)
                 and kickoff == datetime.fromisoformat(result.kickoff_utc)
                 and as_of.tzinfo is not None and kickoff < as_of
                 and datetime.fromisoformat(result.retrieved_at_utc) <= as_of
                 and datetime.fromisoformat(self.calendar_retrieved_at_utc) <= as_of,
                 "SAVED_RESULT_IDENTITY_CONFLICT")
        source = {"provider": "sportmonks", "capture_reference": self.capture_reference,
                  "response_sha256": result.response_sha256,
                  "normalized_record": {**asdict(result), "match_date": result.match_date.isoformat()},
                  "calendar": {"provider": "football-data.org", "fixture_id": self.calendar_fixture_id,
                               "bundle_sha256": self.calendar_sha256,
                               "source_sha256": self.calendar_source_sha256,
                               "retrieved_at_utc": self.calendar_retrieved_at_utc},
                  "final_state": "FT", "retrieved_at_utc": result.retrieved_at_utc}
        return current_season(result.match_date), source, {
            "home_corners": result.home_corners, "away_corners": result.away_corners}


def validate_saved_result(*, response, response_sha256, capture_reference,
                          retrieved_at, bindings, season_id, fixture_id,
                          calendar_raw, calendar_sha256):
    """Bind a saved final result to independent completed calendar score/identity.

    Settlement validates calendar freshness at its original retrieval, rather than
    pretending old evidence was fetched now. Forecast freshness rules are untouched.
    """
    try:
        _require(type(season_id) is int and season_id > 0
                 and type(fixture_id) is int and fixture_id > 0
                 and isinstance(retrieved_at, datetime) and retrieved_at.tzinfo is not None
                 and retrieved_at <= datetime.now(timezone.utc), "SAVED_RESULT_REJECTED")
        _require(hashlib.sha256(response).hexdigest() == response_sha256
                 and hashlib.sha256(calendar_raw).hexdigest() == calendar_sha256,
                 "SAVED_RESULT_PROVENANCE_REJECTED")
        bundle = _json(calendar_raw)
        calendar_time = datetime.fromisoformat(bundle["retrieved_at"].replace("Z", "+00:00"))
        _require(calendar_time.tzinfo is not None and calendar_time <= datetime.now(timezone.utc),
                 "SAVED_RESULT_PROVENANCE_REJECTED")
        calendar = parse_calendar(calendar_raw, as_of=calendar_time)
        records = normalize_page(response, retrieved_at=retrieved_at, bindings=bindings,
                                 expected_season_id=season_id)
        selected = [record for record in records if record.provider_fixture_id == fixture_id]
        _require(len(selected) == 1, "SAVED_RESULT_NOT_AVAILABLE")
        result = selected[0]
        kickoff = datetime.fromisoformat(result.kickoff_utc)
        _require(calendar.season_year == result.match_date.year - (result.match_date.month < 7),
                 "SAVED_RESULT_IDENTITY_CONFLICT")
        matches = [match for match in calendar.matches if
                   (match.kickoff, match.home, match.away) ==
                   (kickoff, result.home_team, result.away_team)]
        _require(len(matches) == 1 and matches[0].status == "FINISHED"
                 and matches[0].goals == (result.home_goals, result.away_goals),
                 "SAVED_RESULT_IDENTITY_CONFLICT")
        return SavedResultEvidence(result, capture_reference, calendar.bundle_sha256,
            calendar.source_sha256, matches[0].fixture_id, calendar.retrieved_at.isoformat())
    except OutcomeError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise OutcomeError("SAVED_RESULT_REJECTED") from None


def validate_outcome_source(record):
    """Validate persisted provenance without opening private captures or calendars."""
    from datetime import date
    from modelfc.corner_analysis_store import _canonical_hash
    source = record["source"]
    _require(set(source) == {"provider", "capture_reference", "response_sha256",
        "normalized_record", "calendar", "final_state", "retrieved_at_utc"}, "INVALID_OUTCOME")
    values = dict(source["normalized_record"])
    values["match_date"] = date.fromisoformat(values["match_date"])
    result = SupplementalResult(**values)
    calendar = source["calendar"]
    _require(set(calendar) == {"provider", "fixture_id", "bundle_sha256", "source_sha256",
        "retrieved_at_utc"} and calendar["provider"] == "football-data.org", "INVALID_OUTCOME")
    evidence = SavedResultEvidence(result, source["capture_reference"], calendar["bundle_sha256"],
        calendar["source_sha256"], calendar["fixture_id"], calendar["retrieved_at_utc"])
    fixture = record["fixture"]
    season, reconstructed, corners = evidence.for_fixture(fixture,
        datetime.fromisoformat(fixture["kickoff_at"].replace("Z", "+00:00")),
        as_of=datetime.fromisoformat(record["recorded_at_utc"].replace("Z", "+00:00")))
    _require(source == reconstructed and record["result"] == corners
        and fixture["source_season"] == season
        and record["matching_rule_version"] == "sportmonks-exact-calendar-v1"
        and record["settlement_rule_version"] == "team-total-comparison-v1"
        and record["status"] == "COMPLETED"
        and record["evidence_hash"] == _canonical_hash({"capture": record["capture"],
            "row": source, "matching": record["matching_rule_version"],
            "settlement": record["settlement_rule_version"]}), "INVALID_OUTCOME")


def main(argv=None):
    """Explicit operator-only offline settlement. Nothing is auto-discovered."""
    import argparse
    from modelfc.corner_analysis_outcomes import record_sportmonks_outcome
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--response-file", required=True)
    parser.add_argument("--calendar-file", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--analysis-id", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = _json(read_pinned(args.manifest, args.manifest_sha256, 64 * 1024))
        fields = {"version", "provider", "competition", "capture_reference", "response_sha256",
                  "calendar_sha256", "retrieved_at_utc", "season_id", "fixture_id", "team_bindings"}
        _require(isinstance(manifest, dict) and set(manifest) == fields
                 and type(manifest["version"]) is int and manifest["version"] == 1
                 and manifest["provider"] == "sportmonks" and manifest["competition"] == "E1"
                 and isinstance(manifest["retrieved_at_utc"], str),
                 "SAVED_RESULT_REJECTED")
        evidence = validate_saved_result(
            response=read_pinned(args.response_file, manifest["response_sha256"], MAX_PAGE_BYTES),
            calendar_raw=read_pinned(args.calendar_file, manifest["calendar_sha256"], MAX_CALENDAR_BYTES),
            response_sha256=manifest["response_sha256"], calendar_sha256=manifest["calendar_sha256"],
            capture_reference=manifest["capture_reference"],
            retrieved_at=datetime.fromisoformat(manifest["retrieved_at_utc"].replace("Z", "+00:00")),
            bindings=manifest["team_bindings"], season_id=manifest["season_id"],
            fixture_id=manifest["fixture_id"])
        record, created = record_sportmonks_outcome(state_dir=args.state_dir,
            analysis_id=args.analysis_id, evidence=evidence, idempotency_key=args.key)
        print(json.dumps({"status": "RECORDED" if created else "REUSED",
                          "outcome_id": record["outcome_id"], "provider_requests": 0}))
        return 0
    except OutcomeError as error:
        # Surface only fixed conflict identifiers, never arbitrary exception text.
        conflicts = {"RESULT_CONFLICT", "IDEMPOTENCY_CONFLICT", "SAVED_RESULT_IDENTITY_CONFLICT"}
        reason = error.args[0] if error.args and isinstance(error.args[0], str) and error.args[0] in conflicts else "SAVED_RESULT_SETTLEMENT_REJECTED"
        print(json.dumps({"status": "CONFLICT" if reason in conflicts else "REJECTED",
                          "reason": reason, "provider_requests": 0}))
        return 1
    except (ValueError, OSError):
        print(json.dumps({"status": "REJECTED", "reason": "SAVED_RESULT_SETTLEMENT_REJECTED",
                          "provider_requests": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
