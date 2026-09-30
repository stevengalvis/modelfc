"""Parity and leakage checks for the frozen DeepFC shadow formula."""

from datetime import date, timedelta
import unittest

from modelfc.corner_shadow import expected_corners
from modelfc.matches import TeamCornerObservation, Venue


class ShadowFormulaTests(unittest.TestCase):
    def test_weighted_league_attack_and_concessions(self):
        prediction_date = date(2020, 1, 1)
        old = prediction_date - timedelta(days=360)
        recent = prediction_date - timedelta(days=180)
        history = [
            TeamCornerObservation(old, "A", "B", Venue.HOME, 2, 4),
            TeamCornerObservation(old, "B", "A", Venue.AWAY, 4, 2),
            TeamCornerObservation(recent, "A", "C", Venue.HOME, 10, 3),
            TeamCornerObservation(recent, "C", "A", Venue.AWAY, 3, 10),
            TeamCornerObservation(recent, "D", "B", Venue.HOME, 8, 6),
            TeamCornerObservation(recent, "B", "D", Venue.AWAY, 6, 8),
        ]
        league = (2 * .25 + 10 * .5 + 8 * .5 + 5) / (.25 + .5 + .5 + 5)
        attack = (2 * .25 + 10 * .5 + 5 * league) / (.25 + .5 + 5)
        allowed = (2 * .25 + 8 * .5 + 5 * league) / (.25 + .5 + 5)
        self.assertAlmostEqual(
            expected_corners(history, "A", "B", Venue.HOME, prediction_date),
            attack * allowed / league,
        )
        with self.assertRaises(ValueError):
            expected_corners(history + [TeamCornerObservation(
                prediction_date, "A", "B", Venue.HOME, 100, 0,
            )], "A", "B", Venue.HOME, prediction_date)



# Numeric goldens generated from the exact merged DeepFC source commit. DeepFC
# is not imported or installed by this repository's tests/runtime.
class DeepFcParityTests(unittest.TestCase):
    def test_frozen_deepfc_forecasts_dispersion_and_half_line_probabilities(self):
        from modelfc.corner_shadow import dispersion
        from modelfc.corner_forecasts import corner_line_probabilities
        goldens = (
            (50, 6.665382328598902, 5.44601825913866, .1684614742465982,
             (.7941089073086126, .6849292450617792, .5701691501629262, .45953086836859314)),
            (54, 6.652095923832916, 5.435833279770373, .1676063953039314,
             (.7936638548103339, .6841795674921826, .5691338002173263, .45828418794709136)),
            (59, 6.561468185440162, 5.50135856441713, .162181253090344,
             (.790325483389002, .6787377392289788, .5617729622443858, .44955556413325204)),
        )
        for n, home, away, alpha, probabilities in goldens:
            when = date(2019, 1, 1) + timedelta(days=7 * n)
            history = []
            for i in range(n):
                played = date(2019, 1, 1) + timedelta(days=7 * i)
                hc, ac = (2, 10, 4, 8)[i % 4], (9, 1, 7, 3)[i % 4]
                history += [TeamCornerObservation(played, 'A', 'B', Venue.HOME, hc, ac),
                            TeamCornerObservation(played, 'B', 'A', Venue.AWAY, ac, hc)]
            self.assertAlmostEqual(expected_corners(history, 'A', 'B', Venue.HOME, when), home, places=13)
            self.assertAlmostEqual(expected_corners(history, 'B', 'A', Venue.AWAY, when), away, places=13)
            self.assertAlmostEqual(dispersion(history), alpha, places=14)
            for line, expected in zip((3.5, 4.5, 5.5, 6.5), probabilities):
                self.assertAlmostEqual(corner_line_probabilities(home, line, 1 / alpha).over, expected, places=12)

    def test_exact_poisson_fallback_and_future_exclusion(self):
        from modelfc.corner_shadow import dispersion
        history = [TeamCornerObservation(date(2019, 1, 1), 'A', 'B', Venue.HOME, 4, 4)] * 6
        self.assertEqual(dispersion(history), 0)
        for day in (date(2020, 1, 1), date(2020, 1, 2)):
            with self.assertRaises(ValueError):
                expected_corners(history + [TeamCornerObservation(day, 'A', 'B', Venue.HOME, 4, 4)],
                                 'A', 'B', Venue.HOME, date(2020, 1, 1))


