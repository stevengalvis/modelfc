"""Pure authentic generated-code reconciliation fixtures; no systemd or processes."""
from types import SimpleNamespace as NS
import copy
import json
import unittest
from unittest.mock import Mock

from test_harness import environment, generated
from test_provisional import runtime, fixture, encode


def setup():
    env = environment()
    identity = dict(pid=12345, start=98765, uid=63153, gid=63153,
                    invocation='a'*32, group='/system.slice/zen53-mincompat.service')
    io = env['ProvisionRootIO'](None, None, None)
    io.pidfd, io.identity, io.procfd = 99, identity, 88
    io.stage_check = Mock()
    running = dict(MainPID=str(identity['pid']), InvocationID=identity['invocation'],
                   ControlGroup=identity['group'], ActiveState='active', SubState='running')
    io.query = Mock(return_value=running)
    env['parse_proc_identity'] = Mock(return_value=identity)
    terminal = dict(LoadState='loaded', ActiveState='active', SubState='exited',
        MainPID='0', ControlPID='0', ControlGroup='', InvocationID=identity['invocation'],
        RootDirectory=str(env['BASE']/'rootfs'), ExecMainPID=str(identity['pid']),
        ExecMainCode='1', ExecMainStatus='0', Result='success')
    env['properties'] = Mock(return_value=terminal)
    env['os'] = NS(stat=Mock(side_effect=FileNotFoundError))
    tx = NS(io=io, identity=identity, phase='released')
    return env, tx, terminal


