"""Generated cleanup policy tests; no process, service or native execution."""
import ast
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from tests.test_zen53_workspace_generator import load, WORKSPACE, baseline


class WorkspaceCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = load('workspace_cleanup_generator', WORKSPACE / 'native_generator.py')
        tree = ast.parse(cls.generator.generate(baseline())['supervisor.py'])
        cls.retain = compile(ast.Expression(next(n.value for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'retain'
                for t in n.targets))), '<actual-lock-retention>', 'eval')

    def scenario(self, fixture='BEFORE', verified=False, cleanup='PASS', status='BLOCKED'):
        def need(ok):
            if not ok:
                raise RuntimeError('PROVISIONAL_PROOF_INVALID')
        env = {'os': NS(close=Mock()), 'proof_need': need,
               'result': {'status': status, 'cleanup': cleanup,
                          'native_runtime': 'PASS', 'transition': 'PASS'},
               'provision_transaction': NS(identity={'fixture': True})}
        exec(self.generator.SUPERVISOR, env)
        store = NS(cleanupverify=Mock(return_value={'state':'WORKSPACE_INTEGRITY_VERIFIED',
                                                   'fixture_state':fixture}),
                   postverify=Mock(return_value={'state':'FIXED_WORKSPACE_READBACK_ONLY','patch':'fixed'}),
                   close=Mock())
        env.update(workspace_store=store, workspace_rootfd=9, workspace_verified=verified)
        return env, store

    def retained(self, env):
        return eval(self.retain, {'started': True, 'result': env['result']})

    def test_early_failure_before_releases_only_after_complete_cleanup_without_patch(self):
        env, store = self.scenario()
        env['workspace_cleanup']()
        self.assertEqual(env['result']['cleanup'], 'PASS')
        self.assertEqual(env['result']['status'], 'BLOCKED')
        self.assertEqual(env['result']['workspace_integrity']['fixture_state'], 'BEFORE')
        self.assertFalse(self.retained(env))
        store.postverify.assert_not_called()
        store.close.assert_called_once()
        env['os'].close.assert_called_once_with(9)

    def test_before_cannot_satisfy_task_even_with_claimed_proof(self):
        env, store = self.scenario(verified=True, status='WORKSPACE_READBACK_PENDING')
        env['workspace_cleanup']()
        self.assertEqual(env['result']['status'], 'BLOCKED')
        self.assertFalse(self.retained(env))
        store.postverify.assert_not_called()

    def test_after_requires_every_existing_acceptance_gate(self):
        for missing in ('workspace_verified','native_runtime','transition','cleanup','status'):
            with self.subTest(missing=missing):
                env, store = self.scenario('AFTER', True, status='WORKSPACE_READBACK_PENDING')
                if missing == 'workspace_verified':env[missing] = False
                else:env['result'][missing] = 'BLOCKED'
                env['workspace_cleanup']()
                self.assertNotEqual(env['result']['status'], 'FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE')
                store.postverify.assert_not_called()
                self.assertEqual(self.retained(env), missing == 'cleanup')
        env, store = self.scenario('AFTER', True, status='WORKSPACE_READBACK_PENDING')
        env['workspace_cleanup']()
        self.assertEqual(env['result']['status'], 'FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE')
        self.assertFalse(self.retained(env))
        store.postverify.assert_called_once_with(env['provision_transaction'].identity)

    def test_unexpected_integrity_and_postreadback_failure_keep_hold(self):
        for failure in ('exception','unknown','extra','postverify'):
            with self.subTest(failure=failure):
                env, store = self.scenario('AFTER', True, status='WORKSPACE_READBACK_PENDING')
                if failure == 'exception':store.cleanupverify.side_effect = ValueError('private contents')
                elif failure == 'unknown':store.cleanupverify.return_value['fixture_state'] = 'PARTIAL'
                elif failure == 'extra':store.cleanupverify.return_value['patch'] = 'untrusted'
                else:store.postverify.side_effect = ValueError('changed after inspection')
                env['workspace_cleanup']()
                self.assertTrue(self.retained(env))
                self.assertEqual(env['result']['status'], 'BLOCKED')
                self.assertNotIn('private contents', str(env['result']))
                store.close.assert_called_once()
                env['os'].close.assert_called_once_with(9)

    def test_resource_or_descriptor_failure_never_releases_hold(self):
        for failure in ('resource','store_close','root_close'):
            with self.subTest(failure=failure):
                env, store = self.scenario('BEFORE', cleanup='BLOCKED:RESOURCE' if failure=='resource' else 'PASS')
                if failure == 'store_close':store.close.side_effect = ValueError('private')
                if failure == 'root_close':env['os'].close.side_effect = OSError('private')
                env['workspace_cleanup']()
                self.assertTrue(self.retained(env))
                self.assertEqual(env['result']['status'], 'BLOCKED')
                store.close.assert_called_once()
                env['os'].close.assert_called_once_with(9)
