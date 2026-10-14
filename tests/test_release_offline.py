"""Deterministic release gate, report provenance and cache tests. No external calls."""
import io
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_offline_automation import load, report, SHA, NEW_SHA, OPS, wrapper
from tests import test_vps_validation as validation_tests
controller = validation_tests.controller

policy = importlib.import_module('release_evidence')
gate = load('github_release_validation')


def identity():
    return policy.binding({'repository': gate.REPOSITORY, 'image': 'sha256:' + 'c' * 64},
                          OPS, lambda path: None)


def wire(value):
    return json.dumps(value, sort_keys=True).encode()


class ReleasePolicyTests(unittest.TestCase):
    def test_exact_versioned_report_and_all_bindings(self):
        expected = identity()
        value = policy.envelope(report(), expected)
        self.assertEqual(policy.validate_envelope(wire(value), SHA, expected), value)
        for field, replacement in [('candidate_sha', NEW_SHA), ('schema', True),
                ('schema', 2), ('validator_revision', 'd' * 64),
                ('configuration_digest', 'd' * 64), ('image_digest', 'sha256:' + 'd' * 64),
                ('report', {**report(), 'commit_sha': NEW_SHA})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                policy.validate_envelope(wire({**value, field: replacement}), SHA, expected)
        for raw in (wire(value)[:-1] + b',"schema":1}', b'x' * 16385, wire(report())):
            with self.assertRaises(ValueError):
                policy.validate_envelope(raw, SHA, expected)

    def test_fail_not_success_and_attestation_does_not_override_report(self):
        failed = {**report(), 'result': 'FAIL', 'reason': 'EXECUTION_ERROR'}
        self.assertEqual(policy.validate_envelope(wire(policy.envelope(failed, identity())), SHA)
                         ['report']['result'], 'FAIL')
        for change in ({'provider_request_count': 1}, {'cleanup_status': 'FAILED'},
                       {'credential_leakage_check': False}):
            with self.assertRaises(ValueError):
                policy.validate_envelope(wire(policy.envelope({**report(), **change}, identity())), SHA)

    def test_files_and_effective_configuration_invalidate_identity_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in policy.FILES:
                (root / name).write_bytes((OPS / name).read_bytes())
            config = {'repository': gate.REPOSITORY, 'image': 'sha256:' + 'c' * 64,
                      'provider_key_file': 'SECRET_CANARY', 'github_token_file': 'SECRET_CANARY'}
            original = policy.binding(config, root, lambda path: None)
            self.assertNotIn('SECRET_CANARY', json.dumps(original))
            for name in policy.FILES:
                path = root / name
                before = path.read_bytes()
                path.write_bytes(before + b'\n# revision change\n')
                self.assertNotEqual(policy.binding(config, root, lambda path: None), original)
                path.write_bytes(before)
            config['image'] = 'sha256:' + 'd' * 64
            changed = policy.binding(config, root, lambda path: None)
            self.assertNotEqual(changed['configuration_digest'], original['configuration_digest'])

    def test_binding_checks_ownership_before_reading_each_file(self):
        check = Mock(side_effect=ValueError('INVALID_CONFIGURATION'))
        with self.assertRaises(ValueError):
            policy.revision(OPS, check)
        self.assertEqual(check.call_count, 1)


class ReleaseCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / 'token'
        self.key.write_text('synthetic-token')
        self.config = {'github_token_file': str(self.key)}
        self.verify = Mock()
        self.execute = Mock(return_value=report())
        self.binding = identity()

    def invoke(self, sha=SHA, binding=None):
        current = binding or self.binding
        result = policy.cached_release(self.config, self.root / ('a' * 32), sha, current,
            runs=self.root, verify=self.verify, execute=self.execute,
            current_binding=lambda: current)
        policy.save_success(result, current, self.root)
        return result

    def test_reuses_exact_pass_only_after_rechecking_main_without_rerunning(self):
        self.assertEqual(self.invoke(), report())
        self.assertEqual(self.invoke(), report())
        self.assertEqual(self.execute.call_count, 1)
        self.assertEqual(self.verify.call_count, 2)
        cached = self.root / 'release-evidence.json'
        self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('synthetic-token', cached.read_text())
        self.assertEqual(len(list(self.root.glob('release-evidence*'))), 1)

    def test_sha_revision_and_config_changes_do_not_reuse(self):
        self.invoke()
        self.execute.return_value = {**report(), 'commit_sha': NEW_SHA}
        self.invoke(NEW_SHA)
        for field in policy.BINDING_FIELDS:
            changed = dict(self.binding)
            changed[field] = ('sha256:' if field == 'image_digest' else '') + 'd' * 64
            self.invoke(NEW_SHA, changed)
        self.assertEqual(self.execute.call_count, 5)

    def test_failures_are_never_cached(self):
        self.execute.return_value = {**report(), 'result': 'FAIL', 'reason': 'BUSY'}
        self.assertEqual(self.invoke()['result'], 'FAIL')
        self.assertFalse((self.root / 'release-evidence.json').exists())

    def test_stale_main_fails_even_with_cache(self):
        self.invoke()
        self.verify.side_effect = ValueError('WRONG_SHA')
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(self.execute.call_count, 1)

    def test_symlink_oversize_and_malformed_cache_fail_closed(self):
        cached = self.root / 'release-evidence.json'
        cached.symlink_to(self.key)
        with self.assertRaises(OSError):
            self.invoke()
        cached.unlink()
        for raw in ('x' * 16385, '{}', '{"schema":1,"schema":1}'):
            cached.write_text(raw)
            cached.chmod(0o600)
            with self.assertRaises((ValueError, KeyError)):
                self.invoke()
        self.execute.assert_not_called()

    def test_inflight_binding_change_never_saves_evidence(self):
        with self.assertRaises(ValueError):
            policy.cached_release(self.config, self.root / ('a' * 32), SHA, self.binding,
                runs=self.root, verify=self.verify, execute=self.execute,
                current_binding=lambda: {})
        self.assertFalse((self.root / 'release-evidence.json').exists())


class ReleaseGateTests(unittest.TestCase):
    def environment(self):
        return {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REPOSITORY': gate.REPOSITORY, 'GITHUB_SHA': SHA,
                'GITHUB_WORKFLOW_REF': gate.REPOSITORY + '/.github/workflows/tests.yml@refs/heads/main'}

    def event(self):
        return {'ref': 'refs/heads/main', 'after': SHA, 'deleted': False,
                'repository': {'full_name': gate.REPOSITORY, 'default_branch': 'main'}}

    def test_only_exact_trusted_main_push_is_eligible(self):
        self.assertEqual(gate.candidate(self.event(), self.environment()), SHA)
        for key, value in [('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_SHA', NEW_SHA),
                ('GITHUB_REF', 'refs/pull/1/merge'), ('GITHUB_REPOSITORY', 'evil/repo'),
                ('GITHUB_WORKFLOW_REF', gate.REPOSITORY + '/.github/workflows/tests.yml@refs/pull/1/merge')]:
            with self.subTest(key=key), self.assertRaises(gate.Rejected):
                gate.candidate(self.event(), {**self.environment(), key: value})
        for key, value in [('deleted', True), ('after', NEW_SHA), ('ref', 'refs/heads/other')]:
            with self.assertRaises(gate.Rejected):
                gate.candidate({**self.event(), key: value}, self.environment())

    def test_exact_compatible_envelope_required(self):
        api = Mock()
        api.call.return_value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': SHA}}
        value = policy.envelope(report(), identity())
        remote = Mock(return_value=wire(value))
        self.assertEqual(gate.execute(SHA, api, remote), value)
        remote.assert_called_once_with(None, SHA, release=True)
        for change in ({'validator_revision': 'd' * 64}, {'configuration_digest': 'd' * 64}):
            remote.return_value = wire({**value, **change})
            with self.assertRaises(gate.Rejected):
                gate.execute(SHA, api, remote)
        api.call.return_value['object']['sha'] = NEW_SHA
        remote.reset_mock()
        with self.assertRaises(gate.Rejected):
            gate.execute(SHA, api, remote)
        remote.assert_not_called()

    def test_invalid_transport_is_unverified_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / 'event.json'
            event.write_text(json.dumps(self.event()))
            env = {**self.environment(), 'GITHUB_EVENT_PATH': str(event), 'GITHUB_TOKEN': 'synthetic',
                   'RELEASE_OFFLINE_OUTPUT': str(root / 'evidence.json'),
                   'VALIDATOR_SSH_KEY_BASE64': 'SECRET_CANARY'}
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch.object(gate, 'execute', side_effect=ValueError('SECRET_CANARY')), patch('sys.stdout', output):
                self.assertEqual(gate.main(), 1)
                self.assertNotIn('VALIDATOR_SSH_KEY_BASE64', os.environ)
            self.assertEqual(json.loads(output.getvalue())['status'], 'UNVERIFIED')
            self.assertNotIn('SECRET_CANARY', output.getvalue())

    def test_workflow_orders_release_acceptance_before_deploy_with_existing_permissions(self):
        workflow = (OPS.parents[1] / '.github/workflows/tests.yml').read_text()
        gate_block = workflow.split('\n  release_offline:\n')[1].split('\n  deploy:\n')[0]
        deploy = workflow.split('\n  deploy:\n')[1]
        self.assertIn('needs: [test, frontend]', gate_block)
        self.assertIn('needs: [test, frontend, release_offline]', deploy)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", gate_block)
        self.assertIn('ref: ${{ github.sha }}', gate_block)
        self.assertIn('persist-credentials: false', gate_block)
        self.assertIn('permissions:\n      contents: read', gate_block)
        for value in ('actions: write', 'statuses: write', 'issues: write', 'pull_request_target',
                      'secrets.MODELFC_DEPLOY', 'pip install', 'continue-on-error: true\n        env:'):
            self.assertNotIn(value, gate_block)


class ReleaseControllerTests(unittest.TestCase):
    def test_wrapper_separate_fixed_release_operation(self):
        command = 'validate-offline-release stevengalvis/modelfc ' + SHA
        self.assertEqual(wrapper.controller_arguments(command)[-5:], ['--release', '--sha', SHA, '--mode', 'offline'])
        for value in (command + ' --mode live', command + ';id', command + '\n',
                      command.replace(SHA, 'main'), command.replace('stevengalvis/modelfc', 'evil/repo')):
            with self.assertRaises(ValueError):
                wrapper.controller_arguments(value)

    def test_only_canonical_main_commit_is_accepted(self):
        valid = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': SHA}}
        with patch.object(controller, 'read_url', return_value=(200, wire(valid))):
            controller.verify_release(SHA, 'synthetic')
        for value in ({**valid, 'ref': 'refs/heads/evil'},
                      {**valid, 'object': {'type': 'commit', 'sha': NEW_SHA}}):
            with patch.object(controller, 'read_url', return_value=(200, wire(value))), self.assertRaises(controller.Failure):
                controller.verify_release(SHA, 'synthetic')

    def test_release_source_fetch_stays_on_main_and_only_exports_src(self):
        fixture = validation_tests.ControllerTests()
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            def command(args, **kwargs):
                if 'init' in args: (run / 'git').mkdir()
                if 'fetch' in args: self.assertEqual(args[-1], 'refs/heads/main')
                if 'rev-parse' in args: return SHA.encode()
                if 'archive' in args:
                    self.assertEqual(args[-2:], [SHA, 'src'])
                    return fixture.tar()
                return b''
            with patch.object(controller, 'command', side_effect=command):
                controller.export_source(run, None, SHA, 'synthetic', release=True)
            self.assertTrue((run / 'subject/src/modelfc/__init__.py').is_file())

    def test_parent_caches_only_after_service_exit_and_final_cleanup(self):
        value = policy.envelope(report(), identity())
        for failure in ('none', 'service', 'cleanup'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                token = root / 'token'
                token.write_text('synthetic-token')
                config = {'repository': gate.REPOSITORY, 'image': 'sha256:' + 'c' * 64,
                          'github_token_file': str(token)}
                output = io.StringIO()
                command = Mock(return_value=wire(value))
                cleanup = Mock()
                if failure == 'service': command.side_effect = controller.Failure('TIMEOUT')
                if failure == 'cleanup': cleanup.side_effect = controller.Failure('CLEANUP_FAILED')
                with patch.object(sys, 'argv', ['validator', '--repository', gate.REPOSITORY,
                        '--release', '--sha', SHA]), patch.object(controller, 'configuration', return_value=config), \
                        patch.object(controller, 'root_owned'), patch.object(controller, 'RUNS', root), \
                        patch.object(controller, 'command', command), patch.object(controller, 'cleanup', cleanup), \
                        patch('sys.stdout', output):
                    controller.main()
                evidence = policy.validate_envelope(output.getvalue().encode(), SHA, identity())
                self.assertEqual(evidence['report']['result'], 'PASS' if failure == 'none' else 'FAIL')
                self.assertEqual((root / 'release-evidence.json').exists(), failure == 'none')

    def test_worker_result_is_not_persisted_before_service_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / 'token'
            token.write_text('synthetic')
            result = policy.cached_release({'github_token_file': str(token)}, root / ('a' * 32),
                SHA, identity(), runs=root, verify=Mock(), execute=report,
                current_binding=identity)
            self.assertEqual(result, report())
            self.assertFalse((root / 'release-evidence.json').exists())

    def test_release_service_is_fixed_offline_and_has_no_pr_selector(self):
        args = controller.service_command(Path('/runs/' + 'a' * 32), gate.REPOSITORY,
                                          None, SHA, release=True)
        self.assertIn('--release', args)
        self.assertNotIn('--pr', args)
        self.assertEqual(args[-2:], ['--mode', 'offline'])


if __name__ == '__main__':
    unittest.main()
