"""Fixed-bin descriptive calibration of pinned saved EPL comparison rows only."""
import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from modelfc.history_coverage import timestamp
from modelfc.oddspapi_market_inventory import _read_regular
from modelfc.pl_chronological_evaluation import LINES

EDGES = tuple(i/10 for i in range(11))
MAX_REPORT_BYTES = 16 * 1024 * 1024


def _require(ok):
    if not ok:
        raise ValueError('CALIBRATION_INPUT_REJECTED')


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _bins(rows, forecast):
    buckets = [[] for _ in range(10)]
    for r in rows:
        buckets[min(9,bisect_right(EDGES,r[forecast])-1)].append(r)
    result = []
    for i, bucket in enumerate(buckets):
        n = len(bucket)
        mean = math.fsum(r[forecast] for r in bucket)/n if n else None
        rate = sum(r['outcome'] for r in bucket)/n if n else None
        result.append({'lower':EDGES[i],'upper':EDGES[i+1],'upper_inclusive':i==9,
            'count':n,'date_blocks':len({r['date'] for r in bucket}),
            'mean_probability':mean,'observed_rate':rate,
            'observed_minus_predicted':rate-mean if n else None})
    return result


def calibrate(raw, expected_sha256):
    """Read rows, never fit/recompute a forecast or grant qualification."""
    _require(type(raw) is bytes and len(raw)<=MAX_REPORT_BYTES)
    _require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}',expected_sha256))
    _require(hashlib.sha256(raw).hexdigest()==expected_sha256)
    report=json.loads(raw,object_pairs_hook=_object,parse_constant=lambda _: _require(False))
    _require(report['prospective_capture'] is False and report['live_activation'] is False
             and report['qualification']=='NOT_ASSESSED' and report['provider_requests']==0)
    original=report['original_evaluation']
    _require(original['competition']=='E0' and original['state']=='RETROSPECTIVE_DIAGNOSTICS')
    before=timestamp(original['as_of']).astimezone(ZoneInfo('Europe/London')).date()
    seasons=[s['season'] for s in original['seasons']]
    _require(len(seasons)==5 and len(set(seasons))==5)
    rows=report['paired_rows']['corners']
    _require(type(rows) is list and len(rows)<=100000)
    seen=set(); fixtures=defaultdict(set)
    for r in rows:
        _require(set(r)=={'season','date','home','away','venue','line','model','model_under','baseline','outcome'})
        day=date.fromisoformat(r['date']);year=day.year if day.month>=7 else day.year-1
        _require(r['date']==day.isoformat() and day<before and r['season']==f'{year}/{year+1}'
                 and r['season'] in seasons and r['venue'] in ('home','away')
                 and type(r['line']) in (int,float) and r['line'] in LINES
                 and type(r['outcome']) is bool)
        _require(all(type(r[k]) is str and r[k].strip() for k in ('home','away')) and r['home']!=r['away'])
        _require(all(type(r[k]) in (int,float) and math.isfinite(r[k]) and 0<=r[k]<=1
                     for k in ('model','model_under','baseline')))
        _require(math.isclose(r['model']+r['model_under'],1,rel_tol=0,abs_tol=1e-12))
        fixture=(r['season'],r['date'],r['home'],r['away'])
        key=fixture+(r['venue'],r['line'])
        _require(key not in seen);seen.add(key)
        fixtures[fixture].add((r['venue'],r['line']))
    required={(venue,line) for venue in ('home','away') for line in LINES}
    _require(all(pairs==required for pairs in fixtures.values()))
    for season in original['seasons']:
        n=sum(f[0]==season['season'] for f in fixtures)
        _require(type(season['corners']['eligible_fixture_count']) is int
                 and n==season['corners']['eligible_fixture_count'])
        for line in LINES:
            _require(report['comparisons']['corners'][str(line)]['seasons'][season['season']]['count']==2*n)
    for line in LINES:
        _require(report['comparisons']['corners'][str(line)]['pooled']['count']==2*len(fixtures))
    groups=[]
    for season in ['pooled']+seasons:
        for line in LINES:
            for venue in ('both','home','away'):
                subset=[r for r in rows if r['line']==line and (season=='pooled' or r['season']==season)
                        and (venue=='both' or r['venue']==venue)]
                groups.append({'season':season,'line':line,'venue':venue,'count':len(subset),
                    'date_blocks':len({r['date'] for r in subset}),
                    'model_bins':_bins(subset,'model'),'baseline_bins':_bins(subset,'baseline')})
    return {'state':'DESCRIPTIVE_RETROSPECTIVE_CALIBRATION','input_report_sha256':expected_sha256,
        'calibration_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'as_of':original['as_of'],'bin_edges':EDGES,'groups':groups,
        'bin_rule':'LEFT_CLOSED_RIGHT_OPEN_EXCEPT_LAST_CLOSED',
        'limitations':['descriptive bins only; no binomial independence or confidence intervals assumed',
            'model and baseline bins use their own probabilities on the same overall cohort',
            'sparse bins and correlated team/line observations do not establish qualification',
            'retrospective snapshots; no untouched holdout or prospective performance'],
        'qualification':'NOT_ASSESSED','btts_status':'RESEARCH_ONLY',
        'provider_requests':0,'live_activation':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-offline-calibration',action='store_true')
    parser.add_argument('--report');parser.add_argument('--sha256')
    args=parser.parse_args()
    try:
        if not args.enable_offline_calibration:
            result={'state':'DISABLED','provider_requests':0,'live_activation':False}
        else:
            raw=_read_regular(Path(args.report),MAX_REPORT_BYTES,'CALIBRATION_READ_REJECTED')
            result=calibrate(raw,args.sha256)
        print(json.dumps(result,sort_keys=True,allow_nan=False))
    except (ValueError,TypeError,KeyError,OSError,AttributeError,RecursionError):
        parser.exit(2,'EPL_CALIBRATION_REJECTED\n')


if __name__=='__main__':
    main()
