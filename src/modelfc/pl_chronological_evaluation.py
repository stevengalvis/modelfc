"""Opt-in retrospective EPL diagnostics on five pinned saved seasons; no activation."""
import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.btts_model import MIN_HISTORY, arithmetic_rates, btts_probabilities
from modelfc.corner_probability_evaluation import evaluate_corner_probabilities, summarize
from modelfc.goal_totals import GoalAggregates
from modelfc.history_coverage import validated_sources, timestamp, MAX_SOURCE_BYTES
from modelfc.matches import TeamCornerObservation, Venue
from modelfc.oddspapi_market_inventory import _read_regular

MODEL = 'venue-opponent-negative-binomial'
LINES = (3.5, 4.5, 5.5, 6.5)


def _score(rows):
    if not rows:
        return {'count': 0, 'brier': None, 'log_loss': None, 'infinite_losses': 0}
    losses = [-math.log(p if y else 1-p) if 0 < (p if y else 1-p) else math.inf for p, y in rows]
    infinite = sum(not math.isfinite(x) for x in losses)
    return {'count': len(rows), 'brier': math.fsum((p-y)**2 for p,y in rows)/len(rows),
            'log_loss': None if infinite else math.fsum(losses)/len(rows), 'infinite_losses': infinite}


def _btts(records):
    homes = sorted((r for r in records if r.venue is Venue.HOME), key=lambda r: r.fixture_key)
    scored = []
    for target in homes:
        prior = [r for r in records if r.match_date < target.match_date]
        history = [r for r in prior if r.venue is Venue.HOME]
        if len(history) < MIN_HISTORY or any(not any(r.team == t for r in prior)
                                            for t in (target.team, target.opponent)):
            continue
        home = [r for r in prior if r.team == target.team and r.venue is Venue.HOME]
        away = [r for r in prior if r.team == target.opponent and r.venue is Venue.AWAY]
        inputs = GoalAggregates(len(history), sum(r.goals_for for r in history),
            sum(r.goals_against for r in history), len(home), sum(r.goals_for for r in home),
            sum(r.goals_against for r in home), len(away), sum(r.goals_for for r in away),
            sum(r.goals_against for r in away))
        p, _ = btts_probabilities(*arithmetic_rates(inputs))
        baseline = sum(r.goals_for > 0 and r.goals_against > 0 for r in history)/len(history)
        scored.append((target, p, baseline, target.goals_for > 0 and target.goals_against > 0))
    return scored


