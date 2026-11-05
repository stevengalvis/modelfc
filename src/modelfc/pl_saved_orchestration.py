"""Disabled saved-input EPL stages using the Championship writer/publication path.

No acquisition, scheduler, CLI, environment activation or production caller.
An interrupted operation requires inspection; this coordinator never retries it.
"""
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
import re
import json
import os
import stat

from modelfc import pl_product_source as source
from modelfc.acquisition_storage import encoded, fingerprint, directory as checked_directory
from modelfc.corner_prospective import _lock
from modelfc.ledger_storage import ensure_directory, read_json_record, write_new_record
from modelfc.research_predictions import _sync_directories

DIRECTORY = 'epl-saved-operations'


@dataclass(frozen=True)
class Configuration:
    enabled: bool = False
    contracts: tuple[bytes, ...] = ()
    approved_contracts: tuple[str, ...] = ()


def _wire(value):
    # Type-tagged input fingerprint only; original bytes stay in the source store.
    if type(value) is bytes:
        return ['bytes', fingerprint(value)]
    if isinstance(value, (datetime, date)):
        return [type(value).__name__, value.isoformat()]
    if is_dataclass(value):
        return [type(value).__name__, {f.name: _wire(getattr(value, f.name)) for f in fields(value)}]
    if type(value) is dict:
        source.require(all(type(k) is str for k in value), 'EPL_OPERATION_INVALID')
        return ['dict', {k: _wire(v) for k, v in value.items()}]
    if type(value) in (tuple, list):
        return [type(value).__name__, [_wire(v) for v in value]]
    source.require(value is None or type(value) in (str, int, float, bool), 'EPL_OPERATION_INVALID')
    return [type(value).__name__, value]


def _configuration(config, at):
    source.require(type(config) is Configuration and config.enabled is True, 'EPL_DISABLED')
    source.require(type(config.contracts) is tuple and 1 <= len(config.contracts) <= 2
                   and type(config.approved_contracts) is tuple
                   and len(config.approved_contracts) == len(config.contracts), 'EPL_CONFIGURATION_REQUIRED')
    pins = tuple(fingerprint(raw) for raw in config.contracts)
    source.require(len(set(pins)) == len(pins) and set(pins) == set(config.approved_contracts),
                   'EPL_QUALIFICATION_NOT_APPROVED')
    contracts = [source._approval(raw, config.approved_contracts, at) for raw in config.contracts]
    source.require(len({q['market'] for q in contracts}) == len(contracts), 'EPL_CONFIGURATION_CONFLICT')
    return pins


def _read(path):
    row = read_json_record(path, 'EPL operation', 'EPL_OPERATION_MISSING')
    source.require(set(row) == {'data', 'sha256'} and fingerprint(encoded(row['data'])) == row['sha256'],
                   'EPL_OPERATION_CORRUPT')
    return row['data']


def _save(directory, name, data):
    write_new_record(directory/name, dict(data=data, sha256=fingerprint(encoded(data))))
    _sync_directories(directory, directory.parent)


def _directory(path):
    # Reuse the existing no-symlink, ownership and writable-ancestor guard.
    os.close(checked_directory(path if path.exists() else path.parent))
    source.require(not path.is_symlink(), 'EPL_OPERATION_STORE_REJECTED')
    ensure_directory(path, 'EPL state')
    os.close(checked_directory(path))