class ShadowEvidenceTests(unittest.TestCase):
    def setUp(self):
        from tests import test_corner_prospective as pilot
        pilot.PilotTests.setUp(self)

    # Reuse the established deterministic runner harness, not a second provider.
    from tests import test_corner_prospective as _pilot
    sleep = _pilot.PilotTests.sleep
    http = _pilot.PilotTests.http
    control = _pilot.PilotTests.control
    run_pilot = _pilot.PilotTests.run_pilot
    capture_paths = _pilot.PilotTests.capture_paths
    after_kickoff = _pilot.PilotTests.after_kickoff

    def identity(self):
        import json
        return json.loads(self.capture_paths()[0].read_text())['analysis_id']

    def shadow_path(self):
        return self.state / 'shadow-predictions' / (self.identity() + '.json')

    def public_bytes(self):
        families = ('analyses', 'predictions', 'prediction-targets', 'market-observations', 'opportunities')
        return {str(p.relative_to(self.state)): p.read_bytes() for family in families
                for p in (self.state / family).rglob('*.json')}

    def tamper(self, change, *, rehash=True):
        import json
        from modelfc.corner_analysis_store import _canonical_hash
        record = json.loads(self.shadow_path().read_text())
        change(record)
        if rehash:
            record['record_hash'] = _canonical_hash({k: v for k, v in record.items() if k != 'record_hash'})
        self.shadow_path().write_text(json.dumps(record))

    def test_recover_crash_after_analysis_and_after_prediction(self):
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        from modelfc.providers import oddspapi as provider
        real = provider.capture_quotes
        def interrupt(*args, **kwargs):
            real(*args, **kwargs)
            raise KeyboardInterrupt
        with patch.object(provider, 'capture_quotes', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pilot()
        analysis = self.identity()
        self.assertFalse((self.state / 'shadow-predictions').exists())
        self.assertFalse((self.state / 'prospective/run-receipts').exists())
        original = self.capture_paths()[0].read_bytes()
        result = self.run_pilot()
        self.assertNotIn('SHADOW_CAPTURE_FAILED', result['reasons'])
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(self.control()['period']['reserved'], 3)
        self.assertEqual(self.capture_paths()[0].read_bytes(), original)
        frozen = shadow.read_shadow(self.state, analysis)
        self.assertTrue(frozen['targets'])
        before = self.shadow_path().read_bytes()
        self.run_pilot()
        self.assertEqual(self.shadow_path().read_bytes(), before)
        self.assertEqual(len(list(self.shadow_path().parent.glob('*.json'))), 1)
        # A crash at the next narrower window after the prediction exists also
        # recovers without touching champion evidence or requesting prices.
        self.shadow_path().unlink()  # synthetic interrupted-publication fixture only
        with patch.object(runner, 'store_shadow_from_capture', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pilot()
        public = self.public_bytes()
        self.assertEqual(self.run_pilot()['provider_requests'], 0)
        self.assertEqual(self.public_bytes(), public)

    def test_history_changed_recovery_is_explicit_and_never_backfilled(self):
        from unittest.mock import patch
        from modelfc import corner_prospective as runner
        from modelfc.prospective_run_receipts import latest
        with patch.object(runner, 'store_shadow_from_capture', side_effect=OSError('private-secret')):
            self.run_pilot()
        public = self.public_bytes()
        self.setup.history.write_text(self.setup.history.read_text() + 'E1,19/09/2026,Wolves,West Brom,8,2\n')
        self.now += timedelta(seconds=1)
        report = self.run_pilot()
        self.assertEqual(report['provider_requests'], 0)
        self.assertIn('SHADOW_CAPTURE_FAILED', report['reasons'])
        self.assertIn('SHADOW_CAPTURE_FAILED', latest(self.state, now=self.now)['summary']['reasons'])
        self.assertFalse(self.shadow_path().exists())
        self.assertEqual(self.public_bytes(), public)
        self.assertNotIn('private-secret', str(report))

    def test_missing_shadow_cannot_be_recovered_after_kickoff_or_release_change(self):
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        from modelfc.ledger_storage import LedgerError
        with patch.object(runner, 'store_shadow_from_capture', side_effect=RuntimeError('isolated failure')):
            self.assertGreater(self.run_pilot()['opportunities_created'], 0)
        with patch.object(shadow, 'git_commit_sha', return_value='a' * 40):
            with self.assertRaisesRegex(LedgerError, 'SHADOW_RELEASE_CHANGED'):
                shadow.store_shadow_from_capture(self.state, self.config, self.identity())
        self.now = self.now.replace(hour=16)
        with self.assertRaisesRegex(LedgerError, 'SHADOW_WINDOW_CLOSED'):
            shadow.store_shadow_from_capture(self.state, self.config, self.identity())
        self.assertFalse(self.shadow_path().exists())

    def test_private_record_does_not_change_public_api_evidence_requests_or_budget(self):
        import json
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        from modelfc.corner_api import create_app
        from modelfc.ledger_storage import LedgerError
        with patch.object(runner, 'store_shadow_from_capture', side_effect=LedgerError('missing')):
            report = self.run_pilot()
        self.assertEqual(report['provider_requests'], 3)
        self.assertGreater(report['opportunities_created'], 0)
        public = self.public_bytes()
        client = TestClient(create_app(state_dir=self.state, data_config_path=self.config))
        urls = ['/api/v1/predictions', '/api/v1/opportunities', '/api/v1/prospective/performance']
        offers = client.get(urls[1]).json()
        urls += ['/api/v1/opportunities/' + offers[0]['opportunity_id']]
        responses = {url: client.get(url).json() for url in urls}
        budget = self.control()['period']['reserved']
        calls = len(self.calls)
        with patch.object(shadow, 'write_new_record', wraps=shadow.write_new_record) as writer:
            record, created = shadow.store_shadow_from_capture(self.state, self.config, self.identity())
        self.assertTrue(created)
        self.assertNotIn('evidence_state', writer.call_args.kwargs)
        self.assertEqual(self.public_bytes(), public)
        self.assertEqual(self.control()['period']['reserved'], budget)
        self.assertEqual(len(self.calls), calls)
        for url in urls:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), responses[url])
            self.assertNotIn(shadow.MODEL_NAME, response.text)
        self.assertNotIn('offline-secret', json.dumps(record))
        self.assertEqual(record['model']['competition'], 'E1')
        self.assertEqual(record['model']['deepfc_source_commit'], shadow.DEEPFC_SOURCE_COMMIT)
        self.assertEqual(shadow.compare_settled(self.state)['missing_shadow_predictions'], 0)

    def test_probabilities_are_frozen_and_pushes_excluded_from_decisive_brier(self):
        import json
        from dataclasses import replace
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow
        # Capture valid whole-line selections through the existing adapter path.
        selections = tuple(replace(item, request=replace(item.request, line=5.0))
                           if item.request.market_type == 'TEAM_TOTAL' else item
                           for item in self.setup.quotes.selections)
        # Keep only one pair per bookmaker/team, avoiding duplicate provider
        # alternates after changing lines to a single whole line.
        unique = {}
        for item in selections:
            key = (item.bookmaker, item.request.market_type, item.request.team_side, item.request.side)
            unique.setdefault(key, item)
        from tests import test_oddspapi as recorded
        self.now = recorded.NOW
        self.setup.capture(replace(self.setup.quotes, selections=tuple(unique.values())))
        self.run_pilot()
        record = shadow.read_shadow(self.state, self.identity())
        self.assertEqual({t['direction'] for t in record['targets']}, {'OVER', 'UNDER'})
        for target in record['targets']:
            self.assertGreater(target['push_probability'], 0)
            self.assertAlmostEqual(target['decisive_model_probability'],
                                   target['model_probability'] / (1 - target['push_probability']))
        original = self.shadow_path().read_bytes()
        self.after_kickoff()  # home 6: decisive; away 3: decisive
        self.run_pilot()
        before = shadow.compare_settled(self.state)
        self.assertGreater(before['market_line_targets'], 0)
        import math
        from modelfc import corner_opportunities as opportunities
        production = opportunities.load_prediction(self.state, record['production_prediction_id'])
        self.assertAlmostEqual(before['shadow']['team_mae'], (abs(6 - record['distribution']['home_expected_corners'])
                               + abs(3 - record['distribution']['away_expected_corners'])) / 2)
        self.assertAlmostEqual(before['production']['team_mae'], (abs(6 - production['distribution']['home_expected_corners'])
                               + abs(3 - production['distribution']['away_expected_corners'])) / 2)
        squared = []
        for target in record['targets']:
            actual = 6 if target['team_side'] == 'HOME' else 3
            win = int(actual > target['line'] if target['direction'] == 'OVER' else actual < target['line'])
            squared.append((target['decisive_model_probability'] - win) ** 2)
        self.assertAlmostEqual(before['shadow']['mean_brier'], math.fsum(squared) / len(squared))
        with patch.object(shadow, 'corner_line_probabilities', side_effect=AssertionError('must never regenerate')):
            self.assertEqual(shadow.compare_settled(self.state), before)
            self.assertFalse(shadow.store_shadow_from_capture(self.state, self.config, self.identity())[1])
        self.assertEqual(self.shadow_path().read_bytes(), original)
        # Corrected outcome tip sets one team exactly on the whole line.
        from modelfc import corner_analysis_outcomes as outcomes
        tip = json.loads(next((self.state / 'analysis-outcomes').rglob('*.json')).read_text())
        self.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,5,3\n')
        outcomes.record_outcome(state_dir=self.state, analysis_id=self.identity(), data_config_path=self.config,
                                idempotency_key='shadow-correction', supersedes_outcome_id=tip['outcome_id'], correction_reason='Synthetic corrected corners')
        corrected = shadow.compare_settled(self.state)
        self.assertGreater(corrected['push_targets_excluded'], 0)
        self.assertLess(corrected['market_line_targets'], before['market_line_targets'])

    def test_hash_schema_probability_and_reference_corruption_fail_closed(self):
        import json
        from modelfc import corner_shadow as shadow
        from modelfc.ledger_storage import LedgerError
        self.run_pilot()
        original = self.shadow_path().read_bytes()
        changes = [
            (lambda r: r.update(schema_version=99), True),
            (lambda r: r.update(production_prediction_hash='a' * 64), True),
            (lambda r: r['targets'][0].update(model_probability=1.1), True),
            (lambda r: r['targets'][0].update(push_probability=1.0), True),
            (lambda r: r['targets'][0].update(decisive_model_probability=.123), True),
            (lambda r: r['targets'][0].update(production_target_hash='a' * 64), True),
            (lambda r: r.update(created_at_utc=r['fixture']['kickoff_at']), True),
            (lambda r: r['distribution'].update(home_expected_corners=99), False),
        ]
        for change, rehash in changes:
            with self.subTest(change=change):
                self.shadow_path().write_bytes(original)
                self.tamper(change, rehash=rehash)
                with self.assertRaises(LedgerError):
                    shadow.read_shadow(self.state, self.identity())
                with self.assertRaises(LedgerError):
                    shadow.compare_settled(self.state)
        self.shadow_path().write_bytes(original)
        self.assertEqual(shadow.read_shadow(self.state, self.identity())['schema_version'], 2)

    def test_existing_shadow_validates_without_new_history_and_failed_publication_is_atomic(self):
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        self.run_pilot()
        original = self.shadow_path().read_bytes()
        self.setup.history.write_text('newer or unavailable history')
        with patch.object(shadow, 'configured_history', side_effect=AssertionError('must not read history')):
            self.assertFalse(shadow.store_shadow_from_capture(self.state, self.config, self.identity())[1])
        self.assertEqual(self.shadow_path().read_bytes(), original)
        self.shadow_path().unlink()
        # Reset matching original CSV before exercising publication failure.
        self.setup.history.write_text('\n'.join(['Div,Date,HomeTeam,AwayTeam,HC,AC'] + [
            f"E1,{(self.now - timedelta(days=111-i)).strftime('%d/%m/%Y')},Wolves,West Brom,{3+i%6},{2+i%4}"
            for i in range(110)]) + '\n')
        public = self.public_bytes()
        with patch.object(shadow, 'write_new_record', side_effect=OSError('disk')):
            report = self.run_pilot()
        self.assertIn('SHADOW_CAPTURE_FAILED', report['reasons'])
        self.assertFalse(self.shadow_path().exists())
        self.assertEqual(self.public_bytes(), public)

    def test_source_targets_half_lines_and_later_only_targets_are_counted(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from modelfc import corner_shadow as shadow, corner_opportunities as opportunities
        from modelfc.corner_market_data import CornerMarketObservation
        from modelfc.providers import oddspapi as provider
        from tests import test_oddspapi as recorded
        self.run_pilot()
        record = shadow.read_shadow(self.state, self.identity())
        self.assertTrue(record['targets'])
        self.assertTrue(all(t['push_probability'] == 0 for t in record['targets']))
        self.assertEqual({t['direction'] for t in record['targets']}, {'OVER', 'UNDER'})
        later = self.now + timedelta(minutes=1)
        selections = tuple(replace(item, request=replace(item.request, line=50.5), retrieved_at=later.isoformat())
                           for item in self.setup.quotes.selections if item.request.market_type == 'TEAM_TOTAL')
        fixture = provider.OddsPapiMarketData.fixture_from_provenance(self.fixtures[0], self.now)
        observation, _ = opportunities.store_market_observation(self.state, CornerMarketObservation(
            fixture=fixture, selections=selections, availability={'draftkings': {'status': 'AVAILABLE'}},
            provenance=SimpleNamespace(retrieved_at=later.isoformat())))
        prediction = opportunities.load_prediction(self.state, record['production_prediction_id'])
        opportunities.assess_observation(self.state, prediction, observation)
        self.after_kickoff()
        self.run_pilot()
        report = shadow.compare_settled(self.state)
        self.assertEqual(report['later_targets_excluded'], 4)
        self.assertEqual(report['shadow_predictions'], 1)
        self.assertEqual(report['team_forecasts'], 2)

    def test_missing_pairs_and_candidate_coverage_are_visible(self):
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        from modelfc.ledger_storage import LedgerError
        with patch.object(runner, 'store_shadow_from_capture', side_effect=LedgerError('missing')):
            self.run_pilot()
        report = shadow.compare_settled(self.state)
        self.assertEqual((report['production_predictions'], report['missing_shadow_predictions']), (1, 1))
        self.assertIsNone(report['shadow']['mean_brier'])
        with patch.object(shadow, 'configured_history', return_value=[]):
            with self.assertRaisesRegex(LedgerError, 'SHADOW_HISTORY_INSUFFICIENT'):
                shadow.store_shadow_from_capture(self.state, self.config, self.identity())
        history = [TeamCornerObservation(date(2026, 1, 1), 'Other', 'Elsewhere', Venue.HOME, 4, 4)] * 120
        with patch.object(shadow, 'configured_history', return_value=history):
            with self.assertRaisesRegex(LedgerError, 'SHADOW_HISTORY_INSUFFICIENT'):
                shadow.store_shadow_from_capture(self.state, self.config, self.identity())

    def test_shadow_failure_continues_multiple_champion_opportunities(self):
        from unittest.mock import patch
        from modelfc import corner_prospective as runner
        self.fixtures += [dict(self.fixtures[0], fixtureId='second')]
        with patch.object(runner, 'store_shadow_from_capture', side_effect=RuntimeError('private candidate failure')):
            report = self.run_pilot()
        self.assertEqual(report['captures_created'], 2)
        self.assertGreater(report['opportunities_created'], 1)
        self.assertEqual(report['provider_requests'], 4)
        self.assertEqual(self.control()['period']['reserved'], 4)
        self.assertEqual(report['reasons'], ['SHADOW_CAPTURE_FAILED'])

    def test_publication_guard_closes_if_computation_crosses_kickoff(self):
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        with patch.object(runner, 'store_shadow_from_capture', side_effect=OSError('interrupted')):
            self.run_pilot()
        writer = shadow.write_new_record
        def cross_kickoff(*args, **kwargs):
            self.now = self.now.replace(hour=16)
            return writer(*args, **kwargs)
        with patch.object(shadow, 'write_new_record', side_effect=cross_kickoff):
            report = self.run_pilot()
        self.assertIn('SHADOW_CAPTURE_FAILED', report['reasons'])
        self.assertFalse(self.shadow_path().exists())
        self.assertEqual(list(self.shadow_path().parent.iterdir()), [])

    def test_capture_and_prediction_relationships_are_required_for_recovery(self):
        from copy import deepcopy
        from unittest.mock import patch
        from modelfc import corner_shadow as shadow, corner_prospective as runner
        from modelfc.ledger_storage import LedgerError
        with patch.object(runner, 'store_shadow_from_capture', side_effect=LedgerError('missing')):
            self.run_pilot()
        _, original, _ = shadow._context(self.state, self.identity())
        changes = [lambda r: r['capture_reference'].update(request_hash='a' * 64),
                   lambda r: r['fixture'].update(home_team='Other'),
                   lambda r: r['history'].update(source_data_hashes=[]),
                   lambda r: r['distribution'].update(home_expected_corners=99),
                   lambda r: r.update(analysis_id='a' * 32)]
        for change in changes:
            prediction = deepcopy(original)
            change(prediction)
            with self.subTest(change=change), patch.object(shadow, 'load_prediction', return_value=prediction):
                with self.assertRaises(LedgerError):
                    shadow.store_shadow_from_capture(self.state, self.config, self.identity())
        self.assertFalse(self.shadow_path().exists())

    def test_orphan_shadow_cannot_be_silently_ignored(self):
        import shutil
        from modelfc import corner_shadow as shadow
        from modelfc.ledger_storage import LedgerError
        self.run_pilot()
        shutil.rmtree(self.state / 'predictions')
        with self.assertRaises(LedgerError):
            shadow.compare_settled(self.state)


if __name__ == '__main__':
    unittest.main()
