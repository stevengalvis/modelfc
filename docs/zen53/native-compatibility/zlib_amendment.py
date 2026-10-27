"""Explicit authenticated one-library amendment; no discovery or host reads."""
import hashlib
from pathlib import Path

ROUTE = '/usr/lib/x86_64-linux-gnu/libz.so.1.3'
TARGET = 'rootfs' + ROUTE
OLD = '86200da370f20476a2507e9097a789b5ef97269b4ca8d5e164ad82dab9d99892'
NEW = 'f61677c7bdb7c2aa2a243ea781bfd16afcb361cede7b4ef76127a9d8eb9a5242'
PROOF_SHA256 = 'cbf45d6ddf647ad2b561cbcb63516e51cbc395a7470b1b28065ee0feabf3eda9'


def proof():
    data = Path(__file__).with_name('NATIVE_ZLIB_PROVENANCE.json').read_bytes()
    if hashlib.sha256(data).hexdigest() != PROOF_SHA256:
        raise ValueError('ZLIB_PROVENANCE_PIN')


def amend(pins, dependencies):
    """Change only the exact old hash; refuse already-amended or foreign input."""
    proof()
    record = dependencies['dependencies'][ROUTE]
    if (pins['files'][ROUTE] != OLD or record['sha256'] != OLD or
        record['source'] != ROUTE or record['resolved_source'] != ROUTE or
        (record['source_uid'], record['source_gid'], record['source_mode'], record['mode']) !=
        (0, 0, '0o644', '0o444')):
        raise ValueError('ZLIB_HISTORICAL_PIN')
    pins['files'][ROUTE] = NEW
    record['sha256'] = NEW


def amend_manifest(manifest):
    proof()
    if manifest[TARGET] != OLD:
        raise ValueError('ZLIB_MANIFEST_PIN')
    manifest[TARGET] = NEW


def amend_recovery(recovery):
    """Original recovery bytes remain pinned; historical metadata stays old."""
    proof()
    rows = [recovery['manifest'][TARGET], recovery['dependencies'][ROUTE]]
    if not all(row['kind'] == 'installed' and row['source'] == ROUTE and
               row['sha256'] == OLD and row['historical_source_metadata']['sha256'] == OLD
               for row in rows):
        raise ValueError('ZLIB_RECOVERY_PIN')
    for row in rows:
        row['sha256'] = NEW
