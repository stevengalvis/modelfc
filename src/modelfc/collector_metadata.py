"""Explicit pinned metadata for the E1 collector; no network or fetch fallback."""
import json
from pathlib import Path

from modelfc.acquisition_storage import private_store, encoded, fingerprint
from modelfc.btts_research import digest
from modelfc.corner_market_data import MarketDataError
from modelfc.corner_opportunities import _observation_payload, _id
from modelfc.metadata_cache import load_metadata, VERSION
from modelfc.providers.oddspapi import OddsPapiMarketData


class PinnedCollectorMetadata:
    def __init__(self, cache_dir, entry_id, state_dir, clock):
        self.cache_dir, self.entry_id, self.clock = cache_dir, entry_id, clock
        self.uses = Path(state_dir) / 'collector-metadata-uses'
        self.validate()
        # Separate private evidence: never opt into the API's evidence-read ACL.
        self.uses.mkdir(mode=0o700, exist_ok=True)
        with private_store(self.uses):
            pass

    def validate(self):
        try:
            cached = load_metadata(self.cache_dir, self.entry_id, as_of=self.clock())
            envelope = json.loads(cached.raw)
            OddsPapiMarketData._validate_market_metadata(envelope['markets'])
            self.markets = envelope['markets']
            self.provenance = dict(cache_version=VERSION, entry_id=cached.entry_id,
                filter_version=envelope['filter_version'], source_sha256=envelope['source_sha256'],
                metadata_sha256=cached.metadata_sha256,
                metadata_retrieved_at_utc=cached.retrieved_at.isoformat())
        except (OSError, ValueError, TypeError, KeyError):
            raise MarketDataError('MARKET_METADATA_INVALID') from None

    def install(self, client):
        if not isinstance(client, OddsPapiMarketData):
            raise MarketDataError('MARKET_METADATA_INVALID')
        client.enable_pinned_metadata(self.markets)

    def record_use(self, observation):
        snapshot = observation.supplied_snapshot
        payload = _observation_payload(observation)
        record = dict(version='collector-metadata-use-v1', **self.provenance,
            observation_id=_id('market-observation', payload),
            competition=observation.fixture.competition,
            provider_fixture_id=observation.fixture.provider_fixture_id,
            odds_retrieved_at_utc=snapshot.retrieved_at,
            provider_snapshot_sha256=digest(snapshot.payload),
            provider_metadata_sha256=digest(snapshot.metadata))
        with private_store(self.uses) as store:
            store.put(f'use-{fingerprint(encoded(record))}.json', record)
