"""Explicit protected one-shot host entrypoint; no timer or deployment side effect.

Invoke only after host acceptance and authorization. Imports perform no I/O.
"""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import re
import time

from .bridge_security import disjoint, protected_bytes, protected_dir, protected_json
from .linear_coding_intake import _uuid
from .linear_coding_runtime import (CodingSink, load_approval, load_settings, run_once)
from .linear_transport import Destination, LinearAPI, LinearReader


def _settings(path):
    value = protected_json(path)
    required = {'version', 'enabled', 'runtime_settings', 'request_comment_id', 'mirror',
                'username', 'executable', 'executable_sha256', 'token_file', 'members',
                'audience', 'existing_roots'}
    if (type(value) is not dict or set(value) not in (required, required | {'storage'}, required | {'api_settings'})
            or type(value['version']) is not int or value['version'] != 1
            or value['enabled'] is not True or not _uuid(value['request_comment_id'])
            or any(type(value[k]) is not str or not value[k] for k in
                   ('runtime_settings', 'mirror', 'username', 'executable', 'executable_sha256', 'token_file'))
            or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', value['username'])
            or not re.fullmatch('[a-f0-9]{64}', value['executable_sha256'])
            or not Path(value['executable']).is_absolute()
            or type(value['members']) is not list or not 1 <= len(value['members']) <= 100
            or not all(_uuid(member) for member in value['members'])
            or len(set(value['members'])) != len(value['members'])
            or (value['audience'] is not None and type(value['audience']) is not dict)
            or type(value['existing_roots']) is not dict or set(value['existing_roots']) !=
               {'health_state', 'github_state', 'github_workspace', 'github_runtime', 'github_evidence'}):
        raise ValueError('invalid_entry_settings')
    runtime = load_settings(value['runtime_settings'])
    approval = load_approval(runtime['approval'])
    if 'api_settings' in value and (approval.target != 'agents-api' or type(value['api_settings']) is not str):
        raise ValueError('api_settings_target_mismatch')
    _validate_host(value, runtime, path, (value['runtime_settings'], runtime['approval']) +
                   ((value['api_settings'],) if 'api_settings' in value else ()))
    return value, runtime, approval


def _validate_host(value, runtime, path, control_files):
    """Shared fixed host validation; controls are protected operator file paths."""
    if (any(type(value[k]) is not str or not value[k] for k in
            ('mirror', 'username', 'executable', 'executable_sha256', 'token_file'))
            or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', value['username'])
            or not re.fullmatch('[a-f0-9]{64}', value['executable_sha256'])
            or not Path(value['executable']).is_absolute()
            or type(value['members']) is not list or not 1 <= len(value['members']) <= 100
            or not all(_uuid(x) for x in value['members']) or len(set(value['members'])) != len(value['members'])
            or (value['audience'] is not None and type(value['audience']) is not dict)
            or type(value['existing_roots']) is not dict or set(value['existing_roots']) !=
               {'health_state', 'github_state', 'github_workspace', 'github_runtime', 'github_evidence'}):
        raise ValueError('invalid_host_settings')
    # The host operator pins existing roots; never infer isolation from names.
    current = [protected_dir(value['mirror'])] + [protected_dir(runtime[key]) for key in
                                               ('state_root', 'workspace_root', 'evidence_root')]
    existing = {key: protected_dir(root) for key, root in value['existing_roots'].items()}
    excluded = list(existing.values())
    for index, root in enumerate(current):
        disjoint(root, *current[index+1:], *excluded)
    for file in (path, *control_files, value['token_file'], value['executable']):
        disjoint(file, *current, *excluded)
    if 'storage' in value:
        from .storage import StorageConfiguration
        if type(value['storage']) is not dict:
            raise ValueError('invalid_storage')
        storage = StorageConfiguration(**value['storage'])
        storage.validate()
        if storage.workspace != runtime['workspace_root']:
            raise ValueError('storage_workspace_mismatch')
        for file in (path, *control_files, value['token_file'],
                     value['executable'], storage.image, storage.auth_source, storage.config_source):
            disjoint(file, *current, *excluded, storage.mount)
        mount = protected_dir(storage.mount)
        runtime_root = protected_dir(storage.runtime)
        # Reuse the accepted filesystem, not another task source's directories.
        disjoint(runtime_root, *current, *excluded)
        for root in (current[0], current[1], current[3], existing['health_state'],
                     existing['github_state'], existing['github_evidence']):
            disjoint(root, mount)


