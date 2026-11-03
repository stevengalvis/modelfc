"""Pure visibility model for review. Not wired to the bridge or an admission check.

Inputs must come from a trusted observer: two complete mount snapshots and the
mount ID obtained by securely opening each distinct mountpoint in between. This
model cannot establish that property, prevent races, or prove isolation.
"""
import json
from pathlib import PurePosixPath


class VisibilityBlocked(ValueError):
    pass


def _path(value):
    return (type(value) is str and len(value) <= 4096 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and value.startswith('/') and not value.startswith('//')
            and str(PurePosixPath(value)) == value
            and all(part not in ('.', '..') for part in value.split('/')))


def _snapshot(mounts):
    if type(mounts) is not list or not mounts or len(mounts) > 65536:
        raise VisibilityBlocked('invalid_snapshot')
    result = {}
    for mount in mounts:
        if (type(mount) is not dict or set(mount) !=
                {'id', 'path', 'root', 'device', 'options', 'type', 'super_options'}):
            raise VisibilityBlocked('invalid_mount')
        identity = mount['id']
        if (type(identity) is not int or identity <= 0 or identity in result or not _path(mount['path'])
                or type(mount['root']) is not str or not _path(mount['root'])
                or type(mount['device']) is not str or type(mount['type']) is not str
                or any(type(mount[key]) is not list or not mount[key]
                       or any(type(option) is not str for option in mount[key])
                       for key in ('options', 'super_options'))
                or not ({'ro', 'rw'} & set(mount['options']))
                or {'ro', 'rw'} <= set(mount['options'])):
            raise VisibilityBlocked('invalid_mount')
        result[identity] = mount
    if not any(m['path'] == '/' for m in result.values()):
        raise VisibilityBlocked('root_missing')
    return result


def visible(before, after, observed_ids):
    """Model visible entries; uncertain or inconsistent evidence blocks.

    observed_ids maps *every* distinct mountpoint to the fd's kernel mount ID.
    It must not be derived from mountinfo ordering or from untrusted processes.
    Each successful secure open and both snapshots must share one namespace.
    A trusted operator must prevent privileged mount changes throughout launch
    and execution; equal snapshots alone cannot detect change-and-revert races.
    """
    first, second = _snapshot(before), _snapshot(after)
    if json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True):
        raise VisibilityBlocked('snapshot_changed')
    paths = {m['path'] for m in first.values()}
    if type(observed_ids) is not dict or set(observed_ids) != paths:
        raise VisibilityBlocked('incomplete_observation')
    for path, identity in observed_ids.items():
        if type(identity) is not int or identity not in first:
            raise VisibilityBlocked('unknown_mount_id')
        parent = first[identity]['path']
        if path != parent and PurePosixPath(parent) not in PurePosixPath(path).parents:
            raise VisibilityBlocked('inconsistent_mount_id')
        if observed_ids.get(parent) != identity:
            raise VisibilityBlocked('inconsistent_mount_id')
    return [m for m in before if observed_ids[m['path']] == m['id']]
