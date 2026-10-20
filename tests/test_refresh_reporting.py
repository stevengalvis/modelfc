"""Synthetic offline invocation/history reporting contracts."""
from datetime import date
import json
import unittest
from unittest.mock import patch

from modelfc import corner_refresh, refresh_reporting as reporting
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

    def test_legacy_missing_time_is_not_replaced_by_invocation_time(self):
        self.state.mkdir(parents=True)
        old = {'season': '2627', 'checked_at': '2026-10-20T00:00:00+00:00',
               'results': [{'league': 'SP1', 'status': 'failed', 'error': 'old'}]}
        (self.state / 'status.json').write_text(json.dumps(old))
        with patch.object(corner_refresh.refresh_attempts, 'enabled', return_value=True):
            report = corner_refresh.refresh_data(self.config, TODAY, selection=lambda: ())
        self.assertIsNone(report['results'][0]['checked_at'])
        self.assertIn('last attempt UNKNOWN', corner_refresh.format_report(report, TODAY, 14)[0])
