"""Synthetic protected reads/manager state; all networking forbidden."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modelfc import pl_prepare_entry as entry
from tests.test_prospective_launch import ProspectiveLaunchTests, launcher


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 13, 30, tzinfo=timezone.utc)
        self.a = dict(version=1, release_sha='a'*40, authorization_sha256='b'*64,
                      state_dir=entry.STATE, private_dir='/var/lib/modelfc/state/acquisition',
                      issued_at=self.now.isoformat(),
                      expires_at=(self.now+timedelta(minutes=10)).isoformat(),
                      next_e1_at=(self.now+timedelta(minutes=35)).isoformat())
        self.auth = dict(state_dir=entry.STATE, private_dir=self.a['private_dir'],
                         expires_at=(self.now+timedelta(hours=1)).isoformat())
        self.enterContext(patch('socket.socket', side_effect=AssertionError('network forbidden')))
        self.enterContext(patch.object(entry, 'require_runtime'))
        self.clock = self.enterContext(patch.object(entry, '_now', return_value=self.now))
        self.raw = self.enterContext(patch.object(entry.acquisition, '_local_bytes',
            side_effect=lambda *args, **kwargs: json.dumps(self.a).encode()))
        self.enterContext(patch.object(entry.operator, '_authorization',
                                    side_effect=lambda p: (self.auth, 'b'*64)))
        self.enterContext(patch.object(Path, 'cwd', return_value=Path('/srv/modelfc/releases/'+'a'*40+'-123456abcdef')))
        self.marker = self.enterContext(patch.object(Path, 'read_bytes', return_value=b'a'*40))
        self.manager = self.enterContext(patch.object(entry.subprocess, 'run',
            return_value=SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\nSubState=dead\nJob=\n')))
        self.prepare = self.enterContext(patch.object(entry.operator, 'prepare',
            return_value={'status': 'RETAINED_UNREVIEWED', 'provider_requests': 2}))

    def test_admitted_once_fixed_arguments_no_secret_in_manager_environment(self):
        self.assertEqual(entry.run()['status'], 'RETAINED_UNREVIEWED')
        self.prepare.assert_called_once_with(enabled=True, authorization_path=entry.AUTHORIZATION,
                                             authorization_sha256='b'*64)
        self.assertEqual(self.manager.call_args.kwargs['env'], {'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        self.assertEqual(self.manager.call_args.kwargs['timeout'], 5)

    def test_active_pending_missing_duplicate_manager_state_blocks(self):
        for raw in ('LoadState=loaded\nActiveState=active\nSubState=running\nJob=\n',
                    'LoadState=loaded\nActiveState=inactive\nSubState=dead\nJob=123\n',
                    'LoadState=not-found\n', 'Job=\nJob=\n'):
            with self.subTest(raw=raw):
                self.manager.return_value.stdout=raw
                self.assertEqual(entry.run()['provider_requests'], 0)
        self.prepare.assert_not_called()

    def test_deadline_and_auth_binding_reject_before_prepare(self):
        cases = {'version': True, 'release_sha': 'c'*40, 'authorization_sha256':'c'*64,
                 'private_dir':'/alternate/store', 'state_dir':'/alternate/state',
                 'next_e1_at':(self.now+entry.BOUND).isoformat(),
                 'expires_at':self.now.isoformat(),
                 'issued_at':(self.now+timedelta(seconds=1)).isoformat()}
        for key,value in cases.items():
            with self.subTest(key=key), patch.dict(self.a,{key:value}):
                self.assertEqual(entry.run()['status'],'HOLD')
        self.prepare.assert_not_called()

    def test_query_delay_changed_admission_wrong_marker_and_runtime_hold(self):
        self.clock.side_effect=[self.now,self.now+timedelta(minutes=35)]
        self.assertEqual(entry.run()['status'],'HOLD')
        self.clock.side_effect=None
        self.raw.side_effect=[json.dumps(self.a).encode(),b'changed']
        self.assertEqual(entry.run()['status'],'HOLD')
        self.raw.side_effect=lambda *a,**k:json.dumps(self.a).encode()
        self.marker.return_value=b'c'*40
        self.assertEqual(entry.run()['status'],'HOLD')
        self.marker.return_value=b'a'*40
        with patch.object(entry,'require_runtime',side_effect=ValueError):
            self.assertEqual(entry.run()['status'],'HOLD')
        self.prepare.assert_not_called()

    def test_authorization_must_outlive_bound_and_manager_failure_holds(self):
        self.auth['expires_at']=(self.now+entry.BOUND).isoformat()
        self.assertEqual(entry.run()['status'],'HOLD')
        self.auth['expires_at']=(self.now+timedelta(hours=1)).isoformat()
        self.manager.side_effect=subprocess.TimeoutExpired('systemctl',5)
        self.assertEqual(entry.run()['status'],'HOLD')
        self.prepare.assert_not_called()

    def test_uncertain_primitive_error_is_not_zero_request_result(self):
        self.prepare.side_effect=OSError('uncertain after networking')
        with self.assertRaises(OSError):entry.run()


class PreparationLaunchTests(ProspectiveLaunchTests):
    def test_explicit_mode_uses_existing_pin_and_removes_publication_acl(self):
        for name in ('pl_prepare_entry.py','pl_operator_once.py','guarded_acquisition.py','acquisition_storage.py'):
            (self.a/'src/modelfc'/name).touch()
        with patch.object(Path,'cwd',return_value=self.a), patch.object(launcher.os,'execve') as execute, \
                patch.dict(launcher.os.environ,{'CREDENTIALS_DIRECTORY':str(self.credentials)},clear=True):
            launcher.launch(epl_prepare=True)
        executable,argv,env=execute.call_args.args
        self.assertEqual(argv,[executable,'-B','-P','-s','-m','modelfc.pl_prepare_entry'])
        self.assertNotIn('MODELFC_EVIDENCE_ACL_USER',env)
        self.assertNotIn('CREDENTIALS_DIRECTORY',env)

    def test_finite_unit_has_no_timer_or_restart_and_preserves_boundary(self):
        root=Path(__file__).resolve().parents[1]
        unit=(root/'deploy/modelfc-epl-prepare.service').read_text()
        for text in ('Type=oneshot','TimeoutStartSec=180s','TimeoutStopSec=30',
                     'KillMode=control-group','SendSIGKILL=yes','Restart=no',
                     'User=modelfc-runtime','ProtectSystem=strict','--epl-prepare',
                     'LoadCredential=oddspapi.key:/etc/modelfc/credentials/oddspapi.key'):
            self.assertIn(text,unit)
        self.assertNotIn('[Install]',unit)
        self.assertFalse((root/'deploy/modelfc-epl-prepare.timer').exists())


if __name__ == '__main__':
    unittest.main()
