import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_transport as transport
from ops.bounded_worker import linear_status as status
from ops.bounded_worker.linear_status import Policy, summarize


def uid(n):
    return f'00000000-0000-0000-0000-{n:012d}'


class LinearTransportTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(uid(1), uid(2), uid(3), uid(4))
        self.destination = transport.Destination(self.policy, uid(5), (uid(4), uid(5)))
        self.api = Mock()
        self.data = {
            'organization': {'id': uid(1)},
            'viewer': {'id': uid(5), 'active': True, 'organization': {'id': uid(1)}},
            'issue': {'id': uid(3), 'archivedAt': None, 'inheritsSharedAccess': False,
                      'sharedAccess': {'isShared': False, 'sharedWithCount': 0, 'viewerHasOnlySharedAccess': False},
                      'team': {'id': uid(2), 'archivedAt': None, 'visibility': 'private', 'parent': None,
                               'organization': {'id': uid(1)}, 'members': {
                                   'pageInfo': {'hasNextPage': False},
                                   'nodes': [{'id': uid(4), 'active': True}, {'id': uid(5), 'active': True}]}}},
            'comment': {'id': uid(6), 'body': '{"fixture":true}', 'issue': {'id': uid(3)},
                        'archivedAt': None, 'editedAt': None, 'createdAt': 'fixture-time', 'updatedAt': 'fixture-time',
                        'user': {'id': uid(4), 'active': True, 'organization': {'id': uid(1)}},
                        'botActor': None, 'externalUser': None, 'onBehalfOf': None,
                        'externalThread': None, 'parent': None, 'syncedWith': []}}
        self.api.call.return_value = self.data
        self.reader = transport.LinearReader(self.api, self.destination)
        self.sink = transport.LinearSink(self.api, self.destination)
        self.report = summarize({'LoadState': 'loaded', 'ActiveState': 'inactive'})

    def test_full_standing_flow_offline_and_duplicate_has_no_io(self):
        self.data['comment']['body'] = json.dumps(dict(version=1, job_id='fixture',
            operation='service-status', expires=1100), sort_keys=True, separators=(',', ':'))
        def call(query, variables):
            return self.delivery() if query == transport.POST else self.data
        self.api.call.side_effect = call
        request = self.reader.fetch(uid(6))
        diagnostic = Mock(approval_scope='a' * 64)
        diagnostic.status.return_value = {'LoadState': 'loaded', 'ActiveState': 'inactive'}
        grant = status.StatusGrant('/fixture/grant')
        record = dict(version=1, enabled=True, operation='service-status',
                      scope_sha256='a' * 64, expires=2000, **self.policy.__dict__)
        with sqlite3.connect(':memory:') as db, patch.object(status, 'protected_json', return_value=record):
            status.initialize(db)
            self.assertEqual(status.once(db, request, self.policy, self.reader, diagnostic,
                                         self.sink, lambda: 1000, authorization=grant), 'reported')
            self.api.reset_mock()
            self.assertEqual(status.once(db, request, self.policy, self.reader, diagnostic,
                                         self.sink, lambda: 1000, authorization=grant), 'duplicate')
            self.api.call.assert_not_called()
        diagnostic.status.assert_called_once()
        self.assertEqual(self.sink.last_comment_id, uid(8))

    def test_reader_binds_ids_and_does_not_assert_unedited(self):
        request = self.reader.fetch(uid(6))
        self.assertEqual(request.actor, uid(4))
        self.assertEqual(request.body, b'{"fixture":true}')
        self.assertIsNone(request.unedited)
        self.api.call.assert_called_once_with(transport.READ, {'issue': uid(3), 'comment': uid(6)})

    def test_other_authors_and_missing_provenance_rejected(self):
        for field, value in [('user', None), ('user', {'id': uid(7)}), ('botActor', {}),
                             ('externalUser', {}), ('onBehalfOf', {}), ('externalThread', {}),
                             ('parent', {}), ('syncedWith', [{}]), ('issue', {'id': uid(7)}),
                             ('archivedAt', 'fixture'), ('body', 'x' * 1025)]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.data)
                changed['comment'][field] = value
                self.api.call.return_value = changed
                with self.assertRaises(ValueError):
                    self.reader.fetch(uid(6))
        for field in self.data['comment']:
            changed = copy.deepcopy(self.data)
            del changed['comment'][field]
            self.api.call.return_value = changed
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.reader.fetch(uid(6))

    def test_privacy_and_member_changes_block(self):
        changes = [('visibility', 'public'), ('visibility', 'restricted'), ('archivedAt', 'fixture'),
                   ('parent', {'id': uid(7)}), ('id', uid(7)), ('organization', {'id': uid(7)}),
                   ('members', {'pageInfo': {'hasNextPage': True}, 'nodes': []})]
        for field, value in changes:
            changed = copy.deepcopy(self.data)
            changed['issue']['team'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.destination.check(changed)
        for field in ('isShared', 'viewerHasOnlySharedAccess', 'sharedWithCount'):
            changed = copy.deepcopy(self.data)
            changed['issue']['sharedAccess'][field] = True if field != 'sharedWithCount' else 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.destination.check(changed)
        changed = copy.deepcopy(self.data)
        changed['issue']['team']['members']['nodes'][0]['id'] = uid(7)
        with self.assertRaises(ValueError):
            self.destination.check(changed)

    def delivery(self):
        return {'commentCreate': {'success': True, 'comment': {
            'id': uid(8), 'body': self.report, 'user': {'id': uid(5)}, 'issue': {'id': uid(3)}}}}

    def test_sink_checks_destination_and_confirms_result(self):
        self.api.call.side_effect = [self.data, self.delivery()]
        self.assertIs(self.sink.post(uid(3), self.report), True)
        self.assertEqual(self.sink.last_comment_id, uid(8))
        self.assertEqual(self.api.call.call_args_list[0].args[0], transport.CHECK)
        self.assertEqual(self.api.call.call_args_list[1].args, (transport.POST, {'issue': uid(3), 'body': self.report}))

    def test_sink_rejects_arbitrary_output_and_destination(self):
        for issue, report in [(uid(3), 'private raw log'), (uid(9), self.report)]:
            with self.assertRaises(ValueError):
                self.sink.post(issue, report)
        self.api.call.assert_not_called()

    def test_privacy_failure_prevents_post(self):
        self.data['issue']['team']['visibility'] = 'public'
        with self.assertRaises(ValueError):
            self.sink.post(uid(3), self.report)
        self.api.call.assert_called_once()

    def test_uncertain_post_not_retried(self):
        self.api.call.side_effect = [self.data, TimeoutError('fixture')]
        with self.assertRaises(TimeoutError):
            self.sink.post(uid(3), self.report)
        self.assertEqual(self.api.call.call_count, 2)
        self.assertIsNone(self.sink.last_comment_id)

    def test_unconfirmed_delivery_fails(self):
        for field, value in [('body', 'changed'), ('user', {'id': uid(9)}),
                             ('issue', {'id': uid(9)}), ('id', 'invalid')]:
            result = self.delivery()
            result['commentCreate']['comment'][field] = value
            self.api.call.side_effect = [self.data, result]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.sink.post(uid(3), self.report)

    def test_http_bounded_and_authentication_modes(self):
        for oauth in (False, True):
            api = transport.LinearAPI('fixture-token', oauth=oauth)
            response = Mock(status=200)
            response.read.return_value = b'{"data":{"fixture":true}}'
            api._opener = Mock()
            api._opener.open.return_value.__enter__ = Mock(return_value=response)
            api._opener.open.return_value.__exit__ = Mock(return_value=False)
            self.assertEqual(api.call(transport.CHECK, {'issue': uid(3)}), {'fixture': True})
            request = api._opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.linear.app/graphql')
            self.assertEqual(request.get_header('Authorization'), ('Bearer ' if oauth else '') + 'fixture-token')
            response.read.assert_called_once_with(transport.MAX_RESPONSE + 1)

    def test_http_errors_partial_data_and_duplicates_fail_closed(self):
        for raw in (b'{"data":{},"errors":[]}', b'{}', b'{"data":{},"data":{}}',
                    b'x' * (transport.MAX_RESPONSE + 1), b'not-json'):
            api = transport.LinearAPI('fixture-token')
            response = Mock(status=200)
            response.read.return_value = raw
            api._opener = Mock()
            api._opener.open.return_value.__enter__ = Mock(return_value=response)
            api._opener.open.return_value.__exit__ = Mock(return_value=False)
            with self.assertRaisesRegex(ValueError, '^linear_transport_failed$'):
                api.call(transport.CHECK, {})
            api._opener.open.assert_called_once()

    def test_redirect_and_header_injection_rejected(self):
        self.assertIsNone(transport._NoRedirect().redirect_request(None, None, None, None, None, None))
        for token in ('', 'fixture\nsecret', 'fixture\rsecret'):
            with self.assertRaises(ValueError):
                transport.LinearAPI(token)
