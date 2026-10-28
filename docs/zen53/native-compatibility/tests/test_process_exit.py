"""All monitor checkpoints with authenticated generated functions and fake IO."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

from test_harness import environment


def fixture():
    env = environment()
    identity = dict(pid=12345, start=98765, uid=63153, gid=63153,
                    invocation='a'*32, group='/system.slice/zen53-mincompat.service')
    io = env['ProvisionRootIO'](None, None, None)
    io.pidfd, io.procfd, io.identity = 99, 88, identity
    io.stage_check = Mock()
    io.query = Mock(return_value=dict(MainPID='12345', InvocationID='a'*32,
        ControlGroup=identity['group'], ActiveState='active', SubState='running'))
    terminal = dict(LoadState='loaded', ActiveState='active', SubState='exited',
        MainPID='0', ControlPID='0', ControlGroup='', InvocationID='a'*32,
        RootDirectory=str(env['BASE']/'rootfs'), ExecMainPID='12345',
        ExecMainCode='1', ExecMainStatus='0', Result='success')
    env['properties'] = Mock(return_value=terminal)
    env['os'] = NS(stat=Mock(side_effect=FileNotFoundError))
    reads = {'stat': b'12345 (fixture) '+b' '.join([b'S']+[b'0']*18+[b'98765']),
             'status': b'Uid:\t63153 63153 63153 63153\nGid:\t63153 63153 63153 63153\n',
             'cgroup': b'0::/system.slice/zen53-mincompat.service\n'}
    def read(fd, name, limit=4096):
        if fd != 88: raise AssertionError('unpinned read')
        value = reads[name]
        if isinstance(value, BaseException): raise value
        return value
    env['read_at'] = Mock(side_effect=read)
    env['select'].select.side_effect = [([], [], []), ([99], [], []), ([99], [], [])]
    return env, NS(io=io, identity=identity, phase='released'), reads, terminal


class ProcessExit(unittest.TestCase):
    def test_all_three_terminal_proc_states_enter_full_reconciliation(self):
        for state in (b'Z', b'X', b'x'):
            env, tx, reads, terminal = fixture()
            reads['stat'] = reads['stat'].replace(b') S ', b') '+state+b' ')
            self.assertEqual(env['monitor_provision'](tx), terminal)
            self.assertEqual(env['properties'].call_count, 2)
            self.assertEqual(tx.io.stage_check.call_count, 3)
            self.assertEqual([call.args[1] for call in env['read_at'].call_args_list], ['stat'])

    def test_each_pinned_read_disappearance_errno_is_narrow(self):
        for name in ('stat', 'status', 'cgroup'):
            for number in (2, 3):
                env, tx, reads, terminal = fixture()
                reads[name] = OSError(number, 'private error text')
                self.assertEqual(env['monitor_provision'](tx), terminal)
                self.assertEqual(env['properties'].call_count, 2)
            for number in (1, 5, 9, 13, 20, 40):
                env, tx, reads, _ = fixture(); reads[name] = OSError(number, 'private')
                with self.subTest(name=name, errno=number), self.assertRaises(OSError):
                    env['monitor_provision'](tx)
                env['properties'].assert_not_called()

    def test_precise_authenticated_disappearance_diagnostic(self):
        env, _, reads, _ = fixture(); reads['status'] = OSError(3, 'secret path')
        try: env['parse_proc_identity'](88, 'a'*32, '/system.slice/zen53-mincompat.service')
        except RuntimeError as error: record = env['error_diagnostic']('supervisor', error)
        self.assertEqual(record['predicate'], 'PROVISION_PROC_DISAPPEARED')
        self.assertNotIn('secret', str(record))
        self.assertFalse(record['trace_truncated'])

    def test_pre_release_or_nonterminal_unit_never_converts_death_to_success(self):
        for kind in ('state', 'gone'):
            for phase in ('preparing', 'waiting_release', 'publishing_release'):
                env, tx, reads, _ = fixture(); tx.phase = phase
                reads['stat'] = reads['stat'].replace(b') S ', b') Z ') if kind=='state' else OSError(2, '')
                with self.assertRaisesRegex(RuntimeError, 'COMPLETION_NOT_RELEASED'): env['monitor_provision'](tx)
                env['properties'].assert_not_called()
            for changes in ({'Result':'oom-kill'}, {'ExecMainStatus':'1'}, {'ExecMainCode':'2'},
                            {'InvocationID':'b'*32}, {'ExecMainPID':'54321'}, {'LoadState':'not-found'},
                            {'MainPID':'12345'}, {'SubState':'running'}, {'RootDirectory':'/foreign'}):
                env, tx, reads, terminal = fixture()
                reads['stat'] = reads['stat'].replace(b') S ', b') Z ') if kind=='state' else OSError(3, '')
                terminal.update(changes)
                with self.assertRaisesRegex(RuntimeError, 'COMPLETION_UNIT_BINDING'): env['monitor_provision'](tx)

    def test_malformed_or_changed_identity_is_not_an_exit_observation(self):
        cases = [('stat', b''), ('stat', b'invalid'), ('stat', b'\xff'),
                 *[('stat', b'12345 (fixture) '+state) for state in (b'Z', b'X', b'x')],
                 ('stat', b'12345 (fixture) '+b' '.join([b'S']+[b'0']*18+[b'98766'])),
                 ('stat', b'12345 (fixture) '+b' '.join([b'Z']+[b'0']*18+[b'98766'])),
                 ('status', b''), ('status', b'Uid:\t1 2 1 1\nGid:\t1 1 1 1\n'),
                 ('cgroup', b''), ('cgroup', b'0::/foreign\n'),
                 ('cgroup', b'0::/system.slice/zen53-mincompat.service (deleted)\n')]
        for name, value in cases:
            env, tx, reads, _ = fixture(); reads[name] = value
            with self.subTest(name=name, value=value), self.assertRaises((RuntimeError, ValueError, KeyError, IndexError)):
                env['monitor_provision'](tx)
            env['properties'].assert_not_called()

    def test_same_errno_outside_pinned_reads_is_not_reconciled(self):
        for number in (2, 3):
            env, tx, _, _ = fixture(); tx.io.query.side_effect = OSError(number, 'private')
            with self.assertRaises(OSError): env['monitor_provision'](tx)
            env['properties'].assert_not_called()
        for label in ('PROVISION_PROC_DISAPPEARED', 'PROVISION_PROCESS_DEAD'):
            env, tx, _, _ = fixture(); tx.io.check = Mock(side_effect=RuntimeError(label))
            with self.assertRaises(RuntimeError): env['monitor_provision'](tx)
            env['properties'].assert_not_called()

    def test_live_process_and_both_pidfd_race_windows_keep_original_checks(self):
        env, tx, _, _ = fixture()
        env['select'].select.side_effect = None; env['select'].select.return_value = ([], [], [])
        self.assertIsNone(env['monitor_provision'](tx))
        self.assertEqual(env['read_at'].call_count, 3)
        env['properties'].assert_not_called()
        for before in ([], [([], [], [])]):
            env, tx, _, terminal = fixture()
            env['select'].select.side_effect = before + [([99], [], [])]*3
            self.assertEqual(env['monitor_provision'](tx), terminal)
