"""Fixed protected commissioning entrypoint; no scheduler, retries or capture."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess

from modelfc import guarded_acquisition as acquisition, pl_operator_once as operator
from modelfc.acquisition_storage import require_runtime
from modelfc.providers.oddspapi_saved_response import _json

ADMISSION = Path('/etc/modelfc/epl-preparation-admission.json')
AUTHORIZATION = Path('/etc/modelfc/epl-preparation-authorization.json')
STATE = '/var/lib/modelfc/state'
# Matches the finite unit: 180 seconds execution, 30 stop, 30 scheduling margin.
BOUND = timedelta(seconds=240)


def _now():
    return datetime.now(timezone.utc)


def _e1_idle():
    result = subprocess.run(
        ['/usr/bin/systemctl', '--no-ask-password', 'show',
         'modelfc-corner-prospective.service',
         '--property=LoadState,ActiveState,SubState,Job'],
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, cwd='/',
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=5)
    fields = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition('=')
        if not sep or key in fields:
            raise ValueError('admission')
        fields[key] = value
    return fields == {'LoadState': 'loaded', 'ActiveState': 'inactive',
                      'SubState': 'dead', 'Job': ''}


def _admit():
    require_runtime()
    raw = acquisition._local_bytes(ADMISSION, 8192, private=True)
    a = _json(raw)
    if type(a) is not dict or set(a) != {
            'version', 'release_sha', 'authorization_sha256', 'state_dir', 'private_dir',
            'issued_at', 'expires_at', 'next_e1_at'}:
        raise ValueError('admission')
    now = _now()
    issued, expires, next_e1 = (acquisition._timestamp(a[k]) for k in
                               ('issued_at', 'expires_at', 'next_e1_at'))
    if (type(a['version']) is not int or a['version'] != 1
            or not issued <= now < expires
            or not timedelta(0) < expires-issued <= timedelta(minutes=10)
            or now + BOUND >= next_e1
            or a['state_dir'] != STATE
            or not isinstance(a['release_sha'], str)
            or re.fullmatch('[0-9a-f]{40}', a['release_sha']) is None
            or not isinstance(a['authorization_sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', a['authorization_sha256']) is None):
        raise ValueError('admission')
    # The protected launcher already verifies ownership, marker and physical path.
    release = Path.cwd()
    if (release.parent != Path('/srv/modelfc/releases')
            or re.fullmatch(a['release_sha']+'-[0-9a-f]{12}', release.name) is None
            or (release / '.git/modelfc-deployed-sha').read_bytes() != a['release_sha'].encode()):
        raise ValueError('admission')
    authorization, pin = operator._authorization(AUTHORIZATION)
    if (pin != a['authorization_sha256']
            or authorization['state_dir'] != a['state_dir']
            or authorization['private_dir'] != a['private_dir']
            or now + BOUND >= acquisition._timestamp(authorization['expires_at'])):
        raise ValueError('admission')
    if not _e1_idle():
        raise ValueError('admission')
    # Recheck after the bounded manager query; do not extend a stale admission.
    now = _now()
    if (not issued <= now < expires or now + BOUND >= next_e1
            or now + BOUND >= acquisition._timestamp(authorization['expires_at'])
            or acquisition._local_bytes(ADMISSION, 8192, private=True) != raw):
        raise ValueError('admission')
    return pin


def run():
    try:
        pin = _admit()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return {'status': 'HOLD', 'reason': 'EPL_RUNTIME_ADMISSION_REJECTED',
                'provider_requests': 0}
    # prepare owns its nonblocking shared lock and immutable day claim.
    # An unexpected error after entry must not be relabelled as zero requests.
    return operator.prepare(enabled=True, authorization_path=AUTHORIZATION,
                            authorization_sha256=pin)


def main():
    result = run()
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result.get('status') == 'RETAINED_UNREVIEWED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
