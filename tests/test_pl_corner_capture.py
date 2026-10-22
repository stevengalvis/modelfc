"""Synthetic offline replay only; never prospective or genuine EPL acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
import io
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import private_store
from modelfc.pl_corner_capture import capture, prepare, RECORD_NAME, main, CaptureWriteUncertain
from tests import test_pl_history_readiness as history_fixture
from tests.test_pl_calendar import NOW, sha


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.data = history_fixture.PLHistoryTests()
        self.data.setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name)

    def run_capture(self, **changes):
        raw, options = self.data.options()
        values = dict(sources=self.data.sources, enabled=True, **options)
        values.update(changes)
        return capture(self.store, raw, **values)

    def test_disabled_does_not_read_or_write(self):
        self.assertEqual(capture('/missing', None)['state'], 'DISABLED')
        self.assertEqual(list(self.store.iterdir()), [])

    def test_positive_model_identity_hashes_and_repeat(self):
        with patch('socket.socket', side_effect=AssertionError('network')), patch('socket.getaddrinfo', side_effect=AssertionError('DNS')):
            first = self.run_capture()
            second = self.run_capture()
        payload = first['payload']
        self.assertEqual(payload['state'], 'CAPTURED')
        self.assertEqual(len(payload['forecasts']), 8)
        self.assertFalse(first['prospective_capture'])
        self.assertEqual(first['write_state'], 'CREATED')
        self.assertEqual(second['write_state'], 'EXISTING')
        self.assertEqual(first['payload_sha256'], second['payload_sha256'])
        self.assertEqual(first['recorded_at'], second['recorded_at'])
        forecast = payload['forecasts'][0]
        self.assertEqual(forecast['identity']['provider'], 'football-data.org')
        self.assertEqual(forecast['identity']['home']['source_team_id'], 1000)
        self.assertEqual(forecast['freeze_as_of'], NOW.isoformat())
        self.assertEqual(forecast['prediction']['historical_observation_count'], 340)
        self.assertEqual(forecast['prediction']['latest_history_date'], '2026-10-16')
        self.assertEqual(len(list(self.store.iterdir())), 1)
        with private_store(self.store) as store:
            self.assertEqual(store.get(RECORD_NAME)['payload'], payload)

    def test_whole_day_blocked_does_not_capture_other_eligible_fixtures(self):
        for index, (name, data, _, retrieved) in enumerate(self.data.sources[:-1]):
            data = data.replace(b'Synthetic History 0,', b'Older Label 0,')
            self.data.sources[index] = name, data, sha(data), retrieved
        with patch('modelfc.pl_corner_capture.predict_corner_fixture', side_effect=AssertionError('must not fit')):
            payload = self.run_capture()['payload']
        self.assertEqual(payload['state'], 'BLOCKED')
        self.assertEqual(payload['forecasts'], [])
        self.assertEqual(len(payload['skips']), 8)
        self.assertIn('HOME_VENUE_HISTORY_INSUFFICIENT', payload['skips'][0]['reasons'])
        self.assertTrue(all('WHOLE_DAY_READINESS_BLOCKED' in s['reasons'] for s in payload['skips']))

    def test_future_and_same_day_results_never_enter_model(self):
        raw, options = self.data.options()
        baseline = prepare(raw, sources=self.data.sources, enabled=True, **options)
        self.data.replace_current(lambda b: b+b'E0,17/10/2026,Other Home,Other Away,1,0,H,999,999\n')
        raw, options = self.data.options()
        changed = prepare(raw, sources=self.data.sources, enabled=True, **options)
        self.assertEqual(changed['model_input_sha256'], baseline['model_input_sha256'])
        self.assertEqual([f['prediction'] for f in changed['forecasts']], [f['prediction'] for f in baseline['forecasts']])
        self.assertEqual(changed['readiness']['excluded_at_or_after_cutoff'], 1)
        self.data.replace_current(lambda b: b.replace(b'17/10/2026', b'18/10/2026'))
        self.assertEqual(self.run_capture()['payload']['state'], 'BLOCKED')

    def test_stale_missing_and_conflicting_evidence(self):
        raw, options = self.data.options()
        for changes in ({'as_of': NOW+timedelta(hours=7)}, {'sources': ()}, {'bundle_sha256': '0'*64},
                        {'reviewed_contract_sha256': '0'*64}):
            opts = dict(sources=self.data.sources, enabled=True, **options)
            opts.update(changes)
            payload = prepare(raw, **opts)
            self.assertEqual(payload['state'], 'BLOCKED')
            self.assertEqual(payload['forecasts'], [])
        self.data.replace_current(lambda b: b.replace(b',1,0,H,', b',2,0,H,', 1))
        self.assertEqual(self.run_capture()['payload']['state'], 'BLOCKED')

    def test_postponed_and_started(self):
        self.data.payload['matches'][0]['status'] = 'POSTPONED'
        payload = self.run_capture()['payload']
        self.assertIn('FIXTURE_NOT_SCHEDULED', payload['skips'][0]['reasons'])
        self.assertEqual(payload['forecasts'], [])
        self.data.payload['matches'][0]['status'] = 'TIMED'
        raw, options = self.data.options()
        options['as_of'] = NOW+timedelta(hours=4)
        payload = prepare(raw, sources=self.data.sources, enabled=True, **options)
        self.assertEqual(payload['forecasts'], [])
        self.assertIn('KICKOFF_NOT_AFTER_OBSERVATION', payload['skips'][0]['reasons'])

    def test_changed_kickoff_time_or_sources_cannot_overwrite(self):
        self.run_capture()
        before = (self.store/RECORD_NAME).read_bytes()
        self.data.payload['matches'][0]['utcDate'] = '2026-10-17T15:00:00Z'
        with self.assertRaisesRegex(ValueError, 'CAPTURE_STORE_CONFLICT'):
            self.run_capture()
        self.assertEqual(before, (self.store/RECORD_NAME).read_bytes())
        self.data.payload['matches'][0]['utcDate'] = '2026-10-17T14:00:00Z'
        with self.assertRaisesRegex(ValueError, 'CAPTURE_STORE_CONFLICT'):
            self.run_capture(as_of=NOW+timedelta(minutes=1))

    def test_corrupt_or_symlinked_store_record_rejects(self):
        self.run_capture()
        path = self.store/RECORD_NAME
        original = path.read_bytes()
        path.write_bytes(original.replace(b'CAPTURED', b'CORRUPT!'))
        with self.assertRaises(ValueError):
            self.run_capture()
        path.unlink()
        (self.store/'other').write_bytes(original)
        path.symlink_to(self.store/'other')
        with self.assertRaises(OSError):
            self.run_capture()

    def test_empty_day_and_model_failure_are_explicit(self):
        for match in self.data.payload['matches'][:8]:
            match['utcDate'] = '2026-10-18T14:00:00Z'
        self.assertEqual(self.run_capture()['payload']['state'], 'EMPTY')
        for match in self.data.payload['matches'][:8]:
            match['utcDate'] = '2026-10-17T14:00:00Z'
        raw, options = self.data.options()
        with patch('modelfc.pl_corner_capture.predict_corner_fixture', side_effect=ValueError('private details')):
            result = prepare(raw, sources=self.data.sources, enabled=True, **options)
        self.assertEqual(result['state'], 'MODEL_REJECTED')
        self.assertEqual(result['forecasts'], [])
        self.assertNotIn('private details', json.dumps(result))

    def test_cli_saved_inputs_idempotence_and_missing_input(self):
        raw, options = self.data.options()
        source_root = self.store/'inputs'
        source_root.mkdir()
        output = self.store/'output'
        output.mkdir(mode=0o700)
        (source_root/'bundle').write_bytes(raw)
        (source_root/'contract').write_bytes(options['contract_raw'])
        args = ['--enable-offline-capture', '--store', str(output),
            '--bundle', str(source_root/'bundle'), '--bundle-sha256', options['bundle_sha256'],
            '--contract', str(source_root/'contract'), '--reviewed-contract-sha256', options['reviewed_contract_sha256'],
            '--season-year', '2026', '--day', '2026-10-17', '--as-of', NOW.isoformat()]
        for name, data, pin, retrieved in self.data.sources:
            (source_root/name).write_bytes(data)
            args += ['--source', str(source_root/name), pin, retrieved.isoformat()]
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source_root.iterdir()}
        for expected in ('CREATED', 'EXISTING'):
            stream = io.StringIO()
            with redirect_stdout(stream), patch('socket.socket', side_effect=AssertionError('network')):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(stream.getvalue())['write_state'], expected)
            self.assertNotIn(str(source_root), stream.getvalue())
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source_root.iterdir()})
        args[-3] = str(source_root/'missing.csv')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 1)
            self.assertEqual(main(args[1:]), 1)

    def test_generator_source_binding_matches_tuple(self):
        first = self.run_capture(sources=iter(self.data.sources))
        second = self.run_capture()
        self.assertEqual(first['payload_sha256'], second['payload_sha256'])
        self.assertEqual(second['write_state'], 'EXISTING')

    def test_write_timestamp_is_sampled_after_model_work(self):
        with patch('modelfc.pl_corner_capture.now', side_effect=[NOW, NOW+timedelta(hours=5)]):
            record = self.run_capture()
        self.assertFalse(record['recorded_before_all_kickoffs'])
        self.assertEqual(record['recorded_at'], (NOW+timedelta(hours=5)).isoformat())
        self.assertEqual(record['recorded_at_semantics'], 'WRITE_START_NOT_DURABILITY_COMPLETION')

    def test_post_link_fsync_failure_has_uncertain_outcome(self):
        real_fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('simulated directory fsync failure')
            return real_fsync(fd)
        with patch('modelfc.acquisition_storage.os.fsync', side_effect=fail_directory):
            with self.assertRaises(CaptureWriteUncertain):
                self.run_capture()
        self.assertTrue((self.store/RECORD_NAME).exists())
        self.assertEqual(self.run_capture()['write_state'], 'EXISTING')

    def test_partial_model_failure_records_every_fixture_skip(self):
        from modelfc.corner_forecasts import predict_corner_fixture
        count = 0
        def fail_one(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('model failure')
            return predict_corner_fixture(*args, **kwargs)
        with patch('modelfc.pl_corner_capture.predict_corner_fixture', side_effect=fail_one):
            result = self.run_capture()['payload']
        self.assertEqual(result['forecasts'], [])
        self.assertEqual(len(result['skips']), 8)
        self.assertEqual(sum(s['reasons'] == ['MODEL_BATCH_REJECTED'] for s in result['skips']), 7)

    def test_future_observation_and_original_input_hashes(self):
        originals = list(self.data.sources)
        with patch('modelfc.pl_corner_capture.now', return_value=NOW-timedelta(seconds=1)):
            with self.assertRaisesRegex(ValueError, 'OBSERVATION_IN_FUTURE'):
                self.run_capture()
        self.assertEqual(list(self.store.iterdir()), [])
        record = self.run_capture()
        self.assertEqual(originals, self.data.sources)
        forecast = record['payload']['forecasts'][0]
        self.assertEqual(forecast['calendar']['bundle_sha256'], self.data.options()[1]['bundle_sha256'])
        self.assertEqual([s['sha256'] for s in forecast['sources']], [s[2] for s in originals])
        self.assertFalse(record['recorded_before_all_kickoffs'])

    def test_concurrent_repeat_is_exclusive(self):
        def attempt(_):
            try:
                return self.run_capture()['write_state']
            except BlockingIOError:
                return 'BUSY'
        with ThreadPoolExecutor(max_workers=2) as workers:
            states = list(workers.map(attempt, range(2)))
        self.assertEqual(states.count('CREATED'), 1)
        self.assertTrue(set(states) <= {'CREATED', 'EXISTING', 'BUSY'})
        self.assertEqual(len(list(self.store.iterdir())), 1)
