import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest

from ops.bounded_worker.bridge_dispatch import Dispatcher
from ops.bounded_worker.bridge_envelope import Policy


class Reader:
    def __init__(self, comments):
        self.value = comments
        self.calls = 0
        self.changed = False

    def comments(self):
        self.calls += 1
        if self.changed and self.calls > 1:
            node, body, meta = self.value[0]
            return [(node, body, {**meta, 'last_edited_at': 'edited'})]
        return self.value


class Worker:
    def __init__(self, result='completed'):
        self.calls = 0
        self.result = result

    def run(self, task):
        self.calls += 1
        if self.result == 'interrupted':
            raise RuntimeError('uncertain')
        return {'task_id': task.task_id, 'status': self.result}


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy = Policy(123, 55, ('docs/example.md',), publication_authorized=True)
        meta = {'actor_id': 123, 'thread': 55, 'comment_id': 8, 'last_edited_at': None}
        obj = {'version': 1, 'job_id': 'job1', 'base': 'a' * 40,
               'paths': ['docs/example.md'], 'prompt': 'Update docs', 'expires': 8000,
               'timeout': 90, 'output': 262144, 'diff': 4096, 'publish': True}
        body = json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()
        self.reader = Reader([('node1', body, meta)])
        self.worker = Worker()
        self.now = 100
        self.dispatch = Dispatcher(self.tmp.name, self.reader, self.policy, self.worker, clock=lambda: self.now)

    def test_launch_at_most_once(self):
        self.assertEqual(self.dispatch.once()['status'], 'completed')
        self.now += 3601
        self.assertEqual(self.dispatch.once()['status'], 'idle')
        self.assertEqual(self.worker.calls, 1)

    def test_changed_before_launch_is_reserved(self):
        self.reader.changed = True
        self.assertEqual(self.dispatch.once()['status'], 'blocked')
        self.reader.changed = False
        self.now += 3601
        self.assertEqual(self.dispatch.once()['status'], 'blocked')
        self.assertEqual(self.worker.calls, 0)

    def test_interrupted_never_relaunches(self):
        self.worker.result = 'interrupted'
        self.assertEqual(self.dispatch.once()['status'], 'blocked')
        self.now += 3601
        self.assertEqual(self.dispatch.once()['status'], 'blocked')
        self.assertEqual(self.worker.calls, 1)

    def test_nonblocking_lock(self):
        with open(Path(self.tmp.name) / 'bridge.lock', 'w') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.dispatch.once()['status'], 'busy')
            self.assertEqual(self.worker.calls, 0)

    def test_changed_replay_never_launches(self):
        self.assertEqual(self.dispatch.once()['status'], 'completed')
        node, body, meta = self.reader.value[0]
        altered = json.loads(body)
        altered['prompt'] = 'Different task'
        self.reader.value = [('different-node', json.dumps(altered, sort_keys=True, separators=(',', ':')).encode(),
                              {**meta, 'comment_id': 9})]
        self.now += 3601
        self.assertEqual(self.dispatch.once()['status'], 'idle')
        self.assertEqual(self.worker.calls, 1)

    def test_wrong_author_is_ignored(self):
        node, body, meta = self.reader.value[0]
        self.reader.value = [(node, body, {**meta, 'actor_id': 999})]
        self.assertEqual(self.dispatch.once()['status'], 'idle')
        self.assertEqual(self.worker.calls, 0)
