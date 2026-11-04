"""Paired retrospective baselines and season-stratified match-date uncertainty."""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import random
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.corner_probability_evaluation import evaluate_corner_probabilities
from modelfc.history_coverage import validated_sources, timestamp, MAX_SOURCE_BYTES
from modelfc.matches import TeamCornerObservation, Venue
from modelfc.oddspapi_market_inventory import _read_regular
from modelfc.pl_chronological_evaluation import evaluate, _btts, MODEL, LINES


def _loss(p, y, metric, under=None):
    if metric == 'brier':
        return (p-y)**2
    probability = p if y else (1-p if under is None else under)
    return -math.log(probability) if probability > 0 else math.inf


def _row_loss(row, forecast, metric):
    return _loss(row[forecast], row['outcome'], metric, row.get(forecast+'_under'))


def _percentile(values, p):
    ordered = sorted(values)
    position = (len(ordered)-1)*p
    lo = int(position)
    return ordered[lo]+(ordered[min(lo+1,len(ordered)-1)]-ordered[lo])*(position-lo)


def paired_summary(rows, *, resamples=2000, seed=20261104):
    """Resample whole dates within seasons; variable row counts stay weighted."""
    if type(resamples) is not int or not 100 <= resamples <= 10000 or type(seed) is not int:
        raise ValueError('BOOTSTRAP_OPTIONS_REJECTED')
    groups = defaultdict(lambda: defaultdict(list))
    seen = set()
    for row in rows:
        day = date.fromisoformat(row['date'])
        year = day.year if day.month >= 7 else day.year-1
        key = (row['date'],row['home'],row['away'],row['venue'],row['line'])
        if key in seen or row['season'] != f'{year}/{year+1}':
            raise ValueError('PAIRED_ROW_REJECTED')
        seen.add(key)
        if type(row['outcome']) is not bool or any(type(row[k]) not in (int,float)
            or not math.isfinite(row[k]) or not 0 <= row[k] <= 1 for k in ('model','baseline','model_under') if k in row):
            raise ValueError('PAIRED_ROW_REJECTED')
        groups[row['season']][row['date']].append(row)
    result = {'count': len(rows), 'date_blocks': sum(len(g) for g in groups.values()),
              'season_date_blocks': {s:len(groups[s]) for s in sorted(groups)}, 'metrics': {}}
    for metric in ('brier','log_loss'):
        model = [_row_loss(r,'model',metric) for r in rows]
        baseline = [_row_loss(r,'baseline',metric) for r in rows]
        bad = sum(not math.isfinite(x) for x in model+baseline)
        valid = bool(rows) and not bad
        point = math.fsum(a-b for a,b in zip(model,baseline))/len(rows) if valid else None
        enough = valid and all(len(g) >= 2 for g in groups.values())
        samples = []
        if enough:
            strata = []
            for season in sorted(groups):
                strata.append([(math.fsum(_row_loss(r,'model',metric)
                    - _row_loss(r,'baseline',metric) for r in groups[season][d]),
                    len(groups[season][d])) for d in sorted(groups[season])])
            rng = random.Random(seed)
            for _ in range(resamples):
                total, count = 0.0, 0
                for blocks in strata:
                    for _ in blocks:
                        loss, n = blocks[rng.randrange(len(blocks))]
                        total += loss; count += n
                samples.append(total/count)
        result['metrics'][metric] = {
            'model': math.fsum(model)/len(rows) if rows and all(map(math.isfinite,model)) else None,
            'baseline': math.fsum(baseline)/len(rows) if rows and all(map(math.isfinite,baseline)) else None,
            'model_minus_baseline': point, 'nonfinite_losses': bad,
            'percentile_95_interval': [_percentile(samples,.025),_percentile(samples,.975)] if samples else None,
            'uncertainty_status': 'AVAILABLE' if samples else 'EMPTY' if not rows else
                'NONFINITE_LOSS' if bad else 'INSUFFICIENT_DATE_BLOCKS'}
    return result


