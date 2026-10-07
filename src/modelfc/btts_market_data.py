"""Provider-independent, research-only full-match BTTS wire contracts."""

from datetime import datetime, timezone
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from modelfc.corner_markets import american_odds_terms
from modelfc.corner_opportunities import DECIMAL_AMERICAN_ODDS_TOLERANCE

Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Bookmaker = Literal["draftkings", "fanduel"]
BOOKMAKERS = ("draftkings", "fanduel")
ENABLED_COMPETITIONS = frozenset({"E1"})
Availability = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]


def require_competition(competition: str) -> str:
    if competition not in ENABLED_COMPETITIONS:
        raise ValueError("UNSUPPORTED_BTTS_COMPETITION")
    return competition


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("BTTS timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("BTTS timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ResearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class BttsFixture(ResearchContract):
    competition: Text
    provider: Text
    provider_fixture_id: Text
    home_team: Text
    away_team: Text
    kickoff_utc: Text

    @model_validator(mode="after")
    def validate_fixture(self):
        require_competition(self.competition)
        timestamp(self.kickoff_utc)
        if self.home_team == self.away_team:
            raise ValueError("BTTS fixture teams must differ")
        return self


class BttsSelection(ResearchContract):
    competition: Text
    provider: Text
    provider_fixture_id: Text
    market_type: Literal["BTTS"] = "BTTS"
    period: Literal["FULL_MATCH"] = "FULL_MATCH"
    bookmaker: Bookmaker
    side: Literal["YES", "NO"]
    american_odds: int
    decimal_odds: Annotated[float, Field(gt=1)]
    retrieved_at_utc: Text
    # Opaque adapter-generated digest, not a provider market/outcome ID.
    provider_quote_reference: Digest
    changed_at_utc: Text | None = None
    bookmaker_changed_at_utc: Text | None = None

    @model_validator(mode="after")
    def validate_price(self):
        require_competition(self.competition)
        profit, _ = american_odds_terms(self.american_odds)
        if not math.isclose(self.decimal_odds, 1 + profit, rel_tol=0,
                            abs_tol=DECIMAL_AMERICAN_ODDS_TOLERANCE):
            raise ValueError("BTTS price representations disagree")
        retrieved = timestamp(self.retrieved_at_utc)
        for changed in (self.changed_at_utc, self.bookmaker_changed_at_utc):
            if changed is not None and timestamp(changed) > retrieved:
                raise ValueError("BTTS change timestamp is in the future")
        return self


class BookAvailability(ResearchContract):
    competition: Text
    bookmaker: Bookmaker
    status: Availability

    @field_validator("competition")
    @classmethod
    def supported(cls, value):
        return require_competition(value)


class BttsObservation(ResearchContract):
    competition: Text
    fixture: BttsFixture
    retrieved_at_utc: Text
    selections: tuple[BttsSelection, ...]
    availability: tuple[BookAvailability, ...]
    provider_snapshot_sha256: Digest
    provider_metadata_sha256: Digest

    @model_validator(mode="after")
    def validate_observation(self):
        require_competition(self.competition)
        if self.competition != self.fixture.competition:
            raise ValueError("BTTS competition mismatch")
        observed = timestamp(self.retrieved_at_utc)
        if observed >= timestamp(self.fixture.kickoff_utc):
            raise ValueError("BTTS observation must precede kickoff")
        states = {s.bookmaker: s.status for s in self.availability}
        if len(states) != len(self.availability) or set(states) != set(BOOKMAKERS):
            raise ValueError("BTTS snapshot requires each supported book's coverage state")
        if any(s.competition != self.competition for s in self.availability):
            raise ValueError("BTTS availability competition mismatch")
        seen = set()
        for selection in self.selections:
            if ((selection.bookmaker, selection.side) in seen
                    or selection.competition != self.competition
                    or selection.provider != self.fixture.provider
                    or selection.provider_fixture_id != self.fixture.provider_fixture_id
                    or timestamp(selection.retrieved_at_utc) != observed
                    or states[selection.bookmaker] == "UNAVAILABLE"):
                raise ValueError("BTTS selection does not belong to coherent snapshot")
            seen.add((selection.bookmaker, selection.side))
        for book, state in states.items():
            sides = {s.side for s in self.selections if s.bookmaker == book}
            if state == "AVAILABLE" and sides != {"YES", "NO"}:
                raise ValueError("BTTS available book requires a complete pair")
        return self
