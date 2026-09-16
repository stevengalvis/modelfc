"""Configuration and history selection for locally managed European CSVs."""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
import re
from typing import Iterator

from modelfc.matches import TeamCornerObservation
from modelfc.providers.football_data import load_corner_history


FOOTBALL_DATA_LEAGUES = (
    "E0", "E1", "SP1", "I1", "D1", "F1", "P1",
    "I2", "F2", "D2", "SP2", "T1",
)


@dataclass(frozen=True)
class CornerDataConfig:
    directory: Path
    leagues: tuple[str, ...]
    max_age_days: int


@contextmanager
def configured_history_lock(config: CornerDataConfig) -> Iterator[None]:
    """Keep managed CSVs stable while a reader loads and fingerprints them."""
    state = config.directory / "data" / "corner-refresh"
    try:
        state.mkdir(parents=True, exist_ok=True)
        with (state / "refresh.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError as error:
        raise ValueError(f"could not lock configured corner history: {error}") from error


def load_data_config(path: Path) -> CornerDataConfig:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"could not read corner data config: {error}") from error
    fields = {"data_directory", "leagues", "max_age_days"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("config requires exactly data_directory, leagues, max_age_days")
    directory, leagues, age = (value[key] for key in ("data_directory", "leagues", "max_age_days"))
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError("data_directory must be a non-empty path")
    if (
        not isinstance(leagues, list) or not leagues
        or any(not isinstance(code, str) or code not in FOOTBALL_DATA_LEAGUES for code in leagues)
        or len(set(leagues)) != len(leagues)
    ):
        raise ValueError("leagues must contain unique supported Football-Data codes")
    if isinstance(age, bool) or not isinstance(age, int) or age < 1:
        raise ValueError("max_age_days must be a positive integer")
    return CornerDataConfig((path.resolve().parent / directory).resolve(), tuple(leagues), age)


def configured_history_paths(config: CornerDataConfig, league: str) -> list[Path]:
    """Return canonical season files for one configured competition."""
    if league not in config.leagues:
        raise ValueError(f"competition {league!r} is not enabled in the data config")
    # Only canonical season names; never include *_update.csv or backups.
    pattern = re.compile(rf"{re.escape(league)}_[0-9]{{4}}\.csv")
    paths = sorted(path for path in config.directory.glob(f"{league}_*.csv") if pattern.fullmatch(path.name))
    if not paths:
        raise ValueError(f"no {league}_NNNN.csv history files in {config.directory}")
    return paths


def configured_history(config: CornerDataConfig, league: str) -> list[TeamCornerObservation]:
    paths = configured_history_paths(config, league)
    observations = load_corner_history(paths, competition=league)
    seen = set()
    for item in observations:
        identity = (item.match_date, item.team, item.opponent, item.venue)
        if identity in seen:
            raise ValueError(f"overlapping {league} history: {identity}; remove duplicate source files")
        seen.add(identity)
    return observations
