"""Explicit post-completion export command; never invokes coding or polling."""
import argparse
import json
from .bridge_security import protected_json, protected_bytes
from .linear_coding_intake import Request, binding
from .linear_coding_runtime import receipt_store, replies_safe
from .linear_coding_entry import _executor, _validate_host
from .linear_remote_entry import settings
from .linear_transport import Destination, LinearAPI
from .linear_patch_delivery import deliver_artifact


def run(host_path, disclosure_path):
    try:
        loaded = settings(host_path)
        value, runtime, policy = loaded
        disclosure = protected_json(disclosure_path)
        # Reuse host separation checks for the new protected disclosure control.
        _validate_host(value, runtime, host_path, (value['channel_policy'], disclosure_path))
        if disclosure.get('enabled') is not True or disclosure.get('viewer') != runtime['viewer']:
            return 'blocked'
        with receipt_store(runtime['state_root']) as db:
            if not replies_safe(db, policy.policy.issue, runtime['viewer']):
                return 'blocked'
            row = db.execute('SELECT request,phase FROM linear_coding_jobs WHERE binding=?',
                             (disclosure.get('request_sha256'),)).fetchone()
            if row is None or row[1] != 'completed':
                return 'blocked'
            saved = json.loads(row[0])
            request = Request(saved['body'].encode(), saved['workspace'], saved['team'],
                              saved['issue'], saved['actor'], saved['comment'], None, saved.get('created_at'))
            if binding(request) != disclosure['request_sha256']:
                return 'blocked'
        # Release lookup lock before delivery acquires it. Delivery revalidates
        # the exact completed receipt under its own lock; there is no nested flock.
        def unchanged():
            if settings(host_path) != loaded or protected_json(disclosure_path) != disclosure:
                raise ValueError('export_settings_changed')
        unchanged()
        token = protected_bytes(value['token_file'], 8192).decode().strip()
        class CheckedAPI(LinearAPI):
            def call(self, query, variables):
                unchanged()
                return super().call(query, variables)
        destination = Destination(policy.policy, runtime['viewer'], tuple(value['members']), audience=value['audience'])
        config = _executor(value, runtime, policy).config
        unchanged()
        return deliver_artifact(runtime['state_root'], request, config, disclosure_path,
                                CheckedAPI(token, oauth=False), destination)
    except BlockingIOError:
        return 'busy'
    except Exception:
        return 'blocked'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('host_settings')
    parser.add_argument('disclosure_policy')
    args = parser.parse_args()
    print(json.dumps({'artifact': run(args.host_settings, args.disclosure_policy)}, sort_keys=True))


if __name__ == '__main__':
    main()
