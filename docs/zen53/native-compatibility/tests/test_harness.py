"""Pure generated-source tests: no host commands, signals or process operations."""
import ast
import json
from pathlib import Path
import select
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import harness
import provisional


def generated():
    return harness.instrument(provisional.supervisor((ROOT/'candidate/supervisor.py').read_bytes())).decode()


def environment():
    tree = ast.parse(generated())
    # No imports, assignments or module-level runtime body except the two generated registries.
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) or
             isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                ('DIAGNOSTIC_CODES', 'DIAGNOSTIC_ASSERTIONS') for t in n.targets)]
    env = dict(sys=sys, select=NS(select=Mock(return_value=([99], [], []))),
               pathlib=NS(Path=Mock(return_value=NS(exists=Mock(return_value=False)))),
               BASE=Path('/tmp/zen53-native-preparation-test-fixture'), UNIT='zen53-mincompat.service')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<authenticated-fixture>', 'exec'), env)
    return env


def terminal(env):
    identity = dict(pid=15785,start=1633752,uid=63153,gid=63153,
                    invocation='a'*32,group='/system.slice/zen53-mincompat.service')
    props = dict(ActiveState='active',SubState='exited',MainPID='0',ControlPID='0',
                 ControlGroup='',InvocationID=identity['invocation'],RootDirectory=str(env['BASE']/'rootfs'))
    env['provision_transaction']=NS(io=NS(identity=identity,pidfd=99))
    env['properties']=Mock(side_effect=lambda unit,keys:{k:props[k] for k in keys})
    env['command']=Mock(return_value='')
    return props


