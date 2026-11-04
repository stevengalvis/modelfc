"""One finite deployability pass: synthetic interruption and authority scenarios."""
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from modelfc import pl_corner_research_runner as runner
from modelfc import pl_corner_research_capture as capture
from modelfc.acquisition_storage import Store
from tests import test_pl_corner_research_runner as fixtures
from tests.test_pl_calendar import NOW


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.ResearchRunnerTests();self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.authority=self.base.authority()

    def capture(self, registry=None, authority=None, operation='one'):
        raw,options=self.base.data.options();options.pop('as_of')
        return runner.run_once(registry or self.base.registry,self.base.a,raw,operation_id=operation,
            authority=authority or self.authority,enabled=True,sources=self.base.data.sources,**options)

    def snapshot(self):
        return {str(p.relative_to(self.base.root)):p.read_bytes() for p in self.base.root.rglob('*.json')}

    def report(self, **options):
        before=self.snapshot()
        with patch.object(runner,'now',side_effect=AssertionError('no new timestamp')), \
             patch.object(capture,'capture',side_effect=AssertionError('no model/capture')), \
             patch.object(capture,'_pins',side_effect=AssertionError('use recorded pins')), \
             patch.object(Store,'put',side_effect=AssertionError('no write')):
            result=runner.reconcile_operation(self.base.registry,operation_id='one',authority=self.authority,**options)
        self.assertEqual(before,self.snapshot());self.assertEqual(result['writes'],0)
        self.assertFalse(result['eligibility_reassessed'])
        return result

    def test_no_reservation_and_reservation_only_are_not_completion(self):
        self.assertEqual(self.report()['state'],'NO_RESERVATION')
        with patch.object(capture,'capture',side_effect=OSError('crash after reservation')):
            with self.assertRaises(OSError):self.capture()
        report=self.report();self.assertEqual(report['state'],'HOLD')
        self.assertEqual(report['artifact_state'],'RESERVATION_ONLY')
        self.assertIsNone(report['durability_receipt'])

    def test_candidate_without_receipt_cannot_reconstruct_timestamp(self):
        real=Store.put
        def put(store,name,record):
            if name==capture.RECEIPT:raise OSError('crash before receipt')
            return real(store,name,record)
        with patch.object(Store,'put',put):
            with self.assertRaises(capture.ResearchWriteUncertain):self.capture()
        report=self.report();self.assertEqual(report['state'],'HOLD')
        self.assertEqual(report['artifact_state'],'CANDIDATE_WITHOUT_DURABILITY_RECEIPT')
        self.assertIsNone(report['capture_state']);self.assertIsNone(report['durability_receipt'])

    def test_valid_capture_missing_done_remains_hold_even_after_restart(self):
        real=runner._put
        def put(store,name,row):
            if name.startswith('done-'):raise OSError('crash before done')
            return real(store,name,row)
        with patch.object(runner,'_put',put):
            with self.assertRaises(OSError):self.capture()
        self.authority=runner.RegistryAuthority(**asdict(self.authority))
        report=self.report();self.assertEqual(report['state'],'HOLD')
        self.assertEqual(report['artifact_state'],'VALID_CAPTURE_WITHOUT_RUNNER_COMPLETION')
        self.assertEqual(report['capture_state'],'PAYLOAD_DURABLE_PRE_KICKOFF')
        self.assertEqual(report['durability_receipt']['payload_durability_observed_at'],NOW.isoformat())
        self.assertEqual(self.capture()['state'],'INCOMPLETE')

    def test_completed_and_expected_event_present_or_absent(self):
        self.capture();self.assertEqual(self.report()['artifact_state'],'COMPLETE_RECORDS')
        event=self.base.append(self.base.result())
        report=self.report(expected_transition_sha256=event['reviewed_transition_sha256'])
        self.assertEqual(report['expected_event_state'],'PRESENT')
        self.assertEqual(report['expected_event'],event)
        self.assertEqual(self.report(expected_transition_sha256='0'*64)['expected_event_state'],'ABSENT_NOT_RECONSTRUCTED')

    def test_candidate_must_match_reserved_input_pins(self):
        with patch.object(capture,'capture',side_effect=OSError('interrupt')):
            with self.assertRaises(OSError):self.capture()
        self.base.data.replace_current(lambda b:b.replace(b',5,3',b',6,3',1))
        raw,options=self.base.data.options();options.pop('as_of')
        capture.capture(self.base.a,raw,enabled=True,sources=self.base.data.sources,**options)
        with self.assertRaisesRegex(ValueError,'CAPTURE_INTENT_MISMATCH'):self.report()

    def test_registry_authority_blocks_different_path_replacement_and_symlink(self):
        other=self.base.root/'other';other.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'PATH_MISMATCH'):self.capture(registry=other)
        with self.assertRaisesRegex(ValueError,'AUTHORITY_REQUIRED'):
            runner.reconcile_operation(self.base.registry,operation_id='one')
        original=self.base.root/'original';self.base.registry.rename(original);self.base.registry.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'IDENTITY_MISMATCH'):self.capture()
        self.base.registry.rmdir();self.base.registry.symlink_to(original,target_is_directory=True)
        with self.assertRaises(OSError):self.capture()
        self.assertEqual(list(other.iterdir()),[]);self.assertEqual(list(original.iterdir()),[])

    def test_authority_required_for_evidence_and_inspection(self):
        self.capture();identity,_=self.base.frozen()
        with self.assertRaisesRegex(ValueError,'AUTHORITY_REQUIRED'):
            runner.inspect_fixture(self.base.registry,operation_id='one',fixture_id=identity['provider_fixture_id'])
        with self.assertRaisesRegex(ValueError,'AUTHORITY_REQUIRED'):
            runner.record_evidence(self.base.registry,operation_id='one',fixture_id=identity['provider_fixture_id'],
                raw=b'{}',source_sha256=runner.fingerprint(b'{}'),kind='result',reviewed_transition_sha256='0'*64,enabled=True)

    def test_corrupt_receipt_is_not_reported_as_missing(self):
        self.capture();p=self.base.a/capture.RECEIPT;p.write_bytes(b'{}')
        with self.assertRaises(ValueError):self.report()

    def test_new_kickoff_before_reservation_vs_after_capture(self):
        self.base.data.payload['matches'][0]['utcDate']='2026-10-17T15:00:00Z'
        self.capture()
        intent=json.loads(next(self.base.registry.glob('intent-*.json')).read_bytes())['record']
        identity=next(i for i in intent['fixtures'] if i['provider_fixture_id']==str(self.base.data.payload['matches'][0]['id']))
        self.assertEqual(identity['kickoff_utc'],'2026-10-17T15:00:00+00:00')
        self.base.data.payload['matches'][0]['utcDate']='2026-10-17T16:00:00Z'
        with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):self.capture(operation='two')

    def test_cancellation_reversal_stays_explicitly_held_and_corrections_append(self):
        self.capture();original={p.name:p.read_bytes() for p in self.base.a.iterdir()}
        self.base.data.payload['matches'][0]['status']='CANCELLED'
        raw,options=self.base.data.options();options.pop('as_of');options.pop('selected_day');options.pop('bundle_sha256')
        canceled=self.base.append(raw,'calendar',options=options)
        self.base.data.payload['matches'][0]['status']='TIMED';raw,_=self.base.data.options()
        reversed_event=self.base.append(raw,'calendar',options=options,previous=canceled['event_sha256'],reason='Reviewed synthetic reversal')
        self.assertEqual(reversed_event['state'],'HELD_STATUS_CONFLICT')
        final=self.base.append(self.base.result(),previous=reversed_event['event_sha256'],reason='Reviewed synthetic final')
        corrected=self.base.append(self.base.result(6,3),previous=final['event_sha256'],reason='Reviewed synthetic correction')
        self.assertEqual(corrected['state'],'HELD_STATUS_CONFLICT')
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.base.a.iterdir()})
        self.assertEqual(self.report()['event_count'],4)

    def test_postponed_before_capture_retains_whole_day_exclusion(self):
        self.base.data.payload['matches'][0]['status']='POSTPONED'
        self.assertEqual(self.capture()['capture_state'],'NO_ELIGIBLE_FORECASTS')
        self.base.data.payload['matches'][0]['status']='TIMED'
        self.assertEqual(self.capture(operation='two')['state'],'DUPLICATE')
        self.assertEqual(self.report()['capture_state'],'NO_ELIGIBLE_FORECASTS')

    def test_event_write_interrupted_before_record_is_not_reconstructed(self):
        self.capture();raw=self.base.result();observed={};real=Store.put
        def put(store,name,record):
            if name.startswith('event-'):
                observed['pin']=record['reviewed_transition_sha256']
                raise OSError('interrupted before event record')
            return real(store,name,record)
        with patch.object(Store,'put',put):
            with self.assertRaises(capture.ResearchWriteUncertain):self.base.append(raw)
        report=self.report(expected_transition_sha256=observed['pin'])
        self.assertEqual(report['expected_event_state'],'ABSENT_NOT_RECONSTRUCTED')
        self.assertIsNone(report['expected_event']);self.assertEqual(report['event_count'],0)
        self.assertEqual(report['artifact_state'],'COMPLETE_RECORDS')
