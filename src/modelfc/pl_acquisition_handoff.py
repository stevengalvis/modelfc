"""Opt-in saved acquisition correlation; no acquisition, ledger or publication."""
import base64
from dataclasses import asdict
import json

from modelfc.acquisition_storage import encoded, fingerprint, private_store
from modelfc.five_league_readiness import SavedQuotes
from modelfc.history_coverage import timestamp
from modelfc.shared_odds import snapshot_from_bytes
from modelfc import pl_product_source as product


def name(kind, identity):
    return kind+'-'+fingerprint(encoded(identity))+'.json'


def retain(store, experiment, snapshot, metadata):
    """Called only by acquisition while its existing shared runner lock is held.

    This records correlation at successful acquisition, not a historical backfill.
    Receipt publication remains the acquisition caller's final completion step.
    """
    product.require(fingerprint(metadata)==snapshot.metadata_sha256)
    session=store.get(name('session',experiment));reservation=store.get(name('reservation',experiment))
    product.require(session is not None and session['experiment_id']==experiment and session['status']=='CLAIMED')
    product.require(reservation==dict(credits_reserved=1,experiment_id=experiment))
    payload=store.get(name('payload',snapshot.payload_sha256))
    product.require(payload is not None and payload['sha256']==snapshot.payload_sha256
                    and timestamp(payload['retrieved_at'])==snapshot.retrieved_at)
    row=dict(version=1,experiment_id=experiment,session_sha256=fingerprint(encoded(session)),
        reservation_sha256=fingerprint(encoded(reservation)),payload_sha256=snapshot.payload_sha256,
        metadata_sha256=snapshot.metadata_sha256,metadata_base64=base64.b64encode(metadata).decode('ascii'),
        observation_id=snapshot.observation_id,retrieved_at=snapshot.retrieved_at.isoformat())
    store.put(name('handoff',experiment),row)
    return fingerprint(encoded(row))


def prepare(private_dir, state, *, experiment, forecast_id, binding, approved_contracts=(),
            team_side=None, line=None, enabled=False):
    """Read-only handoff to existing observe stage; preserves original quote time.

    Caller supplies the existing approved forecast and explicit fixture binding.
    No forecast creation, inferred association, approval or quota mutation occurs.
    Old receipts without correlation fail closed; they are never reconstructed.
    """
    if enabled is not True:return {'state':'DISABLED','provider_requests':0}
    product.require(type(experiment) is str and 0<len(experiment)<=120)
    with private_store(private_dir) as store:
        # Immutable completion receipt is the commit marker; incomplete writes fail closed.
        receipt=store.get(name('receipt',experiment));row=store.get(name('handoff',experiment))
        product.require(receipt is not None and receipt['status']=='RECORDED' and row is not None,
                        'EPL_HANDOFF_INCOMPLETE')
        product.require(receipt.get('handoff_sha256')==fingerprint(encoded(row)), 'EPL_HANDOFF_PIN_REJECTED')
        product.require(row['version']==1 and row['experiment_id']==experiment)
        session=store.get(name('session',experiment));reservation=store.get(name('reservation',experiment))
        product.require(session is not None and session['experiment_id']==experiment and session['status']=='CLAIMED')
        product.require(reservation==dict(credits_reserved=1,experiment_id=experiment))
        product.require(fingerprint(encoded(session))==row['session_sha256']
                        and fingerprint(encoded(reservation))==row['reservation_sha256'])
        payload=store.get(name('payload',row['payload_sha256']))
        product.require(payload is not None and payload['http_status']==200
                        and payload['sha256']==row['payload_sha256']
                        and timestamp(payload['retrieved_at'])==timestamp(row['retrieved_at']))
        raw=base64.b64decode(payload['payload_base64'],validate=True)
        metadata=base64.b64decode(row['metadata_base64'],validate=True)
        product.require(fingerprint(raw)==row['payload_sha256'] and fingerprint(metadata)==row['metadata_sha256'])
        saved=SavedQuotes(raw,row['payload_sha256'],metadata,row['metadata_sha256'],timestamp(row['retrieved_at']))
        snapshot=snapshot_from_bytes(raw,metadata,retrieved_at=saved.retrieved_at)
        product.require(snapshot.observation_id==row['observation_id'])
        retained=store.get(name('snapshot',snapshot.observation_id))
        expected=json.loads(json.dumps(asdict(snapshot),default=lambda v:v.isoformat(),allow_nan=False))
        product.require(retained==expected,'EPL_HANDOFF_SNAPSHOT_REJECTED')
    record,identity=product._frozen(state,forecast_id)
    at=product.now()
    product.require(record['data']['qualification_sha256'] in approved_contracts,'EPL_FORECAST_NOT_APPROVED')
    product._approval(encoded(record['data']['qualification']),approved_contracts,at)
    product.require(record['data']['qualification']['market']=='TEAM_TOTAL','EPL_HANDOFF_MARKET_UNSUPPORTED')
    f=record['data']['forecast'];b=json.loads(binding)
    expected=dict(forecast_id=f['forecast_sha256'],competition='E0',
        source_fixture_id=identity['provider_fixture_id'],home=identity['home_team'],away=identity['away_team'],
        kickoff=identity['kickoff_utc'],bookmaker='fanduel')
    product.require(set(b)==set(expected)|{'quote_fixture_id'} and all(b[k]==v for k,v in expected.items()))
    quote_id=b['quote_fixture_id']
    product.require(type(quote_id) is str and 0<len(quote_id)<=120)
    product.require(team_side in ('HOME','AWAY') and type(line) in (int,float) and line in (3.5,4.5,5.5,6.5))
    product.require(timestamp(record['recorded_at'])<saved.retrieved_at<=at<timestamp(identity['kickoff_utc']),
                    'EPL_QUOTE_BEFORE_FREEZE_OR_WINDOW')
    matches=[v for v in snapshot.fixtures if v.identity.competition=='E0' and v.identity.tournament_id==17
             and v.identity.fixture_id==quote_id and v.identity.kickoff_utc==timestamp(identity['kickoff_utc'])
             and (v.home_team,v.away_team)==(identity['home_team'],identity['away_team'])]
    product.require(len(matches)==1,'EPL_HANDOFF_FIXTURE_REJECTED')
    product.require(any(o['competition']=='E0' and o['fixture_id']==quote_id
                        and timestamp(o['kickoff_utc'])==timestamp(identity['kickoff_utc'])
                        and o['bookmaker']=='fanduel'
                        for o in session['obligations']),'EPL_HANDOFF_UNCLAIMED_FIXTURE')
    eligible=True
    try: product._consume(record,saved,binding,at,team_side,line)
    except ValueError: eligible=False
    # A correlated but unavailable/stale pair must still reach observe, whose
    # immutable latest failed observation suppresses any older usable quote.
    return dict(state='PREPARED',provider_requests=0,publication_allowed=False,
        handoff_sha256=fingerprint(encoded(row)),quote_eligible=eligible,
        inputs=dict(forecast_id=forecast_id,saved=saved,binding=binding,team_side=team_side,line=line))
