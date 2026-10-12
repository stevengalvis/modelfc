"""Private retained calendar bridge; no network and no provider-ID substitution.

Publisher and collector must hold the existing shared prospective runner lock.
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


class CalendarGateRejected(ValueError):
    pass


def directory(state, *, create=False):
    state = Path(state)
    root = state / 'odds-calendar'
    for path in (state, root):
        if path == root and create:
            root.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022 or info.st_uid != os.geteuid():
            raise ValueError('unsafe calendar directory')
    return root


def read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('unsafe calendar file')
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('calendar too large')
        return raw


def retained(state):
    root = directory(state)
    pin = json.loads(read(root / 'latest.json', 256))
    if not isinstance(pin, dict) or set(pin) != {'bundle_sha256'} or not isinstance(pin['bundle_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', pin['bundle_sha256']):
        raise ValueError('invalid calendar pin')
    record = json.loads(read(root / (pin['bundle_sha256'] + '.json'), 8 * MAX_CALENDAR_BYTES))
    if not isinstance(record, dict) or set(record) != {'raw'} or not isinstance(record['raw'], str):
        raise ValueError('invalid calendar record')
    raw = record['raw'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != pin['bundle_sha256']:
        raise ValueError('calendar pin mismatch')
    return raw


def publish(state, raw, *, as_of):
    """Retain original bytes before the forecast runner's no-eligible early return."""
    calendar = parse_calendar(raw, as_of=as_of)
    root = directory(state, create=True)
    path = root / (calendar.bundle_sha256 + '.json')
    if (root / 'latest.json').exists():
        previous = retained(state)
        # Validate old evidence at its original time, even when now stale.
        from modelfc.acquisition_planner import utc
        from datetime import datetime
        old_time = utc(datetime.fromisoformat(json.loads(previous)['retrieved_at'].replace('Z', '+00:00')))
        old = parse_calendar(previous, as_of=old_time)
        if old.retrieved_at > calendar.retrieved_at or (old.retrieved_at == calendar.retrieved_at and previous != raw):
            raise ValueError('calendar publication regression/conflict')
        if previous == raw:
            return
    if path.exists():
        if json.loads(read(path, 8 * MAX_CALENDAR_BYTES)) != {'raw': raw.decode('utf-8')}:
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
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def load(state, *, as_of):
    try:
        raw = retained(state)
        return raw, parse_calendar(raw, as_of=as_of)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise CalendarGateRejected('CALENDAR_EVIDENCE_UNAVAILABLE') from None


def active(calendar, as_of):
    return {(m.kickoff, m.home, m.away) for m in calendar.matches
            if m.status in {'SCHEDULED', 'TIMED'} and m.kickoff > as_of
            and m.kickoff.date() == as_of.date()}


def identity(fixture):
    return fixture.kickoff_utc, fixture.home_team, fixture.away_team
