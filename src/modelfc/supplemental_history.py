"""Validated, immutable recent E1 results layered over the CSV archive.

Provider payloads never enter this store.  Normalized records retain hashes and
provider-local identities, while model readers receive deterministic in-memory CSV
bytes.  The archive itself is never replaced or edited.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
from typing import Iterable
from zoneinfo import ZoneInfo

from modelfc.ledger_storage import (LedgerError, ensure_directory, ledger_lock,
                                    read_json_record, write_new_record)


SCHEMA_VERSION = 1
PROVIDER = "sportmonks"


class SupplementalCsvConflict(ValueError):
    """Validated supplemental values disagree with an installed CSV result."""

    def __init__(self):
        super().__init__("SUPPLEMENTAL_CSV_CONFLICT")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sync_directories(*directories: Path) -> None:
    for directory in directories:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError("SUPPLEMENTAL_TIMESTAMP_REJECTED") from None
    if parsed.tzinfo is None:
        raise ValueError("SUPPLEMENTAL_TIMESTAMP_REJECTED")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SupplementalResult:
    competition: str
    provider: str
    provider_fixture_id: int
    provider_league_id: int
    provider_season_id: int
    kickoff_utc: str
    match_date: date
    home_provider_team_id: int
    away_provider_team_id: int
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    home_corners: int
    away_corners: int
    retrieved_at_utc: str
    response_sha256: str
    normalized_sha256: str

    def __post_init__(self):
        integers = (self.provider_fixture_id, self.provider_league_id,
                    self.provider_season_id, self.home_provider_team_id,
                    self.away_provider_team_id, self.home_goals, self.away_goals,
                    self.home_corners, self.away_corners)
        if (self.competition != "E1" or self.provider != PROVIDER
                or any(type(value) is not int or value < 0 for value in integers)
                or any(value == 0 for value in integers[:5])
                or self.home_provider_team_id == self.away_provider_team_id
                or not self.home_team or not self.away_team or self.home_team == self.away_team
                or not re.fullmatch(r"[0-9a-f]{64}", self.response_sha256)
                or not re.fullmatch(r"[0-9a-f]{64}", self.normalized_sha256)):
            raise ValueError("SUPPLEMENTAL_RESULT_REJECTED")
        kickoff = _timestamp(self.kickoff_utc)
        retrieved = _timestamp(self.retrieved_at_utc)
        if (kickoff >= retrieved
                or kickoff.astimezone(ZoneInfo("Europe/London")).date() != self.match_date):
            raise ValueError("SUPPLEMENTAL_RESULT_REJECTED")
        payload = asdict(self)
        claimed = payload.pop("normalized_sha256")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=lambda item: item.isoformat()).encode()
        if claimed != _sha(encoded):
            raise ValueError("SUPPLEMENTAL_RESULT_REJECTED")

    @property
    def key(self):
        return self.match_date, self.home_team, self.away_team


def make_result(**values) -> SupplementalResult:
    payload = dict(values)
    payload["normalized_sha256"] = _sha(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        default=lambda item: item.isoformat(),
    ).encode())
    return SupplementalResult(**payload)


def _path(state: Path, record: SupplementalResult) -> Path:
    identity = _sha(json.dumps({"provider": record.provider,
        "provider_fixture_id": record.provider_fixture_id}, sort_keys=True,
        separators=(",", ":")).encode())
    return state / "supplemental-history" / "E1" / f"{identity}.json"


def publish_result(state_dir: str | Path, record: SupplementalResult) -> bool:
    state = Path(state_dir)
    path = _path(state, record)
    ensure_directory(state, "Model FC state directory")
    ensure_directory(path.parent, "supplemental history directory")
    envelope = {"schema_version": SCHEMA_VERSION, "record_type": "supplemental_result",
                **asdict(record)}
    envelope["match_date"] = record.match_date.isoformat()
    with ledger_lock(state) as lock_fd:
        if path.exists():
            old = read_json_record(path, "supplemental history", "UNKNOWN_SUPPLEMENTAL_RESULT")
            if old != envelope:
                raise LedgerError("SUPPLEMENTAL_RESULT_CONFLICT")
            _sync_directories(path.parent, path.parent.parent, state)
            return False
        # Supplemental provider evidence remains runtime-private. Only the
        # separately projected research prediction is granted the API read ACL.
        write_new_record(path, envelope)
        _sync_directories(path.parent, path.parent.parent, state)
    return True


def load_results(state_dir: str | Path) -> tuple[SupplementalResult, ...]:
    parent = Path(state_dir) / "supplemental-history"
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise LedgerError("INVALID_SUPPLEMENTAL_RESULT")
    directory = parent / "E1"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise LedgerError("INVALID_SUPPLEMENTAL_RESULT")
    records = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        if path.is_symlink() or not path.is_file():
            raise LedgerError("INVALID_SUPPLEMENTAL_RESULT")
        value = read_json_record(path, "supplemental history", "UNKNOWN_SUPPLEMENTAL_RESULT")
        try:
            if value.pop("schema_version") != SCHEMA_VERSION or value.pop("record_type") != "supplemental_result":
                raise ValueError
            value["match_date"] = date.fromisoformat(value["match_date"])
            record = SupplementalResult(**value)
            if path != _path(Path(state_dir), record):
                raise ValueError
            records.append(record)
        except (KeyError, TypeError, ValueError):
            raise LedgerError("INVALID_SUPPLEMENTAL_RESULT") from None
    keys = [record.key for record in records]
    fixtures = [record.provider_fixture_id for record in records]
    if len(keys) != len(set(keys)) or len(fixtures) != len(set(fixtures)):
        raise LedgerError("SUPPLEMENTAL_RESULT_CONFLICT")
    return tuple(sorted(records, key=lambda record: (record.match_date,
                                                       record.kickoff_utc,
                                                       record.provider_fixture_id)))


def augment_sources(sources: tuple[tuple[str, bytes], ...],
                    supplemental: Iterable[SupplementalResult], calendar) -> tuple[tuple[str, bytes], ...]:
    """Return CSV-compatible bytes with missing current results added in memory."""
    if not sources:
        raise ValueError("HISTORY_BOUND_EXCEEDED")
    current_index = max(range(len(sources)), key=lambda index: sources[index][0])
    filename, raw = sources[current_index]
    if re.fullmatch(r"E1_[0-9]{4}\.csv", filename) is None:
        raise ValueError("SUPPLEMENTAL_HISTORY_REJECTED")
    reader = csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    fields = reader.fieldnames
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HC", "AC"}
    if not fields or len(fields) != len(set(fields)) or not required <= set(fields):
        raise ValueError("SUPPLEMENTAL_HISTORY_REJECTED")
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("SUPPLEMENTAL_HISTORY_REJECTED")
    installed = {}
    for row in rows:
        try:
            day = datetime.strptime(row["Date"].strip(), "%d/%m/%Y").date()
            key = day, row["HomeTeam"].strip(), row["AwayTeam"].strip()
            values = tuple(int(row[name].strip()) for name in ("FTHG", "FTAG", "HC", "AC"))
        except (ValueError, KeyError):
            raise ValueError("SUPPLEMENTAL_HISTORY_REJECTED") from None
        if key in installed:
            raise ValueError("SUPPLEMENTAL_HISTORY_REJECTED")
        installed[key] = values
    calendar_by_kickoff = {(match.kickoff, match.home, match.away): match
                           for match in calendar.matches}
    added = []
    for record in supplemental:
        if not isinstance(record, SupplementalResult):
            raise ValueError("SUPPLEMENTAL_HISTORY_REJECTED")
        kickoff = _timestamp(record.kickoff_utc)
        match = calendar_by_kickoff.get((kickoff, record.home_team, record.away_team))
        values = (record.home_goals, record.away_goals,
                  record.home_corners, record.away_corners)
        if (match is None or match.status != "FINISHED" or match.day != record.match_date
                or match.goals != values[:2]):
            raise ValueError("SUPPLEMENTAL_CALENDAR_CONFLICT")
        if record.key in installed:
            if installed[record.key] != values:
                raise SupplementalCsvConflict()
            continue
        row = {name: "" for name in fields}
        row.update(Div="E1", Date=record.match_date.strftime("%d/%m/%Y"),
                   HomeTeam=record.home_team, AwayTeam=record.away_team,
                   FTHG=str(record.home_goals), FTAG=str(record.away_goals),
                   HC=str(record.home_corners), AC=str(record.away_corners))
        if "FTR" in fields:
            row["FTR"] = ("H" if record.home_goals > record.away_goals else
                          "A" if record.away_goals > record.home_goals else "D")
        installed[record.key] = values
        added.append((record.match_date, kickoff, record.provider_fixture_id, row))
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    for _, _, _, row in sorted(added):
        writer.writerow(row)
    result = list(sources)
    result[current_index] = filename, output.getvalue().encode("utf-8")
    return tuple(result)
