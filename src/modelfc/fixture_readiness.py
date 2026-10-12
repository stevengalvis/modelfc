"""Offline saved-calendar projection. The HTTP reader never opens private evidence."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from modelfc.acquisition_planner import utc
from modelfc.history_completeness import parse_calendar, MAX_CALENDAR_BYTES
from modelfc.ledger_storage import write_new_record
from modelfc.research_predictions import records

LIMIT = 2_000_000


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def timestamp(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("INVALID_TIMESTAMP")
        return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        raise ValueError("INVALID_TIMESTAMP") from None


class Market(Contract):
    market: Literal["CORNER", "BTTS"]
    state: Literal["RECORDED", "NOT_RECORDED"]
    frozen_at_utc: str | None

    @model_validator(mode="after")
    def valid(self):
        if (self.state == "RECORDED") != (self.frozen_at_utc is not None):
            raise ValueError("INVALID_MARKET_STATE")
        if self.frozen_at_utc is not None:
            timestamp(self.frozen_at_utc)
        return self


class Fixture(Contract):
    fixture_id: int = Field(gt=0)
    home_team: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z .'-]+$")
    away_team: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z .'-]+$")
    kickoff_utc: str
    status: Literal["SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "SUSPENDED", "POSTPONED", "CANCELLED", "FINISHED"]
    markets: list[Market] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def valid(self):
        kickoff = timestamp(self.kickoff_utc)
        if self.home_team == self.away_team or [m.market for m in self.markets] != ["CORNER", "BTTS"]:
            raise ValueError("INVALID_FIXTURE")
        if any(m.frozen_at_utc and timestamp(m.frozen_at_utc) >= kickoff for m in self.markets):
            raise ValueError("INVALID_FREEZE_TIME")
        return self


class SavedProjection(Contract):
    version: Literal[1]
    generated_at_utc: str
    calendar_retrieved_at_utc: str
    fixtures: list[Fixture] = Field(min_length=552, max_length=552)

    @model_validator(mode="after")
    def valid(self):
        generated, retrieved = timestamp(self.generated_at_utc), timestamp(self.calendar_retrieved_at_utc)
        if generated < retrieved or len({f.fixture_id for f in self.fixtures}) != len(self.fixtures):
            raise ValueError("INVALID_PROJECTION")
        if len({(f.home_team, f.away_team) for f in self.fixtures}) != len(self.fixtures):
            raise ValueError("INVALID_PROJECTION")
        if any(m.frozen_at_utc and timestamp(m.frozen_at_utc) > generated
               for f in self.fixtures for m in f.markets):
            raise ValueError("INVALID_PROJECTION")
        return self


def project(raw, *, forecasts, observed):
    """Validate at actual retrieval time; age is evaluated separately by the reader.

    This intentionally does not make stale evidence eligible for forecasting.
    Caller supplies validated research_predictions.records, never private receipts.
    """
    observed = utc(observed)
    envelope = json.loads(raw)
    retrieved = timestamp(envelope["retrieved_at"])
    if retrieved > observed:
        raise ValueError("FUTURE_CALENDAR")
    calendar = parse_calendar(raw, as_of=retrieved)
    fixtures = []
    for match in calendar.matches:
        markets = []
        for market in ("CORNER", "BTTS"):
            matching = [f for f in forecasts if
                (f["competition"], f["provider_fixture_id"], timestamp(f["kickoff_utc"]),
                 f["home_team"], f["away_team"], f["prediction_type"]) ==
                ("E1", str(match.fixture_id), match.kickoff, match.home, match.away, market)]
            frozen = min((timestamp(f["created_at_utc"]) for f in matching), default=None)
            markets.append({"market": market, "state": "RECORDED" if frozen else "NOT_RECORDED",
                            "frozen_at_utc": frozen.isoformat() if frozen else None})
        fixtures.append({"fixture_id": match.fixture_id, "home_team": match.home,
                         "away_team": match.away, "kickoff_utc": match.kickoff.isoformat(),
                         "status": match.status, "markets": markets})
    return SavedProjection.model_validate({"version": 1, "generated_at_utc": observed.isoformat(),
        "calendar_retrieved_at_utc": retrieved.isoformat(), "fixtures": fixtures}).model_dump()


def read_bounded(path, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("INVALID_FILE")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("INVALID_FILE")
        return raw
    finally:
        os.close(fd)


def public_status(path=None, *, observed=None):
    observed = utc(observed or datetime.now(timezone.utc))
    result = {"version": 1, "observed_at_utc": observed.isoformat(),
              "generated_at_utc": None, "calendar_retrieved_at_utc": None,
              "calendar_state": "MISSING", "reason": "PROJECTION_NOT_CONFIGURED",
              "competitions": ["E1", "E0"], "e0_state": "CALENDAR_ADAPTER_REQUIRED",
              "history_coverage": "NOT_ASSESSED", "fixtures": []}
    if not path:
        return result
    try:
        saved = SavedProjection.model_validate_json(read_bounded(path))
        if timestamp(saved.generated_at_utc) > observed:
            raise ValueError("FUTURE_PROJECTION")
    except (OSError, ValueError):
        return {**result, "calendar_state": "UNAVAILABLE", "reason": "PROJECTION_UNAVAILABLE"}
    fresh = observed - timestamp(saved.calendar_retrieved_at_utc) <= timedelta(hours=6)
    return {**result, "generated_at_utc": saved.generated_at_utc,
            "calendar_retrieved_at_utc": saved.calendar_retrieved_at_utc,
            "calendar_state": "FRESH" if fresh else "STALE", "reason": None,
            "fixtures": [f.model_dump() for f in sorted(saved.fixtures,
                         key=lambda f: (timestamp(f.kickoff_utc), f.fixture_id))]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", required=True)
    parser.add_argument("--calendar-sha256", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output", required=True, help="New sanitized file; never overwritten")
    args = parser.parse_args()
    try:
        raw = read_bounded(args.calendar, MAX_CALENDAR_BYTES)
        if hashlib.sha256(raw).hexdigest() != args.calendar_sha256:
            raise ValueError("PIN_MISMATCH")
        observed = datetime.now(timezone.utc)
        value = project(raw, forecasts=records(args.state_dir, now=observed), observed=observed)
        write_new_record(Path(args.output), value)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "FIXTURE_PROJECTION_REJECTED\n")
    print("FIXTURE_PROJECTION_CREATED provider_requests=0")


if __name__ == "__main__":
    main()
