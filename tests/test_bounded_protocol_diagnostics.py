"""Synthetic subprocess fixtures: no app server, model, credentials or network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker.codex import Codex
from ops.bounded_worker.task import Task, execute
import tests.test_bounded_worker as fixtures

SCRIPT = r'''
import json, os, sys, time
mode = sys.argv[1]
def send(obj):
    os.write(1, (json.dumps(obj)+'\n').encode())
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    method=request.get('method')
    if method is None:
        if mode == 'server' and request.get('error', {}).get('code') == -32000:
            send({'method':'turn/completed','params':{'turn':{'id':'t','status':'completed'}}})
        continue
    responses={'initialize':{},'config/read':{'config':{}},
        'permissionProfile/list':{'data':[{'id':'pilot','allowed':True}]},
        'thread/start':{'thread':{'id':'t0'},'model':'gpt-6.1-terra','modelProvider':'openai',
        'approvalPolicy':'never','serviceTier':'default','activePermissionProfile':{'id':'pilot'},
        'cwd':request.get('params',{}).get('cwd')}, 'turn/start':{'turn':{'id':'t'}}}
    if mode == 'request_error' and method == 'turn/start':
        send({'id':request['id'],'error':{'message':'PRIVATE_ERROR'}});continue
    response={'id':request['id'],'result':responses[method]}
    if mode == 'response_shape' and method == 'turn/start':
        send({'id':request['id'],'result':{'turn':[]}});continue
    if method != 'turn/start': send(response);continue
    if mode == 'fragments':
        payload=(json.dumps(response)+'\n'+json.dumps({'method':'item/completed','params':{'item':
          {'type':'commandExecution','status':'failed','exitCode':1,'command':'PRIVATE_COMMAND'}}})+'\n'+
          json.dumps({'method':'turn/completed','params':{'turn':{'id':'t','status':'completed'}}})+'\n').encode()
        os.write(1,payload[:7]);time.sleep(.03);os.write(1,payload[7:]);continue
    send(response)
    if mode == 'server':
        send({'id':'PRIVATE_ID','method':'unknown/private','params':{'secret':'PRIVATE_PAYLOAD'}});continue
    if mode == 'json': os.write(1,b'{PRIVATE_INVALID}\n');continue
    if mode == 'shape': send(['PRIVATE_PAYLOAD']);continue
    if mode == 'nested_shape': send({'method':'item/started','params':['PRIVATE']});continue
    if mode == 'stderr_eof': os.close(2);time.sleep(.1);send({'method':'turn/completed','params':{'turn':{'id':'t','status':'completed'}}});continue
    if mode == 'binding': send({'method':'turn/completed','params':{'turn':{'id':'wrong','status':'completed'}}});continue
    if mode == 'limit': os.write(1,b'x'*3000);continue
    if mode == 'partial': os.write(1,b'{"PRIVATE_PARTIAL"');os.close(1);time.sleep(3);continue
    if mode == 'exit': sys.exit(0)
    if mode == 'timeout': time.sleep(3);continue
'''


class ProtocolDiagnosticsTest(unittest.TestCase):
    setUp = fixtures.BoundedWorkerTest.setUp
    tearDown = fixtures.BoundedWorkerTest.tearDown

    def run_fixture(self, mode, *, output_bytes=65536, timeout=5, fail_write=False):
        adapter = Codex.__new__(Codex)
        adapter.account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir='/home/fake')
        adapter.executable = Path('/home/fake/codex-bin')
        adapter.sha256 = '0' * 64
        class Backend(fixtures.FakeContainment):
            def launch(inner, _cmd, **kwargs):
                inner.events.append('launch')
                for key in ('user', 'group', 'extra_groups'):
                    kwargs.pop(key)
                inner.child = subprocess.Popen([sys.executable, '-u', '-c', SCRIPT, mode], **kwargs)
                return inner.child
            def terminate(inner):
                inner.events.append('terminate')
                if inner.child.poll() is None:
                    inner.child.kill()
                inner.child.wait(timeout=5)
                return True
        backend = Backend()
        task = Task(self.task.task_id, self.task.repository, self.task.base_revision,
                    'PRIVATE_PROMPT', self.task.allowed_paths, timeout_seconds=timeout, output_bytes=output_bytes)
        from ops.bounded_worker import task as module
        original = module._durable_write
        def write(path, data):
            if fail_write and path.name == 'lifecycle.json' and b'"diagnostics"' in data:
                raise OSError('PRIVATE_EXCEPTION')
            return original(path, data)
        evidence = self.evidence.parent / mode
        class AdapterOS:
            getuid = staticmethod(lambda: 0)
            geteuid = staticmethod(lambda: 0)
            @staticmethod
            def write(fd, data):
                if mode == 'closed_input':
                    raise BrokenPipeError('PRIVATE_ERROR')
                return os.write(fd, data)
            @staticmethod
            def read(fd, size):
                if mode == 'io':
                    raise OSError('PRIVATE_ERROR')
                return os.read(fd, size)
            def __getattr__(self, name):
                return getattr(os, name)
        with patch('ops.bounded_worker.codex.os', AdapterOS()), \
             patch('ops.bounded_worker.codex.check_config', return_value=True), \
             patch.object(adapter, '_binary', return_value=('pinned',)), \
             patch.object(module, '_durable_write', side_effect=write):
            result = execute(task, self.work, evidence, adapter, backend)
        self.assertEqual(backend.events, ['launch', 'terminate', 'verify_empty'])
        for path in evidence.iterdir():
            self.assertNotIn(b'PRIVATE_', path.read_bytes())
        life = json.loads((evidence / 'lifecycle.json').read_text()) if (evidence / 'lifecycle.json').exists() else {}
        return result, life

    def test_split_coalesced_and_failed_command_completed_turn(self):
        result, life = self.run_fixture('fragments')
        self.assertEqual(result['status'], 'completed')
        self.assertIn(['command', 'failed'], life['tools'])
        self.assertEqual(life['terminal'], 'turn_completed')

    def test_server_request_rejected_and_diagnosed(self):
        result, life = self.run_fixture('server')
        self.assertEqual(result['status'], 'completed')
        self.assertIn('server_request', [reason for _, reason in life['diagnostics']])

    def test_failure_reasons(self):
        for mode, reason in (('json', 'json_frame'), ('shape', 'event_shape'),
                             ('binding', 'turn_binding'), ('response_shape', 'response_shape'), ('limit', 'output_limit'),
                             ('exit', 'launcher_exit'), ('request_error', 'request_error'),
                             ('closed_input', 'closed_input'), ('io', 'io')):
            with self.subTest(mode=mode):
                result, life = self.run_fixture(mode, output_bytes=1024 if mode == 'limit' else 65536)
                self.assertEqual(result['status'], 'failed')
                self.assertIn(reason, [v for _, v in life['diagnostics']])

    def test_eof_partial_and_deadline(self):
        result, life = self.run_fixture('partial', timeout=1)
        self.assertEqual(result['status'], 'timeout')
        self.assertTrue({'stdout_eof', 'partial_frame', 'deadline'} <= {v for _, v in life['diagnostics']})
        result, life = self.run_fixture('timeout', timeout=1)
        self.assertEqual(result['status'], 'timeout')
        self.assertIn('deadline', [v for _, v in life['diagnostics']])

    def test_unhandled_nested_shape_stays_uncertain_and_diagnosed(self):
        result, life = self.run_fixture('nested_shape')
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('event_shape', [v for _, v in life['diagnostics']])

    def test_stderr_eof_is_observed_not_inferred(self):
        result, life = self.run_fixture('stderr_eof')
        self.assertEqual(result['status'], 'completed')
        self.assertIn('stderr_eof', [v for _, v in life['diagnostics']])

    def test_diagnostics_survive_truncation_and_reject_untrusted_values(self):
        from ops.bounded_worker.task import _Boundary
        self.evidence.mkdir(mode=0o700)
        boundary = _Boundary(fixtures.FakeContainment(), self.evidence)
        for _ in range(130):
            boundary.record('turn_started')
        boundary.diagnostic('turn', 'output_limit')
        boundary.record('turn_completed')
        data = json.loads((self.evidence / 'lifecycle.json').read_text())
        self.assertTrue(data['truncated'])
        self.assertEqual(data['terminal'], 'turn_completed')
        self.assertEqual(data['diagnostics'], [['turn', 'output_limit']])
        with self.assertRaises(ValueError):
            boundary.diagnostic('turn', 'PRIVATE_PAYLOAD')

    def test_diagnostic_write_failure_fails_closed_and_cleans(self):
        result, _ = self.run_fixture('json', fail_write=True)
        self.assertEqual(result['status'], 'uncertain')
        self.assertTrue(result['cleanup_verified'])
