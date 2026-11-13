"""Optional E0 tail of the existing scheduled E1 invocation; no new timer/ledger."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from modelfc import guarded_acquisition as acquisition, pl_operator_once as operator
from modelfc import pl_regular_delivery as delivery
from modelfc.acquisition_storage import encoded, fingerprint, private_store, shared_runner
from modelfc.corner_prospective_budget import month_bounds
from modelfc.history_coverage import timestamp, MAX_SOURCE_BYTES
from modelfc.providers.oddspapi_saved_response import _json


def _now():
    return datetime.now(timezone.utc)


def _require(ok):
    if not ok:
        raise ValueError('EPL_SCHEDULE_REJECTED')


def _protected(path, limit=32768):
    return acquisition._local_bytes(path, limit, private=True)


def _pinned(reference, limit):
    _require(type(reference) is dict and set(reference)=={'path','sha256'})
    raw=acquisition._local_bytes(reference['path'],limit)
    _require(fingerprint(raw)==reference['sha256'])
    return raw


def _config(path, state_dir):
    raw=_protected(path);c=_json(raw)
    _require(type(c) is dict and set(c)-{'delivery'}=={'version','enabled','state_dir','private_dir',
        'input_dir','period_start','period_end','expires_at','request_ceiling','e1_reserve','days'})
    _require(type(c['version']) is int and c['version']==1 and type(c['enabled']) is bool)
    _require(c['state_dir']==str(state_dir))
    _require(all(type(c[k]) is str and Path(c[k]).is_absolute() for k in ('state_dir','private_dir','input_dir')))
    start,end=(date.fromisoformat(c[k]) for k in ('period_start','period_end'))
    _require(month_bounds(start)==(start,end) and start.isoformat()==c['period_start']
             and end.isoformat()==c['period_end'])
    _require(type(c['days']) is list and 1<=len(c['days'])<=31 and len(set(c['days']))==len(c['days']))
    for value in c['days']:
        d=date.fromisoformat(value)
        _require(d.isoformat()==value and start<=d<=end)
    # Two preparation calls plus one capture per approved day, including failed
    # reservations. No independent mutable monthly accounting is introduced.
    _require(type(c['request_ceiling']) is int and 3*len(c['days'])<=c['request_ceiling']<=180)
    _require(type(c['e1_reserve']) is int and 1<=c['e1_reserve']<180)
    if 'delivery' in c:
        d=c['delivery']
        _require(type(d) is dict and set(d)=={'publish','settle','approved_contracts'}
                 and type(d['publish']) is bool and type(d['settle']) is bool
                 and type(d['approved_contracts']) is list and len(d['approved_contracts'])==1)
    at=_now();expiry=timestamp(c['expires_at'])
    _require(start<=at.date()<end and at<expiry<=datetime.combine(end,datetime.min.time(),timezone.utc))
    return c,fingerprint(raw)


def _freeze(m, day):
    _require(type(m) is dict and set(m)=={'calendar','mapping','sources','season_year',
        'approval','approved_contracts','bindings'})
    _require(type(m['sources']) is list and len(m['sources'])==5)
    sources=[]
    for source in m['sources']:
        _require(set(source)=={'file','retrieved_at'})
        raw=_pinned(source['file'],MAX_SOURCE_BYTES)
        sources.append((Path(source['file']['path']).name,raw,source['file']['sha256'],timestamp(source['retrieved_at'])))
    return dict(calendar=_pinned(m['calendar'],2_000_000),sources=tuple(sources),
        options=dict(contract_raw=_pinned(m['mapping'],2_000_000),
            reviewed_contract_sha256=m['mapping']['sha256'],bundle_sha256=m['calendar']['sha256'],
            competition='E0',season_year=m['season_year'],selected_day=day,next_day=True),
        approval_raw=_pinned(m['approval'],4096),approved_contracts=tuple(m['approved_contracts']),
        bindings=m['bindings'])


def _window(entries, at):
    if not entries:
        return 'EMPTY'
    starts=[e.fixture.identity.kickoff_utc-timedelta(hours=27) for e in entries.values()]
    ends=[e.fixture.identity.kickoff_utc-timedelta(hours=21) for e in entries.values()]
    if max(starts)>min(ends) or at>min(ends):
        return 'WINDOW_UNAVAILABLE'
    if at<max(starts):
        return 'WAITING_FOR_WINDOW'
    return None


def tick(*, schedule_path=None, state_dir=None, data_config_path=None):
    """One phase per existing hourly tick, E1 already completed; actual clocks only.

    A root-protected daily input file explicitly changes preparation to capture
    after mapping/qualification review. Missing inputs never cause discovery or
    guessed approval. Uncertain phases cannot be automatically resumed.
    """
    if schedule_path is None:
        return {'status':'DISABLED','provider_requests':0}
    result={'status':'HOLD','provider_requests':0}
    settlement=None
    def finish(value):
        if settlement is not None:value['settlement']=settlement
        return value
    try:
        acquisition.require_runtime()
        c,pin=_config(schedule_path,state_dir)
        if not c['enabled']:
            return {'status':'DISABLED','provider_requests':0}
        with shared_runner(state_dir),private_store(c['private_dir']) as store:
            _require(_config(schedule_path,state_dir)==(c,pin))
            store.put(acquisition._name('eplschedulecontract',c['period_start']),
                      {'schedule_sha256':pin})
        if c.get('delivery',{}).get('settle'):
            try:
                queue=_json(_protected(Path(c['input_dir'])/'results.json'))
                _require(type(queue) is dict and set(queue)=={'version','qualifications','entries'}
                         and type(queue['version']) is int and queue['version']==1
                         and type(queue['qualifications']) is list and len(queue['qualifications'])==1)
                qualifications=[_pinned(q,4096) for q in queue['qualifications']]
                settlement=delivery.reconcile(state_dir,qualifications=qualifications,
                    approved_contracts=c['delivery']['approved_contracts'],entries=queue['entries'],read_pinned=_pinned)
            except FileNotFoundError:
                settlement={'status':'PENDING','reason':'RESULT_INPUTS_MISSING','provider_requests':0}
        day=_now().astimezone(operator.LONDON).date()+timedelta(days=1)
        if day.isoformat() not in c['days']:
            return finish({'status':'NOT_SCHEDULED','provider_requests':0})
        path=Path(c['input_dir'])/(day.isoformat()+'.json')
        raw=_protected(path);m=_json(raw)
        _require(type(m) is dict and set(m)=={'version','day','phase','authorization','capture'})
        _require(type(m['version']) is int and m['version']==1 and m['day']==day.isoformat()
                 and m['phase'] in ('prepare','capture'))
        auth_raw=_protected(m['authorization']['path'],8192)
        _require(fingerprint(auth_raw)==m['authorization']['sha256'])
        auth=_json(auth_raw)
        _require(auth['state_dir']==c['state_dir'] and auth['private_dir']==c['private_dir']
                 and auth['quota_floor']>=c['e1_reserve']
                 and timestamp(auth['expires_at'])<=timestamp(c['expires_at']))
        if m['phase']=='prepare':
            _require(m['capture'] is None and auth['selected_day']==day.isoformat())
            _require(operator._authorization(m['authorization']['path'])==(auth,m['authorization']['sha256']))
            invoke=lambda:operator.prepare(enabled=True,authorization_path=m['authorization']['path'],
                                            authorization_sha256=m['authorization']['sha256'])
        else:
            capture=m['capture']
            _require(type(capture) is dict and set(capture)-{'preparation_sha256'}=={'calendar','metadata','freeze'})
            _require(auth['enabled_competitions']==['E0'])
            _require(acquisition._authorization(m['authorization']['path'],_now(),
                     m['authorization']['sha256'])==auth)
            for k in ('calendar','metadata'):
                _pinned(capture[k],8*1024*1024)
                _require(auth[k+'_sha256']==capture[k]['sha256'])
            _,entries=acquisition._calendar(_pinned(capture['calendar'],2_000_000),_now(),['E0'])
            # Hourly ticks before the whole-day intersection must not consume a
            # day claim. No prices or model are evaluated by this timing check.
            window=_window(entries,_now())
            if window is not None:
                return finish({'status':window,'provider_requests':0})
            freeze=_freeze(capture['freeze'],day)
            if 'delivery' in c:
                _require(freeze['approved_contracts']==tuple(c['delivery']['approved_contracts'])
                         and 'preparation_sha256' in capture)
                delivery.validate_preparation(c['private_dir'],day=day,
                    authorization_sha256=capture['preparation_sha256'],
                    calendar_raw=_pinned(capture['calendar'],2_000_000),
                    metadata_raw=_pinned(capture['metadata'],8*1024*1024),freeze=freeze)
            invoke=lambda:operator.capture(enabled=True,state_dir=state_dir,private_dir=c['private_dir'],
                calendar_path=capture['calendar']['path'],metadata_path=capture['metadata']['path'],
                data_config_path=data_config_path,authorization_path=m['authorization']['path'],epl_freeze=freeze,
                authorization_sha256=m['authorization']['sha256'])
        # Existing private store contains operation claims, not another quota
        # ledger. Claim by day/phase rather than experiment or config hash.
        key=acquisition._name('eplscheduled',{'day':day.isoformat(),'phase':m['phase']})
        with shared_runner(state_dir),private_store(c['private_dir']) as store:
            _require(_config(schedule_path,state_dir)==(c,pin) and _protected(path)==raw
                     and _protected(m['authorization']['path'],8192)==auth_raw)
            # An immutable period contract prevents an edited plan from resetting
            # the approved day count/ceiling in this authoritative store.
            store.put(acquisition._name('eplschedulecontract',c['period_start']),
                      {'schedule_sha256':pin})
            if store.get(key) is not None:
                return finish({'status':'ALREADY_CLAIMED','provider_requests':0})
            store.put(key,{'schedule_sha256':pin,'input_sha256':fingerprint(raw),'claimed_at':_now().isoformat()})
        # Release the same runner lock before the existing operator acquires it.
        # If E1 wins the race, this day/phase remains held rather than retrying.
        result=invoke()
        if m['phase']=='capture' and result['status']=='RECORDED' and 'delivery' in c:
            result['delivery']=delivery.deliver(state_dir,c['private_dir'],experiment=auth['experiment_id'],
                qualification=freeze['approval_raw'],approved_contracts=c['delivery']['approved_contracts'],
                publish=c['delivery']['publish'])
        with shared_runner(state_dir),private_store(c['private_dir']) as store:
            store.put(acquisition._name('eplscheduledresult',key),dict(result=result,
                completed_at=_now().isoformat(),schedule_sha256=pin))
        return finish(result)
    except (ValueError,OSError,KeyError,TypeError,AttributeError):
        return finish({'status':'HOLD','provider_requests':result.get('provider_requests',0),
                'reason':'SCHEDULE_INPUT_OR_COMPLETION_REJECTED'})
