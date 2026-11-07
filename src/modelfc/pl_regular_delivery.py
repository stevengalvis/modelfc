"""Saved authentic-input checks and existing E0 delivery/result stages; no HTTP."""
import base64

from modelfc import pl_acquisition_dispatch as dispatch, pl_product_source as product
from modelfc import pl_saved_orchestration as stages, pl_history_readiness as history
from modelfc.acquisition_storage import encoded, fingerprint, private_store
from modelfc.guarded_acquisition import _name, _calendar
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.history_coverage import timestamp


def validate_preparation(private_dir, *, day, authorization_sha256, calendar_raw, metadata_raw, freeze):
    """Bind reviewed daily inputs to the actual retained preparation bytes/times.

    Independent football-data calendar/history gates and explicit reviewed native
    fixture bindings are required. No timestamp, participant ID or alias is guessed.
    """
    at=product.now()
    key=_name('eplprepclaim',{'competition':'E0','day':day.isoformat()})
    with private_store(private_dir) as store:
        claim=store.get(key);done=store.get(_name('eplprepcomplete',key))
        product.require(claim is not None and done is not None and done['status']=='RETAINED_UNREVIEWED'
                        and done['authorization_sha256']==authorization_sha256
                        and claim['authorization_sha256']==authorization_sha256,'EPL_PREPARATION_MISSING')
        payloads={};receipts={}
        product.require(len(done['receipts'])==2)
        for item in done['receipts']:
            receipt=store.get(item['name'])
            product.require(receipt is not None and fingerprint(encoded(receipt))==item['sha256']
                            and receipt['authorization_sha256']==authorization_sha256)
            raw=base64.b64decode(receipt['payload_base64'],validate=True)
            product.require(fingerprint(raw)==receipt['payload_sha256'])
            product.require(timestamp(receipt['started_at'])<=timestamp(receipt['retrieved_at'])<=at)
            product.require(receipt['kind'] not in payloads)
            payloads[receipt['kind']]=raw;receipts[receipt['kind']]=receipt
    product.require(set(payloads)=={'FIXTURE_DISCOVERY','MARKET_METADATA'})
    product.require(metadata_raw==payloads['MARKET_METADATA'],'EPL_METADATA_PROVENANCE_REJECTED')
    _,entries=_calendar(calendar_raw,at,['E0'])
    calendar=_json(calendar_raw)
    product.require(timestamp(calendar['verified_at'])==timestamp(receipts['FIXTURE_DISCOVERY']['retrieved_at']),
                    'EPL_DISCOVERY_TIME_REJECTED')
    rows=_json(payloads['FIXTURE_DISCOVERY']);product.require(type(rows) is list and len(rows)<=500)
    native={}
    for row in rows:
        fid=row['fixtureId']
        product.require(fid not in native and row['tournamentId']==17 and row['statusId']==0)
        native[fid]=row
    product.require(set(native)==set(entries)==set(freeze['bindings']),'EPL_DISCOVERY_COHORT_REJECTED')
    for fid,entry in entries.items():
        row=native[fid]
        product.require((row['participant1Id'],row['participant2Id'],row['participant1Name'],row['participant2Name'],
            timestamp(row['startTime']))==(entry.home_provider_id,entry.away_provider_id,
            entry.home_provider_name,entry.away_provider_name,entry.fixture.identity.kickoff_utc),
            'EPL_DISCOVERY_IDENTITY_REJECTED')
    report=history.assess(freeze['calendar'],sources=freeze['sources'],enabled=True,as_of=at,**freeze['options'])
    product.require(report['state'] in ('INPUTS_ELIGIBLE','EMPTY'),'EPL_DAILY_INPUTS_NOT_READY')
    fixtures={str(f['fixture_id']):f for f in report['fixtures']}
    product.require(len(set(freeze['bindings'].values()))==len(entries)
                    and set(freeze['bindings'].values())==set(fixtures),'EPL_CALENDAR_COHORT_REJECTED')
    for fid,source_id in freeze['bindings'].items():
        f=fixtures[source_id];e=entries[fid]
        product.require(f['status'] in ('SCHEDULED','TIMED') and
            (f['home'],f['away'],timestamp(f['kickoff']))==
            (e.home_provider_name,e.away_provider_name,e.fixture.identity.kickoff_utc),'EPL_CALENDAR_IDENTITY_REJECTED')
    return {'status':'INPUTS_BOUND','fixtures':len(entries),'provider_requests':0}


