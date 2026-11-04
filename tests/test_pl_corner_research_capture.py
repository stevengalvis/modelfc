"""Synthetic clocks and disposable private stores; no prospective study started."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import Store, private_store
from modelfc import pl_corner_research_capture as research
from tests import test_pl_history_readiness as fixture
from tests.test_pl_calendar import NOW


class ResearchCaptureTests(unittest.TestCase):
    def setUp(self):
        self.data=fixture.PLHistoryTests();self.data.setUp()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Path(self.temp.name)

    def run_capture(self, path=None, **changes):
        raw,options=self.data.options();options.pop('as_of')
        options.update(changes)
        return research.capture(path or self.store,raw,enabled=True,sources=self.data.sources,**options)

    def snapshot(self):
        return {p.name:p.read_bytes() for p in self.store.iterdir()}

    def test_disabled_and_no_caller_clock(self):
        with patch.object(research,'private_store',side_effect=AssertionError('store')):
            self.assertEqual(research.capture('/missing',None)['state'],'DISABLED')
        with self.assertRaises(ValueError):self.run_capture(as_of=NOW)
        self.assertEqual(self.snapshot(),{})

    def test_actual_write_observation_pairs_and_repeat_after_kickoff(self):
        with patch.object(research,'now',side_effect=[NOW,NOW+timedelta(seconds=1),NOW+timedelta(seconds=2)]), \
             patch('socket.socket',side_effect=AssertionError('network')):
            first=self.run_capture()
        c=first['candidate'];before=self.snapshot()
        self.assertEqual(first['state'],'PAYLOAD_DURABLE_PRE_KICKOFF')
        self.assertEqual(c['computation']['mode'],'OFFLINE_REPLAY')
        self.assertEqual(c['qualification'],'NOT_ASSESSED')
        self.assertFalse(first['publication_allowed'])
        self.assertEqual(c['write_started_at'],(NOW+timedelta(seconds=1)).isoformat())
        self.assertEqual(first['receipt']['payload_durability_observed_at'],(NOW+timedelta(seconds=2)).isoformat())
        self.assertEqual(len(c['paired_probabilities']),8*2*4)
        for r in c['paired_probabilities']:
            self.assertEqual(r['baseline_over'],float(r['venue']=='home' and r['line']<5))
            self.assertAlmostEqual(r['model_over']+r['model_under'],1)
        with patch.object(research,'now',side_effect=AssertionError('repeat cannot observe time')), \
             patch.object(research.pl_corner_capture,'prepare',side_effect=AssertionError('repeat cannot recompute')):
            again=self.run_capture()
        self.assertEqual(again['receipt'],first['receipt']);self.assertEqual(before,self.snapshot())
        self.assertEqual(again['write_state'],'EXISTING')

    def test_kickoff_crossing_and_equal_excluded(self):
        for delta in (timedelta(hours=4),timedelta(hours=4,seconds=1)):
            with tempfile.TemporaryDirectory() as p,patch.object(research,'now',side_effect=[NOW,NOW,NOW+delta]):
                r=self.run_capture(Path(p))
            self.assertEqual(r['state'],'LATE_EXCLUDED')

    def test_clock_rollback_excluded_or_rejected(self):
        with patch.object(research,'now',side_effect=[NOW,NOW,NOW-timedelta(seconds=1)]):
            self.assertEqual(self.run_capture()['state'],'CLOCK_REJECTED')
        with tempfile.TemporaryDirectory() as p,patch.object(research,'now',side_effect=[NOW,NOW-timedelta(seconds=1)]):
            with self.assertRaises(ValueError):self.run_capture(Path(p))
            self.assertEqual(list(Path(p).iterdir()),[])

    def test_directory_fsync_failure_never_manufactures_receipt(self):
        import os
        real=os.fsync;count=0
        def sync(fd):
            nonlocal count
            count+=1
            if count==2:raise OSError('directory sync failed')
            return real(fd)
        with patch.object(research,'now',return_value=NOW),patch('modelfc.acquisition_storage.os.fsync',side_effect=sync):
            with self.assertRaises(research.ResearchWriteUncertain):self.run_capture()
        before=self.snapshot();self.assertIn(research.CANDIDATE,before)
        with patch.object(research,'now',side_effect=AssertionError('no reconstructed timing')):
            self.assertEqual(self.run_capture()['state'],'INCOMPLETE')
        self.assertEqual(before,self.snapshot())

    def test_crash_before_receipt_and_uncertain_receipt_outcome(self):
        real=Store.put
        for completed in (False,True):
            with tempfile.TemporaryDirectory() as p:
                def put(store,name,record):
                    if name==research.RECEIPT:
                        if completed:real(store,name,record)
                        raise OSError('receipt uncertain')
                    return real(store,name,record)
                with patch.object(research,'now',return_value=NOW),patch.object(Store,'put',put):
                    with self.assertRaises(research.ResearchWriteUncertain):self.run_capture(Path(p))
                with patch.object(research,'now',side_effect=AssertionError('no new clock')):
                    r=self.run_capture(Path(p))
                self.assertEqual(r['state'],'PAYLOAD_DURABLE_PRE_KICKOFF' if completed else 'INCOMPLETE')

    def test_receipt_may_finish_later_attests_only_payload(self):
        real=Store.put;clock=[NOW]
        def put(store,name,record):
            if name==research.RECEIPT:clock[0]=NOW+timedelta(days=1)
            return real(store,name,record)
        with patch.object(research,'now',side_effect=lambda:clock[0]),patch.object(Store,'put',put):
            r=self.run_capture()
        self.assertEqual(r['state'],'PAYLOAD_DURABLE_PRE_KICKOFF')
        self.assertEqual(r['receipt']['semantics'],research.SEMANTICS)
        self.assertEqual(r['receipt']['payload_durability_observed_at'],NOW.isoformat())

    def test_input_correction_or_result_cannot_overwrite(self):
        with patch.object(research,'now',return_value=NOW):self.run_capture()
        before=self.snapshot()
        self.data.replace_current(lambda b:b.replace(b',5,3',b',6,3',1))
        with self.assertRaises(ValueError):self.run_capture()
        self.assertEqual(before,self.snapshot())
        with self.assertRaises(ValueError):self.run_capture(results={'corrected':True})
        self.assertEqual(before,self.snapshot())

    def test_same_day_excluded_future_and_postponed_fail_closed(self):
        with patch.object(research,'now',return_value=NOW):first=self.run_capture()['candidate']
        self.data.replace_current(lambda b:b+b'E0,17/10/2026,Other Home,Other Away,1,0,H,999,999\n')
        with tempfile.TemporaryDirectory() as p,patch.object(research,'now',return_value=NOW):
            second=self.run_capture(Path(p))['candidate']
        self.assertEqual(first['paired_probabilities'],second['paired_probabilities'])
        self.assertEqual(first['computation']['model_input_sha256'],second['computation']['model_input_sha256'])
        self.data.replace_current(lambda b:b.replace(b'17/10/2026',b'18/10/2026'))
        with tempfile.TemporaryDirectory() as p,patch.object(research,'now',return_value=NOW):
            self.assertEqual(self.run_capture(Path(p))['state'],'NO_ELIGIBLE_FORECASTS')
        self.data=fixture.PLHistoryTests();self.data.setUp();self.data.payload['matches'][0]['status']='POSTPONED'
        with tempfile.TemporaryDirectory() as p,patch.object(research,'now',return_value=NOW):
            r=self.run_capture(Path(p))
        self.assertEqual(r['state'],'NO_ELIGIBLE_FORECASTS')
        self.assertEqual(r['candidate']['paired_probabilities'],[])
        self.assertIn('FIXTURE_NOT_SCHEDULED',r['candidate']['computation']['skips'][0]['reasons'])

    def test_missing_calendar_and_empty_day_are_distinct(self):
        raw,options=self.data.options();options.pop('as_of')
        with patch.object(research,'now',return_value=NOW):
            r=research.capture(self.store,None,enabled=True,sources=self.data.sources,**options)
        self.assertEqual(r['candidate']['computation']['state'],'BLOCKED')
        for m in self.data.payload['matches'][:8]:m['utcDate']='2026-10-18T14:00:00Z'
        with tempfile.TemporaryDirectory() as p,patch.object(research,'now',return_value=NOW):
            r=self.run_capture(Path(p))
        self.assertEqual(r['candidate']['computation']['state'],'EMPTY')

    def test_concurrent_attempt_is_busy_without_duplicate(self):
        entered=threading.Event();release=threading.Event();real=Store.put
        def put(store,name,record):
            if name==research.CANDIDATE:
                entered.set();self.assertTrue(release.wait(5))
            return real(store,name,record)
        with patch.object(research,'now',return_value=NOW),patch.object(Store,'put',put),ThreadPoolExecutor() as pool:
            future=pool.submit(self.run_capture)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(BlockingIOError):self.run_capture()
            finally:release.set()
            self.assertEqual(future.result()['state'],'PAYLOAD_DURABLE_PRE_KICKOFF')
        self.assertEqual(len(self.snapshot()),2)

    def test_receipt_hash_binding_and_private_store(self):
        with patch.object(research,'now',return_value=NOW):r=self.run_capture()
        with self.assertRaises(ValueError):research._result(r['candidate'],{**r['receipt'],'candidate_sha256':'0'*64},'EXISTING')
        self.store.chmod(0o755)
        try:
            with self.assertRaises(ValueError):self.run_capture()
        finally:self.store.chmod(0o700)
        with private_store(self.store) as store:self.assertIsNotNone(store.get(research.CANDIDATE))
