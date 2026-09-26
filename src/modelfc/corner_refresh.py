"""Refresh current-season Football-Data CSVs, preserving validated local history."""

import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
import fcntl
from http.client import HTTPException
import json
import os
from pathlib import Path
import pwd
import subprocess
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from modelfc.corner_data import CornerDataConfig, load_data_config
from modelfc.providers.football_data import load_corner_observations


MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def current_season(today: date) -> str:
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def download_csv(url: str) -> bytes:
    with urlopen(url, timeout=45) as response:
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValueError("download exceeds 20 MiB limit")
    return payload


def atomic_write(path: Path, payload: bytes, *, validator_read_user: str | None = None) -> None:
    """Publish one complete file on the same filesystem, cleaning up on error."""
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as target:
            temporary = Path(target.name)
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        if validator_read_user is not None:
            prepare_validator_read(temporary, validator_read_user)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_validator_read(path: Path, user: str) -> None:
    """Grant only the selected user read access before canonical publication."""
    try:
        uid = pwd.getpwnam(user).pw_uid
        subprocess.run(['/usr/bin/setfacl', '-m', f'u:{uid}:r--', '--', str(path)],
                       check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (KeyError, OSError, subprocess.SubprocessError):
        raise ValueError('validator read ACL preparation failed; replacement not published') from None


@contextmanager
def refresh_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    with (state / "refresh-run.lock").open("a") as run_lock:
        try:
            fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("another refresh is already running for this data directory") from error
        with (state / "refresh.lock").open("a") as data_lock:
            try:
                # Forecast readers hold a shared lock here until their source
                # hashes are saved. Wait for them instead of dropping a
                # scheduled refresh; the separate run lock still rejects a
                # second refresh process immediately.
                fcntl.flock(data_lock, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(data_lock, fcntl.LOCK_UN)


def snapshot(path: Path, league: str, season: str, today: date) -> dict:
    """Validate identities, completed scores and dates as well as corner values."""
    observations = load_corner_observations(path)
    if not observations:
        raise ValueError("no completed matches with usable corner pairs")
    start_year = today.year if today.month >= 7 else today.year - 1
    if season != current_season(today):
        raise ValueError("refresh only supports the current European season")
    first, last = date(start_year, 7, 1), date(start_year + 1, 7, 1)
    values = {}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HC", "AC"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("missing division, result or corner columns")
        for row in reader:
            if (row["Div"] or "").strip() != league:
                raise ValueError(f"unexpected division; expected {league}")
            match_date = datetime.strptime((row["Date"] or "").strip(), "%d/%m/%Y").date()
            if not first <= match_date < last or match_date > today:
                raise ValueError(f"match date {match_date} outside current season or in the future")
            home, away = ((row[key] or "").strip() for key in ("HomeTeam", "AwayTeam"))
            if not home or not away or home == away:
                raise ValueError("invalid team identities")
            identity = (match_date, home, away)
            if identity in seen:
                raise ValueError(f"duplicate fixture: {identity}")
            seen.add(identity)
            scores = [(row[key] or "").strip() for key in ("FTHG", "FTAG")]
            if any(not score.isascii() or not score.isdecimal() for score in scores):
                raise ValueError(f"incomplete or invalid final score: {identity}")
            hg, ag = map(int, scores)
            expected = "H" if hg > ag else "A" if ag > hg else "D"
            if (row["FTR"] or "").strip() != expected:
                raise ValueError(f"inconsistent final result: {identity}")
            hc, ac = ((row[key] or "").strip() for key in ("HC", "AC"))
            if hc or ac:
                values[identity] = (int(hc), int(ac))
    return values


def refresh_league(config: CornerDataConfig, league: str, today: date, state: Path,
                   *, validator_read_user: str | None = None) -> dict:
    season = current_season(today)
    target = config.directory / f"{league}_{season}.csv"
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
    payload = download_csv(url)
    with NamedTemporaryFile(dir=config.directory, suffix=".csv") as staging:
        staging.write(payload)
        staging.flush()
        incoming = snapshot(Path(staging.name), league, season, today)
    old_bytes = target.read_bytes() if target.exists() else None
    previous = snapshot(target, league, season, today) if old_bytes is not None else {}
    missing = previous.keys() - incoming.keys()
    if missing:
        raise ValueError(f"download would remove {len(missing)} existing corner fixtures; kept local file")
    changed = old_bytes != payload
    if changed:
        if old_bytes is not None:
            backups = state / "backups"
            backups.mkdir(exist_ok=True)
            atomic_write(backups / target.name, old_bytes)
        if validator_read_user is not None and league in ("E1", "SP1"):
            atomic_write(target, payload, validator_read_user=validator_read_user)
        else:
            atomic_write(target, payload)
    latest = max(key[0] for key in incoming)
    return {
        "league": league, "file": str(target), "source": url,
        "status": "updated" if changed else "unchanged",
        "matches": len(incoming), "added": len(incoming.keys() - previous.keys()),
        "corrected": sum(incoming[key] != previous[key] for key in incoming.keys() & previous.keys()),
        "latest_match": latest.isoformat(),
        "stale": (today - latest).days > config.max_age_days,
    }


def refresh_data(config: CornerDataConfig, today: date | None = None,
                 *, validator_read_user: str | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    config.directory.mkdir(parents=True, exist_ok=True)
    state = config.directory / "data" / "corner-refresh"
    with refresh_lock(state):
        results = []
        for league in config.leagues:
            try:
                results.append(refresh_league(config, league, today, state, validator_read_user=validator_read_user))
            except (OSError, ValueError, csv.Error, HTTPException) as error:
                results.append({"league": league, "status": "failed", "error": str(error)})
        report = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "season": current_season(today), "max_age_days": config.max_age_days,
            "results": results,
        }
        atomic_write(state / "status.json", (json.dumps(report, indent=2) + "\n").encode())
    return report


def format_report(report: dict, today: date, max_age_days: int) -> tuple[str, bool]:
    lines = [f"Last refresh attempt: {report['checked_at']}"]
    unhealthy = False
    for item in report["results"]:
        if item["status"] == "failed":
            lines.append(f"{item['league']}: FAILED: {item['error']}")
            unhealthy = True
            continue
        age = (today - date.fromisoformat(item["latest_match"])).days
        stale = age > max_age_days
        unhealthy |= stale
        lines.append(
            f"{item['league']}: {item['status']}; {item['matches']} matches; "
            f"added {item['added']}; corrected {item['corrected']}; "
            f"latest {item['latest_match']} ({age} days old)"
            + ("; STALE" if stale else "")
        )
    return "\n".join(lines), unhealthy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("corner_data.json"))
    parser.add_argument("--validator-read-user", help="grant read ACLs on published E1/SP1 CSVs before rename")
    parser.add_argument("--status", action="store_true", help="show saved results without downloading; recalculate data age")
    args = parser.parse_args()
    try:
        config = load_data_config(args.config)
        if args.status:
            path = config.directory / "data" / "corner-refresh" / "status.json"
            report = json.loads(path.read_text(encoding="utf-8"))
        else:
            report = refresh_data(config, validator_read_user=args.validator_read_user)
        text, unhealthy = format_report(report, datetime.now(timezone.utc).date(), config.max_age_days)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(text)
    if unhealthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
