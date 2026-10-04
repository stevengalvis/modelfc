"""Cloud-only regression tests: mock every host command/request; use disposable files.

Never calls activate() or installed systemctl/Caddy/ACL tools or providers.
main() is tested only with every host interaction mocked.
"""
import ast
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'ops/vps/team_intelligence_activate.py'
# Parsing first ensures there is no unexpected top-level host invocation.
tree = ast.parse(SCRIPT.read_text())
assert all(isinstance(n, (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign,
                          ast.AnnAssign, ast.ClassDef, ast.FunctionDef, ast.If)) for n in tree.body)
spec = importlib.util.spec_from_file_location('reviewed_activation', SCRIPT)
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)  # definitions/constants only; __main__ guard is false

TIMER = dict(LoadState='loaded', ActiveState='active', SubState='waiting',
             UnitFileState='enabled', TimersCalendar='Mon,Thu 06:00 UTC',
             Persistent='yes', LastTriggerUSec='unchanged', NextElapseUSecRealtime='future')
SERVICE = dict(ActiveState='inactive', SubState='dead', MainPID='0', Result='success')


class ActivationTests(unittest.TestCase):
    def setUp(self):
        a.configure_baseline({
            'release': '/srv/modelfc/releases/'+a.EXPECTED_SHA+'-000000000000',
            'installed_hashes': {str(p):'0'*64 for p in a.INSTALLED_PATHS},
            'identities': {str(p):[1,i+1] for i,p in enumerate(a.IDENTITY_PATHS)},
            'e1_sha256':'1'*64, 'data_cutoff':'2026-09-20',
        })
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Every test starts with commands/network denied unless explicitly mocked.
        self.stack.enter_context(patch.object(a, 'run', side_effect=AssertionError('host command forbidden')))
        self.stack.enter_context(patch.object(a, 'direct_request', side_effect=AssertionError('network forbidden')))
        self.stack.enter_context(patch.object(a, 'public_request', side_effect=AssertionError('network forbidden')))
        self.stack.enter_context(patch.object(a, 'log'))

    def mock(self, name, **kwargs):
        return self.stack.enter_context(patch.object(a, name, **kwargs))

    def context(self):
        return a.Context(timer_before=dict(TIMER), timer_stop_started=True,
                         api_restart_started=True, api_launcher_replace_started=True,
                         refresh_launcher_replace_started=True, acl_mutation_started=True)

    def recovery(self, context, *, journal_fails=False, service=None):
        calls = []
        self.mock('refresh_is_quiescent', return_value=True)
        self.mock('refresh_timer_inactive', return_value=True)
        self.mock('run', side_effect=lambda argv, **kw: calls.append(tuple(map(str, argv))))
        def props(unit, keys):
            values = ({**TIMER, 'ActiveState': 'inactive', 'SubState': 'dead'}
                      if unit == a.REFRESH_TIMER else
                      dict(ActiveState='active', SubState='running', MainPID='44'))
            return {k: values[k] for k in keys}
        self.mock('systemctl_properties', side_effect=props)
        self.mock('timer_recovery_state', return_value=({**TIMER, 'ActiveState': 'inactive'}, service or SERVICE))
        for name in ('remove_new_ingress_first', 'reacquire_refresh_for_rollback',
                     'restore_acls', 'restore_launchers_separately'):
            self.mock(name, side_effect=lambda *args, n=name: (calls.append(n), a.journal(context, n, True)))
        if journal_fails:
            self.mock('_write_journal', side_effect=OSError('disk full'))
        else:
            self.mock('_write_journal')
        def restore(ctx):
            calls.append('restore_timer')
            ctx.timer_restored = True
        self.mock('restore_timer_if_no_catchup_risk', side_effect=restore)
        return calls

    def test_rollback_stops_restored_timer_before_unlock(self):
        context = self.context()
        context.timer_restored = True
        context.timer_start_attempted = True
        calls = self.recovery(context)
        self.assertEqual(a.rollback(context, InterruptedError('after restore')), [])
        stop = ('/usr/bin/systemctl', 'stop', a.REFRESH_TIMER)
        self.assertLess(calls.index(stop), calls.index('reacquire_refresh_for_rollback'))
        self.assertLess(calls.index('remove_new_ingress_first'), calls.index('reacquire_refresh_for_rollback'))

    def test_journal_failure_does_not_skip_restoration_or_restart_timer(self):
        context = self.context()
        calls = self.recovery(context, journal_fails=True)
        failures = a.rollback(context, OSError('disk full'))
        self.assertIn('restore_acls', calls)
        self.assertIn('restore_launchers_separately', calls)
        self.assertNotIn('restore_timer', calls)
        self.assertTrue(any('JOURNAL_FAILED' in f for f in failures))
        self.assertFalse(context.timer_restored)

    def test_unverified_refresh_quiescence_blocks_protected_rollback(self):
        context = self.context()
        calls = self.recovery(context)
        def fail_quiescence(ctx):
            ctx.retain_locks_for_recovery = True
            raise a.ActivationError('unverified')
        self.mock('quiesce_refresh_for_recovery', side_effect=fail_quiescence)
        failures = a.rollback(context, RuntimeError('failure'))
        self.assertNotIn('reacquire_refresh_for_rollback', calls)
        self.assertNotIn('restore_acls', calls)
        self.assertNotIn('restore_timer', calls)
        self.assertTrue(context.retain_locks_for_recovery)
        self.assertTrue(any('QUIESCENCE_UNVERIFIED' in f for f in failures))
        self.assertNotIn(('/usr/bin/systemctl', 'restart', a.API_UNIT), calls)

    def test_early_failure_does_not_stop_unowned_timer(self):
        context = a.Context()
        calls = self.recovery(context)
        self.assertEqual(a.rollback(context, RuntimeError('preflight')), [])
        self.assertNotIn(('/usr/bin/systemctl', 'stop', a.REFRESH_TIMER), calls)
        self.assertNotIn('reacquire_refresh_for_rollback', calls)

    def test_failed_start_latch_precedes_diagnostic_failure(self):
        context = self.context()
        self.mock('run')
        self.mock('quiesce_refresh_for_recovery', side_effect=OSError('cannot journal'))
        with self.assertRaises(OSError):
            a.stop_and_verify_timer_after_failed_start(context)
        self.assertTrue(context.timer_start_failed)
        self.assertFalse(context.timer_restored)

    def test_failed_timer_verification_stops_timer(self):
        context = self.context()
        self.mock('refresh_is_quiescent', return_value=True)
        self.mock('refresh_timer_inactive', return_value=True)
        commands = self.mock('run')
        timers = iter([{**TIMER, 'ActiveState': 'inactive'},
                       {**TIMER, 'LastTriggerUSec': 'changed'}])
        self.mock('systemctl_properties', side_effect=lambda unit, keys: next(timers) if unit == a.REFRESH_TIMER else SERVICE)
        self.mock('parse_systemd_timestamp', return_value=10**20)
        self.mock('journal')
        self.mock('defer_catchable_signals')
        self.mock('timer_recovery_state', return_value=({**TIMER, 'ActiveState': 'inactive'}, SERVICE))
        with self.assertRaises(a.CatchUpRisk):
            a.restore_timer_if_no_catchup_risk(context)
        self.assertEqual([c.args[0][1] for c in commands.call_args_list], ['start', 'stop'])
        self.assertTrue(context.timer_start_failed)

    def test_timer_catchup_risk_prevents_start(self):
        context = self.context()
        run = self.mock('run')
        self.mock('systemctl_properties', side_effect=lambda unit, keys: {**TIMER, 'ActiveState': 'inactive'} if unit == a.REFRESH_TIMER else SERVICE)
        self.mock('parse_systemd_timestamp', return_value=0)
        with self.assertRaises(a.CatchUpRisk):
            a.restore_timer_if_no_catchup_risk(context)
        run.assert_not_called()

    def test_unexpected_active_timer_is_not_silently_accepted(self):
        context = self.context()
        self.mock('systemctl_properties', side_effect=lambda unit, keys: TIMER if unit == a.REFRESH_TIMER else SERVICE)
        with self.assertRaises(a.CatchUpRisk):
            a.restore_timer_if_no_catchup_risk(context)
        self.assertFalse(context.timer_restored)

    def test_lock_transition_revalidates_after_shared_acquisition(self):
        context = a.Context(refresh_fd=123, refresh_mode='exclusive')
        order = []
        self.mock('acquire_bounded', side_effect=lambda *args: order.append('shared'))
        self.mock('revalidate_after_lock_transition', side_effect=lambda *args, **kw: order.append('validate'))
        self.mock('journal')
        a.convert_refresh_to_shared_and_revalidate(context)
        self.assertEqual(order, ['shared', 'validate'])
        self.assertEqual(context.refresh_mode, 'shared')

    def test_rollback_reacquisition_revalidates_before_restoration(self):
        context = a.Context(refresh_fd=123, refresh_mode='shared')
        order = []
        self.mock('release_refresh_lock', side_effect=lambda *args: order.append('unlock'))
        self.mock('acquire_bounded', side_effect=lambda *args: order.append('exclusive'))
        self.mock('revalidate_for_rollback', side_effect=lambda *args: order.append('validate'))
        self.mock('journal')
        a.reacquire_refresh_for_rollback(context)
        self.assertEqual(order, ['unlock', 'exclusive', 'validate'])

    def test_masked_permissions_fail(self):
        with self.assertRaises(a.ActivationError):
            a.assert_effective_named_permission(Path('/unused'), ('user:996:r--', 'mask::---'), 996, 'r--')
        a.assert_effective_named_permission(Path('/unused'), ('user:996:r--', 'mask::r--'), 996, 'r--')

    def test_acl_commands_preserve_mask_and_limit_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root/'history'; folder.mkdir()
            file = root/'E1.csv'; file.write_text('synthetic')
            targets = (folder, file)
            before = {str(folder): ('user::rwx','user:995:--x','group::---','mask::--x','other::---'),
                      str(file): ('user::rw-','user:995:r--','group::---','mask::r--','other::---')}
            context = a.Context(acl_before=before)
            self.mock('ACL_TARGETS', new=targets)
            self.stack.enter_context(patch.object(a.pwd, 'getpwnam', side_effect=lambda name: SimpleNamespace(pw_uid=996 if name=='modelfc-api' else 995)))
            commands = self.mock('run')
            self.mock('journal')
            self.mock('assert_exact_acl', side_effect=lambda path, expected: tuple(expected))
            self.mock('assert_no_obsolete_api_grants')
            a.apply_narrow_acls(context)
            self.assertEqual(len(commands.call_args_list), 2)
            for call, mask in zip(commands.call_args_list, ('--x','r--')):
                self.assertEqual(call.args[0][1:3], ['-n','-m'])
                self.assertIn('m::'+mask,call.args[0][3])

    def test_recovery_journal_disk_failure_does_not_abort_later_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            context = a.Context(recovering=True, manifest_path=Path(temp)/'missing'/'manifest.json')
            a.journal(context, 'rollback_acls_restored', True)
            a.journal(context, 'rollback_launchers_restored', True)
            self.assertEqual(len(context.journal_errors), 2)
            self.assertTrue(context.manifest['rollback_launchers_restored'])

    def test_real_disposable_shared_lock_allows_reader_blocks_writer(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp)/'lock'; lock.touch()
            with lock.open('rb') as retained, lock.open('rb') as reader:
                fcntl.flock(retained, fcntl.LOCK_EX)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(reader, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(retained, fcntl.LOCK_SH)
                fcntl.flock(reader, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(reader, fcntl.LOCK_UN)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(reader, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(retained, fcntl.LOCK_UN)
                fcntl.flock(reader, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_probe_has_no_write_or_missing_file_success(self):
        tree = ast.parse(a.ACCESS_PROBE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'open':
                self.assertEqual(node.args[1].value, 'rb')
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, 'write')
        self.assertNotIn('FileNotFoundError', a.ACCESS_PROBE)

    def test_offline_skip_blocks_and_has_source_path(self):
        runner = self.mock('run', return_value=SimpleNamespace(stdout='OK (skipped=1)'))
        with self.assertRaisesRegex(a.ActivationError, 'skipped'):
            a.run_offline_acceptance()
        self.assertIn(str(a.EXPECTED_RELEASE/'src'),runner.call_args.kwargs['env']['PYTHONPATH'].split(':'))
        self.assertEqual(runner.call_args.kwargs['timeout'], 180)

    def test_offline_timeout_is_not_ignored(self):
        self.mock('run', side_effect=subprocess.TimeoutExpired('synthetic test', 180))
        with self.assertRaises(subprocess.TimeoutExpired):
            a.run_offline_acceptance()

    def test_pinned_metadata_rejects_wrong_cutoff_or_revision(self):
        value = dict(schema_version=1,calculation_version='team-corners-v1',competition='E1',
                     season='2627',data_cutoff='2026-09-20',source_revision=a.EXPECTED_E1_SHA256,
                     source='football-data',roster_state='COMPLETE')
        a.assert_team_metadata({'metadata':value},'test')
        for key, bad in [('data_cutoff','2026-09-19'),('source_revision','0'*64)]:
            with self.subTest(key=key), self.assertRaises(a.ActivationError):
                a.assert_team_metadata({'metadata':{**value,key:bad}},'test')

    def test_caddy_diff_rejects_unrelated_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            old=Path(temp)/'old'; new=Path(temp)/'new'
            old.write_text('host {\n\t@opportunity_detail {\n}\n}')
            new.write_text(old.read_text().replace('\t@opportunity_detail {', a.TEAM_CADDY_BLOCK+'\t@opportunity_detail {'))
            self.mock('CADDYFILE',new=old); self.mock('SOURCE_CADDYFILE',new=new)
            a.assert_caddy_diff_is_team_routes_only()
            new.write_text(new.read_text()+'\nunrelated')
            with self.assertRaises(a.ActivationError):a.assert_caddy_diff_is_team_routes_only()

    def test_recovery_journal_failure_is_recorded(self):
        context=a.Context(recovering=True)
        self.mock('_write_journal',side_effect=OSError('full'))
        a.journal(context,'test',True)
        self.assertEqual(len(context.journal_errors),1)
        context.recovering=False
        with self.assertRaises(OSError):a.journal(context,'test',True)

    def test_conditional_stop_only_fixed_refresh_service(self):
        context = self.context()
        run = self.mock('run')
        self.mock('journal')
        self.mock('refresh_is_quiescent', side_effect=[False, True])
        self.mock('refresh_timer_inactive', return_value=True)
        a.quiesce_refresh_for_recovery(context)
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['/usr/bin/systemctl','stop',a.REFRESH_TIMER],
            ['/usr/bin/systemctl','stop','modelfc-corner-refresh.service'],
        ])
        self.assertTrue(context.timer_start_failed)
        self.assertFalse(context.retain_locks_for_recovery)

    def test_no_service_stop_if_already_quiescent(self):
        context = self.context()
        run = self.mock('run')
        self.mock('journal')
        self.mock('refresh_is_quiescent', return_value=True)
        self.mock('refresh_timer_inactive', return_value=True)
        a.quiesce_refresh_for_recovery(context)
        run.assert_called_once_with(['/usr/bin/systemctl','stop',a.REFRESH_TIMER])

    def test_stop_timeout_keeps_locks_until_verified(self):
        context = self.context()
        self.mock('run', side_effect=subprocess.TimeoutExpired('mock',30))
        self.mock('journal')
        self.mock('refresh_is_quiescent', return_value=False)
        self.mock('refresh_timer_inactive', return_value=False)
        self.stack.enter_context(patch.object(a.time,'monotonic',side_effect=[0,31]))
        with self.assertRaises(a.ActivationError):
            a.quiesce_refresh_for_recovery(context)
        self.assertTrue(context.retain_locks_for_recovery)

    def test_operator_hold_never_timed_unlocks(self):
        context = a.Context(retain_locks_for_recovery=True)
        self.mock('defer_catchable_signals')
        self.mock('refresh_timer_inactive',return_value=True)
        self.mock('refresh_is_quiescent',side_effect=[False,False,True])
        sleep = self.stack.enter_context(patch.object(a.time,'sleep'))
        a.retain_locks_until_refresh_quiescent(context)
        self.assertEqual(sleep.call_count,2)
        self.assertFalse(context.retain_locks_for_recovery)

    def test_job_and_cgroup_verification_not_only_main_pid(self):
        self.mock('systemctl_properties',return_value=dict(
            ActiveState='inactive',MainPID='0',ControlPID='0',ControlGroup=''))
        self.mock('run',return_value=SimpleNamespace(stdout=json.dumps({'type':'(uo)','data':[9,'/job/9']})))
        self.assertFalse(a.refresh_is_quiescent())
        self.mock('run',return_value=SimpleNamespace(stdout=json.dumps({'type':'(uo)','data':[0,'/']})))
        self.assertTrue(a.refresh_is_quiescent())
        self.mock('systemctl_properties',return_value=dict(
            ActiveState='inactive',MainPID='0',ControlPID='0',ControlGroup='/unexpected'))
        self.assertFalse(a.refresh_is_quiescent())

    def test_default_invocation_cannot_mutate_or_load_host_inventory(self):
        self.mock('parse_args',return_value=SimpleNamespace(apply=False))
        load = self.mock('load_private_baseline')
        activate = self.mock('activate')
        self.assertEqual(a.main(),2)
        load.assert_not_called(); activate.assert_not_called()

    def test_manifest_rejects_unreviewed_source_or_extra_targets(self):
        base = dict(release='/srv/modelfc/releases/'+a.EXPECTED_SHA+'-000000000000',
            installed_hashes={str(p):'0'*64 for p in a.INSTALLED_PATHS},
            identities={str(p):[1,i+1] for i,p in enumerate(a.IDENTITY_PATHS)},
            e1_sha256='1'*64,data_cutoff='2026-09-20')
        for bad in ({**base,'release':'/tmp/release'},
                    {**base,'e1_sha256':'short'},
                    {**base,'installed_hashes':{**base['installed_hashes'],'/etc/passwd':'0'*64}}):
            with self.subTest(bad=bad),self.assertRaises(a.ActivationError):a.configure_baseline(bad)


    def test_main_retains_locks_even_if_rollback_aborts(self):
        self.mock('parse_args',return_value=SimpleNamespace(apply=True))
        self.mock('load_private_baseline')
        self.mock('install_signal_handlers')
        self.mock('defer_catchable_signals')
        self.stack.enter_context(patch.object(a.os,'geteuid',return_value=0))
        def activation(ctx):
            ctx.timer_stop_started=True
            raise RuntimeError('synthetic activation failure')
        self.mock('activate',side_effect=activation)
        self.mock('rollback',side_effect=RuntimeError('synthetic recovery failure'))
        order=[]
        def hold(ctx):
            self.assertTrue(ctx.retain_locks_for_recovery)
            order.append('hold until quiet')
        self.mock('retain_locks_until_refresh_quiescent',side_effect=hold)
        self.mock('release_refresh_lock',side_effect=lambda ctx:order.append('unlock'))
        self.assertEqual(a.main(),1)
        self.assertEqual(order,['hold until quiet','unlock'])

    def test_quiescent_recovery_restores_api_but_not_failed_timer(self):
        context=self.context();context.timer_start_failed=True
        calls=self.recovery(context)
        failures=a.rollback(context,RuntimeError('timer failed'))
        self.assertIn('restore_launchers_separately',calls)
        self.assertIn(('/usr/bin/systemctl','restart',a.API_UNIT),calls)
        self.assertNotIn('restore_timer',calls)
        self.assertTrue(any('STOPPED' in item for item in failures))



if __name__ == '__main__':
    unittest.main()
