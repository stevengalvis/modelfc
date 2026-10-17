"""Reviewed append-only BTTS corrections using the existing validated result sources."""
from datetime import datetime, timezone
from pathlib import Path

from modelfc import btts_outcomes as outcomes
from modelfc.btts_market_data import timestamp
from modelfc.btts_research import digest
from modelfc.corner_analysis_outcomes import _require
from modelfc.ledger_storage import ledger_lock, ensure_directory, write_new_record, utc_timestamp
from modelfc.settlement_reviews import directory, load_record, review_entries
from modelfc.research_predictions import _sync_directories


def chain(state, original, forecasts, *, as_of=None):
    path = directory(state, 'btts-corrections')
    entries = review_entries(state, 'btts-corrections')
    records=[]
    for target in entries:
        r=load_record(target,65536)
        _require(set(r) == {'schema_version','correction_id','outcome_id','supersedes_record_hash',
            'reason','recorded_at_utc','source','result','record_hash'} and r['schema_version']==1
            and r['correction_id']==target.stem and r['correction_id']==digest({k:r[k] for k in
                ('outcome_id','supersedes_record_hash','reason','source')})
            and isinstance(r['reason'],str) and 0 < len(r['reason'].strip()) <= 2000, 'INVALID_BTTS_CORRECTION')
        if r['outcome_id'] == original['outcome_id']:
            recorded=timestamp(r['recorded_at_utc'])
            _require(recorded <= datetime.now(timezone.utc), 'INVALID_BTTS_CORRECTION')
            result=outcomes._source_result(r['source'],original['fixture'],recorded)
            for forecast,_ in forecasts.values():
                if outcomes.fixture_identity(forecast)==original['fixture']:
                    outcomes._provider_binding(forecast,r['source'])
            _require(result==r['result'] and all(type(v) is int for v in r['result'].values()),'INVALID_BTTS_CORRECTION')
            records.append(r)
    tip=original; history=[]
    while True:
        children=[r for r in records if r['supersedes_record_hash']==tip['record_hash']]
        _require(len(children)<=1,'INVALID_BTTS_CORRECTION_CHAIN')
        if not children: break
        r=children[0]
        _require(r not in history and timestamp(r['recorded_at_utc'])>=timestamp(tip['recorded_at_utc'])
            and timestamp(r['source']['retrieved_at_utc']) > timestamp(tip['source']['retrieved_at_utc'])
            and r['result']!=tip['result'],'INVALID_BTTS_CORRECTION_CHAIN')
        history.append(r);tip=r
    _require(len(history)==len(records),'INVALID_BTTS_CORRECTION_CHAIN')
    visible=[r for r in history if as_of is None or timestamp(r['recorded_at_utc'])<=as_of]
    effective=visible[-1] if visible else original
    result=effective['result']
    return {**original,**{k:effective[k] for k in ('record_hash','recorded_at_utc','source','result')},
        'actual_btts':'YES' if min(result.values())>0 else 'NO'}, visible


def correct(state, forecast_id, source, *, supersedes_record_hash, reason):
    state = Path(state)
    _require(isinstance(reason,str) and 0 < len(reason.strip()) <= 2000,'EXPLICIT_CORRECTION_REQUIRED')
    with ledger_lock(state):
        forecasts=outcomes.inventory(state)
        _require(forecast_id in forecasts,'UNKNOWN_BTTS_FORECAST')
        fixture=outcomes.fixture_identity(forecasts[forecast_id][0])
        original=outcomes._load(outcomes._directory_outcomes(state)/(digest(fixture)+'.json'),forecasts)
        tip,history=chain(state,original,forecasts)
        request=dict(outcome_id=original['outcome_id'],supersedes_record_hash=supersedes_record_hash,
                     reason=reason.strip(),source=source)
        identity=digest(request)
        existing=next((r for r in history if r['correction_id']==identity),None)
        if existing:
            _sync_directories(directory(state,"btts-corrections"),state)
            return existing,False
        _require(supersedes_record_hash==tip['record_hash'],'REVISION_CONFLICT')
        recorded=utc_timestamp()
        result=outcomes._source_result(source,fixture,timestamp(recorded))
        for forecast,_ in forecasts.values():
            if outcomes.fixture_identity(forecast)==fixture: outcomes._provider_binding(forecast,source)
        _require(timestamp(recorded)>=timestamp(tip['recorded_at_utc'])
            and timestamp(source['retrieved_at_utc'])>timestamp(tip['source']['retrieved_at_utc']), 'CORRECTION_CHRONOLOGY_CONFLICT')
        _require(result!=tip['result'],'CORRECTION_NOT_REQUIRED')
        row=dict(schema_version=1,correction_id=identity,**request,recorded_at_utc=recorded,result=result)
        row['record_hash']=digest(row)
        path=directory(state,'btts-corrections');ensure_directory(path,'BTTS corrections')
        _require(len(review_entries(state, 'btts-corrections')) < 2000,'CORRECTION_CAPACITY_EXCEEDED')
        write_new_record(path/(identity+'.json'),row)
        _sync_directories(path,state)
        return row,True
