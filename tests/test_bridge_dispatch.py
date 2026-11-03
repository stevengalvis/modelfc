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
        result = self.dispatch.once()
        self.assertEqual(result['status'], 'duplicate_observed')
        self.assertEqual(result['job_id'], 'job1')
        self.assertEqual(result['comment_id'], 8)
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
        self.assertEqual(self.dispatch.once()['status'], 'collision_rejected')
        self.assertEqual(self.worker.calls, 1)

    def test_duplicate_and_collision_do_not_starve_new_valid_job(self):
        self.assertEqual(self.dispatch.once()['status'], 'completed')
        node, body, meta = self.reader.value[0]
        obj = json.loads(body)
        obj['prompt'] = 'Changed content'
        collision = ('node-collision', json.dumps(obj, sort_keys=True, separators=(',', ':')).encode(),
                     {**meta, 'comment_id': 9})
        obj['job_id'] = 'job2'
        fresh = ('node2', json.dumps(obj, sort_keys=True, separators=(',', ':')).encode(),
                 {**meta, 'comment_id': 10})
        self.reader.value = [(node, body, meta), collision, fresh]
        self.now += 3601
        result = self.dispatch.once()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['job_id'], 'job2')
        self.assertEqual(result['duplicate']['job_id'], 'job1')
        self.assertEqual(result['duplicate']['comment_id'], 8)
        self.assertIs(result['collision_rejected'], True)
        self.assertEqual(self.worker.calls, 2)

    def test_identity_or_content_collision_is_not_exact_duplicate(self):
        self.assertEqual(self.dispatch.once()['status'], 'completed')
        node, body, meta = self.reader.value[0]
        changed = json.loads(body)
        changed['prompt'] = 'Changed content'
        changed_body = json.dumps(changed, sort_keys=True, separators=(',', ':')).encode()
        variants = [(node, changed_body, meta),
                    ('other-node', body, {**meta, 'comment_id': 9}),
                    ('other-node', body, meta)]
        for variant in variants:
            with self.subTest(variant=variant[0]):
                self.now += 61
                self.reader.value = [variant]
                self.assertEqual(self.dispatch.once(), {'status': 'collision_rejected'})
        self.assertEqual(self.worker.calls, 1)

    def test_collision_precedes_duplicate_when_no_new_job_runs(self):
        self.assertEqual(self.dispatch.once()['status'], 'completed')
        node, body, meta = self.reader.value[0]
        collision = ('other-node', body, {**meta, 'comment_id': 9})
        self.reader.value = [(node, body, meta), collision]
        self.now += 61
        result = self.dispatch.once()
        self.assertEqual(result['status'], 'collision_rejected')
        self.assertEqual(result['duplicate']['job_id'], 'job1')
        self.assertEqual(result['duplicate']['comment_id'], 8)
        self.assertEqual(self.worker.calls, 1)

    def test_wrong_author_edit_or_expiry_never_counts_as_duplicate(self):
        self.assertEqual(self.dispatch.once()['status'], 'completed')
        node, body, meta = self.reader.value[0]
        self.now += 3601
        for changed in ({**meta, 'actor_id': 999}, {**meta, 'last_edited_at': 'edited'}):
            self.reader.value = [(node, body, changed)]
            self.assertEqual(self.dispatch.once()['status'], 'idle')
            self.now += 61
        self.reader.value = [(node, body, meta)]
        self.now = 8001
        self.assertEqual(self.dispatch.once()['status'], 'idle')
        self.assertEqual(self.worker.calls, 1)

    def test_wrong_author_is_ignored(self):
        node, body, meta = self.reader.value[0]
        self.reader.value = [(node, body, {**meta, 'actor_id': 999})]
        self.assertEqual(self.dispatch.once()['status'], 'idle')
        self.assertEqual(self.worker.calls, 0)
