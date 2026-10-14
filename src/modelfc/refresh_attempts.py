"""Opt-in durable UTC-day caps shared by all managed CSV refresh entrypoints.

The operator installs the cap marker only during separately approved activation.
All calls run under corner_refresh.refresh_lock; reservations survive any exit.
"""
from datetime import datetime, timezone
import json
import os
import stat

from modelfc.ledger_storage import write_new_record
from modelfc.odds_calendar import _sync_root

MARKER = 'daily-attempt-cap.json'


def enabled(state):
    path = state / MARKER
    if not os.path.lexists(path):
        return False
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
        raise ValueError('invalid daily attempt cap control')
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'version', 'enabled'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['enabled']) is not bool):
        raise ValueError('invalid daily attempt cap control')
    return value['enabled']


def reserve(state, league, trigger):
    now = datetime.now(timezone.utc)
    root = state / 'attempts' / now.date().isoformat()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.is_symlink() or root.parent.is_symlink():
        raise ValueError('invalid attempt directory')
    path = root / f'{league}-started.json'
    if os.path.lexists(path) or os.path.lexists(root / f'{league}-finished.json'):
        # Even damaged/interrupted reservations consume the cap. Never retry.
        return root / f'{league}-finished.json', False
    record = {'version': 1, 'league': league, 'utc_day': now.date().isoformat(),
              'started_at_utc': now.isoformat(), 'trigger': trigger,
              'status': 'RESERVED', 'max_get_attempts': 1}
    write_new_record(path, record)
    _sync_root(root); _sync_root(root.parent); _sync_root(state)
    return root / f'{league}-finished.json', True


def finish(path, result):
    write_new_record(path, {'finished_at_utc': datetime.now(timezone.utc).isoformat(), **result})
    _sync_root(path.parent)
