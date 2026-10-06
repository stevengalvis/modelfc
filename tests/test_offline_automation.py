"""Offline automation boundary tests: no SSH, VPS or provider operations."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / 'ops/vps'


def load(name):
    spec = importlib.util.spec_from_file_location(name, OPS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Trusted helper modules only, never import candidate application modules.
sys.path.insert(0, str(OPS))
runner = load('github_offline_validation')
wrapper = load('validate_offline_ssh')
policy = load('offline_report')
SHA = 'a' * 40
NEW_SHA = 'b' * 40


def event():
    user = {'id': 1, 'login': 'maintainer', 'type': 'User'}
    return {'action': 'created', 'repository': {'full_name': runner.REPOSITORY,
            'default_branch': 'main'}, 'issue': {'number': 99, 'pull_request': {'url': 'unused'}},
            'comment': {'body': '/validate-offline', 'author_association': 'OWNER', 'user': user},
            'sender': dict(user)}


def pr(sha=SHA):
    return {'number': 99, 'state': 'open', 'base': {'repo': {'full_name': runner.REPOSITORY}},
            'head': {'sha': sha, 'repo': {'full_name': runner.REPOSITORY}}}


def api(heads=None, permission='write'):
    client = Mock()
    values = iter(heads or [pr(), pr(), pr()])
    def call(path, data=None):
        if path.startswith('/collaborators/'):
            return {'permission': permission, 'user': {'id': 1}}
        if path == '/pulls/99':
            return next(values)
        raise AssertionError(path)
    client.call.side_effect = call
    return client


def report():
    value = {k: 0 for k in policy.COUNTS}
    value.update({k: True for k in policy.FLAGS})
    value.update({k: None for k in policy.TEXT})
    value.update(result='PASS', mode='OFFLINE', commit_sha=SHA, competition='E1',
                 cleanup_status='COMPLETE', reason='COMPLETE', credential_leakage_check=True,
                 core_pipeline='PASS', market_intelligence='PASS', offline_replay='PASS',
                 provider_compatibility='NOT_RUN', scenario_request_count=3,
                 prediction_count=1, target_count=2, supported_team_total_count=4,
                 capture_hash='c' * 64)
    return value


def wire(value):
    return json.dumps(value).encode()


class AuthorizationTests(unittest.TestCase):
    def test_authorized_same_repo_resolves_api_sha(self):
        for permission in ('write', 'admin'):
            self.assertEqual(runner.resolve(event(), api(permission=permission)), (99, SHA))

    def test_unauthorized_roles_fail_closed_without_api(self):
        for role in ('NONE', 'CONTRIBUTOR', 'FIRST_TIME_CONTRIBUTOR', None):
            value = event()
            value['comment']['author_association'] = role
            client = api()
            with self.assertRaises(runner.Rejected):
                runner.resolve(value, client)
            client.call.assert_not_called()
        for permission in ('read', 'none', 'triage', None):
            with self.assertRaises(runner.Rejected):
                runner.resolve(event(), api(permission=permission))

    def test_permission_user_id_must_match(self):
        client = Mock()
        client.call.return_value = {'permission': 'admin', 'user': {'id': 2}}
        with self.assertRaises(runner.Rejected):
            runner.resolve(event(), client)

    def test_issue_not_pr_and_malformed_command_ignored(self):
        values = []
        value = event()
        value['issue'].pop('pull_request')
        values.append(value)
        for body in (' /validate-offline', '/validate-offline\n', '/validate-live',
                     '/validate-offline ' + SHA, '/validate-offline;id', ''):
            value = event()
            value['comment']['body'] = body
            values.append(value)
        for value in values:
            client = api()
            with self.assertRaises(runner.Rejected):
                runner.resolve(value, client)
            client.call.assert_not_called()

    def test_sender_repository_and_event_action(self):
        for field, replacement in (('sender', {'id': 2, 'login': 'maintainer'}),
                                   ('action', 'edited'), ('repository', {'full_name': 'evil/repo'})):
            value = event()
            value[field] = replacement
            with self.assertRaises(runner.Rejected):
                runner.resolve(value, api())

    def test_fork_closed_wrong_base_invalid_sha(self):
        values = []
        value = pr()
        value['head']['repo']['full_name'] = 'fork/modelfc'
        values.append(value)
        value = pr()
        value['state'] = 'closed'
        values.append(value)
        value = pr()
        value['base']['repo']['full_name'] = 'other/repo'
        values.append(value)
        values += [pr('A' * 40), pr('a' * 39), pr('a' * 40 + ';id')]
        for value in values:
            with self.assertRaises(runner.Rejected):
                runner.resolve(event(), api([value]))

    def test_head_movement_before_remote_has_no_status_or_remote(self):
        client = api([pr(NEW_SHA)])
        remote = Mock()
        with self.assertRaises(runner.Rejected):
            runner.execute(event(), SHA, client, remote)
        remote.assert_not_called()
        client.status.assert_not_called()

    def test_sha_from_body_never_accepted(self):
        value = event()
        value['comment']['body'] += ' ' + NEW_SHA
        with self.assertRaises(runner.Rejected):
            runner.resolve(value, api())


class ReportTests(unittest.TestCase):
    def test_exact_pass_and_explicit_older_interface_na(self):
        for component in ('PASS', 'NOT_APPLICABLE'):
            value = report()
            value['market_intelligence'] = component
            self.assertEqual(policy.validate_report(wire(value), SHA), value)

    def test_reject_security_and_component_states(self):
        changes = {'commit_sha': NEW_SHA, 'mode': 'LIVE', 'provider_request_count': 1,
                   'result': 'BLOCKED', 'cleanup_status': 'FAILED',
                   'credential_leakage_check': False, 'core_pipeline': 'NOT_RUN',
                   'offline_replay': 'FAIL', 'market_intelligence': 'FAIL',
                   'provider_compatibility': 'PASS', 'reason': 'BUSY',
                   'scenario_request_count': 2, 'immutable_capture_verified': False,
                   'offline_replay_identical': False, 'replay_api_request_count': 1,
                   'prediction_count': 0, 'target_count': 0, 'capture_hash': None}
        for key, item in changes.items():
            with self.subTest(key=key):
                value = report()
                value[key] = item
                with self.assertRaisesRegex(ValueError, 'INVALID_REPORT'):
                    policy.validate_report(wire(value), SHA)
        for item in (None, 1, 'true'):
            value = report()
            value['credential_leakage_check'] = item
            with self.assertRaises(ValueError):
                policy.validate_report(wire(value), SHA)

    def test_malformed_missing_extra_duplicate_nonfinite_and_size(self):
        bad = [b'no json', b'[]', b'{"result":NaN}', b'x' * (policy.MAX_REPORT + 1)]
        for key in policy.FIELDS:
            value = report()
            del value[key]
            bad.append(wire(value))
        value = report()
        value['candidate_output'] = 'secret'
        bad.append(wire(value))
        bad.append(wire(report())[:-1] + b',"result":"PASS"}')
        for raw in bad:
            with self.assertRaises(ValueError):
                policy.validate_report(raw, SHA)

    def test_failed_report_is_research_result_not_success(self):
        value = report()
        value.update(result='FAIL', reason='ASSERTION_FAILED')
        self.assertEqual(policy.validate_report(wire(value), SHA)['result'], 'FAIL')


class ExecutionTests(unittest.TestCase):
    def test_success_statuses_only_resolved_sha(self):
        client, remote = api(), Mock(return_value=wire(report()))
        self.assertEqual(runner.execute(event(), SHA, client, remote), 'success')
        remote.assert_called_once_with(99, SHA)
        self.assertEqual([c.args[:2] for c in client.status.call_args_list],
                         [(SHA, 'pending'), (SHA, 'success')])

    def test_old_sha_success_never_validates_new_head(self):
        client = api([pr(), pr(NEW_SHA)])
        self.assertEqual(runner.execute(event(), SHA, client, lambda *a: wire(report())), 'success')
        self.assertEqual([c.args[0] for c in client.status.call_args_list], [SHA, SHA])
        self.assertIn('new head needs validation', client.status.call_args.args[2])

    def test_transport_timeout_and_invalid_report_never_success(self):
        for failure in (runner.Rejected(), subprocess.TimeoutExpired('ssh', 420), OSError()):
            client, remote = api(), Mock(side_effect=failure)
            self.assertEqual(runner.execute(event(), SHA, client, remote), 'error')
            remote.assert_called_once()
        client = api()
        self.assertEqual(runner.execute(event(), SHA, client, lambda *a: b'raw unsafe output'), 'error')
        self.assertNotIn('raw unsafe', client.status.call_args.args[2])

    def test_fail_and_busy_no_retry(self):
        for reason in ('BUSY', 'ASSERTION_FAILED'):
            value = report()
            value.update(result='FAIL', reason=reason)
            client, remote = api(), Mock(return_value=wire(value))
            self.assertEqual(runner.execute(event(), SHA, client, remote), 'failure')
            remote.assert_called_once()

    def test_nonzero_ssh_exit_even_with_pass_report_is_error(self):
        with patch.dict('os.environ', {'VALIDATOR_HOST': 'validator.example',
                'VALIDATOR_HOST_KEY': 'validator.example ssh-ed25519 AAAA',
                'VALIDATOR_KEY_FINGERPRINT': 'SHA256:' + 'a' * 43,
                'VALIDATOR_SSH_KEY': 'synthetic offline test key'}), patch.object(
                runner.subprocess, 'run', side_effect=[
                    SimpleNamespace(stdout=b'public', returncode=0),
                    SimpleNamespace(stdout=('256 SHA256:' + 'a' * 43 + ' test').encode(), returncode=0),
                    SimpleNamespace(stdout=wire(report()), returncode=255)]) as run:
            with self.assertRaises(runner.Rejected):
                runner.transport(99, SHA)
            self.assertEqual(run.call_count, 3)
            command = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('ClearAllForwardings=yes', command)
            self.assertIn('BatchMode=yes', command)
            self.assertNotIn('shell', run.call_args.kwargs)


class WrapperTests(unittest.TestCase):
    def test_fixed_offline_arguments(self):
        args = wrapper.controller_arguments('validate-offline stevengalvis/modelfc 99 ' + SHA)
        self.assertEqual(args[-2:], ['--mode', 'offline'])
        self.assertEqual(args[2], wrapper.CONTROLLER)

    def test_reject_every_other_capability(self):
        valid = 'validate-offline stevengalvis/modelfc 99 ' + SHA
        for command in (valid + ' --mode live', valid + ' --worker abc', valid + ';id',
                        valid + '\n', valid.replace('99', '0'), valid.replace('99', '-1'),
                        valid.replace('99', '1.2'), valid.replace(SHA, 'A' * 40),
                        valid.replace(SHA, 'a' * 39), valid.replace(SHA, '$(id)'),
                        valid.replace('stevengalvis/modelfc', 'evil/repo'),
                        valid.replace('validate-offline', 'validate-live'),
                        'cat /etc/passwd', '', 'bash', valid + ' && touch /tmp/x'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                wrapper.controller_arguments(command)

    def test_wrapper_rejects_before_any_process(self):
        with patch.object(wrapper.sys, 'argv', ['wrapper']), patch.dict('os.environ',
                {'SSH_ORIGINAL_COMMAND': 'cat /etc/passwd'}), patch.object(wrapper.subprocess, 'run') as run:
            self.assertEqual(wrapper.main(), 1)
            run.assert_not_called()

    def test_valid_wrapper_uses_minimal_env_fixed_user_and_only_controller_output(self):
        output = SimpleNamespace(buffer=io.BytesIO())
        account = SimpleNamespace(pw_name='modelfc-validator', pw_uid=2000, pw_dir='/validator-home')
        with patch.object(wrapper.sys, 'argv', ['wrapper']), patch.object(wrapper.sys, 'stdout', output), \
                patch.dict('os.environ', {'SSH_ORIGINAL_COMMAND':
                    'validate-offline stevengalvis/modelfc 99 ' + SHA,
                    'ODDSPAPI_API_KEY': 'synthetic-forbidden-value', 'PYTHONPATH': '/candidate'}), \
                patch.object(wrapper.pwd, 'getpwuid', return_value=account), \
                patch.object(wrapper.Path, 'lstat', return_value=SimpleNamespace(st_uid=0, st_mode=0o555)), \
                patch.object(wrapper.Path, 'is_symlink', return_value=False), \
                patch.object(wrapper.subprocess, 'run', return_value=SimpleNamespace(stdout=wire(report()))) as run:
            self.assertEqual(wrapper.main(), 0)
            self.assertEqual(output.buffer.getvalue(), wire(report()))
            self.assertEqual(run.call_args.args[0][-2:], ['--mode', 'offline'])
            self.assertNotIn('ODDSPAPI_API_KEY', run.call_args.kwargs['env'])
            self.assertNotIn('PYTHONPATH', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['env']['XDG_RUNTIME_DIR'], '/run/user/2000')
            self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['timeout'], 390)
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_key_boundary_rejects_deploy_identity_before_process(self):
        with patch.dict('os.environ', {'VALIDATOR_HOST': 'validator.example',
                'VALIDATOR_HOST_KEY': 'validator.example ssh-ed25519 AAAA',
                'VALIDATOR_KEY_FINGERPRINT': runner.DEPLOY_FINGERPRINT,
                'VALIDATOR_SSH_KEY': 'synthetic offline test key'}), \
                patch.object(runner.subprocess, 'run') as run:
            with self.assertRaises(runner.Rejected):
                runner.transport(99, SHA)
            run.assert_not_called()

    def test_workflow_trusted_ref_minimum_permissions_and_no_cancellation(self):
        workflow = (ROOT / '.github/workflows/trusted-offline.yml').read_text()
        self.assertIn('issue_comment:', workflow)
        self.assertIn('types: [created]', workflow)
        self.assertIn('ref: ${{ github.sha }}', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn('statuses: write', workflow)
        for forbidden in ('pull_request_target', 'issues: write', 'secrets.MODELFC_DEPLOY',
                          'github.event.comment.body }}', 'github.event.pull_request.head', 'validate-live'):
            self.assertNotIn(forbidden, workflow)


if __name__ == '__main__':
    unittest.main()
