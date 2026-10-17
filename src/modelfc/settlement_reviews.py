"""Explicit offline simulation-void reviews. Never called by a collector or GET.

Retained full E1 calendar bytes are validated again on read. A reviewed source
hash identifies bytes, not publisher authenticity; the operator must establish
that authenticity before invoking the writer. No sportsbook grading is inferred.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from modelfc.corner_analysis_outcomes import _require, OutcomeError
from modelfc.btts_market_data import timestamp
from modelfc.btts_research import digest
from modelfc.ledger_storage import ensure_directory, ledger_lock, write_new_record, utc_timestamp
from modelfc.pinned_evidence import read_pinned
from modelfc.research_predictions import _sync_directories


def directory(state, name):
    state = Path(state)
    path = state / name
    try:
        _require(stat.S_ISDIR(state.lstat().st_mode), 'INVALID_REVIEW_DIRECTORY')
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        _require(stat.S_ISDIR(info.st_mode), 'INVALID_REVIEW_DIRECTORY')
        return path
    except OSError:
        raise OutcomeError('REVIEW_STORAGE_UNAVAILABLE') from None


def review_entries(state, name, limit=2000):
    """Only ENOENT means absent: pathlib glob suppresses directory read errors."""
    path = directory(state, name)
    try:
        with os.scandir(path) as entries:
            result = []
            for entry in entries:
                _require(len(result) < limit, 'CORRECTION_CAPACITY_EXCEEDED')
                _require(re.fullmatch(r'[0-9a-f]{64}\.json', entry.name)
                         and entry.is_file(follow_symlinks=False), 'INVALID_REVIEW')
                result.append(path / entry.name)
            return sorted(result)
    except FileNotFoundError:
        return []
    except OSError:
        raise OutcomeError('REVIEW_STORAGE_UNAVAILABLE') from None


def load_record(path, limit=4_000_000):
    """Bounded regular-file read; malformed history fails closed."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                     and 0 < info.st_size <= limit, 'INVALID_REVIEW')
            raw = stream.read(limit + 1)
        from modelfc.providers.oddspapi_saved_response import _json
        row = _json(raw)
        _require(isinstance(row, dict) and len(raw) <= limit
                 and row['record_hash'] == digest({k:v for k,v in row.items() if k != 'record_hash'}),
                 'INVALID_REVIEW')
        return row
    except (OSError, KeyError, ValueError, TypeError):
        raise OutcomeError('INVALID_REVIEW') from None


def cancellation(raw, sha256, offer, as_of):
    from modelfc.history_completeness import parse_calendar
    _require(hashlib.sha256(raw).hexdigest() == sha256, 'CANCELLATION_HASH_MISMATCH')
    bundle = json.loads(raw)
    retrieved = timestamp(bundle['retrieved_at'])
    calendar = parse_calendar(raw, as_of=retrieved)
    _require(offer['competition'] == 'E1' and retrieved <= as_of, 'CANCELLATION_IDENTITY_MISMATCH')
    matches = [m for m in calendar.matches if (m.home, m.away, m.kickoff) ==
               (offer['home_team'], offer['away_team'], timestamp(offer['kickoff_utc']))]
    _require(len(matches) == 1, 'CANCELLATION_IDENTITY_MISMATCH')
    match = matches[0]
    _require(offer['provider'] != 'football-data.org' or str(match.fixture_id) == offer['provider_fixture_id'],
             'CANCELLATION_IDENTITY_MISMATCH')
    _require(match.status == 'CANCELLED' and match.goals is None, 'CANCELLATION_NOT_CONFIRMED')
    return {'provider': 'football-data.org', 'fixture_id': match.fixture_id,
            'retrieved_at_utc': retrieved.isoformat(), 'source_sha256': calendar.source_sha256,
            'bundle_sha256': sha256, 'status': 'CANCELLED'}


