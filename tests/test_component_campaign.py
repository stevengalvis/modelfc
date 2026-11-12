"""Real public child-process campaign and independent event-oracle regressions."""
import copy
import json
from pathlib import Path
import unittest

from ops.ci import component_campaign as campaign
from ops.ci.component_public_analog import PublicAnalog
from ops.vps.deployment_acceptance import TEST_IDS


class ComponentCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binding = PublicAnalog(Path(__file__).resolve().parents[1])
        # Broad CI includes optional packages. Actual host inventory is exercised
        # separately in the deployment-only interpreter, never broad discovery.
        cls.observations = {case: cls.binding.run(case) for case in campaign.CASES if case != 'focused'}

    def test_all_real_public_cases_and_fault_attribution(self):
        for case, observation in self.observations.items():
            with self.subTest(case=case):
                result = campaign.check(case, observation, TEST_IDS)
                self.assertEqual(result['status'], 'PASS')
        # Public helper owns immediate child only; report this honestly.
        self.assertTrue(self.observations['descendant'].helper_descendant_alive)
        self.assertTrue(self.observations['descendant'].fixture_cleanup)

    def test_orchestrator_runs_every_case_and_does_not_invent_private_evidence(self):
        observations = self.observations
        visited = []
        class Recorded:
            def run(self, case):
                visited.append(case)
                return observations['hook_candidate' if case == 'focused' else case]
            def report_accepts(self, report, expected_sha, cleanup_complete): return None
        result = campaign.campaign(Recorded(), campaign.SYNTHETIC_IDS)
        self.assertEqual(tuple(visited), campaign.CASES)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['cases']['hook_equivalence']['status'], 'PASS')
        self.assertEqual(result['cases']['wrong_sha']['status'], 'NOT_AVAILABLE')
        class Broken(Recorded):
            def run(self, case):
                visited.append(case)
                if case == 'assertion':
                    bad = copy.deepcopy(observations[case])
                    bad.marker = 'private details never published'
                    return bad
                return observations['hook_candidate' if case == 'focused' else case]
        visited.clear()
        result = campaign.campaign(Broken(), campaign.SYNTHETIC_IDS)
        self.assertEqual(tuple(visited), campaign.CASES)
        self.assertEqual(result['cases']['assertion']['status'], 'FAIL')
        self.assertNotIn('private details', json.dumps(result))

    def test_corrupt_actual_events_are_rejected_even_with_successful_collector(self):
        original = self.observations['hook_candidate']
        rows = [json.loads(row) for row in original.events.splitlines()]
        mutations = []
        for index, value in ((0, True), (0, 7), (1, 'unknown'),
                             (2, 'tests.private.secret'), (3, -1), (3, True)):
            changed = copy.deepcopy(rows)
            changed[0][index] = value
            mutations.append(changed)
        mutations.extend((rows[:-1], rows + [rows[-1]], rows[1:],
                          [rows[1], rows[0]] + rows[2:]))
        changed = copy.deepcopy(rows)
        changed[2][2] = changed[0][2]  # Duplicate one ID despite count=14.
        changed[3][2] = changed[0][2]
        mutations.append(changed)
        for rows in mutations:
            bad = copy.deepcopy(original)
            bad.events = b''.join(json.dumps(row).encode() + b'\n' for row in rows)
            with self.subTest(events=rows[:1]), self.assertRaises(ValueError):
                campaign.check('hook_candidate', bad, TEST_IDS)
        for raw in (original.events[:-1], b'', b'{}\n', b'not-json\n'):
            bad = copy.deepcopy(original)
            bad.events = raw
            with self.assertRaises(ValueError): campaign.check('hook_candidate', bad, TEST_IDS)

    def test_exit_summary_collector_cleanup_and_fault_markers_all_matter(self):
        for field, value in (('code', 0), ('marker', None), ('classification', 'TIMEOUT'),
                             ('stderr', b'Ran 14 tests in 0.1s\n\nOK\n'),
                             ('fixture_cleanup', False)):
            bad = copy.deepcopy(self.observations['assertion'])
            setattr(bad, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                campaign.check('assertion', bad, TEST_IDS)
        bad = copy.deepcopy(self.observations['hook_candidate'])
        bad.collector['observed_completed'] = 13
        with self.assertRaises(ValueError): campaign.check('hook_candidate', bad, TEST_IDS)
        bad = copy.deepcopy(self.observations['timeout'])
        bad.events = self.observations['hook_candidate'].events
        with self.assertRaises(ValueError): campaign.check('timeout', bad, TEST_IDS)

    def test_inventory_cannot_be_empty_duplicate_or_smaller(self):
        for ids in ((), TEST_IDS[:1], (TEST_IDS[0],) * 14):
            with self.assertRaises(ValueError):
                campaign.check('focused', self.observations['hook_candidate'], ids)

    def test_uncertain_cleanup_stops_remaining_execution(self):
        observation = copy.deepcopy(self.observations['hook_candidate'])
        observation.fixture_cleanup = False
        visits = []
        class Unclean:
            def run(self, case):
                visits.append(case)
                return observation
        report = campaign.campaign(Unclean(), campaign.SYNTHETIC_IDS)
        self.assertEqual(visits, ['focused'])
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertTrue(all(report['cases'][name]['status'] == 'NOT_RUN'
                            for name in campaign.CASES[1:]))
        class CleanupError:
            def run(self, case):
                raise OSError('cleanup failed, private details')
        report = campaign.campaign(CleanupError(), campaign.SYNTHETIC_IDS)
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertTrue(all(report['cases'][name]['status'] == 'NOT_RUN'
                            for name in campaign.CASES[1:]))
        self.assertNotIn('private details', json.dumps(report))

    def test_private_outcome_oracle_rejects_wrong_ids_outcomes_and_missing_events(self):
        # MODELED wire-decoder output only; not evidence about the private API.
        observations = copy.deepcopy(self.observations)
        self.assertEqual(campaign.outcome_check(observations), 'NOT_AVAILABLE')
        for case in ('hook_baseline', 'hook_candidate', 'assertion', 'skip'):
            ids = campaign.BASELINE_IDS if case == 'hook_baseline' else campaign.SYNTHETIC_IDS
            observations[case].outcomes = tuple((name, 'failure' if i == 0 and case in ('assertion', 'skip')
                                                else 'success') for i, name in enumerate(ids))
        self.assertEqual(campaign.outcome_check(observations), 'PASS')
        original = observations['assertion'].outcomes
        for bad in (original[:-1], original + original[:1],
                    ((original[0][0], 'success'),) + original[1:],
                    (('unknown', 'failure'),) + original[1:]):
            observations['assertion'].outcomes = bad
            self.assertEqual(campaign.outcome_check(observations), 'FAIL')

    def test_paired_report_cases_require_valid_control_and_rejected_mutation(self):
        # MODELED predicate tests orchestrator discrimination, not controller.
        class Model:
            def report_accepts(self, report, sha, clean):
                return report['sha'] == sha and report['tests_status'] == 'PASS' and clean
        self.assertTrue(all(v['status'] == 'PASS' for v in campaign.report_checks(Model()).values()))
        class AlwaysReject:
            def report_accepts(self, *args): return False
        self.assertTrue(all(v['status'] == 'FAIL' for v in campaign.report_checks(AlwaysReject()).values()))
        class AlwaysAccept:
            def report_accepts(self, *args): return True
        self.assertTrue(all(v['status'] == 'FAIL' for v in campaign.report_checks(AlwaysAccept()).values()))


if __name__ == '__main__':
    unittest.main()
