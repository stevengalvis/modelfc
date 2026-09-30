"""Private counterfactual market evidence, using the established offline runner."""

from copy import deepcopy
import hashlib
import json
import math
import stat
import unittest
from unittest.mock import patch

from modelfc import corner_opportunities as opportunities
from modelfc import corner_prospective as runner
from modelfc import corner_shadow_decisions as decisions
from modelfc.corner_analysis_store import _canonical_hash
from modelfc.ledger_storage import LedgerError
from tests.test_corner_prospective import PilotTests


class QualificationParityTests(unittest.TestCase):
    def test_both_directions_threshold_price_and_watchlist(self):
        policy = opportunities.current_qualification_policy()
        self.assertEqual((policy['version'], policy['minimum_american_odds'],
                          policy['minimum_no_vig_edge'], policy['no_vig_price_source']),
                         ('team-total-no-vig-v1', -200, .05, 'decimal_odds'))
        for direction in ('OVER', 'UNDER'):
            selection = {'direction': direction, 'american_odds': -200}
            implied = {'OVER': .5, 'UNDER': .5}
            at = opportunities.qualification_decision(selection, implied, 1.0, .55, policy)
            self.assertEqual(at['no_vig_market_probability'], .5)
            self.assertTrue(at['qualified'])  # 1e-12 threshold tolerance
            self.assertTrue(at['watchlisted'])
            self.assertFalse(opportunities.qualification_decision(
                selection, implied, 1.0, .55 - 2e-12, policy)['qualified'])
            selection['american_odds'] = -201
            self.assertFalse(opportunities.qualification_decision(
                selection, implied, 1.0, .99, policy)['qualified'])
            selection['american_odds'] = 100
            for champion, shadow, expected in ((.4, .4, (False, False)),
                                               (.6, .4, (True, False)),
                                               (.4, .6, (False, True)),
                                               (.6, .6, (True, True))):
                actual = tuple(opportunities.qualification_decision(
                    selection, implied, 1.0, probability, policy)['qualified']
                    for probability in (champion, shadow))
                self.assertEqual(actual, expected)


