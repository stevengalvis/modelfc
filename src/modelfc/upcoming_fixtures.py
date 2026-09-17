"""Provider-neutral upcoming-fixture snapshots and deterministic matching.

This module deliberately contains no network client and no language-model code.
Concrete schedule providers can implement :class:`UpcomingFixtureProvider` and
return an immutable :class:`FixtureSnapshot`.  Resolution only trusts fixtures
that are present in that snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Mapping, Protocol, Sequence


SNAPSHOT_SCHEMA_VERSION = 1


class FixtureStatus(str, Enum):
    """Normalized schedule status used for deterministic resolution."""

    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class FixtureResolutionStatus(str, Enum):
    """Outcome of matching user clues against one provider snapshot."""

    RESOLVED = "resolved"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    if normalized.utcoffset() != timezone.utc.utcoffset(normalized):
        raise ValueError(f"{field} must be representable in UTC")
    return normalized


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return _utc(datetime.fromisoformat(raw), field)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if value != value.strip():
        raise ValueError(f"{field} must not contain surrounding whitespace")
    return value


@dataclass(frozen=True)
class ProviderFixture:
    """A single provider-backed fixture, including its provenance."""

    provider_fixture_id: str
    competition_id: str
    competition_label: str
    season: str
    kickoff_utc: datetime
    home_team: str
    away_team: str
    status: FixtureStatus
    provider_name: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "provider_fixture_id", "competition_id", "competition_label",
            "season", "home_team", "away_team", "provider_name",
        ):
            _text(getattr(self, field), field)
        if self.home_team.casefold() == self.away_team.casefold():
            raise ValueError("home_team and away_team must be different")
        if not isinstance(self.status, FixtureStatus):
            raise ValueError("status must be a FixtureStatus")
        _utc(self.kickoff_utc, "kickoff_utc")
        _utc(self.fetched_at, "fetched_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_fixture_id": self.provider_fixture_id,
            "competition_id": self.competition_id,
            "competition_label": self.competition_label,
            "season": self.season,
            "kickoff_utc": _iso(self.kickoff_utc),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "status": self.status.value,
            "provider_name": self.provider_name,
            "fetched_at": _iso(self.fetched_at),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProviderFixture":
        if not isinstance(value, dict):
            raise ValueError("fixture must be an object")
        try:
            status = FixtureStatus(value["status"])
            return cls(
                provider_fixture_id=value["provider_fixture_id"],
                competition_id=value["competition_id"],
                competition_label=value["competition_label"],
                season=value["season"],
                kickoff_utc=_parse_datetime(value["kickoff_utc"], "kickoff_utc"),
                home_team=value["home_team"],
                away_team=value["away_team"],
                status=status,
                provider_name=value["provider_name"],
                fetched_at=_parse_datetime(value["fetched_at"], "fetched_at"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid provider fixture: {error}") from error


def _snapshot_payload(
    provider_name: str,
    provider_request_id: str,
    retrieved_at: datetime,
    provider_payload_hash: str | None,
    fixtures: Sequence[ProviderFixture],
) -> dict[str, object]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "provider_name": provider_name,
        "provider_request_id": provider_request_id,
        "retrieved_at": _iso(retrieved_at),
        "provider_payload_hash": provider_payload_hash,
        "fixtures": [fixture.to_dict() for fixture in fixtures],
    }


@dataclass(frozen=True)
class FixtureSnapshot:
    """Immutable, recorded provider output used as resolution ground truth."""

    provider_name: str
    provider_request_id: str
    retrieved_at: datetime
    fixtures: tuple[ProviderFixture, ...]
    provider_snapshot_id: str
    provider_payload_hash: str | None = None
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.provider_name, "provider_name")
        _text(self.provider_request_id, "provider_request_id")
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"unsupported fixture snapshot schema: {self.schema_version}")
        _utc(self.retrieved_at, "retrieved_at")
        if not isinstance(self.fixtures, tuple):
            raise ValueError("fixtures must be a tuple")
        ids: set[str] = set()
        for fixture in self.fixtures:
            if not isinstance(fixture, ProviderFixture):
                raise ValueError("fixtures must contain ProviderFixture values")
            if fixture.provider_name != self.provider_name:
                raise ValueError("fixture provider_name does not match snapshot")
            if fixture.provider_fixture_id in ids:
                raise ValueError("duplicate provider fixture ID")
            ids.add(fixture.provider_fixture_id)
        _text(self.provider_snapshot_id, "provider_snapshot_id")
        expected = self._computed_id()
        if self.provider_snapshot_id != expected:
            raise ValueError("provider_snapshot_id does not match snapshot contents")

    def _computed_id(self) -> str:
        encoded = json.dumps(
            _snapshot_payload(
                self.provider_name, self.provider_request_id, self.retrieved_at,
                self.provider_payload_hash, self.fixtures,
            ), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, object]:
        payload = _snapshot_payload(
            self.provider_name, self.provider_request_id, self.retrieved_at,
            self.provider_payload_hash, self.fixtures,
        )
        payload["provider_snapshot_id"] = self.provider_snapshot_id
        return payload

    @classmethod
    def create(
        cls,
        *,
        provider_name: str,
        provider_request_id: str,
        retrieved_at: datetime,
        fixtures: Sequence[ProviderFixture],
        provider_payload_hash: str | None = None,
    ) -> "FixtureSnapshot":
        normalized_time = _utc(retrieved_at, "retrieved_at")
        normalized_fixtures = tuple(fixtures)
        payload = _snapshot_payload(
            provider_name, provider_request_id, normalized_time,
            provider_payload_hash, normalized_fixtures,
        )
        snapshot_id = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        return cls(
            provider_name=provider_name,
            provider_request_id=provider_request_id,
            retrieved_at=normalized_time,
            fixtures=normalized_fixtures,
            provider_snapshot_id=snapshot_id,
            provider_payload_hash=provider_payload_hash,
        )

    @classmethod
    def from_dict(cls, value: object) -> "FixtureSnapshot":
        if not isinstance(value, dict):
            raise ValueError("fixture snapshot must be an object")
        try:
            fixtures = tuple(ProviderFixture.from_dict(item) for item in value["fixtures"])
            return cls(
                provider_name=value["provider_name"],
                provider_request_id=value["provider_request_id"],
                retrieved_at=_parse_datetime(value["retrieved_at"], "retrieved_at"),
                fixtures=fixtures,
                provider_snapshot_id=value["provider_snapshot_id"],
                provider_payload_hash=value.get("provider_payload_hash"),
                schema_version=value["schema_version"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid fixture snapshot: {error}") from error


class UpcomingFixtureProvider(Protocol):
    """Small interface required by future schedule-provider adapters."""

    provider_name: str

    def fetch_snapshot(
        self,
        *,
        competition_ids: Sequence[str] | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> FixtureSnapshot:
        """Return a recorded snapshot of provider-returned fixtures."""


class RecordedFixtureProvider:
    """Offline provider used by tests and reproducible evaluation runs."""

    def __init__(self, snapshot: FixtureSnapshot) -> None:
        self.provider_name = snapshot.provider_name
        self._snapshot = snapshot

    def fetch_snapshot(
        self,
        *,
        competition_ids: Sequence[str] | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> FixtureSnapshot:
        if from_utc is not None:
            from_utc = _utc(from_utc, "from_utc")
        if to_utc is not None:
            to_utc = _utc(to_utc, "to_utc")
        if from_utc is not None and to_utc is not None and from_utc > to_utc:
            raise ValueError("from_utc must not be after to_utc")
        allowed_competitions = {
            _normalize(value) for value in competition_ids or ()
        }
        fixtures = tuple(
            fixture for fixture in self._snapshot.fixtures
            if (not allowed_competitions or _normalize(fixture.competition_id) in allowed_competitions)
            and (from_utc is None or fixture.kickoff_utc >= from_utc)
            and (to_utc is None or fixture.kickoff_utc <= to_utc)
        )
        if len(fixtures) == len(self._snapshot.fixtures):
            return self._snapshot
        return FixtureSnapshot.create(
            provider_name=self._snapshot.provider_name,
            provider_request_id=self._snapshot.provider_request_id,
            retrieved_at=self._snapshot.retrieved_at,
            fixtures=fixtures,
            provider_payload_hash=self._snapshot.provider_payload_hash,
        )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _canonical(value: str, aliases: Mapping[str, str]) -> str:
    normalized = _normalize(value)
    return _normalize(aliases.get(normalized, value))


@dataclass(frozen=True)
class FixtureClues:
    """Deterministic clues extracted by a parser or supplied by a caller."""

    competition: str | None = None
    match_date: date | None = None
    home_team: str | None = None
    away_team: str | None = None
    team: str | None = None
    team_side: str | None = None

    def __post_init__(self) -> None:
        for field in ("competition", "home_team", "away_team", "team"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} must be non-empty text when provided")
        if self.match_date is not None and not isinstance(self.match_date, date):
            raise ValueError("match_date must be a date when provided")
        if self.team_side is not None and self.team_side not in {"home", "away"}:
            raise ValueError("team_side must be home or away")
        if self.home_team and self.away_team and _normalize(self.home_team) == _normalize(self.away_team):
            raise ValueError("home_team and away_team must be different")
        if self.team_side and not self.team:
            raise ValueError("team_side requires team")


@dataclass(frozen=True)
class FixtureResolution:
    status: FixtureResolutionStatus
    fixture: ProviderFixture | None
    candidates: tuple[ProviderFixture, ...]
    provider_snapshot_id: str
    missing_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _matches(
    fixture: ProviderFixture,
    clues: FixtureClues,
    *,
    team_aliases: Mapping[str, str],
    competition_aliases: Mapping[str, str],
) -> bool:
    if clues.competition and _canonical(clues.competition, competition_aliases) not in {
        _canonical(fixture.competition_id, competition_aliases),
        _canonical(fixture.competition_label, competition_aliases),
    }:
        return False
    if clues.match_date and fixture.kickoff_utc.date() != clues.match_date:
        return False
    if clues.home_team and _canonical(clues.home_team, team_aliases) != _canonical(fixture.home_team, team_aliases):
        return False
    if clues.away_team and _canonical(clues.away_team, team_aliases) != _canonical(fixture.away_team, team_aliases):
        return False
    if clues.team:
        expected = _canonical(clues.team, team_aliases)
        actual = fixture.home_team if clues.team_side == "home" else fixture.away_team if clues.team_side == "away" else None
        if actual is not None:
            if expected != _canonical(actual, team_aliases):
                return False
        elif expected not in {
            _canonical(fixture.home_team, team_aliases),
            _canonical(fixture.away_team, team_aliases),
        }:
            return False
    return True


def resolve_fixture(
    snapshot: FixtureSnapshot,
    clues: FixtureClues | Mapping[str, object],
    *,
    team_aliases: Mapping[str, str] | None = None,
    competition_aliases: Mapping[str, str] | None = None,
) -> FixtureResolution:
    """Match clues without fuzzy guessing or inventing provider fixtures."""

    try:
        if not isinstance(clues, FixtureClues):
            if not isinstance(clues, Mapping):
                raise ValueError("fixture clues must be an object")
            clues = FixtureClues(
                competition=clues.get("competition"),
                match_date=clues.get("match_date"),
                home_team=clues.get("home_team"),
                away_team=clues.get("away_team"),
                team=clues.get("team"),
                team_side=clues.get("team_side"),
            )
        aliases = team_aliases or {}
        competition_map = competition_aliases or {}
        matches = tuple(
            fixture for fixture in snapshot.fixtures
            if _matches(
                fixture, clues, team_aliases=aliases,
                competition_aliases=competition_map,
            )
        )
    except (TypeError, ValueError) as error:
        return FixtureResolution(
            status=FixtureResolutionStatus.INVALID,
            fixture=None,
            candidates=(),
            provider_snapshot_id=snapshot.provider_snapshot_id,
            warnings=(str(error),),
        )

    active = tuple(item for item in matches if item.status is FixtureStatus.SCHEDULED)
    if len(active) == 1:
        return FixtureResolution(
            status=FixtureResolutionStatus.RESOLVED,
            fixture=active[0], candidates=active,
            provider_snapshot_id=snapshot.provider_snapshot_id,
        )
    if len(active) > 1:
        return FixtureResolution(
            status=FixtureResolutionStatus.NEEDS_CONFIRMATION,
            fixture=None, candidates=active,
            provider_snapshot_id=snapshot.provider_snapshot_id,
            warnings=("multiple provider-backed scheduled fixtures match the clues",),
        )
    if matches:
        return FixtureResolution(
            status=FixtureResolutionStatus.UNRESOLVED,
            fixture=None, candidates=matches,
            provider_snapshot_id=snapshot.provider_snapshot_id,
            warnings=("matching fixtures are not currently scheduled",),
        )
    return FixtureResolution(
        status=FixtureResolutionStatus.UNRESOLVED,
        fixture=None, candidates=(),
        provider_snapshot_id=snapshot.provider_snapshot_id,
        warnings=("no provider-backed fixture matches the clues",),
    )
