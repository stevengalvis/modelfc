"""Bounded failure observations only; embedded into authenticated generated sources."""
import hashlib
import json
import os
import time


def workspace_context_uint(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    return value


def workspace_context_identity():
    with open('/proc/self/stat', encoding='ascii') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    closing = raw.rfind(')')
    fields = raw[closing + 2:].split()
    pid = int(raw.split(' ', 1)[0])
    start = int(fields[19])
    link = os.readlink('/proc/self/ns/mnt')
    if closing < 0 or not link.startswith('mnt:[') or not link.endswith(']'):
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    namespace = int(link[5:-1])
    with open('/proc/self/status', encoding='ascii') as stream:
        status = stream.read(16385)
    rows = [line.split(':',1)[1].split() for line in status.splitlines() if line.startswith('NSpid:')]
    if len(status) > 16384 or len(rows) != 1:
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    identity = {'proc_pid':pid,'pid':os.getpid(),'start_ticks':start,'mnt_ns':namespace,
                'nspid':[int(n) for n in rows[0]]}
    workspace_context_validate_identity(identity)
    return identity


def workspace_context_validate_identity(value):
    if type(value) is not dict or set(value) != {'proc_pid','pid','start_ticks','mnt_ns','nspid'}:
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    for name in ('proc_pid','pid','start_ticks','mnt_ns'):
        if workspace_context_uint(value[name]) == 0:
            raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    ids = value['nspid']
    if type(ids) is not list or not 1 <= len(ids) <= 8 or not all(workspace_context_uint(n) > 0 for n in ids):
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')
    if ids[0] != value['proc_pid'] or ids[-1] != value['pid']:
        raise ValueError('WORKSPACE_CONTEXT_UNAVAILABLE')


def workspace_context_begin(mounts, visible):
    """Snapshot exactly the parsed arguments checked, never claim raw mountinfo."""
    try:
        began = workspace_context_uint(time.monotonic_ns())
        identity = workspace_context_identity()
        if type(mounts) is not list or not 0 < len(mounts) <= 512 or type(visible) is not set:
            return None
        raw = json.dumps(mounts, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
        if len(raw) > 131072:
            return None
        workspace = [m for m in mounts if m['destination'] == '/workspace-fixture']
        rows = []
        for mount in mounts:
            target = mount['destination']
            fixed = {'/':0,'/workspace-fixture':1,'/workspace-fixture/.git':2,
                     '/workspace-fixture/.agents':3,'/workspace-fixture/.codex':4,'/workspace-fixture/.aws':5}
            kind = fixed.get(target,6 if target.startswith('/workspace-fixture/') else 7)
            relation = 0
            for row in workspace:
                if mount['device'] == row['device']:
                    relation |= 1 if mount['root'] == row['root'] else 0
                    relation |= 2 if row['root'].startswith(mount['root'].rstrip('/') + '/') else 0
                    relation |= 4 if mount['root'].startswith(row['root'].rstrip('/') + '/') else 0
            if kind == 7 and not relation:
                continue
            device = mount['device'].split(':')
            if len(device) != 2:
                return None
            flags = int('rw' in mount['options'].split(',')) | (int('ro' in mount['options'].split(',')) << 1) | (int(bool(mount['propagation'])) << 2) | (int(mount['root']=='/') << 3)
            filesystem = {'tmpfs':1,'proc':2,'devpts':3,'ext4':4,'xfs':5,'btrfs':6,'overlay':7}.get(mount['type'],0)
            rows.append([workspace_context_uint(n) for n in (mount['id'],mount['parent'],
                int(device[0]),int(device[1]),int(mount['id'] in visible),kind,flags,filesystem,relation)])
        if not 0 < len(rows) <= 16:
            return None
        return {'identity':identity,'began_ns':began,'checked_mounts_sha256':hashlib.sha256(raw).hexdigest(),
                'rows':rows}
    except BaseException:
        return None


def workspace_context_attach(error, before):
    """Attach only to this exception; observation trouble cannot replace the failure."""
    try:
        after = workspace_context_identity()
        ended = workspace_context_uint(time.monotonic_ns())
        if before is None or before['identity'] != after or not 0 < before['began_ns'] <= ended:
            saved = None
        else:
            saved = dict(before, after_identity=after, ended_ns=ended)
        error._zen53_workspace_context = saved
    except BaseException:
        pass


def workspace_failure_context_line(error, checks, failure):
    if type(error) is not RuntimeError or len(error.args) != 1 or type(error.args[0]) is not str or error.args[0] not in checks:
        return ''
    # The ordinary bounded failure line is left unchanged and bound byte-for-byte.
    failure_digest = hashlib.sha256(failure(error).encode('ascii')).hexdigest()
    fallback = 'ZEN53_WORKSPACE_CONTEXT version=1 state=UNKNOWN failure_sha256=' + failure_digest + '\n'
    try:
        saved = getattr(error, '_zen53_workspace_context', None)
        if type(saved) is not dict or set(saved) != {'identity','after_identity','began_ns','ended_ns','checked_mounts_sha256','rows'}:
            return fallback
        for key in ('identity','after_identity'):
            workspace_context_validate_identity(saved[key])
        if saved['identity'] != saved['after_identity'] or not 0 < workspace_context_uint(saved['began_ns']) <= workspace_context_uint(saved['ended_ns']):
            return fallback
        digest = saved['checked_mounts_sha256']
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            return fallback
        rows = saved['rows']
        if type(rows) is not list or not 0 < len(rows) <= 16:
            return fallback
        for row in rows:
            if type(row) is not list or len(row) != 9:
                return fallback
            for number in row:
                workspace_context_uint(number)
            if row[4] > 1 or row[5] > 7 or row[6] > 15 or row[7] > 7 or row[8] > 7:
                return fallback
        record = dict(saved, version=1, state='OBSERVATION_ONLY', topology_source='PARSED_MOUNT_ARGUMENTS',
                      failure_sha256=failure_digest)
        line = 'ZEN53_WORKSPACE_CONTEXT ' + json.dumps(record, sort_keys=True, separators=(',', ':'),allow_nan=False) + '\n'
        return line if line.isascii() and len(line) <= 2048 else fallback
    except BaseException:
        return fallback
