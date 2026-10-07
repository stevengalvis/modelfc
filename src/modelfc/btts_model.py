"""Frozen DeepFC arithmetic Poisson BTTS research reference, with no network I/O."""

import csv
from datetime import date, datetime
import hashlib
from io import StringIO
import math
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import Field, model_validator

from modelfc.btts_market_data import (
    BttsFixture, Digest, ResearchContract, Text, require_competition, timestamp, utc,
)
from modelfc.corner_data import configured_history_paths, load_data_config
from modelfc.ledger_storage import existing_read_lock

DEEPFC_SOURCE_COMMIT = "ecbae64d0684fceaad63d843e0b74ca54d21c0fc"
MODEL_NAME = "team-opponent-arithmetic-poisson-btts"
MODEL_VERSION = "deepfc-arithmetic-btts-v1"
PRIOR_MATCHES = 5
MIN_HISTORY = 100
Count = Annotated[int, Field(ge=0)]


class GoalResult(ResearchContract):
    competition: Text
    match_date: date
    home_team: Text
    away_team: Text
    home_goals: Count
    away_goals: Count

    @model_validator(mode="after")
    def validate_result(self):
        require_competition(self.competition)
        if self.home_team == self.away_team:
            raise ValueError("BTTS result teams must differ")
        return self


class HistorySource(ResearchContract):
    competition: Text
    filename: Text
    sha256: Digest

    @model_validator(mode="after")
    def validate_source(self):
        require_competition(self.competition)
        if not re.fullmatch(rf"{self.competition}_[0-9]{{4}}\.csv", self.filename):
            raise ValueError("BTTS history source must be a canonical season file")
        return self


class GoalHistory(ResearchContract):
    competition: Text
    results: tuple[GoalResult, ...]
    sources: tuple[HistorySource, ...]

    @model_validator(mode="after")
    def validate_history(self):
        require_competition(self.competition)
        if not self.sources or any(s.competition != self.competition for s in self.sources):
            raise ValueError("BTTS history source competition mismatch")
        names = [s.filename for s in self.sources]
        if names != sorted(set(names)):
            raise ValueError("BTTS history sources must be unique and ordered")
        keys = [(r.match_date, r.home_team, r.away_team) for r in self.results]
        if len(keys) != len(set(keys)) or any(r.competition != self.competition for r in self.results):
            raise ValueError("BTTS duplicate result or competition mismatch")
        return self


def history_from_bytes(competition: str, sources: tuple[tuple[str, bytes], ...]) -> GoalHistory:
    """Validate goals and retain the DeepFC completed-corner cohort; corners are not features."""
    require_competition(competition)
    results, provenance, seen = [], [], set()
    for filename, content in sorted(sources):
        provenance.append(HistorySource(competition=competition, filename=filename,
                                        sha256=hashlib.sha256(content).hexdigest()))
        reader = csv.DictReader(StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HC", "AC"}
        if not required <= set(reader.fieldnames or ()) or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("INVALID_BTTS_HISTORY")
        for row in reader:
            if None in row or any(v is None for v in row.values()) or row["Div"] != competition:
                raise ValueError("INVALID_BTTS_HISTORY")
            for pattern in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    day = datetime.strptime(row["Date"].strip(), pattern).date()
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("INVALID_BTTS_HISTORY")
            home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
            if not home or not away or home == away:
                raise ValueError("INVALID_BTTS_HISTORY")
            key = (day, home, away)
            if key in seen:
                raise ValueError("INVALID_BTTS_HISTORY")
            seen.add(key)
            def pair(first, second):
                raw = [row[first].strip(), row[second].strip()]
                if raw == ["", ""]:
                    return None
                values = [float(v) for v in raw]
                if any(not math.isfinite(v) or v < 0 or not v.is_integer() for v in values):
                    raise ValueError("INVALID_BTTS_HISTORY")
                return tuple(int(v) for v in values)
            goals, corners = pair("FTHG", "FTAG"), pair("HC", "AC")
            if corners is not None and goals is None:
                raise ValueError("INVALID_BTTS_HISTORY")
            if goals is not None and corners is not None:
                results.append(GoalResult(competition=competition, match_date=day,
                    home_team=home, away_team=away, home_goals=goals[0], away_goals=goals[1]))
    return GoalHistory(competition=competition, results=tuple(results), sources=tuple(provenance))


def load_goal_history(config_path: Path, competition: str) -> GoalHistory:
    """Offline writer-side loader. Read coherent configured bytes, then release before parsing."""
    require_competition(competition)
    config = load_data_config(Path(config_path))
    sources = []
    with existing_read_lock(config.directory / "data" / "corner-refresh" / "refresh.lock"):
        paths = configured_history_paths(config, competition)
        if len(paths) > 32:
            raise ValueError("BTTS history exceeds V1 bounds")
        for path in paths:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
                    raise ValueError("INVALID_BTTS_HISTORY")
                content = stream.read(2_000_001)
                if len(content) > 2_000_000:
                    raise ValueError("INVALID_BTTS_HISTORY")
                sources.append((path.name, content))
    return history_from_bytes(competition, tuple(sources))


class FrozenInputs(ResearchContract):
    competition: Text
    cutoff_date: date
    latest_history_date: date
    history_matches: Annotated[int, Field(ge=MIN_HISTORY)]
    league_home_goals: Count
    league_away_goals: Count
    home_venue_matches: Count
    home_goals_for: Count
    home_goals_against: Count
    away_venue_matches: Count
    away_goals_for: Count
    away_goals_against: Count
    sources: tuple[HistorySource, ...]

    @model_validator(mode="after")
    def validate_inputs(self):
        require_competition(self.competition)
        if self.latest_history_date >= self.cutoff_date or not self.sources:
            raise ValueError("BTTS history must strictly precede the freeze date")
        if any(s.competition != self.competition for s in self.sources):
            raise ValueError("BTTS history competition mismatch")
        names = [s.filename for s in self.sources]
        if names != sorted(set(names)):
            raise ValueError("BTTS duplicate/unordered sources")
        if (max(self.home_venue_matches, self.away_venue_matches) > self.history_matches
            or max(self.home_goals_for, self.away_goals_against) > self.league_home_goals
            or max(self.away_goals_for, self.home_goals_against) > self.league_away_goals):
            raise ValueError("BTTS inconsistent aggregate history")
        if (self.home_venue_matches == 0 and (self.home_goals_for or self.home_goals_against)
                or self.away_venue_matches == 0 and (self.away_goals_for or self.away_goals_against)):
            raise ValueError("BTTS inconsistent empty venue history")
        return self


def arithmetic_rates(inputs: FrozenInputs) -> tuple[float, float]:
    home_average = max(1e-9, inputs.league_home_goals / inputs.history_matches)
    away_average = max(1e-9, inputs.league_away_goals / inputs.history_matches)
    def smoothed(total, count, average):
        return (total + PRIOR_MATCHES * average) / (count + PRIOR_MATCHES)
    return (
        (smoothed(inputs.home_goals_for, inputs.home_venue_matches, home_average)
         + smoothed(inputs.away_goals_against, inputs.away_venue_matches, home_average)) / 2,
        (smoothed(inputs.away_goals_for, inputs.away_venue_matches, away_average)
         + smoothed(inputs.home_goals_against, inputs.home_venue_matches, away_average)) / 2,
    )


def btts_probabilities(home_rate: float, away_rate: float) -> tuple[float, float]:
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
           for v in (home_rate, away_rate)):
        raise ValueError("BTTS rates must be finite and nonnegative")
    yes = -math.expm1(-home_rate) * -math.expm1(-away_rate)
    return yes, 1 - yes


