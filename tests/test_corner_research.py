"""Private immutable research projections, using recorded offline runner evidence."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from modelfc import corner_research as research
from modelfc import corner_shadow as shadow
from modelfc import corner_shadow_decisions as decisions
from modelfc import corner_prospective_read as public
from modelfc.corner_analysis_store import _canonical_hash
from tests import test_corner_prospective as pilot


class ResearchTests(unittest.TestCase):
    setUp = pilot.PilotTests.setUp
    sleep = pilot.PilotTests.sleep
    http = pilot.PilotTests.http
    control = pilot.PilotTests.control
    run_pilot = pilot.PilotTests.run_pilot
    capture_paths = pilot.PilotTests.capture_paths
    after_kickoff = pilot.PilotTests.after_kickoff

    def snapshot(self):
        result = research.create_snapshot(self.state)
        return result['snapshot_id']

    def manifest(self, identity):
        return json.loads((self.state / 'research-snapshots' / (identity + '.json')).read_text())

    def test_empty_state_without_lazy_lock_is_valid_private_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            with patch.object(research, 'write_new_record', wraps=research.write_new_record) as publish:
                identity = research.create_snapshot(state)['snapshot_id']
            self.assertNotIn('evidence_state', publish.call_args.kwargs)
            self.assertFalse((state / '.lock').exists())
            report = research.research_summary(state, identity)
            self.assertEqual(report['coverage']['predictions_included'], 0)
            self.assertIsNone(report['forecast_quality']['production']['team_mae'])
            self.assertIsNone(report['forecast_quality']['shadow']['mean_brier'])
            self.assertIsNone(report['market_decisions']['shadow']['roi_on_settled_hypothetical_events'])
            for by in research.SEGMENTS:
                self.assertTrue(all(g['paired_team_forecasts'] == 0 for g in research.segment_comparison(state, identity, by)['groups']))
            path = state / 'research-snapshots' / (identity + '.json')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
            self.assertNotIn('offline-secret', json.dumps(report))

    def test_unsettled_paired_fixture_and_summary_parity(self):
        self.run_pilot()
        identity = self.snapshot()
        summary = research.research_summary(self.state, identity)
        self.assertEqual(summary['forecast_quality'], shadow.compare_settled(self.state))
        self.assertEqual(summary['market_decisions'], decisions.compare_decisions(self.state))
        self.assertIsNone(summary['forecast_quality']['production']['team_mae'])
        pid = self.manifest(identity)['entries'][0]['prediction']['id']
        detail = research.inspect_fixture(self.state, identity, pid)
        self.assertIsNotNone(detail['historical_context'])
        self.assertIsNotNone(detail['shadow'])
        self.assertIsNone(detail['settlement'])
        self.assertGreater(len(detail['observations'][0]['decisions']), 0)
        self.assertEqual(detail['observations'][0]['policy']['minimum_no_vig_edge'], .05)
        self.assertNotIn('raw_response', json.dumps(detail))
        self.assertNotIn(str(self.state), json.dumps(detail))
        self.assertEqual(summary['coverage']['settled_predictions'], 0)

    def test_settled_metric_and_hypothetical_report_parity(self):
        self.run_pilot()
        self.after_kickoff()
        self.run_pilot()
        identity = self.snapshot()
        report = research.research_summary(self.state, identity)
        self.assertEqual(report['forecast_quality'], shadow.compare_settled(self.state))
        self.assertEqual(report['market_decisions'], decisions.compare_decisions(self.state))
        groups = research.segment_comparison(self.state, identity, 'venue')['groups']
        self.assertEqual([g['segment'] for g in groups], ['HOME', 'AWAY'])
        self.assertEqual([g['paired_team_forecasts'] for g in groups], [1, 1])
        self.assertTrue(all(g['warnings'] == ['LOW_SAMPLE_DESCRIPTIVE_ONLY'] for g in groups))
        self.assertAlmostEqual(sum(g['forecast_quality']['production']['team_mae'] for g in groups) / 2,
                               report['forecast_quality']['production']['team_mae'])

    def test_later_observation_and_settlement_do_not_change_old_snapshot(self):
        self.run_pilot()
        identity = self.snapshot()
        before = research.research_summary(self.state, identity)
        self.now = self.now.replace(hour=10)
        self.run_pilot()
        self.after_kickoff()
        self.run_pilot()
        with patch.object(research, 'existing_read_lock', side_effect=AssertionError('analysis must not lock')):
            self.assertEqual(research.research_summary(self.state, identity), before)
        newer = research.research_summary(self.state, self.snapshot())
        self.assertEqual(newer['coverage']['settled_predictions'], 1)
        self.assertEqual(newer['market_decisions']['observation_snapshots'], 2)

    def test_outcome_correction_freezes_chain_prefix(self):
        from modelfc import corner_analysis_outcomes as outcomes
        self.run_pilot()
        self.after_kickoff()
        self.run_pilot()
        identity = self.snapshot()
        before = research.research_summary(self.state, identity)
        pid = self.manifest(identity)['entries'][0]['prediction']['id']
        detail = research.inspect_fixture(self.state, identity, pid)
        aid = self.manifest(identity)['entries'][0]['capture']['id']
        self.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,5,3\n')
        outcomes.record_outcome(state_dir=self.state, analysis_id=aid, data_config_path=self.config,
                                idempotency_key='research-correction',
                                supersedes_outcome_id=detail['settlement']['outcome_id'], correction_reason='Synthetic correction')
        self.assertEqual(research.research_summary(self.state, identity), before)
        self.assertEqual(research.inspect_fixture(self.state, identity, pid), detail)
        new = research.inspect_fixture(self.state, self.snapshot(), pid)
        self.assertNotEqual(new['settlement']['outcome_id'], detail['settlement']['outcome_id'])

    def test_missing_shadow_and_assessment_remain_missing_after_recovery(self):
        self.run_pilot()
        paths = list((self.state / 'shadow-decisions').rglob('*.json'))
        saved = [(path, path.read_bytes()) for path in paths]
        for path in paths:
            path.unlink()
        shadow_path = next((self.state / 'shadow-predictions').glob('*.json'))
        shadow_bytes = shadow_path.read_bytes()
        shadow_path.unlink()
        identity = self.snapshot()
        report = research.research_summary(self.state, identity)
        self.assertEqual(report['forecast_quality']['missing_shadow_predictions'], 1)
        self.assertEqual(report['market_decisions']['missing_assessments'], 1)
        shadow_path.write_bytes(shadow_bytes)
        for path, data in saved:
            path.write_bytes(data)
        self.assertEqual(research.research_summary(self.state, identity), report)

    def test_snapshot_identity_hash_public_isolation_and_no_budget_requests(self):
        self.run_pilot()
        reports = (public.read_predictions(self.state), public.read_opportunities(self.state), public.read_performance(self.state))
        before = {p: p.read_bytes() for p in self.state.rglob('*.json')}
        calls = len(self.calls)
        identity = self.snapshot()
        manifest = self.manifest(identity)
        payload = {k: v for k, v in manifest.items() if k != 'record_hash'}
        self.assertEqual(_canonical_hash(payload), manifest['record_hash'])
        self.assertEqual(identity, research._id('research-snapshot', {k: v for k, v in payload.items() if k != 'snapshot_id'}))
        self.assertEqual(len(self.calls), calls)
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
        self.assertEqual((public.read_predictions(self.state), public.read_opportunities(self.state), public.read_performance(self.state)), reports)
        self.assertNotEqual(self.snapshot(), identity)

    def test_tampered_manifest_and_reference_fail_closed(self):
        self.run_pilot()
        identity = self.snapshot()
        manifest = self.manifest(identity)
        path = self.state / 'research-snapshots' / (identity + '.json')
        manifest['entries'][0]['prediction']['hash'] = '0' * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaises(research.ResearchError):
            research.research_summary(self.state, identity)

    def test_symlink_state_snapshot_and_evidence_fail_closed(self):
        self.run_pilot()
        identity = self.snapshot()
        path = self.state / 'research-snapshots' / (identity + '.json')
        saved = path.read_bytes()
        path.unlink()
        elsewhere = self.state / 'elsewhere.json'
        elsewhere.write_bytes(saved)
        path.symlink_to(elsewhere)
        with self.assertRaises(research.ResearchError):
            research.research_summary(self.state, identity)
        evidence = next((self.state / 'predictions').glob('*.json'))
        evidence.unlink()
        evidence.symlink_to(elsewhere)
        with self.assertRaises(research.ResearchError):
            research.create_snapshot(self.state)

    def test_cli_json_safe_errors_and_no_path_arguments(self):
        self.run_pilot()
        identity = self.snapshot()
        with patch.dict(os.environ, {'MODELFC_STATE_DIR': str(self.state)}):
            for args, expected in ((['summary', '--snapshot-id', identity], 0),
                                   (['summary', '--snapshot-id', '../../secret'], 1)):
                stream = StringIO()
                with redirect_stdout(stream):
                    self.assertEqual(research.main(args), expected)
                data = json.loads(stream.getvalue())
                self.assertNotIn(str(self.state), json.dumps(data))
        with self.assertRaises(research.ResearchError):
            research.segment_comparison(self.state, identity, '__dict__')
        with self.assertRaises(research.ResearchError):
            research.inspect_fixture(self.state, identity, '0' * 32)

    def test_output_and_population_bounds(self):
        self.run_pilot()
        with patch.object(research, 'MAX_PREDICTIONS', 0):
            with self.assertRaises(research.ResearchError):
                self.snapshot()
        identity = self.snapshot()
        with patch.object(research, 'MAX_FIXTURE_TARGETS', 1), patch.object(research, 'MAX_FIXTURE_DECISIONS', 1):
            detail = research.inspect_fixture(self.state, identity, self.manifest(identity)['entries'][0]['prediction']['id'])
        self.assertGreater(detail['targets_omitted'], 0)
        self.assertGreater(detail['observations'][0]['decisions_omitted'], 0)
        with patch.object(research, 'MAX_RESPONSE_BYTES', 1):
            with self.assertRaises(research.ResearchError):
                research.research_summary(self.state, identity)

    def test_legacy_context_is_unknown_not_reconstructed(self):
        self.run_pilot()
        prediction_path = next((self.state / 'predictions').glob('*.json'))
        prediction = json.loads(prediction_path.read_text())
        prediction['schema_version'] = 1
        prediction.pop('historical_context')
        prediction['record_hash'] = _canonical_hash({k: v for k, v in prediction.items() if k != 'record_hash'})
        prediction_path.write_text(json.dumps(prediction))
        for family in ('shadow-predictions', 'shadow-decisions'):
            for path in (self.state / family).rglob('*.json'):
                path.unlink()
        self.run_pilot()  # Synthetic recovery recreates private links to the legacy record.
        self.after_kickoff()
        self.run_pilot()
        identity = self.snapshot()
        report = research.research_summary(self.state, identity)
        self.assertEqual(report['coverage']['legacy_context_unavailable'], 1)
        groups = research.segment_comparison(self.state, identity, 'venue_history')['groups']
        self.assertEqual([g['segment'] for g in groups], ['0-9', '10-19', '20+', 'UNKNOWN'])
        self.assertEqual(groups[-1]['paired_team_forecasts'], 2)
        self.assertTrue(all(g['paired_team_forecasts'] == 0 for g in groups[:-1]))

    def test_manifest_queries_ignore_unreferenced_new_evidence_and_history(self):
        self.run_pilot()
        identity = self.snapshot()
        report = research.research_summary(self.state, identity)
        prediction_id = self.manifest(identity)['entries'][0]['prediction']['id']
        (self.state / 'prediction-targets' / prediction_id / ('0' * 32 + '.json')).write_text('{broken')
        self.setup.history.write_text('not a history dataset')
        self.assertEqual(research.research_summary(self.state, identity), report)
        self.assertEqual(research.read_snapshot(self.state, identity)['predictions_included'], 1)

    def test_later_only_targets_and_missing_decision_parity(self):
        full = deepcopy(self.payload)
        self.payload['bookmakerOdds']['fanduel']['markets'].pop('101420')
        self.run_pilot()
        self.payload = full
        self.now = self.now.replace(hour=10)
        self.run_pilot()
        later = next(path for path in (self.state / 'shadow-decisions').rglob('*.json')
                     if not json.loads(path.read_text())['source_observation'])
        later.unlink()
        identity = self.snapshot()
        report = research.research_summary(self.state, identity)
        self.assertEqual(report['market_decisions'], decisions.compare_decisions(self.state))
        self.assertEqual(report['market_decisions']['missing_assessments'], 1)
        self.assertGreater(report['forecast_quality']['shadow_predictions'], 0)

    def test_invalid_schema_invalid_release_and_deleted_referenced_record(self):
        self.run_pilot()
        identity = self.snapshot()
        path = self.state / 'research-snapshots' / (identity + '.json')
        original = path.read_bytes()
        for field, value in (('schema_version', 99), ('release_sha', 'unknown')):
            record = json.loads(original)
            record[field] = value
            path.write_text(json.dumps(record))
            with self.assertRaises(research.ResearchError):
                research.read_snapshot(self.state, identity)
        path.write_bytes(original)
        next((self.state / 'predictions').glob('*.json')).unlink()
        with self.assertRaises(research.ResearchError):
            research.read_snapshot(self.state, identity)

    def test_lock_scope_publication_failure_and_preflight_limit(self):
        self.run_pilot()
        events = []
        original_read_lock = research.existing_read_lock
        original_state_lock = research.ledger_read_lock
        original_write = research.write_new_record
        from contextlib import contextmanager
        @contextmanager
        def runner_lock(path):
            events.append('runner-enter')
            with original_read_lock(path):
                yield
            events.append('runner-exit')
        @contextmanager
        def state_lock(path):
            events.append('state-enter')
            with original_state_lock(path):
                yield
            events.append('state-exit')
        def write(path, record, **kwargs):
            self.assertEqual(events, ['runner-enter', 'state-enter'])
            return original_write(path, record, **kwargs)
        with patch.object(research, 'existing_read_lock', side_effect=runner_lock), patch.object(research, 'ledger_read_lock', side_effect=state_lock), patch.object(research, 'write_new_record', side_effect=write):
            identity = self.snapshot()
        self.assertEqual(events, ['runner-enter', 'state-enter', 'state-exit', 'runner-exit'])
        with patch.object(research, 'existing_read_lock', side_effect=AssertionError('no diagnostic locks')), patch.object(research, 'ledger_read_lock', side_effect=AssertionError('no diagnostic locks')):
            research.research_summary(self.state, identity)
            research.segment_comparison(self.state, identity, 'venue')
            research.inspect_fixture(self.state, identity, self.manifest(identity)['entries'][0]['prediction']['id'])
        before = list((self.state / 'research-snapshots').glob('*.json'))
        with patch.object(research, 'write_new_record', side_effect=OSError('private path')):
            with self.assertRaisesRegex(research.ResearchError, '^RESEARCH_EVIDENCE_UNAVAILABLE$'):
                self.snapshot()
        self.assertEqual(list((self.state / 'research-snapshots').glob('*.json')), before)
        with patch.object(research, 'MAX_FILES', 1):
            with self.assertRaisesRegex(research.ResearchError, '^RESEARCH_LIMIT_EXCEEDED$'):
                self.snapshot()

    def test_cli_commands_and_unknown_options(self):
        self.run_pilot()
        identity = self.snapshot()
        pid = self.manifest(identity)['entries'][0]['prediction']['id']
        with patch.dict(os.environ, {'MODELFC_STATE_DIR': str(self.state)}):
            for args in (['snapshot'], ['segment', '--snapshot-id', identity, '--by', 'venue'],
                         ['fixture', '--snapshot-id', identity, '--prediction-id', pid]):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(research.main(args), 0)
                self.assertIsInstance(json.loads(output.getvalue()), dict)
        with redirect_stdout(StringIO()), self.assertRaises(SystemExit) as error:
            research.main(['summary', '--snapshot-id', identity, '--state-dir', '/secret'])
        self.assertEqual(error.exception.code, 2)

    def test_existing_empty_runner_lock_is_taken_without_creating_state_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'prospective').mkdir()
            (state / 'prospective' / 'runner.lock').touch()
            with patch.object(research, 'existing_read_lock', wraps=research.existing_read_lock) as locked:
                research.create_snapshot(state)
            self.assertEqual(locked.call_count, 1)
            self.assertFalse((state / '.lock').exists())

    def test_live_shadow_constant_drift_does_not_change_frozen_snapshot(self):
        self.run_pilot()
        identity = self.snapshot()
        expected = research.research_summary(self.state, identity)
        with patch.object(shadow, 'MODEL_VERSION', 'future-v2'), patch.object(shadow, 'HALF_LIFE_DAYS', 90), patch.object(decisions, 'MODEL_VERSION', 'future-v2'):
            self.assertEqual(research.research_summary(self.state, identity), expected)
