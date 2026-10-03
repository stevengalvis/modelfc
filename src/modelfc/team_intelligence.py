"""Bounded, descriptive E1 corner intelligence. No provider or evidence access."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
import csv
import fcntl
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from modelfc.corner_data import load_data_config
from modelfc.corner_refresh import current_season

CALCULATION_VERSION = "team-corners-v1"
RULE_VERSION = "corner-patterns-v1"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_FIXTURES = 552
# Fixed, editorial family order. Native magnitude ranks only within a family.
FAMILY_ORDER = (
    "THRESHOLD_STREAK", "ATTACK_INCREASE", "ATTACK_DECLINE",
    "CONCESSION_INCREASE", "CONCESSION_DECLINE", "DIFFERENTIAL_CHANGE",
    "HOME_AWAY_SPLIT", "HIGH_MATCH_CORNER_ENVIRONMENT", "LOW_MATCH_CORNER_ENVIRONMENT",
)
Family = Literal["THRESHOLD_STREAK", "ATTACK_INCREASE", "ATTACK_DECLINE",
                 "CONCESSION_INCREASE", "CONCESSION_DECLINE", "DIFFERENTIAL_CHANGE",
                 "HOME_AWAY_SPLIT", "HIGH_MATCH_CORNER_ENVIRONMENT", "LOW_MATCH_CORNER_ENVIRONMENT"]
State = Literal["AVAILABLE", "INSUFFICIENT_SAMPLE", "INCOMPLETE_COVERAGE"]
Scope = Literal["ALL", "HOME", "AWAY"]
Metric = Literal["won", "conceded", "differential", "total"]


class TeamIntelligenceError(ValueError):
    """Fixed domain error; public boundary never returns exception details."""


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class TeamIdentity(Contract):
    team_id: str = Field(pattern=r"^[a-z]+(?:-[a-z]+)*$", max_length=32)
    display_name: str
    source_name: str


# Historical-source identities only. Provider aliases deliberately live elsewhere.
_IDENTITIES = (
    ("birmingham", "Birmingham City", "Birmingham"),
    ("blackburn", "Blackburn Rovers", "Blackburn"),
    ("bolton", "Bolton Wanderers", "Bolton"),
    ("bristol-city", "Bristol City", "Bristol City"),
    ("burnley", "Burnley", "Burnley"), ("cardiff", "Cardiff City", "Cardiff"),
    ("charlton", "Charlton Athletic", "Charlton"), ("derby", "Derby County", "Derby"),
    ("lincoln", "Lincoln City", "Lincoln"), ("middlesbrough", "Middlesbrough", "Middlesbrough"),
    ("millwall", "Millwall", "Millwall"), ("norwich", "Norwich City", "Norwich"),
    ("portsmouth", "Portsmouth", "Portsmouth"), ("preston", "Preston North End", "Preston"),
    ("qpr", "Queens Park Rangers", "QPR"), ("sheffield-united", "Sheffield United", "Sheffield United"),
    ("southampton", "Southampton", "Southampton"), ("stoke", "Stoke City", "Stoke"),
    ("swansea", "Swansea City", "Swansea"), ("watford", "Watford", "Watford"),
    ("west-brom", "West Bromwich Albion", "West Brom"), ("west-ham", "West Ham United", "West Ham"),
    ("wolves", "Wolverhampton Wanderers", "Wolves"), ("wrexham", "Wrexham", "Wrexham"),
)
REGISTRY = tuple(TeamIdentity(team_id=i, display_name=d, source_name=s) for i, d, s in _IDENTITIES)
if len({t.team_id for t in REGISTRY}) != len(REGISTRY) or len({t.source_name for t in REGISTRY}) != len(REGISTRY):
    raise RuntimeError("Invalid E1 identity registry")
BY_SOURCE = {t.source_name: t for t in REGISTRY}
BY_ID = {t.team_id: t for t in REGISTRY}


class Sample(Contract):
    completed: int = Field(ge=0, le=46)
    n: int = Field(ge=0, le=46)
    start_date: str | None
    end_date: str | None
    won_sum: int = Field(ge=0)
    conceded_sum: int = Field(ge=0)
    won: float | None
    conceded: float | None
    differential: float | None
    total: float | None


class Coverage(Contract):
    completed: int = Field(ge=0, le=46)
    covered: int = Field(ge=0, le=46)
    missing: int = Field(ge=0, le=46)
    home: int = Field(ge=0, le=23)
    away: int = Field(ge=0, le=23)
    latest_result_date: str
    latest_corner_date: str | None


class Window(Contract):
    state: State
    requested: Literal[5, 10]
    sample: Sample


class Recency(Contract):
    last_five: Window
    previous_five: Window
    last_ten: Window
    trend_state: State
    won_change: float | None
    conceded_change: float | None
    differential_change: float | None
    total_change: float | None


class Rank(Contract):
    rank: int | None = Field(ge=1, le=24)
    cohort_size: int = Field(ge=0, le=24)


class Ranks(Contract):
    won: Rank
    conceded: Rank
    differential: Rank
    total: Rank


class Threshold(Contract):
    threshold: Literal[9, 10, 11]
    count: int = Field(ge=0, le=46)
    denominator: int = Field(ge=0, le=46)
    frequency: float | None = Field(ge=0, le=1)


class Insight(Contract):
    rule_version: Literal["corner-patterns-v1"] = RULE_VERSION
    family: Family
    team: TeamIdentity
    scope: Scope
    metric: Metric
    recent: Sample
    baseline: Sample | None
    difference: float | None
    threshold: Literal[9, 10, 11] | None
    streak: int | None = Field(ge=5, le=46)
    # All nested threshold evidence is retained, even when only one is selected.
    threshold_runs: list[int] = Field(min_length=0, max_length=3)


class TeamSummary(Contract):
    team: TeamIdentity
    coverage: Coverage
    primary: Sample
    home: Sample
    away: Sample
    recency: Recency
    ranks: Ranks
    thresholds: list[Threshold] = Field(min_length=3, max_length=3)


class RecentMatch(Contract):
    date: str
    opponent: TeamIdentity
    venue: Literal["HOME", "AWAY"]
    won: int | None = Field(ge=0)
    conceded: int | None = Field(ge=0)
    total: int | None = Field(ge=0)


class Metadata(Contract):
    schema_version: Literal[1] = 1
    calculation_version: Literal["team-corners-v1"] = CALCULATION_VERSION
    competition: Literal["E1"] = "E1"
    season: str = Field(pattern=r"^[0-9]{4}$")
    data_cutoff: str | None
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["football-data"] = "football-data"
    roster_state: Literal["COMPLETE", "PARTIAL"]


class TrendSummary(Contract):
    trend_state: State
    won_change: float | None


class TeamOverview(Contract):
    team: TeamIdentity
    coverage: Coverage
    primary: Sample
    ranks: Ranks
    recency: TrendSummary


class TeamList(Contract):
    metadata: Metadata
    teams: list[TeamOverview] = Field(max_length=24)
    insights: list[Insight] = Field(max_length=6)


class TeamProfile(Contract):
    metadata: Metadata
    summary: TeamSummary
    recent_matches: list[RecentMatch] = Field(max_length=10)
    insights: list[Insight] = Field(max_length=3)


class InsightList(Contract):
    metadata: Metadata
    insights: list[Insight] = Field(max_length=6)


@dataclass(frozen=True)
class Match:
    day: date
    opponent: TeamIdentity
    venue: Literal["HOME", "AWAY"]
    won: int | None
    conceded: int | None


@contextmanager
def _directory(path: Path):
    """O_PATH permits traversal without directory listing permission; no symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise TeamIntelligenceError("HISTORY_UNAVAILABLE")
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            following = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = following
        yield fd
    finally:
        os.close(fd)


