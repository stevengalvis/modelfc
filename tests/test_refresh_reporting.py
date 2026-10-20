"""Synthetic offline invocation/history reporting contracts."""
from datetime import date, datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from modelfc import corner_refresh, refresh_reporting as reporting
from modelfc.corner_data import CornerDataConfig
from modelfc.production_status import _refresh
from tests import test_corner_refresh as fixtures
from tests.test_corner_refresh import NEW, TODAY


class ReportingTests(unittest.TestCase):
    def test_mixed_aggregation_and_unknown_legacy(self):
        rows = [dict(league='E1', status='updated', checked_at='2026-10-20T00:00:00+00:00',
                     latest_match='2026-10-19'), dict(league='E0', status='failed',
                     checked_at='2026-10-19T00:00:00+00:00', error='HTTP 302')]
        report = dict(results=rows, invocation=[dict(league='E1', status='VALIDATED_SOURCE_RESPONSE'),
                                               dict(league='E0', status='NOT_SELECTED')])
        summary = reporting.summarize(report, date(2026, 10, 20), 14)
        self.assertEqual(summary['invocation_status'], 'SUCCESS')
        self.assertEqual(summary['dataset_health'], 'UNHEALTHY')
        self.assertEqual(summary['leagues'][1]['invocation_outcome'], 'NOT_SELECTED')
        report['invocation'][0]['status'] = 'DELIVERY_FAILED'
        self.assertEqual(reporting.summarize(report, date(2026, 10, 20), 14)['invocation_status'], 'FAILED')
        del report['invocation']
        self.assertEqual(reporting.summarize(report, date(2026, 10, 20), 14)['invocation_status'], 'UNKNOWN')

    def test_future_attempt_timestamp_is_unknown_and_unhealthy(self):
        row = dict(league='E1', status='updated', latest_match='2026-10-19',
                   checked_at='2099-01-01T00:00:00+00:00')
        report = dict(results=[row], invocation=[dict(league='E1', status='NOT_SELECTED')])
        self.assertEqual(reporting.dataset_health(row, date(2026, 10, 20), 14), 'UNKNOWN')
        self.assertEqual(reporting.summarize(report, date(2026, 10, 20), 14)['dataset_health'], 'UNKNOWN')
        self.assertTrue(corner_refresh.format_report(report, date(2026, 10, 20), 14)[1])

    def test_interruption_without_known_failure_does_not_invent_history(self):
        prior = dict(league='E1', status='failed', error=reporting.INTERRUPTED_ATTEMPT)
        success = dict(league='E1', status='updated', checked_at='2026-10-20T00:00:00+00:00')
        self.assertNotIn('last_failure', reporting.retain_history(success, prior))

    def test_interruption_materializes_legacy_failure_without_replacing_it(self):
        prior = dict(league='E1', status='failed', error='HTTP 302',
                     checked_at='2026-10-19T03:00:00+00:00')
        interrupted = dict(league='E1', status='failed', error=reporting.INTERRUPTED_ATTEMPT,
                           checked_at=None)
        self.assertEqual(reporting.retain_history(interrupted, prior)['last_failure'], prior)

    def test_missing_invalid_and_stale_timestamps(self):
        row = dict(league='E1', status='updated', latest_match='2026-10-19')
        for stamp in (None, '', 'bad', '2026-10-20T00:00:00'):
            self.assertEqual(reporting.dataset_health(dict(row, checked_at=stamp), date(2026, 10, 20), 14), 'UNKNOWN')
        row['checked_at'] = '2026-09-01T00:00:00+00:00'
        self.assertEqual(reporting.dataset_health(row, date(2026, 10, 20), 14), 'STALE')
        self.assertEqual(reporting.summarize({'results': []}, TODAY, 14)['dataset_health'], 'UNKNOWN')


