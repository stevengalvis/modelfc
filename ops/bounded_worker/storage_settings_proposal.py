"""Offline proposal from retained evidence; never installs or approves settings.

The operator record's schema is not known here. Its digest binds the retained
bytes but does not prove their meaning, successful host admission or capacity.
No caller may treat this proposal as activation or credential-isolation evidence.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from .bridge_envelope import _pairs
from .bridge_security import disjoint
from .storage import StorageConfiguration, service_properties

TERMINAL_SHA256 = '7be678d162e3c14771c270beb9b601b0330162873df12f36d9b661407e1f918d'
OPERATOR_SHA256 = 'aab5f93bc9861c6162ea10183f408282b3051826d87ad6dbf6a60c3455e4e3b1'
_SETTINGS = {'policy', 'state_root', 'mirror', 'workspace_root', 'evidence_root', 'publisher_root',
             'username', 'executable', 'executable_sha256', 'reader_token_file', 'publisher_token_file'}


def _object(raw, limit):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError('invalid_input')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_number')))
    if type(value) is not dict:
        raise ValueError('invalid_object')
    return value


def propose(settings_bytes, terminal_bytes, operator_record_bytes):
    """Return proposed settings bytes only; no I/O, install, or PASS claim."""
    if (type(terminal_bytes) is not bytes or hashlib.sha256(terminal_bytes).hexdigest() != TERMINAL_SHA256
            or type(operator_record_bytes) is not bytes or not 0 < len(operator_record_bytes) <= 1048576
            or hashlib.sha256(operator_record_bytes).hexdigest() != OPERATOR_SHA256):
        raise ValueError('evidence_digest_mismatch')
    settings = _object(settings_bytes, 16384)
    terminal = _object(terminal_bytes, 65536)
    fields = {'state', 'identity', 'fstab', 'allocation_receipt', 'settings_sha256',
              'unresolved_marker_sha256', 'allocation_intent_sha256', 'proposed_storage'}
    if (set(settings) != _SETTINGS or any(type(settings[key]) is not str or not settings[key] for key in _SETTINGS)
            or set(terminal) != fields or terminal['state'] != 'capacity_pending'
            or hashlib.sha256(settings_bytes).hexdigest() != terminal['settings_sha256']):
        raise ValueError('settings_or_terminal_mismatch')
    for key in ('allocation_receipt', 'settings_sha256', 'unresolved_marker_sha256', 'allocation_intent_sha256'):
        if type(terminal[key]) is not str or not re.fullmatch(r'[0-9a-f]{64}', terminal[key]):
            raise ValueError('invalid_evidence_digest')
    try:
        storage = StorageConfiguration(**terminal['proposed_storage'])
        storage.validate()
    except (TypeError, ValueError):
        raise ValueError('invalid_proposed_storage') from None
    home = Path(storage.auth_source).parent.parent
    service_properties(storage, work=Path(storage.workspace) / 'proposal',
                       runtime=Path(storage.runtime) / 'proposal', home=home,
                       unit='modelfc-bounded-' + '0' * 32 + '.service')
    identity = terminal['identity']
    if (type(identity) is not dict or set(identity) != {'mount_id', 'device', 'image_device', 'image_inode',
                                                        'image_bytes', 'filesystem_bytes', 'filesystem_inodes'}
            or type(identity['mount_id']) is not int or identity['mount_id'] <= 0
            or type(identity['device']) is not str or not re.fullmatch(r'[0-9]+:[0-9]+', identity['device'])
            or any(type(identity[key]) is not int or identity[key] != getattr(storage, key) for key in
                   ('image_device', 'image_inode', 'image_bytes', 'filesystem_bytes', 'filesystem_inodes'))
            or storage.image_bytes != 1073741824 or storage.filesystem_bytes != 1063956480
            or storage.filesystem_inodes != 32768 or storage.reserve_bytes != 134217728
            or storage.reserve_inodes != 2048):
        raise ValueError('storage_identity_mismatch')
    settings['workspace_root'] = storage.workspace
    roots = [Path(settings[key]) for key in ('state_root', 'mirror', 'workspace_root', 'evidence_root', 'publisher_root')]
    for index, root in enumerate(roots):
        if not root.is_absolute() or '..' in root.parts:
            raise ValueError('invalid_root')
        for other in roots[index + 1:]:
            if root == other or root in other.parents or other in root.parents:
                raise ValueError('overlapping_roots')
    for key in ('reader_token_file', 'publisher_token_file'):
        disjoint(settings[key], *roots, storage.mount)
    for path in (storage.image, storage.auth_source, storage.config_source):
        disjoint(path, *roots, storage.mount)
    for key in ('state_root', 'mirror', 'evidence_root', 'publisher_root'):
        disjoint(settings[key], storage.mount)
    settings['storage'] = asdict(storage)
    output = (json.dumps(settings, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(output) > 16384:
        raise ValueError('settings_size_rejected')
    return output
