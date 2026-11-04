"""Test-only metadata scope for credential-denial experiments.

The operator must substantiate completeness and alias resolution separately;
this parser cannot discover unknown credential stores or authenticate claims.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .bridge_envelope import _pairs
from .bridge_security import protected_bytes

TEST_ACCOUNT = 'modelfc-containment-test'


def load_scope(scope_path, scope_sha256, evidence_path, account, production_account, *,
               _read=protected_bytes, _lstat=os.lstat, _boottime=None, _bootid=None):
    """Validate protected metadata only; never open or read credential stores."""
    if (type(scope_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', scope_sha256)
            or account.pw_name != TEST_ACCOUNT or account.pw_uid <= 0 or account.pw_gid <= 0
            or production_account.pw_uid <= 0 or production_account.pw_uid == account.pw_uid):
        raise ValueError('credential_scope_identity_rejected')
    test_home = getattr(account, 'pw_dir', None)
    if (type(test_home) is not str or not re.fullmatch(r'/[A-Za-z0-9_./-]+', test_home)
            or test_home == '/' or str(Path(test_home)) != test_home or '..' in Path(test_home).parts
            or test_home == production_account.pw_dir):
        raise ValueError('test_home_unverified')
    for component in reversed(Path(test_home).parents):
        info = _lstat(component)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('test_home_unverified')
    try:
        _lstat(test_home)
    except FileNotFoundError:
        pass
    else:
        # A fresh no-home test account avoids inspecting any existing home data.
        raise ValueError('test_home_not_absent')
    raw = _read(scope_path, maximum=16384)
    if hashlib.sha256(raw).hexdigest() != scope_sha256:
        raise ValueError('credential_scope_hash_mismatch')
    data = json.loads(raw, object_pairs_hook=_pairs)
    fields = {'version', 'test_username', 'test_uid', 'test_gid', 'production_uid', 'stores',
              'complete', 'aliases_resolved', 'same_uid_processes_absent', 'keyrings_clear', 'evidence_sha256',
              'boot_id', 'observed_boottime_ns'}
    if (type(data) is not dict or set(data) != fields or type(data['version']) is not int or data['version'] != 1
            or data['test_username'] != TEST_ACCOUNT or type(data['test_uid']) is not int
            or type(data['test_gid']) is not int or type(data['production_uid']) is not int
            or data['test_uid'] != account.pw_uid or data['test_gid'] != account.pw_gid
            or data['production_uid'] != production_account.pw_uid
            or any(data[name] is not True for name in ('complete', 'aliases_resolved',
                   'same_uid_processes_absent', 'keyrings_clear'))
            or type(data['evidence_sha256']) is not str
            or not re.fullmatch(r'[0-9a-f]{64}', data['evidence_sha256'])
            or type(data['boot_id']) is not str or not re.fullmatch(r'[0-9a-f-]{36}', data['boot_id'])
            or type(data['observed_boottime_ns']) is not int or data['observed_boottime_ns'] <= 0):
        raise ValueError('credential_scope_incomplete')
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if _boottime is None else _boottime
    boot = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip() if _bootid is None else _bootid
    if data['boot_id'] != boot or not 0 <= now - data['observed_boottime_ns'] <= 300_000_000_000:
        raise ValueError('credential_scope_stale')
    evidence = _read(evidence_path, maximum=65536)
    if not evidence or hashlib.sha256(evidence).hexdigest() != data['evidence_sha256']:
        raise ValueError('credential_scope_evidence_mismatch')
    stores = data['stores']
    if type(stores) is not list or not 1 <= len(stores) <= 32:
        raise ValueError('credential_scope_stores_rejected')
    paths = []
    for item in stores:
        if (type(item) is not dict or set(item) != {'path', 'device', 'inode', 'uid', 'gid', 'mode', 'ctime_ns'}
                or type(item['path']) is not str or len(item['path']) > 256
                or not re.fullmatch(r'/[A-Za-z0-9_./-]+', item['path'])
                or str(Path(item['path'])) != item['path'] or '..' in Path(item['path']).parts
                or item['path'] == '/' or type(item['device']) is not int or item['device'] <= 0
                or type(item['inode']) is not int or item['inode'] <= 0
                or any(type(item[key]) is not int or item[key] < 0 for key in ('uid', 'gid', 'mode', 'ctime_ns'))):
            raise ValueError('credential_scope_path_rejected')
        path = Path(item['path'])
        for component in reversed(path.parents):
            info = _lstat(component)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('credential_scope_path_rejected')
        info = _lstat(path)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, production_account.pw_uid)
                or info.st_mode & 0o022 or (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                    stat.S_IMODE(info.st_mode), info.st_ctime_ns) != (item['device'], item['inode'],
                    item['uid'], item['gid'], item['mode'], item['ctime_ns'])):
            raise ValueError('credential_scope_identity_changed')
        paths.append(item['path'])
    if len(set(paths)) != len(paths) or production_account.pw_dir not in paths:
        raise ValueError('credential_scope_incomplete')
    return tuple(paths)