def evaluate(sources, *, season_year, as_of, enabled=False):
    if not enabled:
        return {'state': 'DISABLED', 'provider_requests': 0, 'live_activation': False}
    now = utc(as_of)
    if type(season_year) is not int or not 2004 <= season_year <= 2098:
        raise ValueError('SEASON_REJECTED')
    required = {f'E0_{y%100:02d}{(y+1)%100:02d}.csv' for y in range(season_year-4, season_year+1)}
    if len(sources) != 5 or {s[0] for s in sources} != required:
        raise ValueError('FIVE_SEASONS_REQUIRED')
    before = now.astimezone(ZoneInfo('Europe/London')).date()
    # Same strict structural/goal checks in both cohorts; missing corners do not
    # exclude valid goals. Malformed populated corner data fails its cohort.
    goals, provenance = validated_sources('E0', sources, before=before, observed_at=now,
        enforce_seasons=True, validation_scope='GOALS')
    corners, corner_provenance = validated_sources('E0', sources, before=before, observed_at=now,
        enforce_seasons=True, validation_scope='GOALS_CORNERS')
    goals = [r for r in goals if r.match_date < before]
    observations = [TeamCornerObservation(r.match_date, r.team, r.opponent, r.venue,
        r.corners_for, r.corners_against) for r in corners if r.match_date < before
        and r.corners_for is not None and r.corners_against is not None]
    btts = _btts(goals)
    seasons = []
    for year in range(season_year-4, season_year+1):
        start, end = date(year, 7, 1), date(year+1, 6, 30)
        fixtures = [r for r in goals if r.venue is Venue.HOME and start <= r.match_date <= end]
        cohort = [r for r in btts if start <= r[0].match_date <= end]
        complete = sum(r.venue is Venue.HOME and start <= r.match_date <= end for r in observations)
        try:
            report = evaluate_corner_probabilities(observations, model=MODEL, lines=LINES,
                min_history=100, min_venue_history=5, smoothing_matches=5,
                start_date=start, end_date=end)
            metrics = {str(line): asdict(summarize(o for o in report.outcomes if o.line == line)) for line in LINES}
            for metric in metrics.values():
                metric['infinite_log_loss'] = not math.isfinite(metric['log_loss'])
                if metric['infinite_log_loss']:
                    metric['log_loss'] = None
            eligible = report.eligible_fixtures
        except ValueError as exc:
            if str(exc) != 'no eligible fixtures in scoring window; check history and coverage':
                raise
            eligible, metrics = 0, {}
        seasons.append({'season': f'{year}/{year+1}', 'supplied_fixture_count': len(fixtures),
            'corners': {'complete_fixture_count': complete, 'eligible_fixture_count': eligible,
                'missing_corner_fixtures': len(fixtures)-complete, 'skipped_history_or_venue': complete-eligible,
                'team_line_metrics': metrics},
            'btts': {'eligible_fixture_count': len(cohort), 'skipped_history_or_team': len(fixtures)-len(cohort),
                'model': _score([(p,y) for _,p,_,y in cohort]),
                'prior_empirical_baseline_same_cohort': _score([(b,y) for _,_,b,y in cohort])}})
    pins = {}
    for name in ('pl_chronological_evaluation.py', 'corners.py', 'corner_probability_evaluation.py',
                 'corner_forecasts.py', 'btts_model.py', 'goal_totals.py', 'history_coverage.py',
                 'providers/football_data.py'):
        pins[name] = hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
    return {'state': 'RETROSPECTIVE_DIAGNOSTICS', 'competition': 'E0', 'as_of': now.isoformat(),
        'source_receipts': sorted(provenance,key=lambda p:p['filename']),
        'corner_source_receipts': sorted(corner_provenance,key=lambda p:p['filename']),
        'implementation_sha256': pins, 'seasons': seasons,
        'newest_season_separately_reported': f'{season_year}/{season_year+1}',
        'training_policy': 'EXPANDING_SUPPLIED_FIVE_SEASONS_STRICTLY_BEFORE_TARGET_DATE',
        'corner_model': MODEL, 'corner_lines': list(LINES),
        'btts_model': 'team-opponent-arithmetic-poisson-btts',
        'calendar_completeness': 'NOT_ASSESSED', 'historical_availability': 'NOT_ESTABLISHED',
        'qualification': 'NOT_ASSESSED', 'prospective_capture': False,
        'provider_requests': 0, 'live_activation': False, 'recommendation': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-offline-evaluation', action='store_true')
    parser.add_argument('--season-year', type=int)
    parser.add_argument('--as-of')
    parser.add_argument('--source', action='append', nargs=3, default=[], metavar=('CSV','SHA256','RETRIEVED_AT'))
    args = parser.parse_args()
    try:
        if not args.enable_offline_evaluation:
            result = evaluate((), season_year=None, as_of=None)
        else:
            if len(args.source) != 5:
                raise ValueError('FIVE_SEASONS_REQUIRED')
            sources = [(Path(p).name, _read_regular(Path(p), MAX_SOURCE_BYTES, 'SOURCE_READ_REJECTED'), pin, timestamp(t)) for p,pin,t in args.source]
            result = evaluate(sources, season_year=args.season_year, as_of=timestamp(args.as_of), enabled=True)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (ValueError, TypeError, OSError, KeyError):
        parser.exit(2, 'EPL_EVALUATION_REJECTED\n')


if __name__ == '__main__':
    main()
