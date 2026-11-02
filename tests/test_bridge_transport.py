import json
import unittest
from unittest.mock import patch

from ops.bounded_worker.bridge_transport import GitHubReader


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
        self.node = {'id': 'node', 'databaseId': 8, 'body': '{}', 'lastEditedAt': None,
                     'includesCreatedEdit': False, 'author': {'__typename': 'User', 'databaseId': 123}}
        self.data = {'data': {'repository': {'nameWithOwner': 'stevengalvis/modelfc',
                     'pullRequest': {'number': 55, 'comments': {
                         'pageInfo': {'hasPreviousPage': False}, 'nodes': [self.node]}}}}}

    def read(self):
        payload = json.dumps(self.data).encode()
        with patch.object(self.reader._opener, 'open', return_value=Response(payload)):
            return self.reader.comments()

    def test_authenticated_fields_are_not_taken_from_body(self):
        result = self.read()[0]
        self.assertEqual(result[2]['actor_id'], 123)
        self.assertIsNone(result[2]['last_edited_at'])

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
