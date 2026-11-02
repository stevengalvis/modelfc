import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ops.bounded_worker.bridge_dispatch import Dispatcher
from ops.bounded_worker.bridge_envelope import Policy
from ops.bounded_worker.bridge_transport import GitHubReader, QUERY


class Response:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.payload[:size]


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.reader = GitHubReader('secret', 55)
        self.node = {'id': 'node', 'fullDatabaseId': '6566494718', 'body': '{}', 'lastEditedAt': None,
                     'includesCreatedEdit': False, 'author': {'__typename': 'User', 'databaseId': 123}}
        self.data = {'data': {'repository': {'nameWithOwner': 'stevengalvis/modelfc',
                     'pullRequest': {'number': 55, 'comments': {
                         'pageInfo': {'hasPreviousPage': False}, 'nodes': [self.node]}}}}}

    def read(self):
        payload = json.dumps(self.data).encode()
        with patch.object(self.reader._opener, 'open', return_value=Response(payload)):
            return self.reader.comments()

    def test_authenticated_fields_are_not_taken_from_body(self):
        self.assertIn('id fullDatabaseId body', QUERY)
        self.assertIn('... on User { databaseId }', QUERY)
        self.assertIn('... on Bot { databaseId }', QUERY)
        result = self.read()[0]
        self.assertEqual(result[2]['actor_id'], 123)
        self.assertEqual(result[2]['comment_id'], 6566494718)
        self.assertIsNone(result[2]['last_edited_at'])

    def test_comment_id_rejects_noncanonical_and_out_of_range_values(self):
        for value in ('0', '-1', '+1', '01', '', '1.0', ' 1', '1\n', '١',
                      '9' * 20, '9223372036854775808', 1, 1.0, None, True):
            with self.subTest(value=value):
                self.node['fullDatabaseId'] = value
                with self.assertRaises(ValueError):
                    self.read()
        del self.node['fullDatabaseId']
        with self.assertRaises(ValueError):
            self.read()
        self.node['fullDatabaseId'] = '9223372036854775807'
        self.assertEqual(self.read()[0][2]['comment_id'], 9223372036854775807)

    def test_author_id_is_a_positive_graphql_int_for_user_and_bot(self):
        for kind in ('User', 'Bot'):
            self.node['author']['__typename'] = kind
            for value in (None, 0, -1, True, 1.0, '123', 2147483648):
                with self.subTest(kind=kind, value=value):
                    self.node['author']['databaseId'] = value
                    with self.assertRaises(ValueError):
                        self.read()
            self.node['author']['databaseId'] = 2147483647
            self.assertEqual(self.read()[0][2]['actor_id'], 2147483647)

    def test_large_comment_id_is_reserved_and_not_launched_twice(self):
        task = {'version': 1, 'job_id': 'job1', 'base': 'a' * 40,
                'paths': ['docs/example.md'], 'prompt': 'Update docs', 'expires': 8000,
                'timeout': 90, 'output': 262144, 'diff': 4096, 'publish': True}
        self.node['body'] = json.dumps(task, sort_keys=True, separators=(',', ':'))
        payload = json.dumps(self.data).encode()

        class Worker:
            calls = 0

            def run(self, task):
                self.calls += 1
                return {'task_id': task.task_id, 'status': 'completed'}

        worker = Worker()
        now = [100]
        with tempfile.TemporaryDirectory() as root:
            dispatcher = Dispatcher(root, self.reader, Policy(123, 55, ('docs/example.md',), True),
                                    worker, clock=lambda: now[0])
            with patch.object(self.reader._opener, 'open', side_effect=lambda *_args, **_kwargs: Response(payload)):
                self.assertEqual(dispatcher.once()['status'], 'completed')
                now[0] += 3601
                self.assertEqual(dispatcher.once()['status'], 'idle')
            self.assertEqual(worker.calls, 1)
            with sqlite3.connect(root + '/receipts.sqlite') as db:
                self.assertEqual(db.execute('SELECT comment FROM jobs').fetchone()[0], 6566494718)

    def test_edit_marker_is_preserved(self):
        self.node['includesCreatedEdit'] = True
        self.assertEqual(self.read()[0][2]['last_edited_at'], 'edited')
        self.node['includesCreatedEdit'] = False
        self.node['lastEditedAt'] = '2026-01-01T00:00:00Z'
        self.assertEqual(self.read()[0][2]['last_edited_at'], '2026-01-01T00:00:00Z')

    def test_partial_pages_and_bad_identity_block(self):
        self.data['data']['repository']['pullRequest']['comments']['pageInfo']['hasPreviousPage'] = True
        with self.assertRaises(ValueError):
            self.read()
        self.data['data']['repository']['pullRequest']['comments']['pageInfo']['hasPreviousPage'] = False
        self.node['author']['databaseId'] = None
        with self.assertRaises(ValueError):
            self.read()
