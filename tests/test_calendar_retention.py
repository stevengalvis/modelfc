"""Private producer acceptance with synthetic bytes; all transports are offline."""
from datetime import timedelta
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modelfc import odds_calendar as store
from modelfc.acquisition_storage import shared_runner
from tests import test_forecast_only_runner as fixtures
from tests.test_history_completeness import NOW, bundle, raw


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ForecastOnlyRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state
        self.content = raw(bundle())
        self.root = self.state / 'odds-calendar'
        self.network = patch.object(socket, 'socket', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def publish(self, content=None):
        with shared_runner(self.state):
            store.publish(self.state, content or self.content, as_of=NOW)

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.glob('*.json')}

    def test_source_bytes_hash_time_identity_and_private_modes(self):
        self.publish()
        self.assertEqual(store.retained(self.state), self.content)
        saved = json.loads(store.retained(self.state))
        self.assertEqual(saved['source'], 'football-data.org')
        self.assertEqual(saved['retrieved_at'], bundle()['retrieved_at'])
        self.assertEqual(saved['payload'].encode(), bundle()['payload'].encode())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.root.iterdir()))
        self.assertTrue((self.root / (hashlib.sha256(self.content).hexdigest()+'.json')).exists())
        before = self.snapshot()
        self.publish()
        self.assertEqual(self.snapshot(), before)

    def test_conflict_regression_tamper_invalid_input_and_symlink(self):
        self.publish()
        before = self.snapshot()
        conflict = bundle(); conflict['payload'] += ' '
        with self.assertRaises(ValueError): self.publish(raw(conflict))
        older = bundle(); older['retrieved_at'] = (NOW-timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError): self.publish(raw(older))
        with self.assertRaises(ValueError): self.publish(b'{}')
        self.assertEqual(self.snapshot(), before)
        record = self.root / (hashlib.sha256(self.content).hexdigest()+'.json')
        record.write_text('{}')
        with self.assertRaises(ValueError): self.publish()
        record.unlink(); record.symlink_to(self.state/'prospective/control.json')
        with self.assertRaises(OSError): self.publish()

    def test_unsafe_directory_and_file_modes_rejected_without_chmod(self):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        self.root.rmdir(); self.root.symlink_to(self.state/'prospective', target_is_directory=True)
        with self.assertRaises(ValueError): self.publish()
        self.root.unlink(); self.publish()
        pin = self.root/'latest.json'; pin.chmod(0o644)
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(pin.stat().st_mode & 0o777, 0o644)

    def test_capacity_guard_preserves_shared_state_and_old_pin(self):
        self.publish()
        before = self.snapshot()
        newer = bundle(); newer['retrieved_at'] = (NOW+timedelta(seconds=1)).isoformat()
        for free_bytes, free_inodes in [(0, 1000), (10**12, 0)]:
            with self.subTest(free_bytes=free_bytes), patch.object(store.os, 'statvfs',
                    return_value=SimpleNamespace(f_bavail=free_bytes, f_frsize=1, f_favail=free_inodes)):
                with self.assertRaises(OSError):
                    store.publish(self.state, raw(newer), as_of=NOW+timedelta(seconds=1))
            self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.state/'prospective/control.json').read_text(), '{}')

    def test_atomic_failure_before_pin_keeps_previous_bytes_and_retry_works(self):
        self.publish(); before = self.snapshot()
        newer = bundle(); newer['retrieved_at'] = (NOW+timedelta(seconds=1)).isoformat()
        content = raw(newer)
        with patch.object(store.os, 'replace', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError):
                store.publish(self.state, content, as_of=NOW+timedelta(seconds=1))
        self.assertEqual(store.retained(self.state), self.content)
        self.assertTrue(all(self.snapshot()[name] == value for name, value in before.items()))
        self.assertFalse(any(not p.name.endswith('.json') for p in self.root.iterdir()))
        store.publish(self.state, content, as_of=NOW+timedelta(seconds=1))
        self.assertEqual(store.retained(self.state), content)
        self.assertEqual(self.snapshot()[hashlib.sha256(self.content).hexdigest()+'.json'],
                         before[hashlib.sha256(self.content).hexdigest()+'.json'])

    def test_write_permission_and_full_filesystem_failure_leave_no_pin(self):
        for error in (PermissionError(errno.EACCES, 'private'), OSError(errno.ENOSPC, 'full')):
            with self.subTest(error=error), patch.object(store, 'write_new_record', side_effect=error):
                with self.assertRaises(OSError): self.publish()
            self.assertFalse((self.root/'latest.json').exists())
        self.publish()

    def test_directory_sync_failure_retry_and_no_false_durability(self):
        sync = os.fsync
        for failure_path in (self.state, self.root):
            with self.subTest(path=failure_path):
                def fail(fd):
                    if Path(os.readlink(f'/proc/self/fd/{fd}')) == failure_path:
                        raise OSError(errno.ENOSPC, 'full')
                    sync(fd)
                with patch.object(store.os, 'fsync', side_effect=fail):
                    with self.assertRaises(OSError): self.publish()
                self.publish()
                self.assertEqual(store.retained(self.state), self.content)

    def test_no_eligible_retains_without_extra_requests_and_receipt_has_provenance(self):
        result = self.fixture.execute(when=NOW+timedelta(hours=6))
        self.assertEqual(result['status'], 'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(result['calendar_retention']['status'], 'RETAINED')
        self.assertEqual(result['calendar_retention']['source_sha256'],
                         hashlib.sha256(bundle()['payload'].encode()).hexdigest())
        self.assertEqual(store.retained(self.state), self.content)
        self.assertEqual(result['provider_requests'], {'football_data_org':1, 'sportmonks':0, 'oddspapi':0})

    def test_retention_failure_does_not_stop_eligible_or_no_eligible_forecasts(self):
        for when, status in ((NOW, 'RECORDED'), (NOW+timedelta(hours=6), 'NO_ELIGIBLE_FIXTURES')):
            with self.subTest(status=status), patch.object(store, 'write_new_record',
                    side_effect=OSError(errno.ENOSPC, 'private path must not leak')):
                result = self.fixture.execute(when=when)
            self.assertEqual(result['status'], status, result)
            self.assertEqual(result['calendar_retention']['status'], 'UNAVAILABLE')
            self.assertNotIn('private path', json.dumps(result))
            self.assertEqual(result['provider_requests'], {'football_data_org':1, 'sportmonks':0, 'oddspapi':0})
        self.assertFalse((self.root/'latest.json').exists())

    def test_forecast_bytes_unchanged_and_shared_lock_blocks_all_work(self):
        first = self.fixture.execute()
        before = {p: p.read_bytes() for p in self.state.rglob('*.json')
                  if p.parent.name != 'forecast-only-runs'}
        second = self.fixture.execute(when=NOW+timedelta(minutes=1))
        self.assertEqual(first['forecasts_prepared'], second['forecasts_reused'])
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))
        with shared_runner(self.state):
            result = self.fixture.execute(when=NOW+timedelta(minutes=2))
        self.assertEqual(result['reason'], 'BUSY')
        self.assertEqual(result['calendar_retention']['status'], 'NOT_ATTEMPTED')
        self.assertEqual(sum(result['provider_requests'].values()), 0)

    def test_eligible_original_forecasts_identical_with_and_without_retention(self):
        other = fixtures.ForecastOnlyRunnerTests()
        other.setUp(); self.addCleanup(other.doCleanups)
        with patch('modelfc.forecast_only_runner.publish_odds_calendar'):
            baseline = other.execute()
        result = self.fixture.execute()
        self.assertEqual(result['forecasts_prepared'], baseline['forecasts_prepared'])
        for directory in ('research-predictions', 'prepared-forecasts'):
            before = {p.name:p.read_bytes() for p in (other.state/directory).glob('*.json')}
            after = {p.name:p.read_bytes() for p in (self.state/directory).glob('*.json')}
            self.assertEqual(after, before)
        self.assertTrue(list((self.state/'research-predictions').glob('*.json')))

    def test_real_readonly_writer_directory_preserved(self):
        if os.geteuid() == 0:
            self.skipTest('root bypasses ordinary DAC; real runtime writer acceptance is separate')
        self.root.mkdir(mode=0o700)
        # Owner remains correct, but no write permission. Validation rejects this
        # unsafe mode before any attempted write; never chmod it back for the user.
        self.root.chmod(0o500)
        self.addCleanup(self.root.chmod, 0o700)
        result = self.fixture.execute(when=NOW+timedelta(hours=6))
        self.assertEqual(result['status'], 'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(result['calendar_retention']['status'], 'UNAVAILABLE')
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o500)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_record_link_failure_cleans_temporary_without_publishing_pin(self):
        with patch.object(store.os, 'link', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(ValueError): self.publish()
        self.assertEqual(list(self.root.iterdir()), [])
        self.publish()

    def test_dangling_pin_is_not_silently_repaired(self):
        self.root.mkdir(mode=0o700)
        pin = self.root/'latest.json'; pin.symlink_to(self.root/'missing')
        with self.assertRaises(OSError): self.publish()
        self.assertTrue(pin.is_symlink())
        self.assertEqual(list(self.root.iterdir()), [pin])

    def test_hash_consistent_malformed_old_timestamp_cannot_stop_forecasts(self):
        self.root.mkdir(mode=0o700)
        malformed = [{'retrieved_at': value} for value in (42, None, [], {}, 'invalid', '9999-12-31T23:59:59-12:00')]
        malformed.extend([[], 42, {}])
        for index, value in enumerate(malformed):
            with self.subTest(value=value):
                previous = json.dumps(value).encode()
                digest = hashlib.sha256(previous).hexdigest()
                record = self.root/(digest+'.json')
                record.write_text(json.dumps({'raw': previous.decode()})); record.chmod(0o600)
                pin = self.root/'latest.json'
                pin.write_text(json.dumps({'bundle_sha256':digest})); pin.chmod(0o600)
                before = self.snapshot()
                result = self.fixture.execute(when=NOW+timedelta(minutes=index))
                self.assertEqual(result['status'], 'RECORDED', result)
                self.assertEqual(result['calendar_retention']['status'], 'UNAVAILABLE')
                self.assertEqual(result['provider_requests'],
                                 {'football_data_org':1, 'sportmonks':0, 'oddspapi':0})
                self.assertEqual(self.snapshot(), before)

    def test_actual_module_entrypoint_with_recorded_transport(self):
        credentials = self.fixture.root/'credentials'; credentials.mkdir()
        (credentials/'forecast-only.json').write_text(json.dumps(self.fixture.configuration))
        (credentials/'football-data.key').write_text('calendar-token')
        (credentials/'sportmonks.key').write_text('MODELFC_SPORTMONKS_NOT_CONFIGURED')
        script = '''import runpy, socket, sys
from unittest.mock import patch
from tests.test_forecast_only_runner import _CalendarClient
from modelfc.providers import football_data_calendar
with patch.object(football_data_calendar, 'FootballDataCalendarClient', _CalendarClient), patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
    sys.argv = ['modelfc.forecast_only_runner', '--state-dir', sys.argv[1], '--data-config', sys.argv[2]]
    runpy.run_module('modelfc.forecast_only_runner', run_name='__main__')
'''
        env = {k:v for k,v in os.environ.items() if 'TOKEN' not in k and 'KEY' not in k}
        env.update(CREDENTIALS_DIRECTORY=str(credentials), PYTHONPATH='src:.')
        result = subprocess.run([sys.executable, '-c', script, str(self.state), str(self.fixture.config_path)],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['calendar_retention']['status'], 'RETAINED')
        self.assertEqual(report['provider_requests'], {'football_data_org':1, 'sportmonks':0, 'oddspapi':0})
        self.assertEqual(store.retained(self.state), self.content)
