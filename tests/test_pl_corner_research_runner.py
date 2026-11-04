"""Synthetic registry coordination and immutable research event chains only."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import threading
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc import pl_corner_research_runner as runner
from modelfc import pl_corner_research_capture as capture
from modelfc.acquisition_storage import Store, encoded, fingerprint
from tests import test_pl_history_readiness as fixture
from tests.test_pl_calendar import NOW


class ResearchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.data=fixture.PLHistoryTests();self.data.setUp()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.registry=self.root/'registry';self.registry.mkdir(mode=0o700)
        self.a=self.root/'a';self.a.mkdir(mode=0o700)
        self.b=self.root/'b';self.b.mkdir(mode=0o700)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.clock=self.stack.enter_context(patch.object(runner,'now',return_value=NOW))
        self.stack.enter_context(patch.object(capture,'now',return_value=NOW))
        self.stack.enter_context(patch('socket.socket',side_effect=AssertionError('network')))

    def authority(self):
        info=self.registry.stat()
        return runner.RegistryAuthority(str(self.registry),info.st_dev,info.st_ino,info.st_uid)

    def run_capture(self, operation='one', store=None):
        raw,options=self.data.options();options.pop('as_of')
        return runner.run_once(self.registry,store or self.a,raw,operation_id=operation,authority=self.authority(),enabled=True,
                               sources=self.data.sources,**options)

    def frozen(self):
        rows={p.name:json.loads(p.read_bytes())['record'] for p in self.registry.iterdir()}
        intent=next(r for n,r in rows.items() if n.startswith('intent-'))
        done=next(r for n,r in rows.items() if n.startswith('done-'))
        return intent['fixtures'][0],done['candidate_sha256']

    def append(self, raw, kind='result', previous=None, reason=None, reviewed=None, options=None):
        identity,pin=self.frozen();source=fingerprint(raw)
        transition=dict(operation_id='one',fixture=identity,candidate_sha256=pin,kind=kind,
                        source_sha256=source,previous=previous,reason=reason)
        return runner.record_evidence(self.registry,operation_id='one',fixture_id=identity['provider_fixture_id'],
            raw=raw,source_sha256=source,kind=kind,reviewed_transition_sha256=reviewed or fingerprint(encoded(transition)),
            previous=previous,reason=reason,calendar_options=options,authority=self.authority(),enabled=True)

    def result(self, home=5, away=3):
        identity,_=self.frozen();at=NOW+timedelta(hours=7);self.clock.return_value=at
        return encoded(dict(fixture=identity,status='FINISHED',period='REGULATION',
            completed_at=(NOW+timedelta(hours=6)).isoformat(),retrieved_at=at.isoformat(),
            home_goals=None,away_goals=None,home_corners=home,away_corners=away,
            provider='synthetic-fixture',source_sha256='a'*64))

    def inspect(self):
        identity,_=self.frozen()
        return runner.inspect_fixture(self.registry,operation_id='one',fixture_id=identity['provider_fixture_id'],authority=self.authority())

    def test_disabled_without_io_and_duplicate_across_stores(self):
        self.assertEqual(runner.run_once('/missing','/missing',None,operation_id='x')['state'],'DISABLED')
        self.assertEqual(self.run_capture()['capture_state'],'PAYLOAD_DURABLE_PRE_KICKOFF')
        original={p.name:p.read_bytes() for p in self.a.iterdir()}
        with patch.object(capture,'capture',side_effect=AssertionError('no duplicate capture')):
            self.assertEqual(self.run_capture(store=self.b)['state'],'EXISTING')
            self.assertEqual(self.run_capture('two',self.b)['state'],'DUPLICATE')
        self.assertEqual(list(self.b.iterdir()),[])
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.a.iterdir()})

    def test_changed_inputs_operation_and_kickoff_conflict(self):
        self.run_capture();self.data.replace_current(lambda b:b.replace(b',5,3',b',6,3',1))
        with self.assertRaisesRegex(ValueError,'OPERATION_CONFLICT'):self.run_capture()
        self.data.payload['matches'][0]['utcDate']='2026-10-17T15:00:00Z'
        with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):self.run_capture('two',self.b)
        self.assertEqual(list(self.b.iterdir()),[])

    def test_interrupted_capture_is_not_retried(self):
        with patch.object(capture,'capture',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):self.run_capture()
        with patch.object(capture,'capture',side_effect=AssertionError('no retry')):
            self.assertEqual(self.run_capture()['state'],'INCOMPLETE')
            with self.assertRaisesRegex(ValueError,'RECOVERY_REQUIRED'):self.run_capture('two',self.b)
        self.assertEqual(list(self.a.iterdir()),[])

    def test_done_write_uncertainty_preserves_original(self):
        real=Store.put
        def put(store,name,row):
            result=real(store,name,row)
            if name.startswith('done-'):raise OSError('done sync uncertain')
            return result
        with patch.object(Store,'put',put):
            with self.assertRaises(capture.ResearchWriteUncertain):self.run_capture()
        with patch.object(capture,'capture',side_effect=AssertionError('no recapture')):
            self.assertEqual(self.run_capture(store=self.b)['state'],'EXISTING')

    def test_missing_pending_final_correction_and_duplicate(self):
        self.run_capture();original={p.name:p.read_bytes() for p in self.a.iterdir()}
        self.assertEqual(self.inspect()['state'],'MISSING_RESULT')
        pending=self.append(self.result(None,None));self.assertEqual(pending['state'],'PENDING')
        final=self.append(self.result(),previous=pending['event_sha256'],reason='Synthetic final receipt reviewed')
        self.assertEqual(final['state'],'FINAL')
        corrected=self.append(self.result(6,3),previous=final['event_sha256'],reason='Synthetic correction reviewed')
        self.assertEqual(corrected,self.append(self.result(6,3),previous=final['event_sha256'],reason='Synthetic correction reviewed'))
        self.assertEqual(len(self.inspect()['events']),3)
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.a.iterdir()})
        with self.assertRaisesRegex(ValueError,'STALE_CHAIN_TIP'):
            self.append(self.result(7,3),previous=final['event_sha256'],reason='Stale review')

    def test_result_identity_chronology_and_review_rejection(self):
        self.run_capture();raw=self.result()
        with self.assertRaisesRegex(ValueError,'NOT_REVIEWED'):self.append(raw,reviewed='0'*64)
        value=json.loads(raw);value['fixture']['home_team']='Wrong team'
        with self.assertRaises(ValueError):self.append(encoded(value))
        value=json.loads(raw);value['completed_at']=NOW.isoformat()
        with self.assertRaises(ValueError):self.append(encoded(value))
        self.assertEqual(self.inspect()['state'],'MISSING_RESULT')

    def test_postponement_reschedule_and_cancellation_hold_results(self):
        for status in ('POSTPONED','CANCELLED','TIMED'):
            with self.subTest(status=status):
                # Independent synthetic registry for each scenario.
                self.registry=self.root/status;self.registry.mkdir(mode=0o700)
                destination=self.root/(status+'capture');destination.mkdir(mode=0o700)
                self.data=fixture.PLHistoryTests();self.data.setUp();self.clock.return_value=NOW
                self.run_capture(store=destination)
                self.data.payload['matches'][0]['status']=status
                if status=='TIMED':self.data.payload['matches'][0]['utcDate']='2026-10-17T15:00:00Z'
                raw,options=self.data.options();options.pop('as_of');options.pop('selected_day');options.pop('bundle_sha256')
                event=self.append(raw,'calendar',options=options)
                self.assertEqual(event['state'],{'POSTPONED':'POSTPONED','CANCELLED':'CANCELED','TIMED':'RESCHEDULED'}[status])
                held=self.append(self.result(),previous=event['event_sha256'],reason='Final receipt retained for review')
                self.assertEqual(held['state'],'HELD_STATUS_CONFLICT')

    def test_late_capture_cannot_get_successful_result(self):
        with patch.object(capture,'now',side_effect=[NOW,NOW,NOW+timedelta(hours=4)]):
            self.assertEqual(self.run_capture()['capture_state'],'LATE_EXCLUDED')
        self.assertEqual(self.append(self.result())['state'],'EXCLUDED_CAPTURE')

    def test_event_write_uncertainty_repeated_delivery_no_new_timestamp(self):
        self.run_capture();raw=self.result();real=Store.put
        def put(store,name,row):
            value=real(store,name,row)
            if name.startswith('event-'):raise OSError('uncertain event write')
            return value
        with patch.object(Store,'put',put):
            with self.assertRaises(capture.ResearchWriteUncertain):self.append(raw)
        self.clock.return_value=NOW+timedelta(days=1)
        event=self.append(raw)
        self.assertEqual(event['recorded_at'],(NOW+timedelta(hours=7)).isoformat())
        self.assertEqual(len(self.inspect()['events']),1)

    def test_capacity_and_preused_destination_fail_before_capture(self):
        with patch.object(runner,'MAX_RECORDS',1):
            with self.assertRaises(ValueError):self.run_capture()
        self.assertEqual(list(self.a.iterdir()),[])
        raw,options=self.data.options();options.pop('as_of')
        capture.capture(self.a,raw,enabled=True,sources=self.data.sources,**options)
        with self.assertRaisesRegex(ValueError,'DESTINATION_ALREADY_USED'):self.run_capture()

    def test_mixed_duplicate_blocks_entire_batch_and_does_not_mask_conflict(self):
        self.run_capture();before={p.name:p.read_bytes() for p in self.registry.iterdir()}
        self.data.payload['matches'][8]['utcDate']='2026-10-17T16:00:00Z'
        mixed=self.run_capture('two',self.b)
        self.assertEqual(mixed['state'],'BLOCKED_PARTIAL_DUPLICATE')
        self.assertEqual(len(mixed['overlaps']),8);self.assertEqual(len(mixed['uncovered']),1)
        self.assertTrue(mixed['whole_batch_blocked'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.registry.iterdir()})
        self.data.payload['matches'][7]['utcDate']='2026-10-17T17:00:00Z'
        with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):self.run_capture('two',self.b)
        self.assertEqual(list(self.b.iterdir()),[])

    def test_later_reverse_identity_conflict_not_masked_by_earlier_duplicate(self):
        self.run_capture();self.data.payload['matches'][7]['id']=999999
        with self.assertRaisesRegex(ValueError,'REVERSE_IDENTITY_CONFLICT'):self.run_capture('two',self.b)

    def test_intent_sync_uncertainty_and_same_registry_lock(self):
        real=Store.put
        def put(store,name,row):
            value=real(store,name,row)
            if name.startswith('intent-'):raise OSError('intent sync uncertain')
            return value
        with patch.object(Store,'put',put):
            with self.assertRaises(capture.ResearchWriteUncertain):self.run_capture()
        self.assertEqual(self.run_capture()['state'],'INCOMPLETE')
        self.assertEqual(list(self.a.iterdir()),[])

    def test_missing_fixture_status_and_changed_store_reject(self):
        self.run_capture();raw,options=self.data.options();options.pop('as_of');options.pop('selected_day');options.pop('bundle_sha256')
        # A valid full calendar whose target ID changed is not evidence of cancellation.
        self.data.payload['matches'][0]['id']=999999
        raw,_=self.data.options()
        with self.assertRaisesRegex(ValueError,'MISSING_FIXTURE'):self.append(raw,'calendar',options=options)
        self.data=fixture.PLHistoryTests();self.data.setUp()
        self.a.rename(self.root/'original');self.a.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'STORE_REPLACED'):self.run_capture()

    def test_concurrent_different_store_cannot_pass_registry_reservation(self):
        entered=threading.Event();release=threading.Event();real=capture.capture
        def held(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(5))
            return real(*args,**kwargs)
        with patch.object(capture,'capture',held),ThreadPoolExecutor() as pool:
            pending=pool.submit(self.run_capture)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(BlockingIOError):self.run_capture('two',self.b)
            finally:release.set()
            self.assertEqual(pending.result()['capture_state'],'PAYLOAD_DURABLE_PRE_KICKOFF')
        self.assertEqual(list(self.b.iterdir()),[])

    def test_unbounded_source_iterator_rejected_without_consuming(self):
        def stream():
            raise AssertionError('must not consume arbitrary iterator')
            yield None
        self.data.sources=stream()
        with self.assertRaisesRegex(ValueError,'FINITE_SOURCES_REQUIRED'):self.run_capture()
        self.assertEqual(list(self.registry.iterdir()),[])
