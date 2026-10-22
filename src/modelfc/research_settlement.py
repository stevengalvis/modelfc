"""Opt-in, network-free research settlement after a validated CSV refresh.

Invoked after refresh success or failure, not an independent timer. Never holds a history
lock while taking a ledger lock. Existing market writers remain authoritative.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid

from modelfc.btts_market_data import timestamp
from modelfc.corner_analysis_outcomes import _require
from modelfc.corner_data import load_data_config
from modelfc.ledger_storage import existing_read_lock, write_new_record, git_commit_sha
from modelfc.odds_calendar import read, _sync_root
from modelfc.pinned_evidence import read_pinned
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.refresh_result_evidence import load, LIMIT
from modelfc import btts_outcomes, research_outcomes


def evidence(config, *, as_of=None):
    history = config.directory
    refresh = history / 'data/corner-refresh'
    # Copy and verify all evidence under the existing history publication lock.
    with existing_read_lock(refresh / 'refresh.lock'):
        as_of = as_of or datetime.now(timezone.utc)
        report = _json(read(refresh / 'status.json', 1024 * 1024))
        rows = report['results']
        _require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
            and {r['league'] for r in rows} == set(config.leagues)
            and len(rows) == len(config.leagues) and 'E1' in config.leagues,
            'REFRESH_NOT_SUCCESSFUL')
        row = next(r for r in rows if r['league'] == 'E1')
        reported = timestamp(report['checked_at'])
        attempted = row.get('checked_at', report['checked_at'])
        _require(isinstance(attempted, str), 'REFRESH_EVIDENCE_STALE')
        checked = timestamp(attempted)
        _require(checked <= reported <= as_of, 'REFRESH_EVIDENCE_STALE')
        _require(checked <= as_of and (as_of - checked).total_seconds() <= 86400,
                 'REFRESH_EVIDENCE_STALE')
        # Overall refresh health remains nonzero for other leagues. Only E1's
        # own successful publication may authorize this E1-only consumer.
        _require(row['status'] in ('updated', 'unchanged') and row['stale'] is False,
                 'REFRESH_NOT_SUCCESSFUL')
        if 'result_availability' in row or 'settlement_eligibility' in row:
            _require(row.get('settlement_eligibility') == 'VERIFIED_FINAL_SCORES',
                     'REFRESH_CALENDAR_CONFLICT_OR_UNVERIFIED')
        _require(isinstance(row.get('result_evidence'), dict) and row['result_evidence'].get('status') == 'RETAINED', 'REFRESH_EVIDENCE_UNAVAILABLE')
        pin = row['result_evidence']['sha256']
        metadata, raw = load(refresh, pin, as_of=as_of)
        retrieved = timestamp(metadata['retrieved_at_utc'])
        _require(retrieved <= checked and (checked - retrieved).total_seconds() <= 3600,
                 'REFRESH_EVIDENCE_STALE')
        _require(metadata['filename'] == f"E1_{report['season']}.csv"
                 and row['source'] == metadata['url'], 'INVALID_REFRESH_EVIDENCE')
        # A superseded/mutated publication must not be silently relabeled.
        _require(read_pinned(history / metadata['filename'], metadata['file_sha256'], LIMIT) == raw,
                 'INVALID_REFRESH_EVIDENCE')
    return metadata, raw, pin


def run(config, state, *, enabled=False, as_of=None):
    if not enabled:
        return {'status': 'DISABLED', 'provider_requests': 0}
    observed = as_of or datetime.now(timezone.utc)
    state = Path(state)
    root = state / 'research-settlement-runs'
    receipt = {'schema_version': 1, 'run_id': uuid.uuid4().hex, 'started_at_utc': observed.isoformat(),
               'git_commit': git_commit_sha(), 'status': 'REJECTED', 'provider_requests': 0}
    lock = None
    try:
        _require(state.is_dir() and not state.is_symlink(), 'INVALID_RESEARCH_STATE')
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'INVALID_RESEARCH_STATE')
        lock = os.open(root / '.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        _require(stat.S_ISREG(os.fstat(lock).st_mode), 'INVALID_RESEARCH_STATE')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'BUSY', 'provider_requests': 0}
        # A killed/timeout run leaves an explicit start without a terminal receipt.
        write_new_record(root / (receipt['run_id'] + '-started.json'), {**receipt, 'status': 'STARTED'})
        _sync_root(root); _sync_root(state)
        try:
            metadata, raw, pin = evidence(config, as_of=as_of)
            evaluated = as_of or datetime.now(timezone.utc)
            receipt['evaluated_at_utc'] = evaluated.isoformat()
            receipt['evidence_sha256'] = pin
            receipt['source'] = metadata
            receipt['eligibility_scope'] = 'E1_SUCCESSFUL_PUBLICATION'
            receipt.update(research_outcomes.sweep(state, lambda fixture:
                btts_outcomes.csv_bytes_evidence(raw, metadata['filename'], metadata['file_sha256'],
                    metadata['retrieved_at_utc'], fixture), as_of=evaluated,
                reconcile=True, include_bookmaker=True))
        except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
            reasons = {'REFRESH_CALENDAR_CONFLICT_OR_UNVERIFIED', 'REFRESH_EVIDENCE_STALE', 'REFRESH_NOT_SUCCESSFUL', 'REFRESH_EVIDENCE_UNAVAILABLE',
                       'INVALID_REFRESH_EVIDENCE', 'SAVED_RESULT_PROVENANCE_REJECTED'}
            receipt['reason'] = str(error) if str(error) in reasons else 'REFRESH_OR_SETTLEMENT_REJECTED'
        receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        write_new_record(root / (receipt['run_id'] + '.json'), receipt)
        _sync_root(root); _sync_root(state)
        return receipt
    except (ValueError, OSError, KeyError, TypeError):
        return {'status': 'REJECTED', 'reason': 'SETTLEMENT_RECEIPT_UNAVAILABLE', 'provider_requests': 0}
    finally:
        if lock is not None: os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--enabled', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = run(load_data_config(args.config), args.state_dir, enabled=args.enabled)
    except (ValueError, OSError):
        result = {'status': 'REJECTED', 'reason': 'SETTLEMENT_CONFIGURATION_REJECTED', 'provider_requests': 0}
    # Detailed evidence stays in the private receipt, not service logs.
    print(json.dumps({k: result[k] for k in ('status', 'reason', 'run_id', 'provider_requests') if k in result}, sort_keys=True))
    return 0 if result['status'] in ('DISABLED', 'COMPLETE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
