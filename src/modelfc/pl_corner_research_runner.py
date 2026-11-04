"""Finite, disabled private research coordination; no collector, timer or publisher."""
from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re

from modelfc import pl_corner_research_capture as capture
from modelfc.acquisition_storage import private_store, encoded, fingerprint, read_file
from modelfc.history_coverage import MAX_SOURCE_BYTES, timestamp
from modelfc.pl_calendar import parse_calendar

MAX_RECORDS = 512
MAX_RECORD_BYTES = 262144
MAX_FIXTURES = 20


@dataclass(frozen=True)
class RegistryAuthority:
    """Operator-configured identity, not self-enrollment or proof of approval."""
    path: str
    device: int
    inode: int
    owner: int


@contextmanager
def _registry(path, authority):
    require(type(authority) is RegistryAuthority, 'RESEARCH_REGISTRY_AUTHORITY_REQUIRED')
    require(type(authority.path) is str and Path(authority.path).is_absolute()
            and str(Path(path))==authority.path, 'RESEARCH_REGISTRY_PATH_MISMATCH')
    require(all(type(v) is int for v in (authority.device,authority.inode,authority.owner)))
    with private_store(path) as store:
        fcntl.flock(store.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        info=os.fstat(store.fd)
        require((info.st_dev,info.st_ino,info.st_uid)==(authority.device,authority.inode,authority.owner),
                'RESEARCH_REGISTRY_IDENTITY_MISMATCH')
        yield store


def now():
    return datetime.now(timezone.utc)


def require(ok, reason='RESEARCH_RUNNER_REJECTED'):
    if not ok: raise ValueError(reason)


def _name(kind, key):
    return kind+'-'+fingerprint(key.encode())+'.json'


def _inventory(store):
    rows={}
    with os.scandir(store.fd) as entries:
        for entry in entries:
            require(len(rows)<MAX_RECORDS and re.fullmatch(r'(intent|done|event)-[0-9a-f]{64}\.json',entry.name),
                    'RESEARCH_REGISTRY_CAPACITY_OR_CONTENT')
            read_file(store.fd,entry.name,MAX_RECORD_BYTES,owner=os.geteuid(),mode=0o600)
            rows[entry.name]=store.get(entry.name)
    return rows


def _put(store, name, row):
    require(len(encoded(row))<MAX_RECORD_BYTES-256,'RESEARCH_RECORD_TOO_LARGE')
    try: store.put(name,row)
    except OSError: raise capture.ResearchWriteUncertain('RESEARCH_REGISTRY_WRITE_UNCERTAIN') from None


def _calendar(raw, options):
    require(type(raw) is bytes and len(raw)<=2_000_000 and fingerprint(raw)==options['bundle_sha256'])
    return parse_calendar(raw,**{k:options[k] for k in
        ('contract_raw','reviewed_contract_sha256','competition','season_year')})


def _identity(match):
    return dict(competition='E0',provider='football-data.org',provider_fixture_id=str(match.fixture_id),
                home_team=match.home,away_team=match.away,kickoff_utc=match.kickoff.isoformat())


def _key(identity):
    return (identity['competition'],identity['provider'],identity['provider_fixture_id'])


def _capture_artifacts(intent):
    with private_store(intent['capture_store']) as store:
        fcntl.flock(store.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        info=os.fstat(store.fd)
        require([info.st_dev,info.st_ino]==intent['store_identity'],'RESEARCH_STORE_REPLACED')
        candidate=store.get(capture.CANDIDATE);receipt=store.get(capture.RECEIPT)
        if candidate is None:
            require(receipt is None,'RESEARCH_ORPHAN_DURABILITY_RECEIPT')
            return None,None
        request=intent['request']
        expected=dict(version=capture.VERSION,calendar_sha256=request['calendar'],options=request['options'],
            contract_sha256=request['contract'],sources=request['sources'],
            implementation_sha256=request['capture_implementation'])
        require(candidate['request']==expected and candidate['request_sha256']==fingerprint(encoded(expected)),
                'RESEARCH_CAPTURE_INTENT_MISMATCH')
        require(timestamp(candidate['computation_started_at'])>=timestamp(intent['recorded_at']),
                'RESEARCH_CAPTURE_CLOCK_REJECTED')
        capture._result(candidate,receipt,'EXISTING')
        return candidate,receipt


def _capture_result(intent, done):
    require(done['intent_sha256']==fingerprint(encoded(intent)))
    candidate,receipt=_capture_artifacts(intent)
    require(candidate is not None and receipt is not None,'RESEARCH_CAPTURE_INCOMPLETE')
    result=capture._result(candidate,receipt,'EXISTING')
    require(fingerprint(encoded(candidate))==done['candidate_sha256'] and result['state']==done['capture_state'])
    return result


def run_once(registry, store_dir, raw, *, operation_id, authority=None, enabled=False, sources=(), **options):
    """Reserve fixture identities globally in this registry before any capture.

    Every participating caller must use this one registry. Different registries
    are not coordinated. Pending intent blocks all further captures, not evidence
    inspection. No rescheduled or uncertain attempt is automatically regenerated.
    """
    if enabled is not True:return {'state':'DISABLED','provider_requests':0}
    require(type(operation_id) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}',operation_id))
    require(set(options)=={'contract_raw','reviewed_contract_sha256','competition','season_year',
                           'selected_day','bundle_sha256'})
    require(type(sources) in (list,tuple) and len(sources)==5
            and all(type(s) in (list,tuple) and len(s)==4 for s in sources),'RESEARCH_FINITE_SOURCES_REQUIRED')
    sources=tuple(tuple(s) for s in sources);options=deepcopy(options)
    require(len(sources)==5 and all(type(s[1]) is bytes and len(s[1])<=MAX_SOURCE_BYTES for s in sources))
    calendar=_calendar(raw,options)
    fixtures=[_identity(m) for m in calendar.matches if m.day==options['selected_day']]
    require(len(fixtures)<=MAX_FIXTURES)
    request=capture.pl_corner_capture._json_value(dict(calendar=fingerprint(raw),
        options={k:v for k,v in options.items() if k!='contract_raw'},contract=fingerprint(options['contract_raw']),
        sources=[(n,fingerprint(b),p,t.isoformat() if t else None) for n,b,p,t in sources],
        capture_implementation=capture._pins(),runner_sha256=fingerprint(Path(__file__).read_bytes())))
    request_pin=fingerprint(encoded(request));intent_name=_name('intent',operation_id);done_name=_name('done',operation_id)
    with _registry(registry,authority) as store:
        rows=_inventory(store);old=rows.get(intent_name);done=rows.get(done_name)
        if old is not None:
            require(old['request']==request and old['request_sha256']==request_pin,'RESEARCH_OPERATION_CONFLICT')
            if done is None:return {'state':'INCOMPLETE','operation_id':operation_id,'reason':'INSPECT_NO_AUTOMATIC_RETRY'}
            result=_capture_result(old,done)
            return dict(state='EXISTING',operation_id=operation_id,capture_state=result['state'],candidate_sha256=done['candidate_sha256'])
        require(done is None)
        intents=[r for n,r in rows.items() if n.startswith('intent-')]
        require(all(_name('done',r['operation_id']) in rows for r in intents),'RESEARCH_RECOVERY_REQUIRED')
        overlaps=[]
        for intent in intents:
            for original in intent['fixtures']:
                for target in fixtures:
                    if _key(original)==_key(target):
                        require(original==target,'RESEARCH_FIXTURE_IDENTITY_CONFLICT')
                        overlaps.append(dict(original_operation_id=intent['operation_id'],fixture=original))
                        continue
                    require((original['home_team'],original['away_team'],original['kickoff_utc'])!=
                            (target['home_team'],target['away_team'],target['kickoff_utc']), 'RESEARCH_REVERSE_IDENTITY_CONFLICT')
        if overlaps:
            covered={_key(r['fixture']) for r in overlaps}
            uncovered=[f for f in fixtures if _key(f) not in covered]
            return dict(state='BLOCKED_PARTIAL_DUPLICATE' if uncovered else 'DUPLICATE',
                        overlaps=overlaps,uncovered=uncovered,whole_batch_blocked=True)
        require(len(rows)<=MAX_RECORDS-2,'RESEARCH_REGISTRY_CAPACITY')
        with private_store(store_dir) as destination:
            info=os.fstat(destination.fd);root=os.fstat(store.fd)
            require((info.st_dev,info.st_ino)!=(root.st_dev,root.st_ino))
            require(destination.get(capture.CANDIDATE) is None and destination.get(capture.RECEIPT) is None,
                    'RESEARCH_DESTINATION_ALREADY_USED')
        intent=dict(operation_id=operation_id,request=request,request_sha256=request_pin,fixtures=fixtures,
            capture_store=str(Path(store_dir)),store_identity=[info.st_dev,info.st_ino],recorded_at=now().isoformat())
        _put(store,intent_name,intent)
        result=capture.capture(store_dir,raw,enabled=True,sources=sources,**options)
        require(result['state']!='INCOMPLETE' and result['write_state']=='CREATED','RESEARCH_CAPTURE_INCOMPLETE')
        require(timestamp(result['candidate']['computation_started_at'])>=timestamp(intent['recorded_at']),
                'RESEARCH_CAPTURE_CLOCK_REJECTED')
        done=dict(intent_sha256=fingerprint(encoded(intent)),candidate_sha256=fingerprint(encoded(result['candidate'])),
                  capture_state=result['state'])
        _capture_result(intent,done)
        _put(store,done_name,done)
        return dict(state='RECORDED',operation_id=operation_id,**done)


def _chain(rows, operation_id, fixture_id):
    events=[r for n,r in rows.items() if n.startswith('event-') and
            r['operation_id']==operation_id and r['fixture_id']==fixture_id]
    chain=[];tip=None
    while events:
        children=[e for e in events if e['transition']['previous']==tip]
        require(len(children)==1,'RESEARCH_EVENT_CHAIN_CONFLICT')
        event=children[0]
        require(event['event_sha256']==fingerprint(encoded({k:v for k,v in event.items() if k!='event_sha256'})))
        require(event['reviewed_transition_sha256']==fingerprint(encoded(event['transition'])))
        if chain:require(timestamp(event['recorded_at'])>=timestamp(chain[-1]['recorded_at']))
        chain.append(event);events.remove(event);tip=event['event_sha256']
    return chain


def record_evidence(registry, *, operation_id, fixture_id, raw, source_sha256, kind,
                    reviewed_transition_sha256, previous=None, reason=None, calendar_options=None, authority=None, enabled=False):
    """Append a reviewed status/result transition; never update frozen probabilities.

    Review pin identifies canonical transition bytes (not provider authenticity or
    qualification). Caller must establish original evidence authenticity separately.
    No missing result can be converted to a successful outcome.
    """
    if enabled is not True:return {'state':'DISABLED','provider_requests':0}
    require(type(raw) is bytes and 0<len(raw)<=2_000_000 and fingerprint(raw)==source_sha256)
    require(kind in ('calendar','result') and type(fixture_id) is str)
    with _registry(registry,authority) as store:
        rows=_inventory(store);intent=rows.get(_name('intent',operation_id));done=rows.get(_name('done',operation_id))
        require(intent is not None and done is not None,'RESEARCH_CAPTURE_INCOMPLETE')
        result=_capture_result(intent,done)
        matches=[i for i in intent['fixtures'] if i['provider_fixture_id']==fixture_id]
        require(len(matches)==1,'RESEARCH_UNKNOWN_FIXTURE');identity=matches[0]
        transition=dict(operation_id=operation_id,fixture=identity,candidate_sha256=done['candidate_sha256'],
                        kind=kind,source_sha256=source_sha256,previous=previous,reason=reason)
        require(fingerprint(encoded(transition))==reviewed_transition_sha256,'RESEARCH_TRANSITION_NOT_REVIEWED')
        chain=_chain(rows,operation_id,fixture_id)
        for event in chain:
            if event['transition']==transition:return event
        require(previous==(chain[-1]['event_sha256'] if chain else None),'RESEARCH_STALE_CHAIN_TIP')
        require((not chain and reason is None) or (chain and type(reason) is str and 0<len(reason.strip())<=2000))
        observed=now()
        require(observed>=timestamp(result['candidate']['write_started_at']),'RESEARCH_CLOCK_REJECTED')
        if chain:require(observed>=timestamp(chain[-1]['recorded_at']),'RESEARCH_CLOCK_REJECTED')
        if kind=='calendar':
            require(calendar_options is not None and calendar_options['reviewed_contract_sha256']==intent['request']['contract'])
            options={**calendar_options,'bundle_sha256':source_sha256}
            calendar=_calendar(raw,options)
            original_retrieved=timestamp(result['candidate']['computation']['readiness']['calendar']['retrieved_at'])
            require(original_retrieved<=calendar.retrieved_at<=observed,'RESEARCH_STALE_STATUS_EVIDENCE')
            matched=[m for m in calendar.matches if str(m.fixture_id)==fixture_id]
            require(len(matched)==1,'RESEARCH_MISSING_FIXTURE')
            m=matched[0];updated=_identity(m)
            require(all(updated[k]==identity[k] for k in identity if k!='kickoff_utc'),'RESEARCH_FIXTURE_IDENTITY_CONFLICT')
            evidence=dict(bundle_sha256=source_sha256,source_sha256=calendar.source_sha256,
                          retrieved_at=calendar.retrieved_at.isoformat(),fixture=updated,status=m.status)
            state=('RESCHEDULED' if updated['kickoff_utc']!=identity['kickoff_utc'] else
                   'CANCELED' if m.status=='CANCELLED' else 'POSTPONED' if m.status=='POSTPONED' else 'PENDING')
        else:
            # Pure existing normalized receipt validator only; never call its
            # qualification, writer, projection or publication functions.
            from modelfc.pl_product_source import _result_source
            evidence=_result_source(raw,source_sha256,identity,observed)
            state='PENDING' if evidence['home_corners'] is None else 'FINAL'
            if result['state']!='PAYLOAD_DURABLE_PRE_KICKOFF':state='EXCLUDED_CAPTURE'
            if any(e['state'] in ('RESCHEDULED','POSTPONED','CANCELED') for e in chain):state='HELD_STATUS_CONFLICT'
        if state=='PENDING' and any(e['state'] in ('RESCHEDULED','POSTPONED','CANCELED') for e in chain):
            state='HELD_STATUS_CONFLICT'
        if chain:require(timestamp(evidence['retrieved_at'])>=timestamp(chain[-1]['evidence']['retrieved_at']),
                         'RESEARCH_EVIDENCE_TIME_REGRESSION')
        require(len(rows)<MAX_RECORDS,'RESEARCH_REGISTRY_CAPACITY')
        event=dict(operation_id=operation_id,fixture_id=fixture_id,transition=transition,
                   recorded_at=observed.isoformat(),evidence=evidence,state=state,
                   reviewed_transition_sha256=reviewed_transition_sha256,
                   implementation_sha256={n:fingerprint((Path(__file__).parent/n).read_bytes())
                      for n in ('pl_corner_research_runner.py','pl_product_source.py','pl_calendar.py')})
        event['event_sha256']=fingerprint(encoded(event))
        _put(store,_name('event',event['event_sha256']),event)
        return event


def inspect_fixture(registry, *, operation_id, fixture_id, authority=None):
    """Read-only bounded status; no outcome when evidence is absent or incomplete."""
    with _registry(registry,authority) as store:
        rows=_inventory(store);intent=rows.get(_name('intent',operation_id));done=rows.get(_name('done',operation_id))
        require(intent is not None and any(i['provider_fixture_id']==fixture_id for i in intent['fixtures']))
        if done is None:return {'state':'INCOMPLETE','events':[]}
        result=_capture_result(intent,done);chain=_chain(rows,operation_id,fixture_id)
        return {'state':chain[-1]['state'] if chain else 'MISSING_RESULT','capture_state':result['state'],
                'events':chain,'candidate_sha256':done['candidate_sha256'],'research_only':True,'publication_allowed':False}


def reconcile_operation(registry, *, operation_id, authority=None, expected_transition_sha256=None):
    """Read-only artifact recognition. HOLD never clears intent or grants eligibility.

    No clock sample, model call, timestamp reconstruction, write, fsync or retry.
    Integrity/path errors propagate as blockers rather than looking like absence.
    """
    require(type(operation_id) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}',operation_id))
    require(expected_transition_sha256 is None or type(expected_transition_sha256) is str
            and re.fullmatch('[0-9a-f]{64}',expected_transition_sha256))
    with _registry(registry,authority) as store:
        rows=_inventory(store);intent=rows.get(_name('intent',operation_id));done=rows.get(_name('done',operation_id))
        if intent is None:
            require(done is None and not any(n.startswith('event-') and r['operation_id']==operation_id
                    for n,r in rows.items()),'RESEARCH_ORPHAN_RECORD')
            return {'state':'NO_RESERVATION','operation_id':operation_id,'eligibility_reassessed':False,'writes':0}
        require(intent['operation_id']==operation_id and intent['request_sha256']==fingerprint(encoded(intent['request'])))
        candidate,receipt=_capture_artifacts(intent)
        if done is not None:
            result=_capture_result(intent,done);artifact='COMPLETE_RECORDS'
        elif receipt is not None:
            result=capture._result(candidate,receipt,'EXISTING');artifact='VALID_CAPTURE_WITHOUT_RUNNER_COMPLETION'
        else:
            result=None;artifact='CANDIDATE_WITHOUT_DURABILITY_RECEIPT' if candidate is not None else 'RESERVATION_ONLY'
        events=[]
        for fixture in intent['fixtures']:
            events.extend(_chain(rows,operation_id,fixture['provider_fixture_id']))
        all_events=[r for n,r in rows.items() if n.startswith('event-') and r['operation_id']==operation_id]
        require(len(events)==len(all_events),'RESEARCH_ORPHAN_EVENT')
        require(not events or done is not None,'RESEARCH_EVENT_WITHOUT_COMPLETION')
        for event in events:
            transition=event['transition']
            require(transition['operation_id']==operation_id and transition['fixture'] in intent['fixtures']
                    and transition['fixture']['provider_fixture_id']==event['fixture_id']
                    and transition['candidate_sha256']==done['candidate_sha256'],'RESEARCH_EVENT_BINDING_CONFLICT')
        matched=[e for e in events if e['reviewed_transition_sha256']==expected_transition_sha256]
        require(len(matched)<=1,'RESEARCH_EVENT_CHAIN_CONFLICT')
        return {'state':'RECORDS_PRESENT' if done else 'HOLD','artifact_state':artifact,
            'operation_id':operation_id,'request_sha256':intent['request_sha256'],
            'intent_recorded_at':intent['recorded_at'],
            'capture_state':result['state'] if result else None,
            'candidate_sha256':fingerprint(encoded(candidate)) if candidate else None,
            'durability_receipt':receipt,'expected_event':matched[0] if matched else None,
            'expected_event_state':('PRESENT' if matched else 'ABSENT_NOT_RECONSTRUCTED')
                if expected_transition_sha256 else 'NOT_REQUESTED',
            'event_count':len(events),'eligibility_reassessed':False,'writes':0,
            'next_action':'REVIEW_ONLY_NO_AUTOMATIC_RECOVERY','research_only':True,'publication_allowed':False}
