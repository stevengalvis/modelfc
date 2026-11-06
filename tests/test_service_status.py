import subprocess
import sys
import unittest
from unittest.mock import Mock, mock_open, patch

from ops.bounded_worker import service_status as service

POLICY = {'version': 1, 'unit': 'fixture.service', 'machine_id': '1' * 32}
GOOD = b'LoadState=loaded\nActiveState=inactive\n'


class ServiceStatusTests(unittest.TestCase):
    def adapter(self, policy=None):
        with patch.object(service, 'protected_json', return_value=policy or POLICY):
            return service.ServiceStatus('/fixture/policy')

    def test_approval_scope_binds_host_and_service(self):
        scope = self.adapter().approval_scope
        self.assertEqual(scope, self.adapter().approval_scope)
        self.assertNotEqual(scope, self.adapter(dict(POLICY, unit='other.service')).approval_scope)
        self.assertNotEqual(scope, self.adapter(dict(POLICY, machine_id='2' * 32)).approval_scope)

    def test_fixed_invocation_and_host_binding(self):
        adapter = self.adapter()
        with patch.object(service, '_host_matches') as host, patch.object(service, '_read', return_value=GOOD) as read:
            self.assertEqual(adapter.status(), {'LoadState': 'loaded', 'ActiveState': 'inactive'})
        self.assertEqual(host.call_count, 2)
        host.assert_called_with('1' * 32)
        read.assert_called_once_with(['/usr/bin/systemctl', '--system', '--no-pager',
                                     '--no-ask-password', 'show', '--property=LoadState,ActiveState',
                                     '--', 'fixture.service'])

    def test_missing_or_unverified_host_blocks(self):
        with patch.object(service, 'protected_json', side_effect=ValueError('unprotected')):
            with self.assertRaises(ValueError):
                service.ServiceStatus('/fixture/policy')
        adapter = self.adapter()
        with patch.object(service, '_host_matches', side_effect=ValueError('host_mismatch')), patch.object(service, '_read') as read:
            with self.assertRaises(ValueError):
                adapter.status()
            read.assert_not_called()

    def test_machine_identity_comparison(self):
        with patch.object(service, '_protected_regular'), patch.object(service.os, 'access', return_value=True):
            with patch('builtins.open', mock_open(read_data=b'1' * 32 + b'\n')):
                service._host_matches('1' * 32)
            with patch('builtins.open', mock_open(read_data=b'2' * 32 + b'\n')):
                with self.assertRaises(ValueError):
                    service._host_matches('1' * 32)

    def test_uncertain_reaping_is_distinct_from_diagnostic_failure(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('fixture', 1)
        selector = Mock()
        selector.select.return_value = [True]
        with patch.object(service.subprocess, 'Popen', return_value=process), \
                patch.object(service.selectors, 'DefaultSelector') as factory, \
                patch.object(service.os, 'read', return_value=b''):
            factory.return_value.__enter__.return_value = selector
            with self.assertRaises(service.DiagnosticUncertain):
                service._read(['fixture-only'])
        process.kill.assert_called_once()

    def test_policy_rejects_commands_patterns_and_unknown_fields(self):
        for unit in ('*.service', '--host=remote', 'foo@bar.service', '../foo.service', 'foo.service;id'):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                self.adapter(dict(POLICY, unit=unit))
        for updates in ({'machine_id': ''}, {'machine_id': '0' * 32}, {'version': True}, {'command': 'id'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.adapter(dict(POLICY, **updates))

    def test_parser_fails_closed(self):
        for raw in (GOOD + b'Description=private\n', GOOD + b'LoadState=loaded\n',
                    b'ActiveState=active\n', b'LoadState=unknown\nActiveState=active\n',
                    b'\xff', b'x' * 4097, GOOD + b'\n'):
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                service.parse(raw)

    def run_fixture(self, source):
        # Real local pipe/timeout exercise; never invokes systemctl or a host service.
        original = subprocess.Popen
        processes = []
        def launch(argv, **kwargs):
            self.assertEqual(kwargs['env'], {'LC_ALL': 'C', 'SYSTEMD_PAGER': '', 'SYSTEMD_COLORS': '0'})
            self.assertIs(kwargs['shell'], False)
            process = original([sys.executable, '-c', source], **kwargs)
            processes.append(process)
            return process
        with patch.object(service.subprocess, 'Popen', side_effect=launch):
            try:
                return service._read(['fixture-only'])
            finally:
                self.assertTrue(all(p.poll() is not None for p in processes))

    def test_bounded_pipe_success(self):
        self.assertEqual(self.run_fixture("print('LoadState=loaded'); print('ActiveState=inactive')"), GOOD)

    def test_stderr_discard_and_exit_failure(self):
        self.assertEqual(self.run_fixture("import sys; sys.stderr.write('private'); print('ok')"), b'ok\n')
        with self.assertRaises(ValueError):
            self.run_fixture('raise SystemExit(1)')

    def test_output_overflow_kills_and_reaps(self):
        with self.assertRaises(ValueError):
            self.run_fixture("import os; os.write(1, b'x' * 8192)")

    def test_timeout_kills_and_reaps(self):
        with patch.object(service, 'TIMEOUT', 0.1), self.assertRaises(ValueError):
            self.run_fixture('import time; time.sleep(10)')