def validate_void(record, pick, namespace):
    _require(set(record) == {'schema_version','policy','publication_id','publication_hash','namespace',
        'reason','recorded_at_utc','calendar_bytes','calendar_sha256','evidence','record_hash'}, 'INVALID_SIMULATION_VOID')
    _require(record['schema_version'] == 1 and record['policy'] == 'reviewed-simulation-void-v1'
        and record['publication_id'] == pick['publication_id'] and record['publication_hash'] == pick['record_hash']
        and record['namespace'] == namespace and isinstance(record['reason'],str)
        and 0 < len(record['reason'].strip()) <= 2000, 'INVALID_SIMULATION_VOID')
    recorded = timestamp(record['recorded_at_utc'])
    _require(timestamp(pick['published_at_utc']) <= recorded <= datetime.now(timezone.utc), 'INVALID_SIMULATION_VOID')
    evidence = cancellation(record['calendar_bytes'].encode(), record['calendar_sha256'], pick['offer'], recorded)
    _require(record['evidence'] == evidence and timestamp(evidence['retrieved_at_utc']) >= timestamp(pick['published_at_utc']),
             'INVALID_SIMULATION_VOID')
    return {k:record[k] for k in ('policy','record_hash','reason','recorded_at_utc','evidence')}


def apply_voids(state, rows, namespace):
    path = directory(state, 'simulation-voids')
    for row in rows:
        row["simulation_void"] = None
        key = digest([namespace, row['publication_id']])
        target = path / (key + '.json')
        try:
            target.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise OutcomeError("REVIEW_STORAGE_UNAVAILABLE") from None
        else:
            record = load_record(target)
            try:
                review = validate_void(record, row, namespace)
            except (ValueError, KeyError, TypeError, AttributeError):
                raise OutcomeError("INVALID_SIMULATION_VOID") from None
            row.update(result='SIMULATION_VOID', profit_units=0.0, simulation_void=review)
    return rows


def review_void(state_dir, *, namespace, publication_id, calendar_file, calendar_sha256, reason):
    from modelfc import published_recommendations as published
    _require(namespace in {'published-recommendations','strategy-picks'}
             and isinstance(publication_id,str) and re.fullmatch('[0-9a-f]{64}',publication_id)
             and isinstance(reason,str) and 0 < len(reason.strip()) <= 2000, 'INVALID_VOID_REVIEW')
    # Full source eligibility validation before the write lock; originals are immutable.
    report = published.read(state_dir, directory_name=namespace)
    picks = [r for r in report['publications'] if r['publication_id'] == publication_id]
    _require(len(picks) == 1, 'UNKNOWN_PUBLISHED_RECOMMENDATION')
    pick = picks[0]
    raw = read_pinned(calendar_file, calendar_sha256, 2_000_000)
    state = Path(state_dir)
    with ledger_lock(state):
        originals = published._records(state, directory_name=namespace)
        _require(any(r['record_hash'] == pick['record_hash'] for r in originals), 'PUBLICATION_CHANGED')
        path = directory(state, 'simulation-voids')
        target = path / (digest([namespace,publication_id]) + '.json')
        if target.exists():
            old = load_record(target)
            validate_void(old,pick,namespace)
            _require(old['calendar_sha256'] == calendar_sha256 and old['reason'] == reason.strip(), 'VOID_REVIEW_CONFLICT')
            _sync_directories(path,state)
            return old, False
        recorded = utc_timestamp()
        evidence = cancellation(raw,calendar_sha256,pick['offer'],timestamp(recorded))
        _require(timestamp(pick['published_at_utc']) <= timestamp(evidence['retrieved_at_utc']) <= timestamp(recorded),
                 'INVALID_VOID_REVIEW')
        row = dict(schema_version=1,policy='reviewed-simulation-void-v1',publication_id=publication_id,
            publication_hash=pick['record_hash'],namespace=namespace,reason=reason.strip(),recorded_at_utc=recorded,
            calendar_bytes=raw.decode('utf-8'),calendar_sha256=calendar_sha256,evidence=evidence)
        row['record_hash'] = digest(row)
        _require(len(json.dumps(row,indent=2,sort_keys=True).encode()) < 4_000_000,'REVIEW_CAPACITY_EXCEEDED')
        ensure_directory(path,'simulation void reviews')
        # No ACL/security configuration changes. Installed-reader access is a separate rollout prerequisite.
        write_new_record(target,row)
        _sync_directories(path,state)
        return row, True


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('state-dir','namespace','publication-id','calendar-file','calendar-sha256','reason'):
        parser.add_argument('--'+key,required=True)
    args=vars(parser.parse_args(argv))
    try:
        state=args.pop('state_dir'); row,created=review_void(state,**args)
        print(json.dumps({'status':'RECORDED' if created else 'REUSED','review_id':row['record_hash'],'provider_requests':0}))
        return 0
    except (OutcomeError,ValueError,KeyError,TypeError,OSError):
        print(json.dumps({'status':'REJECTED','reason':'VOID_REVIEW_REJECTED','provider_requests':0}))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
