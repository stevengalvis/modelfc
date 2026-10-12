"""Delivered credentials and command entrypoint; not a systemd emulation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from modelfc.forecast_only_runner import _sportmonks_credential, ForecastRunRejected

ROOT = Path(__file__).resolve().parents[1]
MARKER = b'MODELFC_SPORTMONKS_NOT_CONFIGURED'


class OptionalCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.key = self.directory / 'sportmonks.key'
        env = patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(self.directory)})
        env.start(); self.addCleanup(env.stop)

    def test_missing_and_exact_public_marker_are_no_token(self):
        self.assertIsNone(_sportmonks_credential())
        self.key.write_bytes(MARKER)
        self.assertIsNone(_sportmonks_credential())

    def test_present_credential_remains_usable(self):
        self.key.write_bytes(b'dummy-only-token\n')
        self.assertEqual(_sportmonks_credential(), 'dummy-only-token')

    def test_malformed_delivered_credentials_fail_closed(self):
        for raw in (b'', b' \n', b'\xff', b'x' * 4097, MARKER + b'\n', b'x' + MARKER):
            with self.subTest(raw_length=len(raw)):
                self.key.write_bytes(raw)
                with self.assertRaises((ForecastRunRejected, UnicodeError)):
                    _sportmonks_credential()

    def test_links_and_permission_errors_are_not_absence(self):
        target = self.directory / 'target'; target.write_bytes(b'dummy-only-token')
        self.key.symlink_to(target)
        with self.assertRaises(OSError): _sportmonks_credential()
        self.key.unlink(); os.link(target, self.key)
        with self.assertRaises(ForecastRunRejected): _sportmonks_credential()
        with patch('modelfc.forecast_only_runner.os.open', side_effect=PermissionError):
            with self.assertRaises(PermissionError): _sportmonks_credential()

    def test_unit_fallback_is_only_public_marker_and_keeps_required_inputs(self):
        service = (ROOT / 'deploy/modelfc-forecast-only.service').read_text()
        self.assertEqual([line for line in service.splitlines() if line.startswith('SetCredential=')],
                         ['SetCredential=sportmonks.key:' + MARKER.decode()])
        for name in ('sportmonks.key', 'football-data.key', 'forecast-only.json'):
            self.assertIn(f'LoadCredential={name}:/etc/modelfc/credentials/{name}', service)
        self.assertIn('InaccessiblePaths=-/etc/modelfc/credentials', service)
        self.assertIn('User=modelfc-runtime', service)

    def test_real_entrypoint_never_passes_marker_or_prints_token(self):
        (self.directory / 'forecast-only.json').write_text('{}')
        (self.directory / 'football-data.key').write_text('dummy-calendar')
        script = '''import json, sys
from unittest.mock import patch
import modelfc.forecast_only_runner as runner
expected = None if sys.argv.pop(1) == 'absent' else 'dummy-only-token'
def offline(**kwargs):
    assert kwargs['sportmonks_token'] == expected
    return {'status':'BLOCKED','reason':'SPORTMONKS_CREDENTIAL_MISSING' if expected is None else 'SIMULATED_AUTHORIZATION_FAILURE'}
with patch.object(runner, 'run_once', side_effect=offline), patch('socket.socket', side_effect=AssertionError('network forbidden')):
    runner.main()
'''
        for raw in (None, MARKER, b'dummy-only-token', b'', MARKER + b'\n'):
            with self.subTest(case='missing' if raw is None else len(raw)):
                if raw is None: self.key.unlink(missing_ok=True)
                else: self.key.write_bytes(raw)
                valid = raw in (None, MARKER, b'dummy-only-token')
                result = subprocess.run([sys.executable, '-c', script,
                    'present' if raw == b'dummy-only-token' else 'absent',
                    '--data-config', '/unused', '--state-dir', '/unused'],
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0 if valid else 1, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED' if valid else 'REJECTED')
                for secret in ('dummy-only-token', 'dummy-calendar', MARKER.decode()):
                    self.assertNotIn(secret, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