def deliver(state, private_dir, *, experiment, qualification, approved_contracts, publish=False):
    """Immediately consume all saved pairs; unavailable observations stay explicit."""
    config=stages.Configuration(enabled=True,contracts=(qualification,),approved_contracts=tuple(approved_contracts))
    stages._configuration(config,product.now())
    with private_store(private_dir) as store:
        record=store.get(_name('epldispatch',experiment))
        product.require(record is not None and 1<=len(record['forecasts'])<=64,'EPL_DELIVERY_MISSING')
        ids=[r['binding']['quote_fixture_id'] for r in record['forecasts']]
        product.require(len(ids)==len(set(ids)))
    observed=unavailable=0
    for fid in ids:
        for side in ('HOME','AWAY'):
            for line in (3.5,4.5,5.5,6.5):
                handoff=dispatch.handoff(private_dir,state,experiment=experiment,quote_fixture_id=fid,
                    approved_contracts=approved_contracts,team_side=side,line=line,enabled=True)
                key='delivery-'+fingerprint(encoded([experiment,fid,side,line]))
                stages.run_once(state,config=config,operation_id=key,stage='observe',inputs=handoff['inputs'])
                observed+=1;unavailable+=not handoff['quote_eligible']
    published=None
    if publish is True:
        outcome=stages.run_once(state,config=config,operation_id='publish-'+fingerprint(encoded(experiment)),stage='publish')
        published=outcome['value']
    return {'status':'DELIVERED','observations':observed,'unavailable':unavailable,
            'publication_result':published,'provider_requests':0}


def reconcile(state, *, qualifications, approved_contracts, entries, read_pinned):
    """Bounded reviewed normalized finals plus retained provider originals only.

    Missing entries are pending. Corrections require explicit parent/reason; the
    existing result chain validates identity, chronology and immutable originals.
    """
    product.require(type(entries) is list and len(entries)<=32)
    config=stages.Configuration(enabled=True,contracts=tuple(qualifications),approved_contracts=tuple(approved_contracts))
    outcomes=[]
    for entry in entries:
        product.require(type(entry) is dict and set(entry)=={'forecast_id','receipt','original','supersedes','reason'})
        frozen,_=product._frozen(state,entry['forecast_id'])
        pins=stages._configuration(config,timestamp(frozen['recorded_at']))
        product.require(frozen['data']['qualification_sha256'] in pins,'EPL_FORECAST_NOT_APPROVED')
        if entry['receipt'] is None or entry['original'] is None:
            outcomes.append({'status':'PENDING','reason':'AUTHENTIC_FINAL_EVIDENCE_MISSING'});continue
        raw=read_pinned(entry['receipt'],65536);value=_json(raw)
        original=read_pinned(entry['original'],8*1024*1024)
        product.require(fingerprint(original)==value['source_sha256'],'EPL_FINAL_ORIGINAL_REJECTED')
        inputs=dict(forecast_id=entry['forecast_id'],raw=raw,result_sha256=fingerprint(raw),
                    supersedes=entry['supersedes'],reason=entry['reason'])
        identity=fingerprint(encoded([entry['forecast_id'],fingerprint(raw),entry['supersedes'],entry['reason']]))
        result=stages.run_once(state,config=config,operation_id='result-'+identity,stage='result',inputs=inputs)
        outcomes.append({'status':'RECORDED','result_id':result['value']})
    return {'status':'RECONCILED','outcomes':outcomes,'provider_requests':0}
