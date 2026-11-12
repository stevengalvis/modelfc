"""Explicit, immutable OddsPapi metadata imports and offline shared-response reuse.

No credentials, provider client, latest pointer, eviction, scheduler or HTTP path.
"""
import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from modelfc.acquisition_storage import private_store, encoded, fingerprint
from modelfc.acquisition_planner import utc
from modelfc.oddspapi_research_metadata import filter_metadata, MAX_SOURCE_BYTES, FILTER_VERSION
from modelfc.oddspapi_market_inventory import _read_regular, MAX_METADATA_BYTES, _metadata_index
from modelfc.providers.oddspapi_saved_response import _json, MAX_RESPONSE_BYTES
from modelfc.shared_odds import snapshot_from_bytes, OddsBatchSnapshot

VERSION = 'oddspapi-metadata-cache-v1'
MAX_AGE = timedelta(days=7)


class MetadataCacheRejected(ValueError):
    """Fixed local diagnostic, never source content or credentials."""


def _time(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError
        return utc(datetime.fromisoformat(value.replace('Z', '+00:00')))
    except (ValueError, OverflowError):
        raise MetadataCacheRejected('METADATA_TIMESTAMP_REJECTED') from None


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise MetadataCacheRejected('METADATA_PIN_REJECTED')
    return value


def import_metadata(cache_dir, raw, *, source_sha256, retrieved_at, observed_at):
    """Preserve the real source retrieval time, including for historical imports.

    Reimporting the same provenance is idempotent. A new retrieval gets a distinct
    entry even when its dictionary bytes match. Import time is never retrieval time.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise MetadataCacheRejected('METADATA_SOURCE_REJECTED')
    if fingerprint(raw) != _hash(source_sha256):
        raise MetadataCacheRejected('METADATA_PIN_MISMATCH')
    retrieved = utc(retrieved_at)
    if retrieved > utc(observed_at):
        raise MetadataCacheRejected('METADATA_FUTURE_RETRIEVAL')
    metadata, _ = filter_metadata(raw)
    provenance = {'version': VERSION, 'provider': 'oddspapi', 'filter_version': FILTER_VERSION,
                  'source_sha256': source_sha256, 'metadata_sha256': fingerprint(metadata),
                  'retrieved_at_utc': retrieved.isoformat()}
    identity = fingerprint(encoded(provenance))
    record = {**provenance, 'metadata_base64': base64.b64encode(metadata).decode('ascii')}
    with private_store(cache_dir) as store:
        try:
            store.put(f'metadata-{identity}.json', record)
        except FileExistsError:
            # Concurrent identical import may win the atomic link. Never replace it.
            if store.get(f'metadata-{identity}.json') != record:
                raise MetadataCacheRejected('METADATA_CACHE_CONFLICT') from None
    return identity


@dataclass(frozen=True)
class CachedMetadata:
    entry_id: str
    retrieved_at: datetime
    metadata_sha256: str
    raw: bytes


def load_metadata(cache_dir, entry_id, *, as_of):
    """Explicit pinned entry only; fail closed on absence/staleness, never fetch."""
    identity = _hash(entry_id)
    with private_store(cache_dir) as store:
        record = store.get(f'metadata-{identity}.json')
    if record is None:
        raise MetadataCacheRejected('METADATA_CACHE_MISSING')
    fields = {'version', 'provider', 'filter_version', 'source_sha256',
              'metadata_sha256', 'retrieved_at_utc', 'metadata_base64'}
    try:
        if set(record) != fields or (record['version'], record['provider'], record['filter_version']) != (
                VERSION, 'oddspapi', FILTER_VERSION):
            raise ValueError
        provenance = {k: v for k, v in record.items() if k != 'metadata_base64'}
        if fingerprint(encoded(provenance)) != identity:
            raise ValueError
        _hash(record['source_sha256']); _hash(record['metadata_sha256'])
        raw = base64.b64decode(record['metadata_base64'], validate=True)
        if not 0 < len(raw) <= MAX_METADATA_BYTES or fingerprint(raw) != record['metadata_sha256']:
            raise ValueError
        value = _json(raw)
        _metadata_index(value)
        if value['source_sha256'] != record['source_sha256'] or value['filter_version'] != FILTER_VERSION:
            raise ValueError
        retrieved = _time(record['retrieved_at_utc'])
        age = utc(as_of) - retrieved
    except (ValueError, TypeError, KeyError, OverflowError):
        raise MetadataCacheRejected('METADATA_CACHE_REJECTED') from None
    if age < timedelta(0):
        raise MetadataCacheRejected('METADATA_FUTURE_RETRIEVAL')
    if age > MAX_AGE:
        raise MetadataCacheRejected('METADATA_CACHE_STALE')
    return CachedMetadata(identity, retrieved, record['metadata_sha256'], raw)


@dataclass(frozen=True)
class CachedReplay:
    metadata_entry_id: str
    metadata_retrieved_at: datetime
    snapshot: OddsBatchSnapshot


def replay_cached_response(cache_dir, entry_id, response_file, *, response_sha256, retrieved_at):
    """Replay at original price retrieval time, not a claim of prices usable today.

    E1/E0 inventory shares one FanDuel response and metadata version. All existing
    per-market quote timestamps, states and one-pass normalization are preserved.
    """
    metadata = load_metadata(cache_dir, entry_id, as_of=retrieved_at)
    payload = _read_regular(Path(response_file), MAX_RESPONSE_BYTES, 'RESPONSE_REJECTED')
    if fingerprint(payload) != _hash(response_sha256):
        raise MetadataCacheRejected('RESPONSE_PIN_MISMATCH')
    snapshot = snapshot_from_bytes(payload, metadata.raw, retrieved_at=retrieved_at)
    return CachedReplay(metadata.entry_id, metadata.retrieved_at, snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--retrieved-at', required=True)
    args = parser.parse_args()
    try:
        identity = import_metadata(args.cache_dir,
            _read_regular(args.source, MAX_SOURCE_BYTES, 'METADATA_SOURCE_REJECTED'),
            source_sha256=args.source_sha256, retrieved_at=_time(args.retrieved_at),
            observed_at=datetime.now(timezone.utc))
    except (OSError, ValueError, TypeError, OverflowError):
        parser.exit(1, 'METADATA_CACHE_IMPORT_REJECTED\n')
    print(f'metadata_entry_id={identity} provider_requests=0')


if __name__ == '__main__':
    main()
