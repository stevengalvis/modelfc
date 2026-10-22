"""Private acquisition evidence retained at the existing CSV fetch boundary.

Only the refresh writer publishes, under refresh.lock. Hashes bind saved bytes;
they are not a substitute for the trusted HTTPS acquisition/host boundary.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from modelfc.btts_market_data import timestamp
from modelfc.corner_analysis_outcomes import _require
from modelfc.ledger_storage import write_new_record
from modelfc.odds_calendar import read, _sync_root, MIN_FREE_BYTES, MIN_FREE_INODES
from modelfc.pinned_evidence import read_pinned
from modelfc.providers.oddspapi_saved_response import _json
import os
import stat

HEADERS = ('Date', 'Last-Modified', 'ETag', 'Content-Type')
LIMIT = 2_000_000


def validate(metadata, raw, *, as_of):
    _require(isinstance(metadata, dict) and set(metadata) == {
        'schema_version', 'url', 'response_url', 'http_status', 'headers',
        'requested_at_utc', 'retrieved_at_utc', 'filename', 'file_sha256'}, 'INVALID_REFRESH_EVIDENCE')
    name = metadata['filename']
    _require(isinstance(name, str) and re.fullmatch(r'E1_[0-9]{4}\.csv', name), 'INVALID_REFRESH_EVIDENCE')
    # Accept the published direct link and previously accepted, pinned acquisitions.
    # Both request and response must use the SAME exact URL; no redirect aliases.
    urls = {f'https://{host}/mmz4281/{name[3:7]}/E1.csv'
            for host in ('football-data.co.uk', 'www.football-data.co.uk')}
    _require(type(metadata['schema_version']) is int and metadata['schema_version'] == 1
        and isinstance(metadata['url'], str) and metadata['url'] in urls
        and metadata['url'] == metadata['response_url']
        and type(metadata['http_status']) is int and metadata['http_status'] == 200
        and timestamp(metadata['requested_at_utc']) <= timestamp(metadata['retrieved_at_utc']) <= as_of
        and isinstance(raw, bytes) and 0 < len(raw) <= LIMIT
        and hashlib.sha256(raw).hexdigest() == metadata['file_sha256'], 'INVALID_REFRESH_EVIDENCE')
    headers = metadata['headers']
    _require(isinstance(headers, dict) and set(headers) <= set(HEADERS)
        and all(isinstance(v, str) and len(v) <= 4096 and not any(ord(c) < 32 for c in v)
                for v in headers.values()), 'INVALID_REFRESH_EVIDENCE')


def retain(state, metadata, raw):
    validate(metadata, raw, as_of=datetime.now(timezone.utc))
    root = Path(state) / 'result-evidence'
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o700, 'INVALID_REFRESH_EVIDENCE')
    record = {'metadata': metadata, 'csv_base64': base64.b64encode(raw).decode('ascii')}
    encoded = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode()
    pin = hashlib.sha256(encoded).hexdigest()
    path = root / (pin + '.json')
    if path.exists():
        _require(read(path, 4 * LIMIT) == encoded, 'INVALID_REFRESH_EVIDENCE')
    else:
        space = os.statvfs(root)
        _require(space.f_bavail * space.f_frsize >= MIN_FREE_BYTES + len(encoded)
                 and space.f_favail >= MIN_FREE_INODES + 1, 'REFRESH_EVIDENCE_CAPACITY')
        write_new_record(path, record)
    _sync_root(root); _sync_root(Path(state))
    return pin


def load(state, pin, *, as_of):
    _require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin), 'INVALID_REFRESH_EVIDENCE')
    value = _json(read_pinned(Path(state) / 'result-evidence' / (pin + '.json'), pin, 4 * LIMIT))
    _require(isinstance(value, dict) and set(value) == {'metadata', 'csv_base64'}, 'INVALID_REFRESH_EVIDENCE')
    raw = base64.b64decode(value['csv_base64'], validate=True)
    validate(value['metadata'], raw, as_of=as_of)
    return value['metadata'], raw
