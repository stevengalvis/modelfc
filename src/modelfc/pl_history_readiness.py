"""Opt-in offline EPL corner-input audit; never a production authorization."""
from datetime import date, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.history_coverage import validated_sources, MAX_SOURCE_BYTES, MAX_SOURCES, timestamp
from modelfc.matches import Venue
from modelfc.pl_calendar import parse_calendar, _contract, _time, MAX_CALENDAR_BYTES
from modelfc.pl_calendar_readiness import readiness, read_bounded, _Parser


def assess(raw, *, sources=(), enabled=False, **options):
    """Caller pins saved bytes and attests original retrieval, not file mtime.

    Fixed operational gates match corner engine defaults: 100 complete team
    observations and five in the requested venue. These are not confidence or
    physical-stadium continuity guarantees. No cross-season aliases are inferred.
    """
    result = {'version': 1, 'state': 'DISABLED', 'live_activation': False,
              'provider_requests': 0, 'forecast_capture': False,
              'history_validity': 'NOT_ASSESSED', 'history_provenance': 'UNKNOWN',
              'history_freshness': 'UNKNOWN', 'calendar_completeness': 'NOT_ASSESSED',
              'teams': [], 'fixtures': []}
    if enabled is not True:
        return result
    result['state'] = 'BLOCKED'
    calendar_report = readiness(raw, **options)
    result['calendar'] = calendar_report
    if calendar_report['validity'] != 'VALID':
        return result
    try:
        observed = utc(options['as_of'])
        day = options['selected_day']
        year = options['season_year']
        if day != observed.astimezone(ZoneInfo('Europe/London')).date():
            raise ValueError('scope')
        calendar = parse_calendar(raw, **{k: options[k] for k in (
            'contract_raw', 'reviewed_contract_sha256', 'competition', 'season_year')})
        contract = _contract(options['contract_raw'], options['reviewed_contract_sha256'], 'E0', year)
        if not sources:
            result['history_validity'] = 'MISSING'
            return result
        records, provenance = validated_sources('E0', sources, before=day,
            observed_at=observed, allow_unknown=True, enforce_seasons=True)
        result['history_validity'] = 'VALID'
        result['sources'] = provenance
        result['history_provenance'] = ('SUPPLIED' if all(p['retrieved_at_utc'] for p in provenance) else 'UNKNOWN')
        required = [f'E0_{y%100:02d}{(y+1)%100:02d}.csv' for y in range(year-4, year+1)]
        result['missing_season_sources'] = sorted(set(required)-{p['filename'] for p in provenance})
        current = next((p for p in provenance if p['filename'] == required[-1]), None)
        result['history_freshness'] = ('UNKNOWN' if current is None or current['age_seconds_at_observation'] is None
            else 'STALE' if current['age_seconds_at_observation'] > timedelta(days=14).total_seconds() else 'FRESH')
        rows = [r for r in records if r.match_date < day and f'{year-4}/' <= r.season <= f'{year}/{year+1}']
        complete = [r for r in rows if r.corners_for is not None and r.corners_against is not None]
        result['complete_corner_observations'] = len(complete)
        result['excluded_at_or_after_cutoff'] = sum(r.match_date >= day for r in records)//2
        latest = max((r.match_date for r in rows), default=None)
        result['latest_match_date'] = latest.isoformat() if latest else None
        result['latest_match_age_days'] = (day-latest).days if latest else None
        expected = {m.key: m for m in calendar.matches if m.status == 'FINISHED' and m.day < day}
        actual = {r.fixture_key[1:]: r for r in rows if r.venue is Venue.HOME and r.season == f'{year}/{year+1}'}
        if current and current['retrieved_at_utc']:
            retrieved = timestamp(current['retrieved_at_utc'])
            if any(expected[k].kickoff >= retrieved for k in set(expected)&set(actual)):
                raise ValueError('chronology')
        missing = set(expected)-set(actual)
        extra = set(actual)-set(expected)
        conflicts = sum((actual[k].goals_for, actual[k].goals_against) != expected[k].goals for k in set(expected)&set(actual))
        missing_corners = sum(actual[k].corners_for is None or actual[k].corners_against is None for k in set(expected)&set(actual))
        result['reconciliation'] = {'missing_finished': len(missing), 'extra_history': len(extra),
                                    'score_conflicts': conflicts, 'missing_corner_fixtures': missing_corners}
        result['calendar_completeness'] = 'MATCHED_BEFORE_DAY' if not (missing or extra or conflicts or missing_corners) else 'INCOMPLETE'
        by_name = {}
        for binding in sorted(contract['team_bindings'], key=lambda b: b['source_team_id']):
            team_rows = [r for r in complete if r.team == binding['history_name']]
            venues = {v.value: sum(r.venue is v for r in team_rows) for v in Venue}
            team = {**binding, 'provider': 'football-data.org', 'complete_corner_venues': venues,
                    'seasons_without_corner_history': [f'{y}/{y+1}' for y in range(year-4, year+1)
                        if not any(r.season == f'{y}/{y+1}' for r in team_rows)],
                    'home_eligible': venues[Venue.HOME.value] >= 5,
                    'away_eligible': venues[Venue.AWAY.value] >= 5}
            result['teams'].append(team)
            by_name[binding['history_name']] = team
        global_ok = (calendar_report['freshness'] == 'FRESH' and calendar_report['prior_state'] == 'RESOLVED'
            and result['history_provenance'] == 'SUPPLIED' and result['history_freshness'] == 'FRESH'
            and not result['missing_season_sources'] and result['calendar_completeness'] == 'MATCHED_BEFORE_DAY'
            and latest is not None and (day-latest).days <= 14 and len(complete) >= 100)
        for match in calendar.matches:
            if match.day != day:
                continue
            eligible = (global_ok and match.status in {'TIMED', 'SCHEDULED'} and match.kickoff > observed
                        and by_name[match.home]['home_eligible'] and by_name[match.away]['away_eligible'])
            result['fixtures'].append({'fixture_id': match.fixture_id, 'kickoff': match.kickoff.isoformat(),
                'home': match.home, 'away': match.away, 'status': match.status, 'corner_inputs_eligible': bool(eligible)})
        if global_ok:
            result['state'] = ('EMPTY' if not result['fixtures'] else 'INPUTS_ELIGIBLE'
                if all(f['corner_inputs_eligible'] for f in result['fixtures']) else 'BLOCKED')
        return result
    except (ValueError, TypeError, OSError, OverflowError, AttributeError, KeyError):
        return {**result, 'state': 'BLOCKED', 'history_validity': 'INVALID_OR_UNSUPPORTED',
                'reason': 'PL_HISTORY_INPUT_REJECTED', 'teams': [], 'fixtures': []}


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--enable-offline-audit', action='store_true')
    required_inputs = ('bundle', 'bundle-sha256', 'contract', 'reviewed-contract-sha256', 'season-year', 'day', 'as-of')
    for name in required_inputs:
        parser.add_argument('--'+name)
    parser.add_argument('--source', action='append', nargs=3, default=[], metavar=('CSV', 'SHA256', 'RETRIEVED_AT_OR_UNKNOWN'))
    args = parser.parse_args(argv)
    if args.enable_offline_audit and any(getattr(args, name.replace('-', '_')) is None for name in required_inputs):
        parser.error('enabled audit requires all evidence arguments')
    try:
        if not args.enable_offline_audit:
            report = assess(None)
        else:
            if len(args.source) > MAX_SOURCES:
                raise ValueError
            try:
                sources = [(Path(p).name, read_bounded(p, MAX_SOURCE_BYTES), pin,
                            None if retrieved == 'UNKNOWN' else timestamp(retrieved)) for p, pin, retrieved in args.source]
            except FileNotFoundError:
                print(json.dumps({'state': 'BLOCKED', 'history_validity': 'MISSING',
                    'reason': 'PL_HISTORY_SOURCE_MISSING', 'live_activation': False, 'provider_requests': 0}))
                return 1
            try:
                bundle = read_bounded(args.bundle, MAX_CALENDAR_BYTES)
            except FileNotFoundError:
                bundle = None
            report = assess(bundle, sources=sources, enabled=True,
                contract_raw=read_bounded(args.contract, 20_000), reviewed_contract_sha256=args.reviewed_contract_sha256,
                bundle_sha256=args.bundle_sha256, competition='E0', season_year=int(args.season_year),
                selected_day=date.fromisoformat(args.day), as_of=_time(args.as_of))
    except (ValueError, TypeError, OSError, OverflowError):
        report = {'state': 'BLOCKED', 'reason': 'PL_HISTORY_INPUT_REJECTED', 'live_activation': False, 'provider_requests': 0}
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report['state'] in {'INPUTS_ELIGIBLE', 'EMPTY'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
