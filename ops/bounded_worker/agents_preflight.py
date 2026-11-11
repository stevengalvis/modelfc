"""Read-only host prerequisites. Does not execute binaries or attest isolation.

Run as the intended unprivileged executor user. No credentials, environment
values, hostname, service logs, production files or network are inspected.
"""
import json
import os
from pathlib import Path
import shutil
import sys


def _bounded_read(path):
    with open(path, 'r', encoding='ascii') as source:
        return source.read(65537)


def inspect_host(*, read_text=_bounded_read, exists=os.path.exists,
                 which=shutil.which, uid=os.geteuid, platform=sys.platform):
    def read(path):
        try:
            value = read_text(path)
            return value if isinstance(value, str) and len(value) <= 65536 else None
        except (OSError, UnicodeError):
            return None

    raw = read('/proc/sys/user/max_user_namespaces')
    try:
        userns_limit = int(raw.strip()) if raw is not None else None
        if userns_limit is not None and userns_limit < 0:
            userns_limit = None
    except ValueError:
        userns_limit = None
    status = read('/proc/self/status')
    fields = {}
    if status is not None:
        for line in status.splitlines():
            key, sep, value = line.partition(':')
            if sep and key in ('NoNewPrivs', 'Seccomp'):
                # Unknown values remain unknown, not a permissive fallback.
                fields[key] = value.strip() if value.strip() in ('0', '1', '2') else None
    checks = {
        'linux': platform.startswith('linux'),
        'nonroot_identity': uid() != 0,
        'cgroup_v2_visible': exists('/sys/fs/cgroup/cgroup.controllers'),
        'systemd_runtime_visible': exists('/run/systemd/system'),
        'bubblewrap_on_path': which('bwrap') is not None,
        'systemd_run_on_path': which('systemd-run') is not None,
        'user_namespaces_configured': None if userns_limit is None else userns_limit > 0,
    }
    missing = [name for name, value in checks.items() if value is not True]
    return {
        'schema': 'agents-api-prerequisites-v2',
        'ready_for_live': False,
        'executor_image_verified': False,
        'checks': checks,
        'missing_or_unknown': missing,
        'process_metadata': {'no_new_privileges': fields.get('NoNewPrivs'),
                             'seccomp_mode': fields.get('Seccomp')},
        'still_requires_verification': [
            'executor_image_identity_and_clean_private_home',
            'filesystem_denies_and_command_sandbox_creation',
            'control_channel_egress_and_command_network_denies',
            'restricted_key_scope_and_secure_injection',
            'durable_single_attempt_reservation',
            'resource_limits_and_cloud_local_cancellation_cleanup',
            'project_credit_model_review_access_and_spending_authorization',
        ],
    }


if __name__ == '__main__':
    print(json.dumps(inspect_host(), sort_keys=True, indent=2))