def read_source(directory: Path, today: date, *, timeout: float = 0.25) -> tuple[bytes, str]:
    """Copy/fingerprint under the existing lock; callers parse after release."""
    try:
        with _directory(directory) as root, _directory(directory / "data/corner-refresh") as locks:
            lock = os.open("refresh.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=locks)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0:
                    raise TeamIntelligenceError("HISTORY_UNAVAILABLE")
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TeamIntelligenceError("HISTORY_UNAVAILABLE") from None
                        time.sleep(0.005)
                name = f"E1_{current_season(today)}.csv"
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root)
                with os.fdopen(fd, "rb") as source:
                    file_info = os.fstat(source.fileno())
                    if not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1 or file_info.st_size > MAX_SOURCE_BYTES:
                        raise TeamIntelligenceError("HISTORY_UNAVAILABLE")
                    payload = source.read(MAX_SOURCE_BYTES + 1)
                    if len(payload) > MAX_SOURCE_BYTES:
                        raise TeamIntelligenceError("HISTORY_UNAVAILABLE")
                    revision = hashlib.sha256(payload).hexdigest()
                final = os.stat("refresh.lock", dir_fd=locks, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (final.st_dev, final.st_ino):
                    raise TeamIntelligenceError("HISTORY_UNAVAILABLE")
                return payload, revision
            finally:
                os.close(lock)
    except (OSError, ValueError) as error:
        raise TeamIntelligenceError("HISTORY_UNAVAILABLE") from error


def _integer(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,4}", value):
        raise TeamIntelligenceError("HISTORY_INVALID")
    return int(value)


def parse_source(payload: bytes, today: date) -> dict[str, list[Match]]:
    """Validate corner/result fields only, without optional-stat dependencies."""
    first_year = today.year - (today.month < 7)
    first, end = date(first_year, 7, 1), date(first_year + 1, 7, 1)
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
        required = {"Date", "HomeTeam", "AwayTeam", "HC", "AC", "Div", "FTHG", "FTAG", "FTR"}
        fields = reader.fieldnames or []
        if not required.issubset(fields) or len(set(fields)) != len(fields):
            raise TeamIntelligenceError("HISTORY_INVALID")
        population: dict[str, list[Match]] = {}
        seen = set()
        team_days = set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise TeamIntelligenceError("HISTORY_INVALID")
            row = {key: value.strip() for key, value in row.items()}
            day = datetime.strptime(row["Date"], "%d/%m/%Y").date()
            home, away = BY_SOURCE[row["HomeTeam"]], BY_SOURCE[row["AwayTeam"]]
            if row["Div"] != "E1" or home == away or not first <= day < end:
                raise TeamIntelligenceError("HISTORY_INVALID")
            identity = (day, home.team_id, away.team_id)
            if identity in seen or len(seen) >= MAX_FIXTURES:
                raise TeamIntelligenceError("HISTORY_INVALID")
            seen.add(identity)
            scores = (row["FTHG"], row["FTAG"], row["FTR"])
            hc, ac = row["HC"], row["AC"]
            if not any(scores):
                if hc or ac:
                    raise TeamIntelligenceError("HISTORY_INVALID")
                continue  # scheduled/incomplete rows do not enter the population
            hg, ag = _integer(scores[0]), _integer(scores[1])
            if scores[2] != ("H" if hg > ag else "A" if ag > hg else "D") or day > today:
                raise TeamIntelligenceError("HISTORY_INVALID")
            if bool(hc) != bool(ac):
                raise TeamIntelligenceError("HISTORY_INVALID")
            won, conceded = (_integer(hc), _integer(ac)) if hc else (None, None)
            for team, opponent, venue, w, c in ((home, away, "HOME", won, conceded), (away, home, "AWAY", conceded, won)):
                key = (team.team_id, day)
                if key in team_days:
                    raise TeamIntelligenceError("HISTORY_INVALID")
                team_days.add(key)
                population.setdefault(team.team_id, []).append(Match(day, opponent, venue, w, c))
        if len(population) > 24:
            raise TeamIntelligenceError("HISTORY_INVALID")
        for matches in population.values():
            matches.sort(key=lambda m: m.day)
            if len(matches) > 46 or sum(m.venue == "HOME" for m in matches) > 23 or sum(m.venue == "AWAY" for m in matches) > 23:
                raise TeamIntelligenceError("HISTORY_INVALID")
        return population
    except (KeyError, ValueError, UnicodeError, csv.Error) as error:
        raise TeamIntelligenceError("HISTORY_INVALID") from error


def sample(matches: list[Match]) -> Sample:
    covered = [m for m in matches if m.won is not None]
    n = len(covered)
    w, c = sum(m.won for m in covered), sum(m.conceded for m in covered)
    return Sample(completed=len(matches), n=n, start_date=matches[0].day.isoformat() if matches else None,
                  end_date=matches[-1].day.isoformat() if matches else None,
                  won_sum=w, conceded_sum=c, won=w/n if n else None, conceded=c/n if n else None,
                  differential=(w-c)/n if n else None, total=(w+c)/n if n else None)


def window(matches: list[Match], n: Literal[5, 10], previous: bool = False) -> Window:
    enough = len(matches) >= n * (2 if previous else 1)
    selected = matches[-2*n:-n] if previous and enough else ([] if previous else matches[-n:])
    s = sample(selected)
    state = "INSUFFICIENT_SAMPLE" if not enough else "AVAILABLE" if s.n == n else "INCOMPLETE_COVERAGE"
    return Window(state=state, requested=n, sample=s)


def recency(matches: list[Match]) -> Recency:
    recent, prior, ten = window(matches, 5), window(matches, 5, True), window(matches, 10)
    state = "INSUFFICIENT_SAMPLE" if len(matches) < 10 else "AVAILABLE" if recent.state == prior.state == "AVAILABLE" else "INCOMPLETE_COVERAGE"
    changes = {f"{metric}_change": getattr(recent.sample, metric) - getattr(prior.sample, metric)
               if state == "AVAILABLE" else None for metric in ("won", "conceded", "differential", "total")}
    return Recency(last_five=recent, previous_five=prior, last_ten=ten, trend_state=state, **changes)


def _fraction(s: Sample, metric: str) -> Fraction:
    numerator = {"won": s.won_sum, "conceded": s.conceded_sum,
                 "differential": s.won_sum-s.conceded_sum, "total": s.won_sum+s.conceded_sum}[metric]
    return Fraction(numerator, s.n)


def _insights(team: TeamSummary, matches: list[Match]) -> list[Insight]:
    findings = []
    def add(family, metric, recent, baseline=None, difference=None, threshold=None, streak=None, runs=()):
        findings.append(Insight(family=family, team=team.team, scope="ALL", metric=metric, recent=recent,
                               baseline=baseline, difference=difference, threshold=threshold, streak=streak,
                               threshold_runs=list(runs)))
    if team.recency.trend_state == "AVAILABLE":
        r, b = team.recency.last_five.sample, team.recency.previous_five.sample
        for metric, positive, negative in (("won", "ATTACK_INCREASE", "ATTACK_DECLINE"),
                                           ("conceded", "CONCESSION_INCREASE", "CONCESSION_DECLINE"),
                                           ("differential", "DIFFERENTIAL_CHANGE", "DIFFERENTIAL_CHANGE")):
            delta = _fraction(r, metric) - _fraction(b, metric)
            if abs(delta) >= 1:
                add(positive if delta > 0 else negative, metric, r, b, float(delta))
    if team.home.n >= 5 and team.away.n >= 5:
        delta = _fraction(team.home, "won") - _fraction(team.away, "won")
        if abs(delta) >= 1:
            add("HOME_AWAY_SPLIT", "won", team.home, team.away, float(delta))
    if team.primary.n >= 5:
        total = _fraction(team.primary, "total")
        if total >= 11:
            add("HIGH_MATCH_CORNER_ENVIRONMENT", "total", team.primary)
        elif total <= 9:
            add("LOW_MATCH_CORNER_ENVIRONMENT", "total", team.primary)
    runs = []
    for threshold in (9, 10, 11):
        count = 0
        for match in reversed(matches):
            if match.won is None or match.won + match.conceded < threshold:
                break
            count += 1
        runs.append(count)
    eligible = [i for i, length in enumerate(runs) if length >= 5]
    if eligible:
        index = max(eligible)
        add("THRESHOLD_STREAK", "total", sample(matches[-runs[index]:]), threshold=9+index,
            streak=runs[index], runs=runs)
    return findings


def _insight_key(item: Insight):
    if item.family == "THRESHOLD_STREAK":
        return (-item.threshold, -item.streak, item.team.team_id)
    if item.family == "LOW_MATCH_CORNER_ENVIRONMENT":
        return (_fraction(item.recent, "total"), item.team.team_id)
    if item.family == "HIGH_MATCH_CORNER_ENVIRONMENT":
        return (-_fraction(item.recent, "total"), item.team.team_id)
    return (-abs(_fraction(item.recent, item.metric) - _fraction(item.baseline, item.metric)), item.team.team_id)


def select_insights(findings: list[Insight], limit: int = 6, *, one_per_team=True) -> list[Insight]:
    queues = {family: sorted((f for f in findings if f.family == family), key=_insight_key) for family in FAMILY_ORDER}
    selected, teams = [], set()
    while len(selected) < limit:
        progress = False
        for family in FAMILY_ORDER:
            while queues[family]:
                candidate = queues[family].pop(0)
                if one_per_team and candidate.team.team_id in teams:
                    continue
                # Change metrics share the same windows: keep one. Streak and
                # environment findings are retained as distinct streak/rate facts.
                if not one_per_team and candidate.baseline is not None and any(
                    s.baseline is not None and s.family != "HOME_AWAY_SPLIT" and candidate.family != "HOME_AWAY_SPLIT"
                    for s in selected
                ):
                    continue
                if not one_per_team and candidate.family in ("HIGH_MATCH_CORNER_ENVIRONMENT", "LOW_MATCH_CORNER_ENVIRONMENT") and any(
                    s.family == "THRESHOLD_STREAK" and s.recent.start_date == candidate.recent.start_date
                    and s.recent.end_date == candidate.recent.end_date for s in selected
                ):
                    continue
                selected.append(candidate)
                teams.add(candidate.team.team_id)
                progress = True
                break
            if len(selected) == limit:
                break
        if not progress:
            break
    return selected


@dataclass(frozen=True)
class Population:
    metadata: Metadata
    summaries: tuple[TeamSummary, ...]
    matches: dict[str, list[Match]]
    findings: tuple[Insight, ...]

    def list_team_intelligence(self) -> TeamList:
        return TeamList(metadata=self.metadata, teams=[TeamOverview(team=s.team, coverage=s.coverage, primary=s.primary, ranks=s.ranks,
                               recency=TrendSummary(trend_state=s.recency.trend_state, won_change=s.recency.won_change))
                           for s in self.summaries], insights=self.find_team_insights().insights)

    def find_team_insights(self) -> InsightList:
        return InsightList(metadata=self.metadata, insights=select_insights(list(self.findings)))

    def get_team_profile(self, team_id: str) -> TeamProfile:
        summary = next((s for s in self.summaries if s.team.team_id == team_id), None)
        if summary is None:
            raise TeamIntelligenceError("TEAM_NOT_FOUND")
        recent = [RecentMatch(date=m.day.isoformat(), opponent=m.opponent, venue=m.venue,
                              won=m.won, conceded=m.conceded, total=m.won+m.conceded if m.won is not None else None)
                  for m in reversed(self.matches[team_id][-10:])]
        return TeamProfile(metadata=self.metadata, summary=summary, recent_matches=recent,
                           insights=select_insights([f for f in self.findings if f.team.team_id == team_id], 3, one_per_team=False))


def calculate_population(payload: bytes, revision: str, today: date) -> Population:
    matches = parse_source(payload, today)
    samples = {tid: sample(ms) for tid, ms in matches.items()}
    eligible = {tid: s for tid, s in samples.items() if s.n >= 5}
    ranks = {}
    for metric in ("won", "conceded", "differential", "total"):
        ranks[metric] = {tid: 1 + sum((_fraction(other, metric) < _fraction(s, metric)) if metric == "conceded"
                                     else (_fraction(other, metric) > _fraction(s, metric)) for other in eligible.values())
                         for tid, s in eligible.items()}
    summaries = []
    for tid in sorted(matches):
        ms, primary = matches[tid], samples[tid]
        home, away = sample([m for m in ms if m.venue == "HOME"]), sample([m for m in ms if m.venue == "AWAY"])
        covered = [m for m in ms if m.won is not None]
        summaries.append(TeamSummary(team=BY_ID[tid], primary=primary, home=home, away=away,
            coverage=Coverage(completed=len(ms), covered=len(covered), missing=len(ms)-len(covered),
                              home=home.completed, away=away.completed, latest_result_date=ms[-1].day.isoformat(),
                              latest_corner_date=covered[-1].day.isoformat() if covered else None),
            recency=recency(ms), ranks=Ranks(**{metric: Rank(rank=ranks[metric].get(tid), cohort_size=len(eligible))
                                             for metric in ranks}),
            thresholds=[Threshold(threshold=t, count=sum(m.won+m.conceded >= t for m in covered),
                                  denominator=len(covered), frequency=sum(m.won+m.conceded >= t for m in covered)/len(covered) if covered else None)
                        for t in (9, 10, 11)]))
    metadata = Metadata(season=current_season(today), source_revision=revision,
                        data_cutoff=max((m.day.isoformat() for ms in matches.values() for m in ms), default=None),
                        roster_state="COMPLETE" if len(matches) == 24 else "PARTIAL")
    return Population(metadata, tuple(summaries), matches,
                      tuple(f for team in summaries for f in _insights(team, matches[team.team.team_id])))


def load_population(config_path: Path, *, today: date | None = None) -> Population:
    today = today or datetime.now(timezone.utc).date()
    try:
        config = load_data_config(config_path, resolve_directory=False)
        if "E1" not in config.leagues:
            raise TeamIntelligenceError("HISTORY_UNAVAILABLE")
        # Preserve the lexical path so no-follow traversal rejects symlinked ancestors.
        payload, revision = read_source(config.directory, today)
        return calculate_population(payload, revision, today)
    except (ValueError, OSError) as error:
        if isinstance(error, TeamIntelligenceError):
            raise
        raise TeamIntelligenceError("HISTORY_UNAVAILABLE") from error
