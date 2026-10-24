"""Allowlisted writer attestations for EPL reads without access to private inputs.

Hashes bind immutable local writer evidence, not provider authenticity. Only the
private validated writer creates these records. Readers never open raw captures.
Result intents make an interrupted projection fail closed rather than hide a tip.
"""
from datetime import timedelta
from pathlib import Path
import re

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.corner_analysis_store import _canonical_hash as digest
from modelfc.history_coverage import timestamp
from modelfc.ledger_storage import LedgerError, ensure_directory, write_new_record
from modelfc.research_predictions import _sync_directories
from modelfc.settlement_reviews import review_entries, load_record

DIRECTORY = 'epl-public-evidence'
POLICY = 'validated-epl-projection-v1'


def require(ok):
    if not ok:
        raise LedgerError('EPL_PUBLIC_EVIDENCE_REJECTED')


def hash_value(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate(row):
    require(set(row) == {'policy', 'kind', 'data', 'record_hash'} and row['policy'] == POLICY
            and row['record_hash'] == digest({k: v for k, v in row.items() if k != 'record_hash'}))
    d = row['data']
    require(isinstance(d, dict))
    if row['kind'] == 'publication':
        require(set(d) == {'publication_id', 'publication_hash', 'epl_evidence', 'verified_at', 'valid_until'})
        require(hash_value(d['publication_id']) and hash_value(d['publication_hash'])
                and set(d['epl_evidence']) == {'forecast_id', 'observation_id', 'qualification_sha256'}
                and all(hash_value(v) for v in d['epl_evidence'].values()))
        require(timestamp(d['verified_at']) < timestamp(d['valid_until']))
    elif row['kind'] == 'result-intent':
        require(set(d) == {'source_record_hash', 'fixture_sha256', 'supersedes', 'recorded_at'}
                and hash_value(d['source_record_hash']) and hash_value(d['fixture_sha256'])
                and (d['supersedes'] is None or hash_value(d['supersedes'])))
        timestamp(d['recorded_at'])
    elif row['kind'] == 'result':
        require(set(d) == {'source_record_hash', 'receipt_sha256', 'supersedes', 'reason_sha256', 'recorded_at', 'projected_at', 'source'})
        require(hash_value(d['source_record_hash']) and hash_value(d['receipt_sha256'])
                and (d['supersedes'] is None or hash_value(d['supersedes']))
                and (d['reason_sha256'] is None or hash_value(d['reason_sha256'])))
        require((d['supersedes'] is None) == (d['reason_sha256'] is None))
        require(timestamp(d['recorded_at']) <= timestamp(d['projected_at']))
        source = d['source']; fixture = source['fixture']
        require(set(fixture) == {'competition', 'provider', 'provider_fixture_id', 'home_team', 'away_team', 'kickoff_utc'}
                and fixture['competition'] == 'E0' and fixture['provider'] == 'football-data.org'
                and all(isinstance(fixture[k], str) and 0 < len(fixture[k]) <= 120
                        and all(c.isprintable() for c in fixture[k])
                        for k in ('provider_fixture_id', 'home_team', 'away_team'))
                and source['provider'] == 'validated-private-receipt')
        from modelfc.pl_product_source import _result_source
        _result_source(encoded(source), fingerprint(encoded(source)), fixture, timestamp(d['recorded_at']))
    else:
        require(False)
    return row


def records(state):
    try:
        paths = review_entries(state, DIRECTORY)
        rows = [validate(load_record(p, limit=65536)) for p in paths]
        # Every filename must bind its content; a renamed or duplicated tip fails.
        for p, row in zip(paths, rows):
            require(p.stem == row['record_hash'])
        return rows
    except (KeyError, TypeError, ValueError, OSError):
        raise LedgerError('EPL_PUBLIC_EVIDENCE_REJECTED') from None


def put(state, kind, data, lock):
    payload = dict(policy=POLICY, kind=kind, data=data)
    row = validate({**payload, 'record_hash': digest(payload)})
    require(len(encoded(row)) <= 65536)
    directory = Path(state) / DIRECTORY
    ensure_directory(directory, 'EPL public projection')
    existing = records(state)
    path = directory / (row['record_hash'] + '.json')
    if path.exists():
        require(validate(load_record(path, limit=65536)) == row)
    else:
        require(len(existing) < 2000)
        write_new_record(path, row, evidence_state=Path(state), evidence_lock_fd=lock)
    _sync_directories(directory, Path(state))
    return row['record_hash']


def attest_publication(state, record, at, lock):
    from modelfc.pl_product_source import validate_publication, publication_window, _frozen
    validate_publication(state, record)
    publication_window(state, record, at)
    frozen, _ = _frozen(state, record['epl_evidence']['forecast_id'])
    until = min(timestamp(frozen['data']['qualification']['expires_at']),
                timestamp(record['offer']['kickoff_utc']),
                timestamp(record['offer']['retrieved_at_utc']) + timedelta(minutes=15))
    put(state, 'publication', dict(publication_id=record['publication_id'], publication_hash=record['record_hash'],
        epl_evidence=record['epl_evidence'], verified_at=at.isoformat(), valid_until=until.isoformat()), lock)


def result_intent(state, row, lock):
    return put(state, 'result-intent', dict(source_record_hash=row['record_hash'],
        fixture_sha256=digest(row['data']['fixture']), supersedes=row['data']['supersedes'],
        recorded_at=row['recorded_at']), lock)


def project_result(state, row, lock):
    # Called only after the private writer has validated and durably saved this row.
    from modelfc.pl_product_source import _get, _result_source
    require(_get(state, row['record_hash']) == row and row['kind'] == 'result')
    d = row['data']; source = d['source']
    _result_source(encoded(source), d['source_pin'], d['fixture'], timestamp(row['recorded_at']))
    # No arbitrary provider label, correction reason, URL, path, payload or metadata.
    safe_source = {k: source[k] for k in ('fixture', 'status', 'period', 'completed_at', 'retrieved_at',
                                          'home_goals', 'away_goals', 'home_corners', 'away_corners', 'source_sha256')}
    safe_source['provider'] = 'validated-private-receipt'
    data = dict(source_record_hash=row['record_hash'], receipt_sha256=d['source_pin'],
        supersedes=d['supersedes'], reason_sha256=fingerprint(d['reason'].encode()) if d['reason'] else None,
        recorded_at=row['recorded_at'], source=safe_source)
    existing = [r for r in records(state) if r['kind'] == 'result'
                and r['data']['source_record_hash'] == row['record_hash']]
    if existing:
        require(len(existing) == 1 and {k:v for k,v in existing[0]['data'].items() if k!='projected_at'} == data)
        put(state, 'result', existing[0]['data'], lock)
        return
    from modelfc.pl_product_source import now
    put(state, 'result', {**data, 'projected_at': now().isoformat()}, lock)


def read_result(state, record, at):
    rows = records(state)
    certificates = [r['data'] for r in rows if r['kind'] == 'publication'
                    and r['data']['publication_hash'] == record['record_hash']]
    require(len(certificates) == 1)
    c = certificates[0]; o = record['offer']
    require(c['publication_id'] == record['publication_id'] and c['epl_evidence'] == record['epl_evidence']
            and timestamp(record['published_at_utc']) <= timestamp(c['verified_at']) < timestamp(c['valid_until'])
            <= min(timestamp(o['kickoff_utc']), timestamp(o['retrieved_at_utc']) + timedelta(minutes=15)))
    fixture = {k: o[k] for k in ('competition', 'provider', 'provider_fixture_id', 'home_team', 'away_team', 'kickoff_utc')}
    intents = [r['data'] for r in rows if r['kind'] == 'result-intent'
               and r['data']['fixture_sha256'] == digest(fixture)]
    pending = [r['data'] for r in rows if r['kind'] == 'result' and r['data']['source']['fixture'] == fixture]
    require(len(intents) == len(pending)
            and {r['source_record_hash'] for r in intents} == {r['source_record_hash'] for r in pending})
    for result in pending:
        matching = [i for i in intents if i['source_record_hash'] == result['source_record_hash']]
        require(len(matching) == 1 and matching[0]['supersedes'] == result['supersedes']
                and matching[0]['recorded_at'] == result['recorded_at'])
    parent = None; previous = None; projected = None; effective = None
    while pending:
        children = [r for r in pending if r['supersedes'] == parent]
        require(len(children) == 1)
        child = children[0]; recorded = timestamp(child['recorded_at'])
        require(previous is None or previous <= recorded)
        require(projected is None or projected <= timestamp(child['projected_at']))
        projected = timestamp(child['projected_at'])
        if recorded <= at and projected <= at:
            effective = child['source']
        previous = recorded; parent = child['source_record_hash']; pending.remove(child)
    if effective is None:
        return 'PENDING', None
    from modelfc.pl_product_source import grade_result
    return grade_result(effective, o)
