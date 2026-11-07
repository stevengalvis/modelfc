"""Explicit protected one-shot host entrypoint; no timer or deployment side effect.

Invoke only after host acceptance and authorization. Imports perform no I/O.
"""
import argparse
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
    if (type(value) is not dict or set(value) not in (required, required | {'storage'})
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
               {'health_state', 'github_state', 'github_workspace', 'github_evidence'}):
        raise ValueError('invalid_entry_settings')
    runtime = load_settings(value['runtime_settings'])
    approval = load_approval(runtime['approval'])
    # The host operator pins existing roots; never infer isolation from names.
    current = [protected_dir(value['mirror'])] + [protected_dir(runtime[key]) for key in
                                               ('state_root', 'workspace_root', 'evidence_root')]
    excluded = [protected_dir(root) for root in value['existing_roots'].values()]
    for index, root in enumerate(current):
        disjoint(root, *current[index+1:], *excluded)
    for file in (path, value['runtime_settings'], runtime['approval'], value['token_file'], value['executable']):
        disjoint(file, *current, *excluded)
    if 'storage' in value:
        from .storage import StorageConfiguration
        if type(value['storage']) is not dict:
            raise ValueError('invalid_storage')
        storage = StorageConfiguration(**value['storage'])
        storage.validate()
        if storage.workspace != runtime['workspace_root']:
            raise ValueError('storage_workspace_mismatch')
        for file in (path, value['runtime_settings'], runtime['approval'], value['token_file'],
                     value['executable'], storage.image, storage.auth_source, storage.config_source):
            disjoint(file, *current, *excluded, storage.mount)
        for root in (current[0], current[1], current[3], *excluded):
            disjoint(root, storage.mount)
    return value, runtime, approval


def _executor(value, runtime, approval):
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
    return Executor(config, value['mirror'])


def run(path, *, clock=time.time):
    try:
        value, runtime, approval = _settings(path)
        if approval.enabled is not True or not int(clock()) < approval.expires:
            return {'status': 'blocked'}
        # One locally protected key and fixed personal-key authentication; no OAuth
        # provisioning, publisher credential or arbitrary transport endpoint.
        token = protected_bytes(value['token_file'], 8192).decode().strip()
        destination = Destination(approval.policy, runtime['viewer'], tuple(value['members']),
                                  audience=value['audience'])
        api = LinearAPI(token, oauth=False)
        executor = _executor(value, runtime, approval)

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

        return run_once(value['runtime_settings'], value['request_comment_id'],
                        CheckedReader(api, destination), executor,
                        CheckedSink(api, destination), clock)
    except Exception:
        return {'status': 'blocked'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('settings', help='Protected operator entry configuration')
    args = parser.parse_args()
    print(json.dumps(run(args.settings), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
