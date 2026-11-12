"""Offline persistent metadata and shared E1/E0 replay; all data synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.metadata_cache import import_metadata, load_metadata, replay_cached_response, MetadataCacheRejected
from modelfc.acquisition_planner import Fixture, plan_acquisition, MAX_TOURNAMENTS
from modelfc.offline_forecasts import KnownFixture, prepare_forecast, prepare_plan, consume_forecast
from modelfc.providers.oddspapi_saved_response import ReplayError
from tests.test_shared_odds import sources
from tests.test_oddspapi_saved_response import saved_fixture
from tests.oddspapi_inventory_fixtures import NOW, metadata


def e0_sources(extra=''):
    rows = ['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC']
    for index in range(99):
        day = date(2026, 7, 1) + timedelta(days=index)
        home, away = ('Home','Away') if index % 2 else ('Away','Home')
        rows.append(f'E0,{day:%d/%m/%Y},{home},{away},2,1,{4+index%3},{3+index%2}')
    return (('E0_2627.csv', ('\n'.join(rows)+'\n'+extra).encode()),)


class MetadataCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = json.dumps(metadata()).encode()
        self.pin = hashlib.sha256(self.raw).hexdigest()

    def put(self, retrieved=NOW):
        return import_metadata(self.root, self.raw, source_sha256=self.pin,
                               retrieved_at=retrieved, observed_at=NOW)

    def test_persistent_idempotent_import_and_strict_expiry(self):
        with patch('socket.socket', side_effect=AssertionError('network')):
            identity = self.put()
            path = self.root/f'metadata-{identity}.json'
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            self.assertEqual(self.put(), identity)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(load_metadata(self.root, identity, as_of=NOW).retrieved_at, NOW)
            load_metadata(self.root, identity, as_of=NOW+timedelta(days=7))
            with self.assertRaisesRegex(MetadataCacheRejected, 'STALE'):
                load_metadata(self.root, identity, as_of=NOW+timedelta(days=7, microseconds=1))
            with self.assertRaisesRegex(MetadataCacheRejected, 'FUTURE'):
                load_metadata(self.root, identity, as_of=NOW-timedelta(seconds=1))
            self.assertNotEqual(self.put(NOW-timedelta(days=1)), identity)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_imports_reuse_one_version(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            identities = list(pool.map(lambda _: self.put(), range(8)))
        self.assertEqual(len(set(identities)), 1)
        self.assertEqual(len(list(self.root.glob('metadata-*.json'))), 1)

    def test_pins_missing_malformed_and_symlinks_fail_closed(self):
        with self.assertRaisesRegex(MetadataCacheRejected, 'MISSING'):
            load_metadata(self.root, '0'*64, as_of=NOW)
        with self.assertRaisesRegex(MetadataCacheRejected, 'PIN_MISMATCH'):
            import_metadata(self.root, self.raw, source_sha256='0'*64, retrieved_at=NOW, observed_at=NOW)
        identity=self.put(); path=self.root/f'metadata-{identity}.json'
        original=path.read_bytes(); path.write_bytes(original.replace(b'oddspapi', b'changed!'))
        with self.assertRaises(ValueError): load_metadata(self.root,identity,as_of=NOW)
        path.unlink(); target=self.root/'other.json'; target.write_bytes(original);path.symlink_to(target)
        with self.assertRaises(OSError): load_metadata(self.root,identity,as_of=NOW)

    def test_one_response_e1_e0_corner_and_e1_btts_reuse(self):
        identities = [Fixture('E1',18,'e1',NOW+timedelta(hours=24)), Fixture('E0',17,'e0',NOW+timedelta(hours=24))]
        known = tuple(KnownFixture(f,'Home','Away') for f in identities)
        plan=plan_acquisition({'E1':[identities[0]],'E0':[identities[1]]},as_of=NOW)
        self.assertEqual(len(plan.batches),1);self.assertEqual(MAX_TOURNAMENTS,5)
        with patch('socket.socket', side_effect=AssertionError('network')):
            prepared=prepare_plan(plan,known,{'E1':sources(),'E0':e0_sources()},
                frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date(),models=('corner',))
            btts=prepare_forecast(known[0],sources(),frozen_at=NOW-timedelta(seconds=1),cutoff=NOW.date(),models=('btts',))
            entry=self.put(NOW-timedelta(days=1))
            rows=[saved_fixture(f.tournament_id,f.fixture_id) for f in identities]
            for row,f in zip(rows,identities):
                row['startTime']=f.kickoff_utc.isoformat()
                for market in row['bookmakerOdds']['fanduel']['markets'].values():
                    for outcome in market['outcomes'].values():
                        for player in outcome['players'].values():
                            player['priceAmerican']='-111' if player['price']==1.9 else '+100'
            path=self.root/'response.json';path.write_text(json.dumps(rows));pin=hashlib.sha256(path.read_bytes()).hexdigest()
            replay=replay_cached_response(self.root,entry,path,response_sha256=pin,retrieved_at=NOW)
            snapshot=replay.snapshot
            self.assertEqual({f.identity.competition for f in snapshot.fixtures},{'E1','E0'})
            for forecast in prepared:
                f=next(f for f in snapshot.fixtures if f.identity==forecast.fixture.identity)
                result=consume_forecast(forecast,snapshot,f)
                self.assertEqual(result.corner_status,'PASS');self.assertEqual(len(result.corner),4)
                self.assertEqual(result.btts_status,'NOT_REQUESTED')
                self.assertEqual(consume_forecast(forecast,snapshot,f),result)
            e1=next(f for f in snapshot.fixtures if f.identity.competition=='E1')
            self.assertEqual(len(consume_forecast(btts,snapshot,e1).btts),1)
            self.assertEqual(replay.metadata_entry_id,entry)
            with self.assertRaisesRegex(MetadataCacheRejected,'RESPONSE_PIN_MISMATCH'):
                replay_cached_response(self.root,entry,path,response_sha256='0'*64,retrieved_at=NOW)
            rows[0]['bookmakerOdds']['draftkings']=rows[0]['bookmakerOdds']['fanduel']
            path.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ReplayError,'UNSUPPORTED_BOOKMAKER'):
                replay_cached_response(self.root,entry,path,response_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),retrieved_at=NOW)

    def test_e0_btts_stale_history_future_leakage_and_identity_gates(self):
        fixture=KnownFixture(Fixture('E0',17,'e0',NOW+timedelta(hours=24)),'Home','Away')
        for models in (('btts',),('corner','btts')):
            forecast=prepare_forecast(fixture,(),frozen_at=NOW,cutoff=NOW.date(),models=models)
            self.assertEqual(forecast.status,'UNSUPPORTED_MODEL');self.assertIsNone(forecast.btts)
        baseline=prepare_forecast(fixture,e0_sources(),frozen_at=NOW,cutoff=NOW.date(),models=('corner',))
        with self.assertRaisesRegex(ValueError, 'FORECAST_SOURCE_COMPETITION_MISMATCH'):
            replace(baseline, fixture=KnownFixture(Fixture('E1',18,'e1',NOW+timedelta(hours=24)),'Home','Away'))
        extra=f'E0,{NOW:%d/%m/%Y},Home,Away,99,99,99,99\nE0,09/10/2026,Home,Away,99,99,99,99\n'
        changed=prepare_forecast(fixture,e0_sources(extra),frozen_at=NOW,cutoff=NOW.date(),models=('corner',))
        self.assertEqual(baseline.corner,changed.corner)
        with self.assertRaises(ValueError):
            prepare_forecast(fixture,sources(),frozen_at=NOW,cutoff=NOW.date(),models=('corner',))
        stale=(('E0_2627.csv',sources()[0][1].replace(b'E1',b'E0')),)
        with self.assertRaisesRegex(ValueError,'E0_HISTORY_STALE'):
            prepare_forecast(fixture,stale,frozen_at=NOW,cutoff=NOW.date(),models=('corner',))
        with self.assertRaisesRegex(ValueError,'E0_COMPLETENESS_CALENDAR_UNSUPPORTED'):
            prepare_forecast(fixture,e0_sources(),frozen_at=NOW,cutoff=NOW.date(),models=('corner',),history_calendar=object())