def _executor(value, runtime, approval, *, db=None, guard=None, clock=time.time):
    from .linear_pickup import SubscriptionAdapter, UnsupportedTarget
    if approval.target == 'agents-api':
        if db is None or 'api_settings' not in value:
            raise UnsupportedTarget('agents_api_not_configured')
        from .agents_linear_wiring import FixedAPIExecutor
        return FixedAPIExecutor(db, value['api_settings'], runtime, value['mirror'], guard=guard, clock=clock)
    if approval.target not in (None, 'codex-subscription'):
        raise UnsupportedTarget('unknown_target')
    # Fixed trusted implementation, not a module/command named by the request.
    from .bridge_executor import Executor
    from .launcher import Configuration
    from .storage import StorageConfiguration
    storage = StorageConfiguration(**value['storage']) if 'storage' in value else None
    config = Configuration(workspace_root=runtime['workspace_root'], evidence_root=runtime['evidence_root'],
                           username=value['username'], executable=value['executable'],
                           executable_sha256=value['executable_sha256'],
                           timeout_seconds=approval.max_timeout, output_bytes=approval.max_output,
                           diff_bytes=approval.max_diff, prepared_baseline=True, storage=storage)
    executor = Executor(config, value['mirror'])
    return executor if approval.target is None else SubscriptionAdapter(executor)


def run(path, *, clock=time.time, _store=None):
    try:
        value, runtime, approval = _settings(path)
        if approval.enabled is not True or not int(clock()) < approval.expires:
            return {'status': 'blocked'}
        # One locally protected key and fixed personal-key authentication; no OAuth
        # provisioning, publisher credential or arbitrary transport endpoint.
        if approval.target == 'agents-api' and 'api_settings' not in value:
            from .linear_pickup import UnsupportedTarget
            raise UnsupportedTarget('agents_api_not_configured')
        executor = None if approval.target == 'agents-api' else _executor(value, runtime, approval)
        token = protected_bytes(value['token_file'], 8192).decode().strip()
        destination = Destination(approval.policy, runtime['viewer'], tuple(value['members']),
                                  audience=value['audience'])
        api = LinearAPI(token, oauth=False)

        def unchanged():
            if _settings(path) != (value, runtime, approval):
                raise ValueError('entry_settings_changed')

        class CheckedReader(LinearReader):
            def fetch(self, comment):
                unchanged()
                result = super().fetch(comment)
                unchanged()
                return result

        class CheckedSink(CodingSink):
            def post(self, issue, body):
                unchanged()
                return super().post(issue, body)

        if approval.target == 'agents-api':
            if 'api_settings' not in value:
                raise UnsupportedTarget('agents_api_not_configured')
            from .linear_coding_runtime import receipt_store, replies_safe
            with (receipt_store(runtime['state_root']) if _store is None else nullcontext(_store)) as db:
                if not replies_safe(db, approval.policy.issue, runtime['viewer']): return {'status': 'blocked'}
                executor = _executor(value, runtime, approval, db=db, guard=unchanged, clock=clock)
                return run_once(value['runtime_settings'], value['request_comment_id'],
                    CheckedReader(api, destination), executor, CheckedSink(api, destination), clock, _store=db)
        return run_once(value['runtime_settings'], value['request_comment_id'],
                        CheckedReader(api, destination), executor,
                        CheckedSink(api, destination), clock, _store=_store)
    except Exception as error:
        from .linear_pickup import UnsupportedTarget
        return {'status': 'unsupported_target'} if isinstance(error, UnsupportedTarget) else {'status': 'blocked'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('settings', help='Protected operator entry configuration')
    args = parser.parse_args()
    print(json.dumps(run(args.settings), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
