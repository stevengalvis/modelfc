"""Supplied scenarios and disposable local state only; no provider requests."""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.acquisition_admission import assess
from modelfc.acquisition_planner import Fixture, Obligation
from modelfc.acquisition_storage import shared_runner, AcquisitionRejected
from modelfc.corner_prospective import _lock, _load, _RequestBudgetGuard, RunnerError
from tests.test_acquisition_dry_run import calendar
from tests import test_acquisition_dry_run as fixtures
from tests.oddspapi_inventory_fixtures import NOW


class AdmissionTests(unittest.TestCase):
    def call(self, control=None, **kwargs):
        raw = json.dumps(calendar()).encode()
        source = json.dumps(fixtures.ControlPreviewTests().control() if control is None else control).encode()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('modelfc.guarded_acquisition.run_once', side_effect=AssertionError('runtime forbidden')):
            return assess(raw, control_raw=source, control_sha256=sha256(source).hexdigest(),
                control_observed_at=NOW, calendar_sha256=sha256(raw).hexdigest(),
                enabled_competitions=['E1','E0','SP1','I1','MLS'], as_of=NOW, **kwargs)

    def test_allocation_required_and_boundary_preserves_protected_scenario_capacity(self):
        self.assertEqual(self.call()['reason'], 'CALLER_ALLOCATION_UNKNOWN')
        c=fixtures.ControlPreviewTests().control(); c['period']['reserved']=177
        self.assertEqual(self.call(c, protected_requests=2)['hypothetical_requests'],1)
        c['period']['reserved']=178
        r=self.call(c, protected_requests=2)
        self.assertEqual(r['reason'],'PROTECTED_CAPACITY')
        self.assertEqual(r['hypothetical_requests'],0)
        self.assertEqual(c['period']['reserved'],178)
        for n in (-1,181,True,1.5):
            with self.assertRaises(ValueError): self.call(protected_requests=n)

    def test_any_e1_attempt_blocks_whole_batch_without_claiming_success(self):
        for state in ('RESERVED','DONE','REVIEW','HISTORY','NO_TEAM_TOTAL'):
            c=fixtures.ControlPreviewTests().control()
            c['attempts']['E1']={'count':1,'state':state,'at':NOW.isoformat()}
            r=self.call(c, protected_requests=2)
            self.assertEqual(r['reason'],'CROSS_CALLER_OVERLAP_REVIEW')
            self.assertEqual(r['conflicting_e1_fixture_ids'],['E1'])
            self.assertEqual(len(r['preview']['batches'][0]['obligations']),5)
            self.assertEqual(r['request_demand'],1)
            self.assertEqual(r['hypothetical_requests'],0)

    def test_missing_inventory_is_unknown_and_unrelated_attempt_does_not_dedup(self):
        c=fixtures.ControlPreviewTests().control(); del c['attempts']
        self.assertEqual(self.call(c,protected_requests=0)['reason'],'ATTEMPT_INVENTORY_UNKNOWN')
        c['attempts']={'other-fixture':None}
        r=self.call(c,protected_requests=0)
        self.assertEqual(r['reason'],'OFFLINE_CANDIDATE_ONLY')
        self.assertFalse(r['authorized']); self.assertFalse(r['runtime_connected'])
        self.assertEqual((r['provider_requests'],r['reservations_written']),(0,0))

    def test_existing_shared_claim_only_deduplicates_its_exact_obligation(self):
        done=Obligation(Fixture('E1',18,'E1',NOW+timedelta(hours=24)))
        r=self.call(protected_requests=0,completed=[done])
        self.assertEqual(r['preview']['batches'][0]['tournament_ids'],[17,8,23,242])
        shifted=Obligation(Fixture('E1',18,'E1',NOW+timedelta(hours=25)))
        c=fixtures.ControlPreviewTests().control(); c['attempts']['E1']={}
        self.assertEqual(self.call(c,protected_requests=0,completed=[shifted])['reason'],
                         'CROSS_CALLER_OVERLAP_REVIEW')


class SharedPrimitivesTests(unittest.TestCase):
    def test_both_callers_exclude_each_other_and_share_final_credit(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('modelfc.corner_prospective._now', return_value=NOW), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')):
            state=Path(temp); directory=state/'prospective'; directory.mkdir(mode=0o700)
            (directory/'runner.lock').touch(mode=0o600)
            path=directory/'control.json'
            c=fixtures.ControlPreviewTests().control(); c['period']['reserved']=179
            path.write_text(json.dumps(c)); path.chmod(0o600)
            with _lock(state):
                with self.assertRaisesRegex(AcquisitionRejected,'BUSY'):
                    with shared_runner(state): pass
            with shared_runner(state) as pinned:
                with self.assertRaisesRegex(RunnerError,'BUSY'):
                    with _lock(state): pass
                guard=_RequestBudgetGuard(pinned,_load(pinned),{'provider_requests':0},
                    allowed_kinds=('TOURNAMENT_ODDS',),invocation_limit=1)
                guard.reserve(1)
            # Simulate an interrupted attempt: no refund, no HTTP, no new ledger.
            with _lock(state) as current:
                self.assertEqual(_load(current)['period']['reserved'],180)
                guard=_RequestBudgetGuard(current,_load(current),{'provider_requests':0})
                with self.assertRaisesRegex(RunnerError,'REQUEST_BUDGET'): guard.reserve(1)
            self.assertEqual(sorted(p.name for p in directory.iterdir()),['control.json','runner.lock'])
