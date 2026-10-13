"""Private retained calendar evidence; no network or enabled consumers.

The publisher must hold the existing shared prospective runner lock.
Only the trusted forecast fetcher publishes; hashes bind bytes, not authenticity.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from modelfc.history_completeness import MAX_CALENDAR_BYTES, parse_calendar
from modelfc.ledger_storage import write_new_record
from modelfc.providers.oddspapi_saved_response import _json as parse_json


# Leave headroom for existing forecast ledgers; this is not a disk reservation.
MIN_FREE_BYTES = 64 * 1024 * 1024
MIN_FREE_INODES = 16


def directory(state, *, create=False):
    state = Path(state)
    root = state / 'odds-calendar'
    for path in (state, root):
        if path == root and create:
            root.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022 or info.st_uid != os.geteuid():
            raise ValueError('unsafe calendar directory')
        if path == root and stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('calendar directory must be private')
    if create:
        # Sync even on retry: an earlier failed first publication may have left
        # the directory visible without durably linking it into its parent.
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    return root


def read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('unsafe calendar file')
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('calendar too large')
        return raw


def retained(state):
    root = directory(state)
    pin = parse_json(read(root / 'latest.json', 256))
    if not isinstance(pin, dict) or set(pin) != {'bundle_sha256'} or not isinstance(pin['bundle_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', pin['bundle_sha256']):
        raise ValueError('invalid calendar pin')
    record = parse_json(read(root / (pin['bundle_sha256'] + '.json'), 8 * MAX_CALENDAR_BYTES))
    if not isinstance(record, dict) or set(record) != {'raw'} or not isinstance(record['raw'], str):
        raise ValueError('invalid calendar record')
    raw = record['raw'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != pin['bundle_sha256']:
        raise ValueError('calendar pin mismatch')
    return raw


def _sync_root(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def publish(state, raw, *, as_of):
    """Retain original bytes before the forecast runner's no-eligible early return."""
    calendar = parse_calendar(raw, as_of=as_of)
    root = directory(state, create=True)
    path = root / (calendar.bundle_sha256 + '.json')
    if os.path.lexists(root / 'latest.json'):
        previous = retained(state)
        # Validate old evidence at its original time, even when now stale.
        from modelfc.acquisition_planner import utc
        from datetime import datetime
        old_bundle = parse_json(previous)
        if not isinstance(old_bundle, dict) or not isinstance(old_bundle.get('retrieved_at'), str):
            raise ValueError('invalid retained calendar timestamp')
        try:
            old_time = utc(datetime.fromisoformat(old_bundle['retrieved_at'].replace('Z', '+00:00')))
        except (ValueError, OverflowError):
            raise ValueError('invalid retained calendar timestamp') from None
        old = parse_calendar(previous, as_of=old_time)
        if old.retrieved_at > calendar.retrieved_at or (old.retrieved_at == calendar.retrieved_at and previous != raw):
            raise ValueError('calendar publication regression/conflict')
        if previous == raw:
            _sync_root(root)
            return
    # Bound this write and stop accumulating before exhausting shared storage.
    # JSON may escape non-ASCII source text, so measure the actual serialized size.
    record = {'raw': raw.decode('utf-8')}
    required = len((json.dumps(record, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    space = os.statvfs(root)
    if (space.f_bavail * space.f_frsize < MIN_FREE_BYTES + required + 4096
            or space.f_favail < MIN_FREE_INODES + 2):
        raise OSError('calendar retention capacity unavailable')
    if os.path.lexists(path):
        if parse_json(read(path, 8 * MAX_CALENDAR_BYTES)) != {'raw': raw.decode('utf-8')}:
            raise ValueError('calendar publication conflict')
    else:
        write_new_record(path, {'raw': raw.decode('utf-8')})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=False, mode='w') as target:
            temporary = Path(target.name)
            json.dump({'bundle_sha256': calendar.bundle_sha256}, target)
            target.flush(); os.fsync(target.fileno())
        os.replace(temporary, root / 'latest.json')
        _sync_root(root)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)

