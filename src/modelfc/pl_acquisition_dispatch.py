"""Disabled qualified EPL pre-dispatch freeze; uses the collector's claimed batch.

Trusted caller supplies saved calendar/history, approved contract pins and explicit
cross-provider IDs. This neither qualifies a model nor acquires those inputs.
"""
from modelfc import pl_product_source as product
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.pl_acquisition_handoff import name, prepare
from modelfc.history_coverage import timestamp


def freeze(store, state, experiment, entries, config):
    """Claim must already be durable. Any interruption leaves that claim held."""
    product.require(type(config) is dict and set(config)=={
        'calendar','sources','options','approval_raw','approved_contracts','bindings'})
    q=product._approval(config['approval_raw'],config['approved_contracts'],product.now())
    product.require(q['market']=='TEAM_TOTAL','EPL_DISPATCH_MARKET_UNSUPPORTED')
    product.require(type(config['bindings']) is dict and len(config['bindings'])==len(entries)
                    and entries,'EPL_DISPATCH_BINDING_REQUIRED')
    product.require(set(config['bindings'])==set(entries),'EPL_DISPATCH_BINDING_REQUIRED')
    source_ids=list(config['bindings'].values())
    product.require(all(type(v) is str and 0<len(v)<=120 for v in source_ids)
                    and len(set(source_ids))==len(source_ids),'EPL_DISPATCH_BINDING_REJECTED')
    session=store.get(name('session',experiment))
    product.require(session is not None and session['status']=='CLAIMED')
    # freeze enforces actual-clock creation, calendar/history provenance and the
    # unchanged qualification contract. No research artifact import is possible.
    ids=product.freeze(state,config['calendar'],sources=config['sources'],
        approval_raw=config['approval_raw'],approved_contracts=config['approved_contracts'],
        enabled=True,**config['options'])
    rows=[]
    for forecast_id in ids:
        record,identity=product._frozen(state,forecast_id)
        matches=[key for key,value in config['bindings'].items() if value==identity['provider_fixture_id']]
        product.require(len(matches)==1,'EPL_DISPATCH_FIXTURE_REJECTED')
        key=matches[0];entry=entries[key]
        product.require((identity['home_team'],identity['away_team'])==
                        (entry.home_provider_name,entry.away_provider_name)
                        and timestamp(identity['kickoff_utc'])==entry.fixture.identity.kickoff_utc,
                        'EPL_DISPATCH_FIXTURE_REJECTED')
        binding=dict(forecast_id=record['data']['forecast']['forecast_sha256'],competition='E0',
            source_fixture_id=identity['provider_fixture_id'],quote_fixture_id=key,
            home=identity['home_team'],away=identity['away_team'],kickoff=identity['kickoff_utc'],bookmaker='fanduel')
        rows.append(dict(forecast_id=forecast_id,binding=binding))
    product.require(len(rows)==len(entries) and len({r['binding']['quote_fixture_id'] for r in rows})==len(entries),
                    'EPL_DISPATCH_INCOMPLETE')
    record=dict(version=1,experiment_id=experiment,session_sha256=fingerprint(encoded(session)),
                forecasts=rows)
    store.put(name('epldispatch',experiment),record)
    product.require(store.get(name('epldispatch',experiment))==record,'EPL_DISPATCH_PERSISTENCE_REJECTED')
    validate(state,record,config['approved_contracts'])
    return record


def validate(state, record, approved_contracts):
    """Revalidate approval and durable forecasts immediately before reservation."""
    at=product.now()
    for row in record['forecasts']:
        forecast,identity=product._frozen(state,row['forecast_id'])
        product._approval(encoded(forecast['data']['qualification']),approved_contracts,at)
        product.require(timestamp(forecast['recorded_at'])<=at<timestamp(identity['kickoff_utc']),
                        'EPL_DISPATCH_WINDOW_CLOSED')


def handoff(private_dir, state, *, experiment, quote_fixture_id, approved_contracts=(),
            team_side=None, line=None, enabled=False):
    """Resolve retained pre-dispatch identity into the existing read-only handoff."""
    if enabled is not True:return {'state':'DISABLED','provider_requests':0}
    from modelfc.acquisition_storage import private_store
    with private_store(private_dir) as store:
        receipt=store.get(name('receipt',experiment));record=store.get(name('epldispatch',experiment))
        product.require(receipt is not None and receipt['status']=='RECORDED' and record is not None,
                        'EPL_DISPATCH_INCOMPLETE')
        product.require(receipt.get('epl_dispatch_sha256')==fingerprint(encoded(record)))
        product.require(record['version']==1 and record['experiment_id']==experiment
                        and record['session_sha256']==fingerprint(encoded(store.get(name('session',experiment)))))
        rows=[r for r in record['forecasts'] if r['binding']['quote_fixture_id']==quote_fixture_id]
        product.require(len(rows)==1,'EPL_DISPATCH_FIXTURE_REJECTED')
    return prepare(private_dir,state,experiment=experiment,forecast_id=rows[0]['forecast_id'],
        binding=encoded(rows[0]['binding']),approved_contracts=approved_contracts,
        team_side=team_side,line=line,enabled=True)
