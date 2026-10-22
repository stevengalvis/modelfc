"""Pinned, offline E1/E0 CSV coverage audit. No fetch, refresh, model or publication."""
import argparse
import csv
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.matches import Venue
from modelfc.oddspapi_market_inventory import _read_regular
from modelfc.providers.football_data import load_team_match_stats_history, unvalidated_statistics

MAX_SOURCES = 32
MAX_SOURCE_BYTES = 2_000_000
FIELDS = {'goals': ('FTHG', 'FTAG'), 'corners': ('HC', 'AC'),
          'shots': ('HS', 'AS'), 'shots_on_target': ('HST', 'AST')}


class CoverageRejected(ValueError):
    """Fixed diagnostic; source data and private paths never enter error output."""


def timestamp(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError
        return utc(datetime.fromisoformat(value.replace('Z', '+00:00')))
    except (ValueError, OverflowError):
        raise CoverageRejected('SOURCE_TIMESTAMP_REJECTED') from None


def validated_sources(competition, sources, *, before, observed_at, allow_unknown=False,
                      enforce_seasons=False, validation_scope='FULL'):
    """sources contains (canonical filename, immutable bytes, SHA256, retrieval UTC).

    The existing strict adapter validates complete goals/FTR and all populated
    statistics by default. Explicit scoped calls label unassessed statistics in
    each record and source receipt; None outside scope does not mean missing.
    A temporary private copy allows reuse of its unchanged path-based interface;
    originals are never reread or modified. No exclusion or repair is implicit.
    """
    unvalidated = unvalidated_statistics(validation_scope)
    observed = utc(observed_at)
    if competition not in {'E1', 'E0'} or type(before) is not date or before > observed.astimezone(ZoneInfo('Europe/London')).date():
        raise CoverageRejected('COVERAGE_SCOPE_REJECTED')
    if not isinstance(sources, (tuple, list)) or not 1 <= len(sources) <= MAX_SOURCES:
        raise CoverageRejected('COVERAGE_SOURCE_LIMIT')
    names, provenance = set(), []
    try:
        with tempfile.TemporaryDirectory(prefix='modelfc-coverage-') as directory:
            paths = []
            for name, raw, pin, retrieved in sources:
                if (not isinstance(name, str) or re.fullmatch(rf'{competition}_[0-9]{{4}}\.csv', name) is None
                        or name in names or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES
                        or not isinstance(pin, str) or re.fullmatch(r'[0-9a-f]{64}', pin) is None
                        or hashlib.sha256(raw).hexdigest() != pin):
                    raise CoverageRejected('COVERAGE_SOURCE_REJECTED')
                retrieved = None if retrieved is None and allow_unknown else utc(retrieved)
                if retrieved is not None and retrieved > observed:
                    raise CoverageRejected('SOURCE_FUTURE_RETRIEVAL')
                names.add(name)
                columns = next(csv.reader(io.StringIO(raw.decode('utf-8-sig'))), [])
                provenance.append({'filename': name, 'sha256': pin,
                    'retrieved_at_utc': retrieved.isoformat() if retrieved else None,
                    'age_seconds_at_observation': (observed-retrieved).total_seconds() if retrieved else None,
                    'columns': {field: {'home': home in columns, 'away': away in columns}
                                for field, (home, away) in FIELDS.items()}})
                if validation_scope != 'FULL':
                    provenance[-1].update(validation_scope=validation_scope,
                                          unvalidated_statistics=list(unvalidated))
                path = Path(directory)/name
                path.write_bytes(raw)
                if enforce_seasons:
                    year = 2000 + int(name[3:5])
                    expected = f"{year}/{year+1}"
                    file_records = load_team_match_stats_history([path], competition=competition, validation_scope=validation_scope)
                    if retrieved is not None and any(
                        r.match_date > retrieved.astimezone(ZoneInfo('Europe/London')).date()
                        for r in file_records
                    ):
                        raise CoverageRejected('SOURCE_CHRONOLOGY_REJECTED')
                    if name != f"{competition}_{year%100:02d}{(year+1)%100:02d}.csv" or any(
                        r.season != expected for r in file_records
                    ):
                        raise CoverageRejected('SOURCE_SEASON_REJECTED')
                paths.append(path)
            records = load_team_match_stats_history(paths, competition=competition, validation_scope=validation_scope)
    except CoverageRejected:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError, csv.Error):
        raise CoverageRejected('HISTORY_VALIDATION_REJECTED') from None
    return records, provenance