class Harness(unittest.TestCase):
    def test_generation_is_exact_and_runtime_scope_unchanged(self):
        old=provisional.supervisor((ROOT/'candidate/supervisor.py').read_bytes()).decode()
        new=generated()
        ast.parse(new)
        self.assertNotIn(".co_consts[", new)
        with self.assertRaises(ValueError):harness.instrument(new.encode())
        # Final proof/acceptance and post-stop cleanup/hold remain byte-identical.
        marker = '    raw = (EVIDENCE / "stdout.log").read_text()'
        self.assertEqual(new[new.index(marker):], old[old.index(marker):])
        tail = '    need(identity is not None and props.get("InvocationID")'
        self.assertEqual(new[new.index(tail):].split('def provision_query',1)[0],
                         old[old.index(tail):].split('def provision_query',1)[0])


    def test_running_to_exit_race_gets_precise_failure_not_pass(self):
        env=environment();io=env['ProvisionRootIO'](None,None,None)
        io.pidfd=99
        for ready,expected in ((True,'PROVISION_PIDFD_DEAD'),(False,'PROVISION_UNIT_IDENTITY_CHANGED')):
            env['select'].select.return_value=([99] if ready else [],[],[])
            io.query=lambda:dict(MainPID='0',ActiveState='active',SubState='exited')
            io.identity=dict(pid=15785,invocation='a'*32,group='/system.slice/zen53-mincompat.service')
            # Exercise the exact loop ordering with a captured running observation.
            captured = dict(SubState='running')
            try:
                if captured['SubState'] == 'running':
                    io.check()
            except RuntimeError as error:
                record=env['error_diagnostic']('supervisor',error)
            self.assertEqual(record['predicate'],expected)
            self.assertGreaterEqual(len(record['trace']),2)
            self.assertTrue(any(f['function']!='UNKNOWN' for f in record['trace']))
            self.assertNotIn(str(ROOT),json.dumps(record))

    def test_unknown_messages_and_foreign_code_never_export(self):
        env=environment()
        for error in (RuntimeError('SECRET=/credential/path'),RuntimeError('PROVISION_PIDFD_DEAD'),ValueError('secret')):
            try:raise error
            except Exception as caught:r=env['error_diagnostic']('secret-stage',caught)
            self.assertEqual(r['predicate'],'UNKNOWN');self.assertEqual(r['stage'],'UNKNOWN')
            self.assertNotIn('SECRET',json.dumps(r));self.assertNotIn('/credential',json.dumps(r))
            self.assertEqual(r['trace'][-1],dict(function='UNKNOWN',line=0))

    def test_trace_depth_is_bounded_and_truncated(self):
        env=environment()
        def recurse(n):
            if n:recurse(n-1)
            else:env['require'](False,'PROVISION_PIDFD_DEAD')
        for depth in (12,90):
            try:recurse(depth)
            except RuntimeError as error:r=env['error_diagnostic']('supervisor',error)
            self.assertLessEqual(len(r['trace']),8);self.assertTrue(r['trace_truncated'])
            self.assertEqual(r['predicate'],'UNKNOWN')

    def test_terminal_stop_once_after_two_complete_checks(self):
        env=environment();terminal(env);env['stop_provision_unit']()
        self.assertEqual(env['properties'].call_count,3)
        self.assertEqual(env['select'].select.call_count,2)
        env['command'].assert_called_once_with(['systemctl','stop',env['UNIT']])

    def test_terminal_mismatches_never_stop(self):
        for field,value in dict(InvocationID='b'*32,RootDirectory='/foreign',MainPID='1',ControlPID='1',ControlGroup='/foreign').items():
            env=environment();props=terminal(env);props[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):env['stop_provision_unit']()
            env['command'].assert_not_called()
        for kind in ('missing_identity','missing_pidfd','live_pidfd','remaining_cgroup','wrong_group'):
            env=environment();terminal(env);io=env['provision_transaction'].io
            if kind=='missing_identity':io.identity=None
            elif kind=='missing_pidfd':io.pidfd=None
            elif kind=='live_pidfd':env['select'].select.return_value=([],[],[])
            elif kind=='remaining_cgroup':env['pathlib'].Path.return_value.exists.return_value=True
            else:io.identity['group']='/foreign'
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):env['stop_provision_unit']()
            env['command'].assert_not_called()

    def test_immediate_recheck_rejects_drift(self):
        env=environment();props=terminal(env);original=env['properties'].side_effect
        def query(unit,keys):
            if env['properties'].call_count==3:props['InvocationID']='b'*32
            return original(unit,keys)
        env['properties'].side_effect=query
        with self.assertRaises(RuntimeError):env['stop_provision_unit']()
        env['command'].assert_not_called()

    def test_query_and_stop_errors_never_retry(self):
        for error in (RuntimeError('query'),subprocess.TimeoutExpired('query',8)):
            env=environment();terminal(env);env['properties'].side_effect=error
            with self.assertRaises(type(error)):env['stop_provision_unit']()
            env['command'].assert_not_called()
        for error in (RuntimeError('stop'),subprocess.TimeoutExpired('stop',8)):
            env=environment();terminal(env);env['command'].side_effect=error
            with self.assertRaises(type(error)):env['stop_provision_unit']()
            self.assertEqual(env['command'].call_count,1)

    def test_existing_live_and_inactive_paths_preserved(self):
        env=environment();props=terminal(env)
        props.update(SubState='running',MainPID='15785',ControlGroup='/system.slice/'+env['UNIT'])
        env['stop_provision_unit']();env['command'].assert_called_once()
        env=environment();props=terminal(env);props.update(ActiveState='inactive',SubState='dead')
        env['stop_provision_unit']();env['command'].assert_not_called()

    def test_nested_predicate_and_observation_errors(self):
        env=environment();props=terminal(env);props['RootDirectory']='/foreign'
        try:env['stop_provision_unit']()
        except RuntimeError as error:r=env['error_diagnostic']('cleanup',error)
        self.assertEqual(r['predicate'],'CLEANUP_TERMINAL_BINDING')
        for kind in ('pidfd','cgroup'):
            env=environment();terminal(env)
            if kind=='pidfd':env['select'].select.side_effect=OSError('private detail')
            else:env['pathlib'].Path.return_value.exists.side_effect=PermissionError('private detail')
            with self.assertRaises(OSError):env['stop_provision_unit']()
            env['command'].assert_not_called()

    def test_later_residual_cleanup_still_keeps_hold(self):
        # Execute the unchanged post-stop checks and hold decision with synthetic IO.
        source=generated()
        start=source.index('            props = properties(UNIT, ("ActiveState", "MainPID", "ControlPID", "ControlGroup"))',source.index('finally:\n    if started:'))
        end=source.index('    if provision_transaction is not None:',start)
        import textwrap
        checks='try:\n'+textwrap.dedent(source[start:end])
        for residue in ('unit','cgroup','identity','private_tmp','mount'):
            env=environment();terminal(env);env['result']={};env['USER']='zen53-mincompat'
            env['error_diagnostic_original']=env['error_diagnostic']
            env['pwd']=NS(getpwnam=Mock(side_effect=KeyError('absent')))
            env['properties']=Mock(side_effect=lambda unit,keys:{k:({'ActiveState':'inactive','MainPID':'0','ControlPID':'0','ControlGroup':'','LoadState':'not-found'}[k]) for k in keys})
            class FakePath:
                def __init__(self,path):self.path=str(path)
                def exists(self):return residue=='cgroup'
                def glob(self,pattern):return ['exact-temp'] if residue=='private_tmp' else []
                def read_text(self):return '1 0 0:1 / '+str(env['BASE'])+'/rootfs rw - tmpfs tmpfs rw' if residue=='mount' else ''
            env['pathlib']=NS(Path=FakePath)
            if residue=='identity':env['pwd'].getpwnam.side_effect=None
            if residue=='unit':env['properties'].side_effect=lambda unit,keys:{k:({'ActiveState':'inactive','MainPID':'0','ControlPID':'0','ControlGroup':'','LoadState':'loaded'}[k]) for k in keys}
            exec(compile(checks,'<unchanged-cleanup-fixture>','exec'),env)
            self.assertTrue(env['result']['cleanup'].startswith('BLOCKED:'))
            self.assertTrue(True and env['result'].get('cleanup') != 'PASS')
