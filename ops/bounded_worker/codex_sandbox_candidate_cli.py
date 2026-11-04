"""Test-only, normal-case entrypoint for the reviewed sandbox candidate.

The operator must separately establish the documented host prerequisites.
This does not provision or mount a filesystem or establish credential scope.
"""
import argparse
import json
import re

from .codex_credential_scope_candidate import TEST_ACCOUNT
from .codex_sandbox_supervisor_candidate import prepare_candidate, run_candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('prepare')
    create.add_argument('--fixture-root', required=True)
    for name in ('uid', 'gid', 'parent-device', 'parent-inode', 'lock-device', 'lock-inode'):
        create.add_argument('--' + name, required=True, type=int)
    run = sub.add_parser('run-normal')
    for name in ('fixture-root', 'loop', 'inventory-path', 'inventory-sha256', 'executable',
                 'executable-sha256', 'credential-scope-path', 'credential-scope-sha256', 'scope-evidence-path'):
        run.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            result = prepare_candidate(args.fixture_root, TEST_ACCOUNT, args.uid, args.gid,
                                       args.parent_device, args.parent_inode, args.lock_device, args.lock_inode)
            if (result.get('status') != 'created' or type(result.get('creation_sha256')) is not str
                    or not re.fullmatch(r'[0-9a-f]{64}', result['creation_sha256'])):
                raise ValueError('prepare_unverified')
            output = {'status': 'created', 'creation_sha256': result['creation_sha256']}
        else:
            result = run_candidate(args.fixture_root, TEST_ACCOUNT, args.loop, args.inventory_path,
                                   args.inventory_sha256, args.executable, args.executable_sha256, 'normal',
                                   credential_scope_path=args.credential_scope_path,
                                   credential_scope_sha256=args.credential_scope_sha256,
                                   scope_evidence_path=args.scope_evidence_path)
            output = {name: result.get(name) for name in ('status', 'mode', 'independent_cleanup',
                      'post_cleanup_reinspection', 'input_record_sha256')}
            if (output['status'] not in ('passed', 'blocked') or output['mode'] != 'normal'
                    or output['independent_cleanup'] not in ('verified', 'unverified')
                    or output['post_cleanup_reinspection'] not in ('verified', 'unverified')
                    or type(output['input_record_sha256']) is not str
                    or not re.fullmatch(r'[0-9a-f]{64}', output['input_record_sha256'])
                    or (output['status'] == 'passed' and (output['independent_cleanup'] != 'verified'
                        or output['post_cleanup_reinspection'] != 'verified' or 'error_class' not in result
                        or result['error_class'] is not None))):
                raise ValueError('invalid_candidate_result')
        print(json.dumps(output, sort_keys=True))
        return 0 if output['status'] in ('created', 'passed') else 1
    except Exception:
        print(json.dumps({'status': 'blocked', 'reason': 'candidate_error'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