class Completion(unittest.TestCase):
    def test_first_and_second_liveness_races_reconcile_only_terminal(self):
        for polls in (([99],), ([], [99])):
            env, tx, terminal = setup()
            env['select'].select.side_effect = [(value, [], []) for value in polls] + [([99], [], [])]*2
            self.assertEqual(env['monitor_provision'](tx), terminal)
            self.assertEqual(env['properties'].call_count, 2)
            self.assertEqual(tx.io.stage_check.call_count, 3)
            self.assertEqual(tx.io.query.call_count, len(polls)-1)

    def test_unit_query_exit_race_and_still_running(self):
        env, tx, terminal = setup()
        tx.io.query.return_value = terminal
        env['select'].select.side_effect = [([], [], []), ([99], [], []), ([99], [], [])]
        self.assertEqual(env['monitor_provision'](tx), terminal)
        env, tx, _ = setup()
        env['select'].select.return_value = ([], [], [])
        self.assertIsNone(env['monitor_provision'](tx))
        env['properties'].assert_not_called()

    def test_pre_release_never_reconciles(self):
        for phase in ('new', 'preparing', 'waiting_release', 'guarding', 'publishing_release'):
            env, tx, _ = setup(); tx.phase = phase
            with self.subTest(phase=phase), self.assertRaisesRegex(RuntimeError, 'COMPLETION_NOT_RELEASED'):
                env['monitor_provision'](tx)
            env['properties'].assert_not_called()

    def test_all_terminal_properties_are_required_on_both_samples(self):
        for sample in (0, 1):
            for field in ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID',
                          'ControlGroup', 'InvocationID', 'RootDirectory', 'ExecMainPID',
                          'ExecMainCode', 'ExecMainStatus', 'Result'):
                for missing in (False, True):
                    env, tx, terminal = setup(); bad = dict(terminal)
                    if missing: del bad[field]
                    else: bad[field] = 'foreign-or-abnormal'
                    snapshots = [dict(terminal), dict(terminal)]; snapshots[sample] = bad
                    env['properties'].side_effect = snapshots
                    with self.subTest(sample=sample, field=field, missing=missing), self.assertRaisesRegex(RuntimeError, 'COMPLETION_UNIT_BINDING'):
                        env['completed_provision_unit'](tx)

    def test_oom_signal_timeout_and_nonzero_exit_rejected(self):
        for changes in ({'Result':'oom-kill'}, {'Result':'timeout'}, {'Result':'signal','ExecMainCode':'2','ExecMainStatus':'9'},
                        {'ExecMainStatus':'1'}, {'ExecMainPID':'23456'}, {'InvocationID':'b'*32}):
            env, tx, terminal = setup(); terminal.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, 'COMPLETION_UNIT_BINDING'):
                env['completed_provision_unit'](tx)

    def test_pidfd_stage_and_cgroup_unknowns_fail_closed(self):
        for kind in ('missing_fd', 'live_fd', 'bad_fd', 'stage', 'cgroup', 'stat_error', 'query_error', 'identity'):
            env, tx, _ = setup()
            if kind == 'missing_fd': tx.io.pidfd = None
            elif kind == 'live_fd': env['select'].select.return_value = ([], [], [])
            elif kind == 'bad_fd': env['select'].select.side_effect = OSError('private')
            elif kind == 'stage': tx.io.stage_check.side_effect = RuntimeError('stage')
            elif kind == 'cgroup': env['os'].stat.side_effect = None
            elif kind == 'stat_error': env['os'].stat.side_effect = PermissionError('private')
            elif kind == 'query_error': env['properties'].side_effect = RuntimeError('query')
            else: tx.io.identity = dict(tx.identity, start=1)
            with self.subTest(kind=kind), self.assertRaises((RuntimeError, OSError)):
                env['completed_provision_unit'](tx)

    def test_unrelated_and_forged_assertions_never_reconcile(self):
        for message in ('PROVISION_PIDFD_DEAD', 'PROVISION_UNIT_IDENTITY_CHANGED', 'private'):
            env, tx, _ = setup(); tx.io.check = Mock(side_effect=RuntimeError(message))
            with self.assertRaises(RuntimeError): env['monitor_provision'](tx)
            env['properties'].assert_not_called()
        env, tx, _ = setup()
        env['select'].select.return_value = ([], [], [])
        env['parse_proc_identity'].return_value = dict(tx.identity, start=1)
        with self.assertRaisesRegex(RuntimeError, 'PROVISION_PROCESS_IDENTITY_CHANGED'):
            env['monitor_provision'](tx)
        env['properties'].assert_not_called()

    def test_terminal_observation_does_not_set_acceptance_or_cleanup(self):
        env, tx, _ = setup(); result = {'native_runtime':'UNTESTED', 'cleanup':'UNKNOWN'}
        env['result'] = result
        env['completed_provision_unit'](tx)
        self.assertEqual(result, {'native_runtime':'UNTESTED', 'cleanup':'UNKNOWN'})
        source = generated()
        self.assertLess(source.index('completion = monitor_provision'), source.index('verified_report_lines = proof_supervisor'))
        self.assertLess(source.index('verified_report_lines = proof_supervisor'), source.index('result["native_runtime"] = "PASS"'))
        self.assertIn('retain = started and result.get("cleanup") != "PASS"', source)

    def test_complete_terminal_still_requires_ordered_bound_proofs(self):
        env, tx, _ = setup()
        env['completed_provision_unit'](tx)
        proof = runtime(); frame, before, after = fixture()
        line = encode(proof, frame)
        records = [dict(phase='native_finish', monotonic_ns=100, pid=13297, wait_status=0, output=line.decode()),
                   dict(phase='native_proof', monotonic_ns=101, frame=line.decode(), before=before, after=after,
                        certificate=proof['proof_verify'](line, before, after)),
                   dict(phase='launcher_complete', monotonic_ns=102, result='REPORTER_FINISHED_NOT_ISOLATION_ACCEPTANCE')]
        binding = {key:before[key] for key in ('identity', 'nonce', 'provision')}
        dump = lambda rows:'\n'.join(json.dumps(row) for row in rows)
        self.assertEqual(proof['proof_supervisor'](dump(records), binding), frame['report'])
        failures = [[], records[:-1], records[1:], list(reversed(records)), records+records[-1:]]
        changed = copy.deepcopy(records); changed[0]['wait_status'] = 9; failures.append(changed)
        changed = copy.deepcopy(records); changed[1]['before']['identity']['start'] += 1; failures.append(changed)
        for rows in failures:
            with self.assertRaises(RuntimeError): proof['proof_supervisor'](dump(rows), binding)


if __name__ == '__main__': unittest.main()