def run_once(state_dir, *, config=Configuration(), operation_id, stage, inputs=None):
    """One explicitly supplied stage; zero provider requests, actual source clocks.

    Stages: freeze(calendar, approval_raw, sources, **existing_prepare_options),
    observe(forecast_id, saved, binding, team_side?, line?),
    result(forecast_id, raw, result_sha256, supersedes?, reason?), or publish().
    A successful operation ID is immutable; different bytes conflict. Any pending
    intent blocks further operations until separately reviewed recovery, including
    failures that may have occurred after a durable source link.
    """
    source.require(type(config) is Configuration and config.enabled is True, 'EPL_DISABLED')
    source.require(isinstance(operation_id, str) and re.fullmatch('[a-zA-Z0-9_-]{1,80}', operation_id),
                   'EPL_OPERATION_INVALID')
    source.require(stage in ('freeze', 'observe', 'result', 'publish'), 'EPL_STAGE_UNSUPPORTED')
    inputs = {} if inputs is None else inputs
    source.require(type(inputs) is dict, 'EPL_OPERATION_INVALID')
    allowed = {
        'freeze': {'calendar','approval_raw','sources','contract_raw','reviewed_contract_sha256',
                   'competition','season_year','selected_day','bundle_sha256'},
        'observe': {'forecast_id','saved','binding','team_side','line'},
        'result': {'forecast_id','raw','result_sha256','supersedes','reason'},
        'publish': set(),
    }
    source.require(set(inputs) <= allowed[stage], 'EPL_OPERATION_INVALID')
    required = {
        'freeze': allowed['freeze'],
        'observe': {'forecast_id', 'saved', 'binding'},
        'result': {'forecast_id', 'raw', 'result_sha256'},
        'publish': set(),
    }
    source.require(required[stage] <= set(inputs), 'EPL_OPERATION_INPUTS_MISSING')
    # Snapshot mutable containers before hashing or passing them to the source.
    from copy import deepcopy
    inputs = deepcopy(inputs)
    # Validate configuration before creating the runner lock or any directories.
    # Result acceptance may outlive a qualification window; validate that same
    # configured contract at the original forecast time, never qualify a new pick.
    at = source.now()
    if stage == 'result':
        frozen, _ = source._frozen(state_dir, inputs.get('forecast_id'))
        at = source.timestamp(frozen['recorded_at'])
    pins = _configuration(config, at)
    if stage in ('observe', 'result'):
        frozen, _ = source._frozen(state_dir, inputs.get('forecast_id'))
        source.require(frozen['data']['qualification_sha256'] in pins, 'EPL_FORECAST_NOT_APPROVED')
    if stage == 'freeze':
        source.require(inputs.get('approval_raw') in config.contracts, 'EPL_QUALIFICATION_NOT_APPROVED')
    request = fingerprint(encoded(_wire(dict(stage=stage, inputs=inputs, config=config))))
    state = Path(state_dir)
    _directory(state)
    _directory(state/'prospective')
    _directory(state/DIRECTORY)
    lock_path = state/'prospective'/'runner.lock'
    source.require(not lock_path.is_symlink(), 'EPL_OPERATION_STORE_REJECTED')
    if lock_path.exists():
        info = lock_path.stat()
        source.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid(),
                       'EPL_OPERATION_STORE_REJECTED')
    with _lock(state):
        directory = state/DIRECTORY
        _directory(directory)
        from itertools import islice
        paths = list(islice(directory.iterdir(), 2001))
        source.require(len(paths) <= 2000 and all(p.is_file() and not p.is_symlink()
                       and p.stat().st_size <= 65536 for p in paths), 'EPL_OPERATION_STORE_REJECTED')
        intent_path = directory/(operation_id+'.intent.json')
        done_path = directory/(operation_id+'.done.json')
        if done_path.exists():
            intent, done = _read(intent_path), _read(done_path)
            source.require(intent['request'] == done['request'] == request, 'EPL_OPERATION_CONFLICT')
            _sync_directories(directory, state)
            return done['result']
        source.require(not any(p.name.endswith('.intent.json') and not
                       p.with_name(p.name.replace('.intent.json','.done.json')).exists() for p in paths),
                       'EPL_OPERATION_RECOVERY_REQUIRED')
        source.require(len(paths) <= 1998, 'EPL_OPERATION_CAPACITY_EXCEEDED')
        _save(directory, intent_path.name, dict(request=request, stage=stage))
        from modelfc.fixture_diagnostics import Recorder
        diagnostics = Recorder()
        diagnostic_start = source.now()
        # Do not catch mutation errors or continue to publication after them.
        if stage == 'freeze':
            value = source.freeze(state, **inputs, approved_contracts=pins, enabled=True)
        elif stage == 'observe':
            value = source.observe(state, **inputs, enabled=True)
        elif stage == 'result':
            value = source.record_result(state, **inputs, enabled=True)
        else:
            from modelfc.pick_strategies import publish_locked
            # No policy activation or unrelated E1 publication from an empty or
            # stale EPL batch. The shared writer revalidates again at link time.
            eligible = any(source.select(state, source.now(), approved_contracts=pins, strategy=strategy,
                                         diagnostics=diagnostics)
                           for strategy in ('best_value', 'higher_probability'))
            value = publish_locked(state, epl_approved_contracts=pins, epl_only=True, diagnostics=diagnostics) if eligible else 0
        result = dict(stage=stage, value=value, provider_requests=0)
        completion = dict(request=request, result=result)
        if stage == 'publish':
            completion.update(diagnostic_started_at=diagnostic_start.isoformat(),
                              diagnostic_completed_at=source.now().isoformat(),
                              fixture_diagnostics=diagnostics.snapshot())
            # Keep the existing operation-record bound even at recorder capacity.
            while len((json.dumps(dict(data=completion, sha256='0'*64),
                                  indent=2, sort_keys=True, allow_nan=False)+'\n').encode()) > 65536:
                completion['fixture_diagnostics']['status'] = 'INCOMPLETE'
                completion['fixture_diagnostics']['events'].pop()
        _save(directory, done_path.name, completion)
        return result
