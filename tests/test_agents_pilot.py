import unittest
from ops.bounded_worker.agents_pilot import Plan, PilotState
from ops.bounded_worker.task import Task


class AgentsPilotTests(unittest.TestCase):
    def setUp(self):
        self.plan = Plan(Task('synthetic', 'stevengalvis/modelfc', 'a' * 40,
                             'Fix clamp.py and run offline tests.', ('clamp.py',)), 'test-model')
        self.state = PilotState(self.plan)

    def bound(self):
        self.state.reserve_create()
        self.state.bind_session('sess_fixture', 'env_fixture', review='auto_review')
        self.state.observe_connection('sess_fixture', 'env_fixture', connected=True)

    def submitted(self):
        self.bound()
        self.state.reserve_input(now=0, admission_passed=True)
        self.state.confirm_input('sess_fixture', 'env_fixture')

    def test_creation_contains_no_input_or_credential(self):
        payload = self.state.reserve_create()
        self.assertNotIn('input', payload)
        self.assertEqual(payload['review'], 'auto_review')
        self.assertEqual(payload['agent']['multi_agent'], {'enabled': False})
        self.assertEqual(payload['environment'], {'type': 'self_hosted', 'workspace_directory': '/workspace'})
        self.assertNotIn('key', repr(payload).lower())

    def test_explicit_provider_spend_limit_and_standard_tier(self):
        plan = Plan(self.plan.task, 'test-model', spend_limit_cents=100)
        self.assertEqual(plan.create_payload()['spend_control'], {'limit': 100})
        self.assertEqual(plan.create_payload()['agent']['service_tier'], 'default')
        self.assertNotIn('spend_control', self.plan.create_payload())
        for limit in (True, 0, -1, 1.5, '100'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                Plan(self.plan.task, 'test-model', spend_limit_cents=limit).create_payload()

    def test_duplicate_create_blocked(self):
        self.state.reserve_create()
        with self.assertRaises(ValueError):
            self.state.reserve_create()

    def test_review_downgrade_fails_closed(self):
        self.state.reserve_create()
        with self.assertRaises(ValueError):
            self.state.bind_session('sess_fixture', 'env_fixture', review='none')
        self.assertEqual(self.state.result(), 'blocked')

    def test_binding_mismatch_blocks(self):
        self.bound()
        with self.assertRaises(ValueError):
            self.state.observe_connection('other', 'env_fixture', connected=True)
        self.assertEqual(self.state.result(), 'blocked')

    def test_input_requires_connected_admitted_environment(self):
        with self.assertRaises(ValueError):
            self.state.reserve_input(now=0, admission_passed=True)
        self.bound()
        for admitted in (False, 1, 'yes'):
            with self.subTest(admitted=admitted), self.assertRaises(ValueError):
                self.state.reserve_input(now=0, admission_passed=admitted)
        self.state.observe_connection('sess_fixture', 'env_fixture', connected=False)
        with self.assertRaises(ValueError):
            self.state.reserve_input(now=0, admission_passed=True)

    def test_submit_reservation_prevents_retry_before_confirmation(self):
        self.bound()
        payload = self.state.reserve_input(now=0, admission_passed=True)
        self.assertEqual(payload['events'][0]['type'], 'agent.session.input.message')
        with self.assertRaises(ValueError):
            self.state.reserve_input(now=1, admission_passed=True)

    def test_uncertain_request_stays_blocked(self):
        self.submitted()
        self.state.uncertain_request()
        self.state.observe_terminal('sess_fixture', 'env_fixture', outcome='completed')
        self.state.verify_local_result(containment_empty=True, changed_paths=('clamp.py',), tests_passed=True)
        self.assertEqual(self.state.result(), 'blocked')

    def test_cloud_and_local_success_both_required(self):
        self.submitted()
        self.state.verify_local_result(containment_empty=True, changed_paths=('clamp.py',), tests_passed=True)
        self.assertEqual(self.state.result(), 'pending')
        self.state.observe_terminal('sess_fixture', 'env_fixture', outcome='completed')
        self.assertEqual(self.state.result(), 'completed')
        self.state.verify_local_result(containment_empty=False, changed_paths=('clamp.py',), tests_passed=True)
        self.assertEqual(self.state.result(), 'pending')

    def test_out_of_scope_patch_blocks(self):
        self.submitted()
        self.state.observe_terminal('sess_fixture', 'env_fixture', outcome='completed')
        self.state.verify_local_result(containment_empty=True, changed_paths=('../secret',), tests_passed=True)
        self.assertEqual(self.state.result(), 'blocked')

    def test_failed_tests_and_cloud_failure_not_completed(self):
        for outcome in ('completed', 'failed', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.submitted()
                self.state.observe_terminal('sess_fixture', 'env_fixture', outcome=outcome)
                self.state.verify_local_result(containment_empty=True, changed_paths=(), tests_passed=False)
                self.assertEqual(self.state.result(), 'not_completed')

    def test_cancel_does_not_claim_cloud_or_local_shutdown(self):
        self.submitted()
        self.assertEqual(self.state.cancellation_payload(), {'events': [{'type': 'agent.session.input.cancel'}]})
        self.assertEqual(self.state.result(), 'pending')
        self.state.observe_terminal('sess_fixture', 'env_fixture', outcome='completed')
        self.state.verify_local_result(containment_empty=True, changed_paths=(), tests_passed=True)
        self.assertEqual(self.state.result(), 'not_completed')

    def test_deadline_and_invalid_clocks(self):
        self.submitted()
        self.assertFalse(self.state.deadline_reached(179))
        self.assertTrue(self.state.deadline_reached(180))
        for now in (-1, float('nan'), float('inf'), True):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.state.deadline_reached(now)

    def test_invalid_or_duplicate_terminal_blocks(self):
        self.submitted()
        with self.assertRaises(ValueError):
            self.state.observe_terminal('sess_fixture', 'env_fixture', outcome='idle')
        self.assertEqual(self.state.result(), 'blocked')


if __name__ == '__main__':
    unittest.main()