class BttsForecast(ResearchContract):
    competition: Text
    fixture: BttsFixture
    frozen_at_utc: Text
    model_name: Literal["team-opponent-arithmetic-poisson-btts"] = MODEL_NAME
    model_version: Literal["deepfc-arithmetic-btts-v1"] = MODEL_VERSION
    deepfc_source_commit: Literal["ecbae64d0684fceaad63d843e0b74ca54d21c0fc"] = DEEPFC_SOURCE_COMMIT
    smoothing_matches: Literal[5] = PRIOR_MATCHES
    inputs: FrozenInputs
    home_expected_goals: Annotated[float, Field(ge=0)]
    away_expected_goals: Annotated[float, Field(ge=0)]
    yes_probability: Annotated[float, Field(ge=0, le=1)]
    no_probability: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def validate_forecast(self):
        if self.competition != self.fixture.competition or self.competition != self.inputs.competition:
            raise ValueError("BTTS forecast competition mismatch")
        frozen = timestamp(self.frozen_at_utc)
        if frozen >= timestamp(self.fixture.kickoff_utc) or self.inputs.cutoff_date != frozen.date():
            raise ValueError("BTTS forecast cutoff mismatch")
        expected = (*arithmetic_rates(self.inputs),)
        probabilities = btts_probabilities(*expected)
        if any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-12) for a, b in zip(
                (self.home_expected_goals, self.away_expected_goals, self.yes_probability, self.no_probability),
                (*expected, *probabilities))):
            raise ValueError("BTTS forecast disagrees with frozen model")
        return self


def freeze_btts_forecast(fixture: BttsFixture, history: GoalHistory, *, frozen_at: datetime) -> BttsForecast:
    if fixture.competition != history.competition:
        raise ValueError("BTTS history competition mismatch")
    frozen_at_utc = utc(frozen_at)
    cutoff = timestamp(frozen_at_utc).date()
    prior = [r for r in history.results if r.match_date < cutoff]
    if len(prior) < MIN_HISTORY:
        raise ValueError("INSUFFICIENT_BTTS_HISTORY")
    home = [r for r in prior if r.home_team == fixture.home_team]
    away = [r for r in prior if r.away_team == fixture.away_team]
    inputs = FrozenInputs(competition=fixture.competition, cutoff_date=cutoff,
        latest_history_date=max(r.match_date for r in prior), history_matches=len(prior),
        league_home_goals=sum(r.home_goals for r in prior), league_away_goals=sum(r.away_goals for r in prior),
        home_venue_matches=len(home), home_goals_for=sum(r.home_goals for r in home),
        home_goals_against=sum(r.away_goals for r in home), away_venue_matches=len(away),
        away_goals_for=sum(r.away_goals for r in away), away_goals_against=sum(r.home_goals for r in away),
        sources=history.sources)
    rates = arithmetic_rates(inputs)
    yes, no = btts_probabilities(*rates)
    return BttsForecast(competition=fixture.competition, fixture=fixture, frozen_at_utc=frozen_at_utc,
        inputs=inputs, home_expected_goals=rates[0], away_expected_goals=rates[1],
        yes_probability=yes, no_probability=no)
