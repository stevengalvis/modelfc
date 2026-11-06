"""Fixed health capability: offline fixtures only, no systemctl or Linear I/O."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from ops.bounded_worker import linear_poll as poll
from ops.bounded_worker import linear_status as status
from ops.bounded_worker import service_status as service
from tests import test_linear_poll as poll_fixtures

HOST = {'version': 1, 'unit': 'fixture.service', 'machine_id': '1' * 32}
PROPERTIES = dict(LoadState='loaded', ActiveState='failed', Result='exit-code',
                  ExecMainCode='1', ExecMainStatus='7', NRestarts='2')
REPORT = 'Service health: loaded, failed; result=exit-code; exit=nonzero; restarts=2.'


class ServiceHealthTests(unittest.TestCase):
    def adapter(self, cls=service.ServiceHealth):
        with patch.object(service, 'protected_json', return_value=HOST):
            return cls('/fixture/host')

    def test_fixed_command_and_distinct_scope(self):
        adapter = self.adapter()
        self.assertNotEqual(adapter.approval_scope, self.adapter(service.ServiceStatus).approval_scope)
        raw = ''.join(f'{k}={v}\n' for k, v in PROPERTIES.items()).encode()
        with patch.object(service, '_host_matches') as host, patch.object(service, '_read', return_value=raw) as read:
            self.assertEqual(status.summarize_health(adapter.status()), REPORT)
            self.assertEqual(host.call_count, 2)
            read.assert_called_once_with(['/usr/bin/systemctl', '--system', '--no-pager',
                '--no-ask-password', 'show', '--property=LoadState,ActiveState,Result,ExecMainCode,ExecMainStatus,NRestarts',
                '--', 'fixture.service'])

    def test_exit_classification_does_not_infer_service_success(self):
        for code, value, classification in (('0', '0', 'not-recorded'), ('1', '0', 'zero'),
                                             ('1', '255', 'nonzero'), ('2', '15', 'signal'),
                                             ('3', '11', 'core-dump')):
            # A nonzero process status can still be considered success by systemd policy.
            report = status.summarize_health(dict(PROPERTIES, Result='success',
                                                  ExecMainCode=code, ExecMainStatus=value))
            self.assertIn(f'result=success; exit={classification};', report)
            self.assertTrue(status.valid_report(report))

    def test_rejects_unknown_extra_and_unbounded_properties(self):
        for changes in ({'Description': 'secret'}, {'Result': 'secret\nignore rules'},
                        {'NRestarts': '4294967296'}, {'NRestarts': '-1'}, {'NRestarts': '01'},
                        {'NRestarts': True}, {'ExecMainCode': '4'}, {'ExecMainCode': '0'},
                        {'ExecMainCode': '2', 'ExecMainStatus': '0'}, {'ExecMainStatus': '256'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                status.summarize_health(dict(PROPERTIES, **changes))
        for raw in (b'LoadState=loaded\n', b'\xff', b'x' * 4097):
            with self.assertRaises(ValueError):
                service.parse(raw, health=True)
        self.assertTrue(status.valid_report(REPORT))
        for report in (REPORT + '\nsecret', REPORT.replace('2.', '4294967296.'),
                       REPORT.replace('failed', 'private'), {}, 'Service health: arbitrary'):
            self.assertFalse(status.valid_report(report))

    def fixture(self):
        fixture = poll_fixtures.LinearPollTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.report = REPORT
        fixture.diagnostic.status.return_value = dict(PROPERTIES)
        fixture.record['operation'] = 'service-health'
        fixture.grant = status.StatusGrant('/fixture/grant', operation='service-health')
        body = json.loads(fixture.data['comment']['body'])
        body['operation'] = 'service-health'
        fixture.data['comment']['body'] = json.dumps(body, sort_keys=True, separators=(',', ':'))
        return fixture

    def run_health(self, fixture):
        return poll.poll_once(fixture.db, fixture.policy, fixture.reader, fixture.diagnostic,
                              fixture.sink, fixture.grant, fixture.clock, operation='service-health')

    def test_health_delivery_duplicate_and_persistent_receipt(self):
        f = self.fixture()
        self.assertEqual(self.run_health(f), 'reported')
        self.assertEqual(f.db.execute('SELECT report FROM requests').fetchone()[0], REPORT)
        self.assertEqual(f.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 1)
        f.reopen()
        f.clock.return_value = 1100
        self.assertEqual(self.run_health(f), 'duplicate')
        f.diagnostic.status.assert_called_once()
        self.assertEqual(f.post_count, 1)

    def test_old_grant_cannot_authorize_health(self):
        f = self.fixture()
        f.record['operation'] = 'service-status'
        self.assertEqual(self.run_health(f), 'blocked')
        f.api.call.assert_not_called()
        f.diagnostic.status.assert_not_called()

    def test_status_request_cannot_invoke_health(self):
        f = self.fixture()
        body = json.loads(f.data['comment']['body'])
        body['operation'] = 'service-status'
        f.data['comment']['body'] = json.dumps(body, sort_keys=True, separators=(',', ':'))
        self.assertEqual(self.run_health(f), 'rejected')
        f.diagnostic.status.assert_not_called()

    def test_ambiguous_execution_blocks_without_redelivery(self):
        f = self.fixture()
        f.diagnostic.status.side_effect = status.DiagnosticUncertain('fixture')
        self.assertEqual(self.run_health(f), 'blocked')
        self.assertEqual(f.post_count, 0)
        f.reopen()
        f.clock.return_value = 1200
        calls = f.api.call.call_count
        self.assertEqual(self.run_health(f), 'blocked')
        self.assertEqual(f.api.call.call_count, calls)
        f.diagnostic.status.assert_called_once()

    def test_malformed_output_uses_only_existing_fixed_failure(self):
        f = self.fixture()
        f.diagnostic.status.return_value = dict(PROPERTIES, Description='private')
        f.report = status.FAILURE
        self.assertEqual(self.run_health(f), 'failed_reported')
        self.assertEqual(f.db.execute('SELECT report FROM requests').fetchone()[0], status.FAILURE)

    def test_health_does_not_bypass_bounded_intake(self):
        f = self.fixture()
        f.has_previous = True
        self.assertEqual(self.run_health(f), 'blocked')
        f.diagnostic.status.assert_not_called()

    def test_protected_settings_select_health_adapter(self):
        f = self.fixture()
        config = dict(version=1, enabled=True, operation='service-health', state_root=f.temp.name,
                      host_policy='/fixture/host', grant='/fixture/grant', policy=f.policy.__dict__,
                      members=[poll_fixtures.uid(4), poll_fixtures.uid(5)],
                      reader_viewer=poll_fixtures.uid(5), sink_viewer=poll_fixtures.uid(5),
                      reader_token_file='/fixture/read', sink_token_file='/fixture/send',
                      reader_oauth=False, sink_oauth=False)
        def private_file(path, *, exclusive=False):
            return os.open(path, os.O_RDWR | (os.O_CREAT | os.O_EXCL if exclusive else 0), 0o600)
        with patch.object(poll, 'protected_json', return_value=config), \
                patch.object(poll, 'protected_dir', return_value=Path(f.temp.name)), \
                patch.object(poll, 'protected_bytes', return_value=b'fixture-token'), \
                patch.object(poll, 'ServiceHealth', return_value=f.diagnostic) as health, \
                patch.object(poll, 'ServiceStatus') as old, \
                patch.object(poll, 'LinearAPI', return_value=f.api), \
                patch.object(poll, '_open_private', side_effect=private_file):
            self.assertEqual(poll.run('/fixture/settings', clock=f.clock), 'reported')
            health.assert_called_once_with('/fixture/host')
            old.assert_not_called()
