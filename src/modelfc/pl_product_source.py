"""Disabled EPL prospective saved-input adapter for the shared publication pipeline.

No provider client, scheduler, HTTP writer or automatic activation. An operator's
trusted allowlist must identify independently reviewed qualification contracts.
Existing replay artifacts cannot be imported as prospective source records.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc import pl_btts, pl_corner_capture, pl_corner_odds, published_btts
from modelfc.goal_totals import GoalAggregates
from modelfc.five_league_readiness import SavedQuotes
from modelfc.history_coverage import timestamp
from modelfc.corner_analysis_store import _canonical_hash as digest
from modelfc.corner_forecasts import CornerLineProbability
from modelfc.corner_markets import price_corner_market, american_odds_terms
from modelfc.corner_opportunities import qualification_decision_for_policy
from modelfc.ledger_storage import LedgerError, ledger_lock, ensure_directory, read_json_record, write_new_record

DIRECTORY = 'epl-prospective'


def now():
    return datetime.now(timezone.utc)


def require(ok, reason='EPL_SOURCE_REJECTED'):
    if not ok:
        raise LedgerError(reason)


def _put(state, kind, data, at, lock, *, deadline=None):
    payload = dict(kind=kind, data=data, recorded_at=at.isoformat())
    identity = digest(payload)
    record = {**payload, 'record_hash': identity}
    require(len(encoded(record)) <= 2_000_000, 'EPL_EVIDENCE_TOO_LARGE')
    directory = Path(state)/DIRECTORY
    ensure_directory(directory, 'EPL evidence')
    path = directory/(identity+'.json')
    if path.exists():
        require(_get(state, identity) == record)
    else:
        from itertools import islice
        require(len(list(islice(directory.iterdir(), 2000))) < 2000, 'EPL_CAPACITY_EXCEEDED')
        write_new_record(path, record,
            before_publish=(lambda: require(at <= now() < deadline, 'EPL_WRITE_WINDOW_CLOSED')) if deadline else None)
    from modelfc.research_predictions import _sync_directories
    _sync_directories(directory, Path(state))
    return identity


def _get(state, identity):
    require(isinstance(identity, str) and re.fullmatch('[0-9a-f]{64}', identity))
    directory = Path(state)/DIRECTORY
    require(not directory.is_symlink())
    path = directory/(identity+'.json')
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 2_000_000)
    r = read_json_record(path, 'EPL evidence', 'EPL_SOURCE_MISSING')
    require(set(r) == {'kind','data','recorded_at','record_hash'} and r['record_hash'] == identity
            and digest({k:v for k,v in r.items() if k != 'record_hash'}) == identity)
    timestamp(r['recorded_at'])
    return r


def _inventory(state, kind):
    directory = Path(state)/DIRECTORY
    require(not directory.is_symlink())
    if not directory.exists(): return []
    from itertools import islice
    paths = list(islice(directory.iterdir(), 2001))
    require(len(paths) <= 2000, 'EPL_CAPACITY_EXCEEDED')
    rows = [_get(state, p.stem) for p in paths]
    return [r for r in rows if r['kind'] == kind]


def _approval(raw, approved, at):
    require(type(raw) is bytes and len(raw) <= 4096, 'EPL_QUALIFICATION_REQUIRED')
    require(fingerprint(raw) in approved, 'EPL_QUALIFICATION_NOT_APPROVED')
    q = json.loads(raw)
    require(encoded(q) == raw and set(q) == {'version','competition','market','model_sha256',
        'evaluation_sha256','effective_at','expires_at','qualification_policy'}, 'EPL_QUALIFICATION_REJECTED')
    require(q['version'] == 1 and q['competition'] == 'E0' and q['market'] in ('BTTS','TEAM_TOTAL'))
    require(all(isinstance(q[k],str) and re.fullmatch('[0-9a-f]{64}',q[k])
                for k in ('model_sha256','evaluation_sha256')))
    require(timestamp(q['effective_at']) <= at < timestamp(q['expires_at']), 'EPL_QUALIFICATION_EXPIRED')
    from modelfc.published_recommendations import QUALIFICATION_POLICY
    require(q['qualification_policy'] == (published_btts.QUALIFICATION_POLICY if q['market']=='BTTS'
                                         else QUALIFICATION_POLICY), 'EPL_POLICY_REJECTED')
    return q


def freeze(state, calendar, *, approval_raw=None, approved_contracts=(), enabled=False, sources=(), **options):
    """Actual-clock creation before quotes. No caller-supplied/backdated as-of."""
    require(enabled is True, 'EPL_DISABLED')
    require('as_of' not in options, 'EPL_CALLER_CLOCK_REJECTED')
    started = now()
    q = _approval(approval_raw, approved_contracts, started)
    prepare = pl_btts.prepare if q['market']=='BTTS' else pl_corner_capture.prepare
    batch = prepare(calendar, sources=sources, enabled=True, as_of=started, **options)
    require(batch['state'] in ('RESEARCH_READY','CAPTURED','EMPTY'), 'EPL_INPUTS_NOT_READY')
    ids = []
    ensure_directory(Path(state), 'state')
    with ledger_lock(Path(state)) as lock:
        for f in batch['forecasts']:
            if q['market']=='BTTS':
                wire=f.wire(); e=wire['evidence']; model=e['implementation_sha256']; kickoff=timestamp(e['kickoff'])
            else:
                wire=f; model=fingerprint(encoded(f['implementation_sha256'])); kickoff=timestamp(f['identity']['kickoff'])
            at=now()
            require(model == q['model_sha256'], 'EPL_MODEL_NOT_QUALIFIED')
            require(started <= at < kickoff and at < timestamp(q['expires_at']), 'EPL_FREEZE_WINDOW_CLOSED')
            # Underlying prepare computes in memory; this actual creation receipt is
            # distinct from pl_corner_capture's historical OFFLINE_REPLAY store.
            ids.append(_put(state, 'forecast', dict(origin='PROSPECTIVE_SAVED_INPUTS',
                qualification=q, qualification_sha256=fingerprint(approval_raw), forecast=wire), at, lock,
                deadline=min(kickoff,timestamp(q['expires_at']))))
    return ids


def _frozen(state, identity):
    record=_get(state,identity); d=record['data']
    require(record['kind']=='forecast' and d['origin']=='PROSPECTIVE_SAVED_INPUTS')
    q=d['qualification']; _approval(encoded(q),(d['qualification_sha256'],),timestamp(record['recorded_at']))
    f=d['forecast']
    if q['market']=='BTTS':
        old=pl_btts.FrozenPLBtts(encoded(f['evidence']),GoalAggregates(**f['aggregates']))
        require(encoded(old.wire())==encoded(f))
        e=f['evidence']; model=e['implementation_sha256']
        identity=dict(competition='E0',provider='football-data.org',provider_fixture_id=e['fixture_id'],
            home_team=e['home'],away_team=e['away'],kickoff_utc=e['kickoff'])
        frozen=timestamp(e['frozen_at'])
    else:
        _,frozen,kickoff,names=pl_corner_odds._forecast(encoded(f),fingerprint(encoded(f)))
        i=f['identity']; model=fingerprint(encoded(f['implementation_sha256']))
        identity=dict(competition='E0',provider='football-data.org',provider_fixture_id=str(i['fixture_id']),
            home_team=names[0],away_team=names[1],kickoff_utc=i['kickoff'])
    require(model==q['model_sha256'] and frozen <= timestamp(record['recorded_at']) < timestamp(identity['kickoff_utc']))
    return record, identity


def _consume(record, saved, binding, at, team_side=None, line=None):
    d=record['data']; f=d['forecast']
    require(fingerprint(saved.payload)==saved.payload_sha256 and fingerprint(saved.metadata)==saved.metadata_sha256,
            'EPL_QUOTE_PIN_REJECTED')
    require(timestamp(record['recorded_at']) < saved.retrieved_at <= at, 'EPL_QUOTE_BEFORE_FREEZE')
    if d['qualification']['market']=='BTTS':
        old=pl_btts.FrozenPLBtts(encoded(f['evidence']),GoalAggregates(**f['aggregates']))
        return json.loads(pl_btts.consume(old,saved.payload,saved.metadata,retrieved_at=saved.retrieved_at,
            as_of=at,binding=binding,binding_sha256=fingerprint(binding)))
    return json.loads(pl_corner_odds.consume(encoded(f),saved,forecast_sha256=fingerprint(encoded(f)),
        binding_raw=binding,binding_sha256=fingerprint(binding),as_of=at,team_side=team_side,line=line,enabled=True))


def observe(state, forecast_id, saved, binding, *, team_side=None, line=None, enabled=False):
    require(enabled is True, 'EPL_DISABLED')
    with ledger_lock(Path(state)) as lock:
        record, identity=_frozen(state,forecast_id); at=now()
        try:
            _consume(record,saved,binding,at,team_side,line)
        except ValueError:
            pass  # Preserve failed latest observation; selection must not resurrect an older quote.
        # Persist exact raw bytes and original receipts; reads revalidate at their
        # historical time, never upgrade a missing or stale latest observation.
        data=dict(forecast_id=forecast_id,payload=saved.payload.decode(),metadata=saved.metadata.decode(),
            payload_sha256=saved.payload_sha256,metadata_sha256=saved.metadata_sha256,
            retrieved_at=saved.retrieved_at.isoformat(),binding=binding.decode(),team_side=team_side,line=line)
        require(at <= now() < timestamp(identity['kickoff_utc']), 'EPL_QUOTE_WINDOW_CLOSED')
        return _put(state,'observation',data,at,lock,deadline=timestamp(identity['kickoff_utc']))


def _offers(state, observation, at):
    d=observation['data']; r,identity=_frozen(state,d['forecast_id']); q=r['data']['qualification']
    require(timestamp(observation['recorded_at']) <= at < timestamp(identity['kickoff_utc']))
    _approval(encoded(q),(r['data']['qualification_sha256'],),at)
    s=SavedQuotes(d['payload'].encode(),d['payload_sha256'],d['metadata'].encode(),d['metadata_sha256'],timestamp(d['retrieved_at']))
    result=_consume(r,s,d['binding'].encode(),at,d['team_side'],d['line'])
    quotes=result['quotes']; implied={v['outcome'].upper():1/v['decimal_odds'] for v in quotes}; total=sum(implied.values())
    from modelfc.published_recommendations import OFFER_FIELDS
    for quote in quotes:
        side=quote['outcome'].upper(); american=int(quote['american_odds']); profit,break_even=american_odds_terms(american)
        if q['market']=='BTTS':
            probability=r['data']['forecast']['probabilities'][0 if side=='YES' else 1]
            offer=dict(**identity,market_type='BTTS',side=side,forecast_id=d['forecast_id'],comparison_id=observation['record_hash'],
                observation_id=observation['record_hash'],bookmaker='fanduel',american_odds=american,
                decimal_odds=quote['decimal_odds'],model_probability=probability,sportsbook_implied_probability=break_even,
                no_vig_market_probability=implied[side]/total,no_vig_probability_edge=probability-implied[side]/total,
                expected_profit=probability*profit-(1-probability),retrieved_at_utc=d['retrieved_at'],
                availability_checked_at_utc=at.isoformat(),observation_age_seconds=(at-s.retrieved_at).total_seconds(),
                policy_version=q['qualification_policy']['version'])
            if not published_btts.eligible(offer): continue
        else:
            p=CornerLineProbability(**result['probabilities']); value=price_corner_market(p,side,american)
            decision=qualification_decision_for_policy(dict(direction=side,american_odds=american),implied,total,
                value.decisive_model_probability,q['qualification_policy'])
            if not decision['qualified'] or value.expected_profit<=0: continue
            offer=dict(**identity,prediction_id=d['forecast_id'],target_id=digest([d['forecast_id'],d['team_side'],d['line'],side]),
                market_type='TEAM_TOTAL',team_side=d['team_side'],team=identity['home_team' if d['team_side']=='HOME' else 'away_team'],
                direction=side,line=d['line'],bookmaker='fanduel',american_odds=american,decimal_odds=quote['decimal_odds'],
                retrieved_at_utc=d['retrieved_at'],availability_checked_at_utc=at.isoformat(),
                model_probability=value.model_probability,push_probability=value.push_probability,
                decisive_model_probability=value.decisive_model_probability,sportsbook_implied_probability=value.implied_probability,
                no_vig_market_probability=decision['no_vig_market_probability'],no_vig_probability_edge=decision['no_vig_probability_edge'],
                expected_profit=value.expected_profit,qualified=True,policy_version=q['qualification_policy']['version'],
                observation_age_seconds=(at-s.retrieved_at).total_seconds(),opportunity_id=observation['record_hash'])
            require(set(offer)==set(OFFER_FIELDS))
        yield offer,r['recorded_at'],dict(forecast_id=d['forecast_id'],observation_id=observation['record_hash'],
                                        qualification_sha256=r['data']['qualification_sha256'])


def select(state, at, *, approved_contracts=(), strategy="best_value", diagnostics=None):
    """Newest captured cohort then latest observation; no best-model cherry-pick."""
    if not approved_contracts: return {}
    # A newer eligible frozen cohort without quotes suppresses older prices too.
    def diagnostic(record, identity, reason, reference=None):
        if diagnostics is not None:
            diagnostics.identity(identity, reason, at,
                market='BTTS' if record['data']['qualification']['market']=='BTTS' else 'CORNER_TEAM_TOTAL',
                strategy=strategy, reference_id=reference or record['record_hash'],
                evidence_hash=record['record_hash'])
    forecasts={}
    for raw in _inventory(state,'forecast'):
        if timestamp(raw['recorded_at'])>at or raw['data']['qualification_sha256'] not in approved_contracts: continue
        r,i=_frozen(state,raw['record_hash'])
        key=tuple(i.values())+(r['data']['qualification']['market'],)
        order=(timestamp(r['recorded_at']),r['record_hash'])
        if key not in forecasts or order>forecasts[key][0]: forecasts[key]=(order,r['record_hash'])
    for _, forecast_id in forecasts.values():
        record, identity = _frozen(state, forecast_id)
        diagnostic(record, identity, 'FORECAST_REUSED')
    latest={}
    for o in _inventory(state,'observation'):
        r,i=_frozen(state,o['data']['forecast_id'])
        key=tuple(i.values())+(r['data']['qualification']['market'],)
        if timestamp(o['recorded_at'])>at or forecasts.get(key,(None,None))[1]!=r['record_hash']: continue
        key+=(o['data']['team_side'],)
        order=(timestamp(o['recorded_at']),o['record_hash'])
        if key not in latest or order>latest[key][0]:latest[key]=(order,o)
    from modelfc.published_recommendations import _identity
    candidates=[]
    observed = {o['data']['forecast_id'] for _, o in latest.values()}
    for _, forecast_id in forecasts.values():
        if forecast_id not in observed:
            record, identity = _frozen(state, forecast_id)
            diagnostic(record, identity, 'EPL_QUOTES_NOT_OBSERVED')
    for _,o in latest.values():
        record, identity = _frozen(state, o['data']['forecast_id'])
        before = len(candidates)
        try:
            candidates.extend(_offers(state,o,at))
        except (ValueError,TypeError,KeyError) as exc:
            from modelfc.fixture_diagnostics import epl_quote_reason
            diagnostic(record, identity, epl_quote_reason(exc), o['record_hash'])
            continue  # never fall back to older evidence
        diagnostic(record, identity, 'ODDS_OBSERVED', o['record_hash'])
        diagnostic(record, identity, 'BASE_VALUE_QUALIFIED' if len(candidates)>before
                   else 'NO_QUALIFYING_VALUE', o['record_hash'])
    for offer, _, evidence in candidates:
        record, identity = _frozen(state, evidence['forecast_id'])
        reason = ('STRATEGY_PROBABILITY_THRESHOLD' if strategy == 'higher_probability'
                  and offer['model_probability'] < .65 else 'VALUE_QUALIFIED')
        diagnostic(record, identity, reason, evidence['observation_id'])
    if strategy == "higher_probability":
        candidates=[row for row in candidates if row[0]['model_probability']>=.65]
    ranked=published_btts.rank_candidates([(v,t) for v,t,_ in candidates if v['market_type']=='BTTS'])
    from modelfc.corner_market_intelligence import rank_supported_offers
    corners=[dict(v,status='SUPPORTED',observation_id=v['opportunity_id'],selection_id=v['target_id'])
             for v,t,_ in candidates if v['market_type']=='TEAM_TOTAL']
    times={v['target_id']:t for v,t,_ in candidates if v['market_type']=='TEAM_TOTAL'}
    for value in rank_supported_offers(corners):
        v={k:w for k,w in value.items() if k not in ('status','observation_id','selection_id')}
        ranked.setdefault(_identity(v),(v,times[v['target_id']]))
    evidence={_identity(v):e for v,t,e in candidates if ranked.get(_identity(v))==(v,t)}
    return {key:(offer,created,evidence[key]) for key,(offer,created) in ranked.items()}


def validate_publication(state, record):
    e=record['epl_evidence']; o=_get(state,e['observation_id'])
    f,_=_frozen(state,e['forecast_id'])
    _approval(encoded(f['data']['qualification']),(e['qualification_sha256'],),timestamp(record['published_at_utc']))
    require(o['kind']=='observation' and o['data']['forecast_id']==e['forecast_id'])
    published=timestamp(record['published_at_utc']); checked=timestamp(record['offer']['availability_checked_at_utc'])
    require(checked <= published and published-timestamp(record['offer']['retrieved_at_utc']) <= timedelta(minutes=15),
            'EPL_PUBLICATION_STALE')
    matches=list(_offers(state,o,checked))
    # Publication may sample the clock after selection; keep its actual quote age.
    for v,_,_ in matches:
        v['observation_age_seconds']=(timestamp(record['published_at_utc'])-timestamp(v['retrieved_at_utc'])).total_seconds()
    from modelfc.published_recommendations import _identity
    chosen=select(state,checked,approved_contracts=(e['qualification_sha256'],),
                  strategy=record.get('strategy','best_value')).get(_identity(record['offer']))
    require(chosen is not None and chosen[2]==e, 'EPL_SELECTION_CONFLICT')
    chosen[0]['observation_age_seconds']=record['offer']['observation_age_seconds']
    require(chosen[0]==record['offer'], 'EPL_SELECTION_CONFLICT')
    require(any(v==record['offer'] and t==record['forecast_created_at_utc'] and evidence==e for v,t,evidence in matches),
            'EPL_PUBLICATION_SOURCE_CONFLICT')


def publication_window(state, record, at):
    """Recheck approval and the original quote/change deadlines at atomic link."""
    observation=_get(state,record['epl_evidence']['observation_id'])
    require(any(e==record['epl_evidence'] for _,_,e in _offers(state,observation,at)),
            'EPL_PUBLICATION_WINDOW_CLOSED')


def _result_source(raw, pin, identity, observed):
    require(type(raw) is bytes and 0 < len(raw) <= 65536 and fingerprint(raw)==pin, 'EPL_RESULT_PIN_REJECTED')
    value=json.loads(raw)
    require(encoded(value)==raw and set(value)=={'fixture','status','period','completed_at','retrieved_at',
        'home_goals','away_goals','home_corners','away_corners','provider','source_sha256'}, 'EPL_RESULT_REJECTED')
    require(value['fixture']==identity and value['status']=='FINISHED' and value['period']=='REGULATION',
            'EPL_RESULT_IDENTITY_OR_STATUS_REJECTED')
    require(isinstance(value['provider'],str) and 0<len(value['provider'])<=120
            and isinstance(value['source_sha256'],str) and re.fullmatch('[0-9a-f]{64}',value['source_sha256']))
    require(timestamp(identity['kickoff_utc']) < timestamp(value['completed_at'])
            <= timestamp(value['retrieved_at']) <= observed, 'EPL_RESULT_CHRONOLOGY_REJECTED')
    for statistic in ('goals','corners'):
        pair=[value[side+'_'+statistic] for side in ('home','away')]
        require(all(v is None for v in pair) or all(type(v) is int and 0<=v<=1000 for v in pair),
                'EPL_RESULT_STATISTICS_REJECTED')
    return value


def result_history(state, identity, at):
    rows=[r for r in _inventory(state,'result') if r['data']['fixture']==identity]
    ordered=[]; parent=None; previous=None
    while rows:
        children=[r for r in rows if r['data']['supersedes']==parent]
        require(len(children)==1, 'EPL_RESULT_CHAIN_REJECTED')
        r=children[0];d=r['data'];when=timestamp(r['recorded_at'])
        _result_source(encoded(d['source']),d['source_pin'],identity,when)
        require(previous is None or previous<=when, 'EPL_RESULT_CHAIN_CHRONOLOGY')
        require(parent is None and d['reason'] is None or parent is not None
                and isinstance(d['reason'],str) and 0<len(d['reason'].strip())<=2000, 'EPL_CORRECTION_REASON_REQUIRED')
        ordered.append(r);rows.remove(r);parent=r['record_hash'];previous=when
    return [r for r in ordered if timestamp(r['recorded_at'])<=at]


def record_result(state, forecast_id, raw, *, result_sha256, supersedes=None, reason=None, enabled=False):
    """Append original or explicitly reviewed correction; never replace originals.

    Source is a pinned provider-independent regulation-final receipt. Its source
    hash binds externally retained original provider evidence; no provider is fetched.
    Missing statistics remain pending. A correction must name the exact current tip.
    """
    require(enabled is True, 'EPL_DISABLED')
    with ledger_lock(Path(state)) as lock:
        _,identity=_frozen(state,forecast_id);at=now()
        source=_result_source(raw,result_sha256,identity,at)
        history=result_history(state,identity,at)
        if history and history[-1]['data']['source_pin']==result_sha256:
            require(supersedes in (None,history[-1]['data']['supersedes']), 'EPL_RESULT_CONFLICT')
            from modelfc.pl_public_evidence import project_result
            project_result(state, history[-1], lock)
            return history[-1]['record_hash']
        require((not history and supersedes is None and reason is None) or
                (history and supersedes==history[-1]['record_hash'] and isinstance(reason,str)
                 and 0<len(reason.strip())<=2000), 'EPL_RESULT_CONFLICT')
        data=dict(fixture=identity,source=source,source_pin=result_sha256,supersedes=supersedes,reason=reason)
        payload=dict(kind='result',data=data,recorded_at=at.isoformat())
        row={**payload,'record_hash':digest(payload)}
        from modelfc.pl_public_evidence import result_intent, project_result, records
        # A pending intent must be explicitly recovered, never silently superseded.
        public=records(state)
        committed={r['data']['source_record_hash'] for r in public if r['kind']=='result'}
        require(not any(r['kind']=='result-intent' and r['data']['fixture_sha256']==digest(identity)
                        and r['data']['source_record_hash'] not in committed for r in public), 'EPL_RESULT_RECOVERY_REQUIRED')
        result_intent(state,row,lock)
        saved=_put(state,'result',data,at,lock)
        project_result(state,_get(state,saved),lock)
        return saved


def resume_result(state, intent_id, forecast_id, raw, *, result_sha256, supersedes=None, reason=None, enabled=False):
    """Explicit exact-intent recovery after an uncertain write; no automatic retry.

    Retains the original observation time; public knowledge starts at projection.
    Supplied bytes and correction fields must reproduce the expected private hash.
    """
    require(enabled is True, 'EPL_DISABLED')
    from modelfc.pl_public_evidence import records, project_result
    with ledger_lock(Path(state)) as lock:
        entries=[r for r in records(state) if r['record_hash']==intent_id and r['kind']=='result-intent']
        require(len(entries)==1, 'EPL_RESULT_INTENT_REQUIRED')
        intent=entries[0]['data'];_,identity=_frozen(state,forecast_id)
        observed=timestamp(intent['recorded_at'])
        require(observed<=now() and intent['fixture_sha256']==digest(identity))
        source=_result_source(raw,result_sha256,identity,observed)
        data=dict(fixture=identity,source=source,source_pin=result_sha256,supersedes=supersedes,reason=reason)
        expected=digest(dict(kind='result',data=data,recorded_at=observed.isoformat()))
        require(expected==intent['source_record_hash'] and supersedes==intent['supersedes'], 'EPL_RESULT_RECOVERY_CONFLICT')
        history=result_history(state,identity,now())
        require((history and history[-1]['record_hash'] in (expected,supersedes))
                or (not history and supersedes is None), 'EPL_RESULT_RECOVERY_CONFLICT')
        saved=_put(state,'result',data,observed,lock)
        project_result(state,_get(state,saved),lock)
        return saved



def publication_result(state, record, at):
    _,identity=_frozen(state,record['epl_evidence']['forecast_id'])
    history=result_history(state,identity,at)
    if not history:return 'PENDING',None
    return grade_result(history[-1]['data']['source'], record['offer'])


def grade_result(result, offer):
    """Shared grading for private evidence and sanitized writer attestations."""
    if offer['market_type']=='BTTS':
        if result['home_goals'] is None:return 'PENDING',None
        actual='YES' if result['home_goals']>0 and result['away_goals']>0 else 'NO'
        won=offer['side']==actual;push=False
    else:
        count=result[offer['team_side'].lower()+'_corners']
        if count is None:return 'PENDING',None
        push=count==offer['line'];won=count>offer['line'] if offer['direction']=='OVER' else count<offer['line']
    profit,_=american_odds_terms(offer['american_odds'])
    return ('PUSH',0.) if push else ('WIN',profit) if won else ('LOSS',-1.)