def compare(sources, *, season_year, as_of, resamples=2000, seed=20261104):
    # Preserve the original report and every validation gate; no historic receipt
    # is reinterpreted as proof of information available at historical kickoff.
    base = evaluate(sources,season_year=season_year,as_of=as_of,enabled=True)
    before = utc(as_of).astimezone(ZoneInfo('Europe/London')).date()
    goals,_ = validated_sources('E0',sources,before=before,observed_at=as_of,
        enforce_seasons=True,validation_scope='GOALS')
    corners,_ = validated_sources('E0',sources,before=before,observed_at=as_of,
        enforce_seasons=True,validation_scope='GOALS_CORNERS')
    goals = [r for r in goals if r.match_date < before]
    observations = [TeamCornerObservation(r.match_date,r.team,r.opponent,r.venue,r.corners_for,r.corners_against)
        for r in corners if r.match_date < before and r.corners_for is not None and r.corners_against is not None]
    corner_rows = []
    try:
        outcomes = evaluate_corner_probabilities(observations,model=MODEL,lines=LINES,
            min_history=100,min_venue_history=5,smoothing_matches=5).outcomes
    except ValueError as exc:
        if str(exc) != 'no eligible fixtures in scoring window; check history and coverage': raise
        outcomes = ()
    for item in outcomes:
        r = item.observation
        prior = [p for p in observations if p.match_date < r.match_date and p.venue is r.venue]
        if not prior: raise ValueError('BASELINE_HISTORY_MISSING')
        year = r.match_date.year if r.match_date.month >= 7 else r.match_date.year-1
        home,away = (r.team,r.opponent) if r.venue is Venue.HOME else (r.opponent,r.team)
        corner_rows.append({'season':f'{year}/{year+1}','date':r.match_date.isoformat(),
            'home':home,'away':away,'venue':r.venue.value,'line':item.line,
            'model':item.over_probability,'model_under':item.under_probability,'baseline':sum(p.corners_for > item.line for p in prior)/len(prior),
            'outcome':item.over_happened})
    btts_rows = [{'season':r.season,'date':r.match_date.isoformat(),'home':r.team,'away':r.opponent,
        'venue':'fixture','line':None,'model':p,'baseline':b,'outcome':y} for r,p,b,y in _btts(goals)]
    def summaries(rows):
        return {'pooled':paired_summary(rows,resamples=resamples,seed=seed),
            'seasons':{s['season']:paired_summary([r for r in rows if r['season']==s['season']],
                resamples=resamples,seed=seed) for s in base['seasons']}}
    return {'original_evaluation':base,'paired_rows':{'corners':corner_rows,'btts':btts_rows},
        'comparisons':{'corners':{str(line):summaries([r for r in corner_rows if r['line']==line]) for line in LINES},
                       'btts':summaries(btts_rows)},
        'corner_baseline':'PRIOR_COMPLETE_CORNER_EMPIRICAL_FREQUENCY_BY_VENUE',
        'bootstrap':{'method':'MATCH_DATE_CLUSTER_WITHIN_SEASON','resamples':resamples,'seed':seed,
            'interval':'PAIRED_LOSS_DIFFERENCE_PERCENTILE_95','negative_difference':'MODEL_LOWER_LOSS',
            'limitations':['within-date dependence only; adjacent dates may remain dependent',
                'conditional on supplied snapshots and eligible cohort; models are not refit',
                'no multiplicity correction; lines are not independent tests']},
        'comparison_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'prospective_capture':False,'qualification':'NOT_ASSESSED','provider_requests':0,'live_activation':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-offline-comparison',action='store_true')
    parser.add_argument('--season-year',type=int)
    parser.add_argument('--as-of')
    parser.add_argument('--source',action='append',nargs=3,default=[])
    args=parser.parse_args()
    try:
        if not args.enable_offline_comparison:
            result={'state':'DISABLED','provider_requests':0,'live_activation':False}
        else:
            if len(args.source)!=5: raise ValueError('FIVE_SEASONS_REQUIRED')
            sources=[(Path(p).name,_read_regular(Path(p),MAX_SOURCE_BYTES,'SOURCE_READ_REJECTED'),pin,timestamp(t))
                     for p,pin,t in args.source]
            result=compare(sources,season_year=args.season_year,as_of=timestamp(args.as_of))
        print(json.dumps(result,sort_keys=True,allow_nan=False))
    except (ValueError,TypeError,OSError,KeyError):
        parser.exit(2,'EPL_COMPARISON_REJECTED\n')


if __name__=='__main__':
    main()