def audit_sources(competition, sources, *, before, observed_at):
    """Report observed coverage without changing the original PR134 contract."""
    observed = utc(observed_at)
    records, provenance = validated_sources(competition, sources, before=before, observed_at=observed)
    eligible = [r for r in records if r.match_date < before]
    fixtures = [r for r in eligible if r.venue is Venue.HOME]
    by_season, by_team = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for row in eligible:
        by_team[row.season][row.team].append(row)
        if row.venue is Venue.HOME:
            by_season[row.season].append(row)
    seasons = []
    for season, cohort in sorted(by_season.items()):
        coverage = {}
        for field in FIELDS:
            known = sum(getattr(r, field+'_for') is not None for r in cohort)
            known += sum(getattr(r, field+'_against') is not None for r in cohort)
            complete = sum(getattr(r, field+'_for') is not None and getattr(r, field+'_against') is not None for r in cohort)
            coverage[field] = {'complete_fixtures': complete, 'fixture_denominator': len(cohort),
                               'known_team_values': known, 'team_value_denominator': 2*len(cohort),
                               'missing_team_values': 2*len(cohort)-known}
        teams = []
        for team, rows in sorted(by_team[season].items()):
            teams.append({'team': team, 'matches': len(rows),
                'fields': {field: {'known_for': sum(getattr(r, field+'_for') is not None for r in rows),
                                   'known_against': sum(getattr(r, field+'_against') is not None for r in rows)}
                           for field in FIELDS}})
        seasons.append({'season': season, 'fixtures': len(cohort), 'fields': coverage, 'teams': teams})
    latest = max((r.match_date for r in fixtures), default=None)
    return {'version': 1, 'competition': competition, 'observed_at_utc': observed.isoformat(),
            'before_date_exclusive': before.isoformat(), 'scope': 'OBSERVED_ROWS_ONLY',
            'calendar_completeness': 'NOT_ASSESSED', 'provider_requests': 0,
            'eligible_fixtures': len(fixtures), 'excluded_at_or_after_cutoff': (len(records)-len(eligible))//2,
            'latest_eligible_match_date': latest.isoformat() if latest else None,
            'days_since_latest_match': (observed.astimezone(ZoneInfo('Europe/London')).date()-latest).days if latest else None,
            'sources': sorted(provenance, key=lambda row: row['filename']), 'seasons': seasons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--competition', choices=('E1', 'E0'), required=True)
    parser.add_argument('--before', type=date.fromisoformat, required=True)
    parser.add_argument('--source', action='append', nargs=3, required=True,
                        metavar=('CSV', 'SHA256', 'RETRIEVED_AT'))
    args = parser.parse_args()
    try:
        if len(args.source) > MAX_SOURCES:
            raise CoverageRejected('COVERAGE_SOURCE_LIMIT')
        sources = [(Path(path).name, _read_regular(Path(path), MAX_SOURCE_BYTES, 'SOURCE_UNAVAILABLE'),
                    pin, timestamp(retrieved)) for path, pin, retrieved in args.source]
        report = audit_sources(args.competition, sources, before=args.before,
                               observed_at=datetime.now(timezone.utc))
    except (OSError, ValueError, TypeError, OverflowError):
        parser.exit(1, 'HISTORY_COVERAGE_REJECTED\n')
    print(json.dumps(report, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