class RefreshHistoryTests(unittest.TestCase):
    setUp = fixtures.CornerRefreshTests.setUp
    refresh = fixtures.CornerRefreshTests.refresh
    def test_old_failure_not_selected_keeps_time_and_nonzero_health(self):
        with patch.object(corner_refresh, 'download_csv', side_effect=OSError('HTTP 302')):
            old = corner_refresh.refresh_data(self.config, TODAY)
        original = old['results'][0]
        original['checked_at'] = '2026-10-19T03:00:00+00:00'
        original['last_failure']['checked_at'] = original['checked_at']
        (self.state / 'status.json').write_text(json.dumps(old))
        with patch.object(corner_refresh.refresh_attempts, 'enabled', return_value=True), patch.object(
                corner_refresh, 'download_csv') as download:
            report = corner_refresh.refresh_data(self.config, date(2026, 10, 20), selection=lambda: ())
        download.assert_not_called()
        self.assertEqual(report['results'][0], original)
        text, unhealthy = corner_refresh.format_report(report, TODAY, 14)
        self.assertTrue(unhealthy)
        self.assertIn('invocation NOT_SELECTED', text)
        self.assertIn(original['checked_at'], text)
        self.assertIn('Invocation status: NOT_ATTEMPTED; dataset health: UNHEALTHY', text)

    def test_new_failure_then_success_retains_bounded_history(self):
        first = self.refresh(NEW)['results'][0]
        with patch.object(corner_refresh, 'download_csv', side_effect=OSError('new failure')):
            failure = corner_refresh.refresh_data(self.config, TODAY)
        self.assertEqual(reporting.outcome(failure['invocation'][0]['status']), 'FAILED')
        self.assertEqual(failure['results'][0]['last_success']['checked_at'], first['checked_at'])
        success = self.refresh(NEW)
        row = success['results'][0]
        self.assertEqual(row['last_failure']['checked_at'], failure['results'][0]['checked_at'])
        self.assertNotIn('last_success', row['last_failure'])
        self.assertEqual(reporting.outcome(success['invocation'][0]['status']), 'SUCCESS')
        self.assertFalse(corner_refresh.format_report(success, TODAY, 14)[1])

    def test_daily_cap_reuses_immutable_receipt_without_new_attempt(self):
        self.state.mkdir(parents=True)
        (self.state / 'daily-attempt-cap.json').write_text('{"version":1,"enabled":true}')
        first = self.refresh(NEW)
        receipts = {p: p.read_bytes() for p in self.state.glob('attempts/*/*.json')}
        self.assertEqual(len(receipts), 2)
        with patch.object(corner_refresh, 'download_csv') as download:
            replay = corner_refresh.refresh_data(self.config, TODAY)
        download.assert_not_called()
        self.assertEqual(replay['results'], first['results'])
        self.assertEqual(reporting.summarize(replay, TODAY, 14)['invocation_status'], 'NOT_ATTEMPTED')
        self.assertEqual({p: p.read_bytes() for p in receipts}, receipts)

    def test_interrupted_reservation_preserves_history_across_replays(self):
        self.refresh(NEW)
        with patch.object(corner_refresh, 'download_csv', side_effect=HTTPError('https://example.invalid', 302, 'redirect refused', {}, None)):
            old = corner_refresh.refresh_data(self.config, TODAY)['results'][0]
        (self.state / 'daily-attempt-cap.json').write_text('{"version":1,"enabled":true}')
        with corner_refresh.refresh_lock(self.state):
            terminal, reserved = corner_refresh.refresh_attempts.reserve(self.state, 'SP1', 'test')
        self.assertTrue(reserved)
        receipts = {p: p.read_bytes() for p in self.state.glob('attempts/*/*.json')}
        for _ in range(2):
            with patch.object(corner_refresh, 'download_csv') as download:
                report = corner_refresh.refresh_data(self.config, TODAY)
            download.assert_not_called()
            row = report['results'][0]
            self.assertEqual(row['last_success'], old['last_success'])
            self.assertEqual(row['last_failure'], old['last_failure'])
            self.assertIsNone(row.get('checked_at'))
            self.assertIn('RESERVED_WITHOUT_TERMINAL_RECEIPT', row['error'])
            self.assertEqual(reporting.summarize(report, TODAY, 14)['invocation_status'], 'NOT_ATTEMPTED')
            text, unhealthy = corner_refresh.format_report(report, TODAY, 14)
            self.assertTrue(unhealthy)
            self.assertIn(f"last_failure {old['last_failure']['checked_at']}; failed; {old['last_failure']['error']}", text)
            self.assertFalse(terminal.exists())
            self.assertEqual({p: p.read_bytes() for p in self.state.glob('attempts/*/*.json')}, receipts)

        # A later real attempt must retain the actual failure, not promote the
        # interrupted reservation's unknown outcome into failure history.
        next_day = datetime.now(timezone.utc) + timedelta(days=1)
        with patch.object(corner_refresh.refresh_attempts, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = next_day
            success = self.refresh(NEW)
        recovered = success['results'][0]
        self.assertEqual(recovered['status'], 'unchanged')
        self.assertEqual(recovered['last_failure'], old['last_failure'])
        self.assertEqual(recovered['last_success']['checked_at'], recovered['checked_at'])
        self.assertFalse(terminal.exists())
        self.assertEqual({p: p.read_bytes() for p in receipts}, receipts)

    def test_legacy_interruption_replay_and_recovery(self):
        for status in ('updated', 'unchanged', 'failed'):
            for timestamp in ('2026-09-15T03:00:00+00:00', None):
                for recovery in ('success', 'failure'):
                    with self.subTest(status=status, timestamp=timestamp, recovery=recovery):
                        root = self.root / f'{status}-{timestamp is None}-{recovery}'
                        config = CornerDataConfig(root, ('E1',), 14)
                        state = root / 'data' / 'corner-refresh'
                        state.mkdir(parents=True)
                        prior = dict(league='E1', status=status, source='https://example.invalid/E1.csv',
                                     file_sha256='a' * 64)
                        if status == 'failed':
                            prior['error'] = 'HTTP 302'
                        else:
                            prior.update(latest_match='2026-09-12', matches=2, added=0, corrected=0)
                        if timestamp is not None:
                            prior['checked_at'] = timestamp
                        (state / 'status.json').write_text(json.dumps(dict(
                            season='2627', max_age_days=14, checked_at='2026-09-15T03:00:00+00:00',
                            results=[prior])))
                        (state / 'daily-attempt-cap.json').write_text('{"version":1,"enabled":true}')
                        with patch.object(corner_refresh, 'datetime', wraps=datetime) as clock, patch.object(
                                corner_refresh.refresh_attempts, 'datetime', wraps=datetime) as attempt_clock:
                            clock.now.return_value = attempt_clock.now.return_value = datetime(2026, 9, 16, tzinfo=timezone.utc)
                            with corner_refresh.refresh_lock(state):
                                terminal, reserved = corner_refresh.refresh_attempts.reserve(state, 'E1', 'test')
                            self.assertTrue(reserved)
                            immutable = {p: p.read_bytes() for p in state.glob('attempts/*/*.json')}
                            snapshot_name = 'last_failure' if status == 'failed' else 'last_success'
                            expected = dict(prior, checked_at=timestamp)
                            for _ in range(2):
                                with patch.object(corner_refresh, 'download_csv') as download:
                                    report = corner_refresh.refresh_data(config, TODAY)
                                download.assert_not_called()
                                row = report['results'][0]
                                self.assertIn('checked_at', row)
                                self.assertIsNone(row['checked_at'])
                                self.assertEqual(_refresh(state / 'status.json', clock.now.return_value, 14),
                                                 dict(state='UNVERIFIED', last_attempt_at_utc=None, e1_result='FAILED'))
                                self.assertEqual(row[snapshot_name], expected)
                                self.assertEqual(report['invocation'][0]['status'], 'DAILY_CAP_CONSUMED')
                            clock.now.return_value = attempt_clock.now.return_value = datetime(2026, 9, 17, tzinfo=timezone.utc)
                            with patch.object(corner_refresh, 'download_csv',
                                    return_value=NEW.replace(b'SP1,', b'E1,'),
                                    side_effect=OSError('new failure') if recovery == 'failure' else None) as download:
                                recovered = corner_refresh.refresh_data(config, date(2026, 9, 17))['results'][0]
                            download.assert_called_once()
                            # Only a genuine new outcome of the same kind supersedes history.
                            superseded = (status == 'failed') == (recovery == 'failure')
                            if not superseded:
                                self.assertEqual(recovered[snapshot_name], expected)
                            else:
                                self.assertEqual(recovered[snapshot_name]['checked_at'], recovered['checked_at'])
                            self.assertNotEqual(recovered.get('last_failure', {}).get('error'), reporting.INTERRUPTED_ATTEMPT)
                            self.assertFalse(terminal.exists())
                            self.assertEqual({p: p.read_bytes() for p in immutable}, immutable)

    def test_legacy_missing_time_is_not_replaced_by_invocation_time(self):
        self.state.mkdir(parents=True)
        old = {'season': '2627', 'checked_at': '2026-10-20T00:00:00+00:00',
               'results': [{'league': 'SP1', 'status': 'failed', 'error': 'old'}]}
        (self.state / 'status.json').write_text(json.dumps(old))
        with patch.object(corner_refresh.refresh_attempts, 'enabled', return_value=True):
            report = corner_refresh.refresh_data(self.config, TODAY, selection=lambda: ())
        self.assertIsNone(report['results'][0]['checked_at'])
        self.assertIn('last attempt UNKNOWN', corner_refresh.format_report(report, TODAY, 14)[0])