class DecisionEvidenceTests(unittest.TestCase):
    def setUp(self):
        PilotTests.setUp(self)

    sleep = PilotTests.sleep
    http = PilotTests.http
    control = PilotTests.control
    run_pilot = PilotTests.run_pilot
    capture_paths = PilotTests.capture_paths
    after_kickoff = PilotTests.after_kickoff

    def evidence(self):
        paths = list((self.state / 'shadow-decisions').rglob('*.json'))
        return [json.loads(path.read_text()) for path in sorted(paths)]

    def public_bytes(self):
        return {str(p.relative_to(self.state)): p.read_bytes() for family in
                ('analyses', 'predictions', 'prediction-targets', 'market-observations', 'opportunities')
                for p in (self.state / family).rglob('*.json')}

    def test_source_decisions_are_private_complete_and_match_champion_opportunities(self):
        report = self.run_pilot()
        self.assertEqual(report['provider_requests'], 3)
        self.assertEqual(len(self.evidence()), 1)
        record = self.evidence()[0]
        self.assertEqual(record['policy']['minimum_no_vig_edge'], opportunities.MINIMUM_NO_VIG_EDGE)
        self.assertEqual(record['policy']['watchlist_no_vig_edge'], opportunities.WATCHLIST_NO_VIG_EDGE)
        self.assertEqual(record['release_sha'],
                         json.loads(next((self.state / 'shadow-observation-policies').glob('*.json')).read_text())['release_sha'])
        self.assertTrue(record['source_observation'])
        self.assertGreater(len(record['decisions']), 3)
        self.assertTrue(any(not d['champion']['qualified'] and not d['shadow']['qualified']
                            for d in record['decisions']))
        production = opportunities.prediction_records(self.state)[0]
        stored = opportunities.opportunity_records(self.state, production['prediction_id'])
        qualified = {(d['target_id'], d['selection_id']) for d in record['decisions']
                     if d['champion']['qualified']}
        self.assertEqual(qualified, {(o['target_id'], o['selection_id']) for o in stored})
        for entry in record['decisions']:
            pair = entry['paired_implied_probabilities']
            no_vig = pair[entry['direction']] / math.fsum(pair.values())
            self.assertEqual(entry['champion']['no_vig_market_probability'], no_vig)
            self.assertEqual(entry['shadow']['no_vig_market_probability'], no_vig)
            self.assertEqual(entry['champion']['decisive_probability'] - no_vig,
                             entry['champion']['no_vig_probability_edge'])
        self.assertEqual(decisions.compare_decisions(self.state)['paired_decision_snapshots'],
                         len(record['decisions']))
        self.assertFalse((self.state / 'shadow-decisions').is_symlink())
        for family in ('shadow-decisions', 'shadow-observation-policies'):
            for path in (self.state / family).rglob('*.json'):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
        self.assertNotIn('offline-secret', json.dumps(record))
        self.assertNotIn('raw_response', json.dumps(record))

    def test_champion_evidence_bytes_match_pre_extraction_main_goldens(self):
        # Recorded from the unmodified ceed1e3 main test harness. This detects
        # policy refactors that alter opportunity IDs, bytes or target evidence.
        self.run_pilot()
        # Prediction bytes intentionally freeze the executing release SHA and
        # thus vary with the branch; target and opportunity bytes do not.
        expected = {'prediction-targets':
                    '8b60fc4926fb99154baaf1f3ff07d76e1ed2e120471d26c9c458133010a3b6e6'}
        for family, digest in expected.items():
            files = sorted((self.state / family).rglob('*.json'))
            self.assertEqual(hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest(), digest)
        offers = [hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  sorted((self.state / 'opportunities').rglob('*.json'))]
        self.assertEqual(offers, [
            '42a61bbed9f0a105c7e715895b3a476a4eb5417993a39ff5189e7941ff834b9e',
            'efb642fb0cd2b96e1c199e65fa7ce52586beae047db031c557252b1d92028c0b',
            '614ab58fab5a5f51250b0c5fcf9a17820ec619e39b5ac1e665e8883399f811f3',
        ])

    def test_later_snapshot_replay_and_settled_hypothetical_events(self):
        self.run_pilot()
        self.now = self.now.replace(hour=10)
        second = self.run_pilot()
        self.assertEqual(second['provider_requests'], 2)
        records = sorted(self.evidence(), key=lambda item: item['observed_at_utc'])
        self.assertEqual(len(records), 2)
        self.assertEqual([r['source_observation'] for r in records], [True, False])
        self.assertNotEqual(records[0]['observation_id'], records[1]['observation_id'])
        self.assertEqual(self.run_pilot()['provider_requests'], 0)
        self.assertEqual(sorted(self.evidence(), key=lambda item: item['observed_at_utc']), records)
        self.after_kickoff()
        self.assertEqual(self.run_pilot()['outcomes_created'], 1)
        report = decisions.compare_decisions(self.state)
        self.assertEqual(report['paired_settled_fixtures'], 1)
        self.assertEqual(report['observation_snapshots'], 2)
        self.assertEqual(report['missing_assessments'], 0)
        self.assertEqual(report['champion']['settled_hypothetical_qualifying_events'],
                         report['champion']['wins'] + report['champion']['losses'] + report['champion']['pushes'])
        self.assertEqual(report['shadow']['settled_hypothetical_qualifying_events'],
                         report['shadow']['wins'] + report['shadow']['losses'] + report['shadow']['pushes'])
        for name in ('champion', 'shadow'):
            item = report[name]
            self.assertAlmostEqual(item['roi_on_settled_hypothetical_events'],
                                   item['standardized_realized_units'] /
                                   item['settled_hypothetical_qualifying_events'])
            self.assertLessEqual(item['unique_target_bookmaker_opportunities'],
                                 item['hypothetical_qualifying_events'])

    def test_later_only_lines_never_expand_frozen_shadow_cohort(self):
        full = deepcopy(self.payload)
        self.payload['bookmakerOdds']['fanduel']['markets'].pop('101420')
        self.run_pilot()
        frozen = json.loads(next((self.state / 'shadow-predictions').glob('*.json')).read_text())
        before = {item['production_target_id'] for item in frozen['targets']}
        self.payload = full
        self.now = self.now.replace(hour=10)
        self.run_pilot()
        later = next(r for r in self.evidence() if not r['source_observation'])
        self.assertGreater(later['later_only_targets_excluded'], 0)
        self.assertTrue({d['target_id'] for d in later['decisions']} <= before)
        self.assertEqual({t['production_target_id'] for t in
                          json.loads(next((self.state / 'shadow-predictions').glob('*.json')).read_text())['targets']},
                         before)

    def test_inconsistent_later_pair_does_not_destroy_other_decisions(self):
        self.run_pilot()
        # An existing later observation may retain a provider price mismatch.
        self.now = self.now.replace(hour=10)
        self.payload['bookmakerOdds']['draftkings']['markets']['101432']['outcomes']['101432']['players']['0']['priceAmerican'] = '-200'
        self.run_pilot()
        later = next(r for r in self.evidence() if not r['source_observation'])
        source = next(r for r in self.evidence() if r['source_observation'])
        self.assertLess(len(later['decisions']), len(source['decisions']))
        self.assertGreater(len(later['decisions']), 0)
        self.assertEqual(decisions.compare_decisions(self.state)['missing_assessments'], 0)

    def test_loss_uses_one_unit_and_push_consumes_settled_unit(self):
        self.run_pilot()
        self.after_kickoff()
        self.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n'
                                      'E1,20/09/2026,Wolves,West Brom,2,1,H,20,20\n')
        self.run_pilot()
        report = decisions.compare_decisions(self.state)
        for name in ('champion', 'shadow'):
            item = report[name]
            self.assertGreater(item['losses'], 0)
            self.assertEqual(item['standardized_realized_units'], -item['losses'])
            self.assertEqual(item['roi_on_settled_hypothetical_events'], -1)

        from modelfc.corner_prospective_read import _profit, _target_settlement
        whole = {'status': 'SUPPORTED', 'market_type': 'TEAM_TOTAL', 'team_side': 'HOME',
                 'direction': 'OVER', 'line': 5.0}
        outcome = {'result': {'home_corners': 5, 'away_corners': 3}}
        self.assertEqual(_target_settlement(whole, outcome)[0], 'PUSH')
        self.assertEqual(_profit('PUSH', 105), 0.0)
        self.assertEqual(_profit('WIN', 105), 1.05)
        self.assertEqual(_profit('LOSS', 105), -1.0)

    def test_crash_between_champion_and_private_assessment_recovers_without_quote(self):
        with patch.object(runner, 'store_assessment', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pilot()
        self.assertGreater(len(list((self.state / 'opportunities').rglob('*.json'))), 0)
        self.assertEqual(self.evidence(), [])
        public = self.public_bytes()
        budget = self.control()['period']['reserved']
        result = self.run_pilot()
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(self.control()['period']['reserved'], budget)
        self.assertEqual(self.public_bytes(), public)
        self.assertEqual(len(self.evidence()), 1)
        self.assertEqual(self.run_pilot()['provider_requests'], 0)
        self.assertEqual(len(self.evidence()), 1)

    def test_missing_or_failed_stamp_cannot_be_manufactured_on_replay(self):
        with patch.object(runner, 'stamp_observation', side_effect=OSError('private-secret')):
            result = self.run_pilot()
        self.assertIn('SHADOW_ASSESSMENT_MISSING', result['reasons'])
        self.assertGreater(result['opportunities_created'], 0)
        self.assertNotIn('private-secret', str(result))
        self.assertEqual(self.evidence(), [])
        public = self.public_bytes()
        self.assertEqual(self.run_pilot()['provider_requests'], 0)
        self.assertEqual(self.public_bytes(), public)
        report = decisions.compare_decisions(self.state)
        self.assertEqual(report['missing_assessments'], 1)

    def test_post_kickoff_stamp_rejected_without_private_publication(self):
        self.run_pilot()
        production = opportunities.prediction_records(self.state)[0]
        observed = deepcopy(opportunities.prediction_observations(self.state, production)[0])
        observed['observation_id'] = 'a' * 32
        self.after_kickoff()
        from modelfc.ledger_storage import git_commit_sha
        with self.assertRaisesRegex(LedgerError, 'INVALID_SHADOW_DECISION'):
            decisions.stamp_observation(self.state, observed, git_commit_sha(), clock=lambda: self.now)
        self.assertFalse((self.state / 'shadow-observation-policies' / ('a' * 32 + '.json')).exists())

    def test_post_kickoff_observation_cannot_join_a_frozen_assessment(self):
        self.run_pilot()
        from modelfc.corner_shadow import read_shadow
        production = opportunities.prediction_records(self.state)[0]
        observation = deepcopy(opportunities.prediction_observations(self.state, production)[0])
        stamp = decisions._policy(self.state, observation)
        shadow = read_shadow(self.state, production['analysis_id'])
        observation['retrieved_at_utc'] = production['fixture']['kickoff_at']
        with self.assertRaisesRegex(LedgerError, 'INVALID_SHADOW_DECISION'):
            decisions._assessment(self.state, production, observation, shadow, stamp)

    def test_unsupported_source_target_is_not_reported_as_later_only(self):
        from modelfc.corner_shadow import read_shadow
        self.run_pilot()
        production = opportunities.prediction_records(self.state)[0]
        observation = opportunities.prediction_observations(self.state, production)[0]
        shadow = deepcopy(read_shadow(self.state, production['analysis_id']))
        shadow['targets'].pop()  # synthetic unsupported source-cohort fixture
        stamp = decisions._policy(self.state, observation)
        assessment = decisions._assessment(self.state, production, observation, shadow, stamp)
        self.assertEqual(assessment['later_only_targets_excluded'], 0)

    def test_policy_and_input_tampering_fail_closed_without_public_mutation(self):
        self.run_pilot()
        baseline = decisions.compare_decisions(self.state)
        with patch.object(decisions, 'current_qualification_policy',
                          return_value={'version': 'future-v2'}):
            self.assertEqual(decisions.compare_decisions(self.state), baseline)
        public = self.public_bytes()
        original = next((self.state / 'shadow-observation-policies').glob('*.json'))
        content = original.read_bytes()
        value = json.loads(content)
        try:
            value['policy']['minimum_no_vig_edge'] = .99
            value['record_hash'] = _canonical_hash({k: v for k, v in value.items() if k != 'record_hash'})
            original.write_text(json.dumps(value))
            with self.assertRaisesRegex(LedgerError, 'INVALID_SHADOW_DECISION'):
                decisions.compare_decisions(self.state)
        finally:
            original.write_bytes(content)
        decision_path = next((self.state / 'shadow-decisions').rglob('*.json'))
        old = decision_path.read_bytes()
        try:
            value = json.loads(old)
            value['decisions'][0]['shadow']['qualified'] = not value['decisions'][0]['shadow']['qualified']
            value['record_hash'] = _canonical_hash({k: v for k, v in value.items() if k != 'record_hash'})
            decision_path.write_text(json.dumps(value))
            with self.assertRaisesRegex(LedgerError, 'INVALID_SHADOW_DECISION'):
                decisions.compare_decisions(self.state)
        finally:
            decision_path.write_bytes(old)
        self.assertEqual(self.public_bytes(), public)

    def test_private_failure_preserves_public_read_contract(self):
        from fastapi.testclient import TestClient
        from modelfc.corner_api import create_app
        with patch.object(runner, 'store_assessment', side_effect=OSError('private-secret')):
            result = self.run_pilot()
        self.assertGreater(result['opportunities_created'], 0)
        public = self.public_bytes()
        client = TestClient(create_app(state_dir=self.state, data_config_path=self.config))
        urls = ['/api/v1/predictions', '/api/v1/opportunities', '/api/v1/prospective/performance']
        opportunity = client.get(urls[1]).json()[0]
        urls.append('/api/v1/opportunities/' + opportunity['opportunity_id'])
        baseline = [client.get(url).json() for url in urls]
        self.run_pilot()
        self.assertEqual(self.public_bytes(), public)
        self.assertEqual([client.get(url).json() for url in urls], baseline)

    def test_one_shadow_capture_failure_does_not_suppress_other_fixture(self):
        self.fixtures += [dict(self.fixtures[0], fixtureId='second')]
        original = runner.store_shadow_from_capture
        calls = 0
        def fail_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('first private failure')
            return original(*args, **kwargs)
        with patch.object(runner, 'store_shadow_from_capture', side_effect=fail_first):
            report = self.run_pilot()
        self.assertEqual(report['captures_created'], 2)
        self.assertGreater(report['opportunities_created'], 0)
        self.assertEqual(report['reasons'], ['SHADOW_CAPTURE_FAILED'])
        self.assertEqual(len(self.evidence()), 1)
        self.assertEqual(len(list((self.state / 'shadow-predictions').glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
