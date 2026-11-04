"""Disabled private research persistence primitive; no study runner or publication."""
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
from pathlib import Path

from modelfc import pl_corner_capture
from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import private_store, encoded, fingerprint
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.history_coverage import validated_sources, timestamp
from modelfc.matches import TeamCornerObservation

VERSION = 'pl-private-corner-research-v1'
CANDIDATE = 'research-' + fingerprint(VERSION.encode()) + '.json'
RECEIPT = 'durability-' + fingerprint(VERSION.encode()) + '.json'
LINES = (3.5, 4.5, 5.5, 6.5)
SEMANTICS = 'TRUSTED_HOST_POST_PAYLOAD_FSYNC_OBSERVATION_NOT_RECEIPT_DURABILITY'


class ResearchWriteUncertain(OSError):
    """Inspect the same store; never recreate an earlier durability observation."""


def now():
    return datetime.now(timezone.utc)


def _require(ok):
    if not ok:
        raise ValueError('RESEARCH_CAPTURE_REJECTED')


def _pins():
    root=Path(__file__).parent
    return {**pl_corner_capture._implementation(), **{name:hashlib.sha256((root/name).read_bytes()).hexdigest()
        for name in ('pl_corner_research_capture.py','acquisition_storage.py','ledger_storage.py')}}


def _baseline(sources, options, started, computed):
    day,year=options['selected_day'],options['season_year']
    records,_=validated_sources('E0',sources,before=day,observed_at=started,enforce_seasons=True,
                                validation_scope='GOALS_CORNERS')
    observations=[TeamCornerObservation(r.match_date,r.team,r.opponent,r.venue,r.corners_for,r.corners_against)
        for r in records if r.match_date<day and f'{year-4}/'<=r.season<=f'{year}/{year+1}'
        and r.corners_for is not None and r.corners_against is not None]
    _require(fingerprint(encoded(pl_corner_capture._json_value([asdict(r) for r in observations])))
             ==computed['model_input_sha256'])
    result={}
    for venue in ('home','away'):
        prior=[r for r in observations if r.venue.value==venue]
        _require(bool(prior))
        result[venue]={'history_count':len(prior),'lines':[
            {'line':line,'over':sum(r.corners_for>line for r in prior)/len(prior),
             'under':sum(r.corners_for<line for r in prior)/len(prior)} for line in LINES]}
    pairs=[]
    for forecast in computed['forecasts']:
        for venue in ('home','away'):
            prediction=forecast['prediction']
            for reference in result[venue]['lines']:
                model=corner_line_probabilities(prediction[venue]['expected_corners'],reference['line'],
                                                prediction['dispersion_size'])
                pairs.append({'fixture_id':forecast['identity']['fixture_id'],'venue':venue,
                    'line':reference['line'],'model_over':model.over,'model_under':model.under,
                    'baseline_over':reference['over'],'baseline_under':reference['under']})
    return result,pairs


def _state(candidate, at):
    forecasts=candidate['computation']['forecasts']
    deadline=min((timestamp(f['identity']['kickoff']) for f in forecasts),default=None)
    state=('CLOCK_REJECTED' if at<timestamp(candidate['write_started_at']) else
           'NO_ELIGIBLE_FORECASTS' if not forecasts else
           'PAYLOAD_DURABLE_PRE_KICKOFF' if at<deadline else 'LATE_EXCLUDED')
    return state,deadline.isoformat() if deadline else None


def _result(candidate, receipt, write_state):
    if receipt is None:
        return {'state':'INCOMPLETE','write_state':write_state,'candidate':candidate,
                'research_only':True,'publication_allowed':False,
                'reason':'NO_DURABILITY_RECEIPT_DO_NOT_RECONSTRUCT'}
    _require(set(receipt)=={'candidate_sha256','request_sha256','write_started_at',
        'payload_durability_observed_at','earliest_kickoff','state','semantics'})
    _require(receipt['candidate_sha256']==fingerprint(encoded(candidate))
             and receipt['request_sha256']==candidate['request_sha256']
             and receipt['write_started_at']==candidate['write_started_at'])
    expected,deadline=_state(candidate,timestamp(receipt['payload_durability_observed_at']))
    _require(receipt['state']==expected and receipt['earliest_kickoff']==deadline and receipt['semantics']==SEMANTICS)
    return {'state':expected,'write_state':write_state,'candidate':candidate,'receipt':receipt,
            'research_only':True,'publication_allowed':False}


def capture(store_dir, raw, *, enabled=False, sources=(), **options):
    """One immutable batch per private store; actual clock, no imported replay.

    Candidate file+directory fsync completes before the clock observation. The
    later immutable receipt attests *candidate* durability, not its own completion
    before kickoff. No receipt after an interrupted attempt is reconstructed.
    """
    if enabled is not True:
        return {'state':'DISABLED','research_only':True,'publication_allowed':False,'provider_requests':0}
    _require('as_of' not in options)
    sources=tuple(sources)
    _require(raw is None or type(raw) is bytes)
    request={'version':VERSION,'calendar_sha256':fingerprint(raw) if raw is not None else None,
        'options':pl_corner_capture._json_value({k:v for k,v in options.items() if k!='contract_raw'}),
        'contract_sha256':fingerprint(options['contract_raw']),
        'sources':[(name,fingerprint(data),pin,retrieved.isoformat() if retrieved else None)
                   for name,data,pin,retrieved in sources], 'implementation_sha256':_pins()}
    request=pl_corner_capture._json_value(request)
    request_sha=fingerprint(encoded(request))
    with private_store(store_dir) as store:
        fcntl.flock(store.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        old=store.get(CANDIDATE);receipt=store.get(RECEIPT)
        if old is not None:
            _require(old['request_sha256']==request_sha and old['request']==request)
            return _result(old,receipt,'EXISTING')
        _require(receipt is None)
        started=utc(now())
        # Pure preparation retains OFFLINE_REPLAY. Only the separate persistence
        # observation, never an existing replay artifact, supplies timing.
        computed=pl_corner_capture.prepare(raw,sources=sources,enabled=True,as_of=started,**options)
        baseline,pairs=_baseline(sources,options,started,computed) if computed['forecasts'] else ({},[])
        writing=utc(now())
        _require(started<=writing)
        candidate={'version':VERSION,'request':request,'request_sha256':request_sha,
            'computation_started_at':started.isoformat(),'write_started_at':writing.isoformat(),
            'computation':computed,'prior_venue_baseline':baseline,'paired_probabilities':pairs,
            'research_only':True,'qualification':'NOT_ASSESSED','provider_requests':0,
            'publication_allowed':False,'live_activation':False}
        try:
            store.put(CANDIDATE,candidate)
            durable=utc(now())
            state,deadline=_state(candidate,durable)
            receipt={'candidate_sha256':fingerprint(encoded(candidate)),'request_sha256':request_sha,
                'write_started_at':writing.isoformat(),'payload_durability_observed_at':durable.isoformat(),
                'earliest_kickoff':deadline,'state':state,'semantics':SEMANTICS}
            store.put(RECEIPT,receipt)
        except OSError:
            raise ResearchWriteUncertain('RESEARCH_WRITE_OUTCOME_UNCERTAIN_INSPECT_SAME_STORE') from None
        return _result(candidate,receipt,'CREATED')
