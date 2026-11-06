import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_transport as transport
from ops.bounded_worker import linear_status as status
from ops.bounded_worker import linear_poll as poll
from tests import test_linear_transport as fixtures

uid = fixtures.uid


def user(n, **updates):
    return dict(id=uid(n), active=True, admin=True, owner=False, guest=False,
                app=False, canAccessAnyPublicTeam=True, archivedAt=None, **updates)


class WorkspaceAudienceTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.LinearTransportTests()
        fixture.setUp()
        self.policy, self.data, self.report = fixture.policy, fixture.data, fixture.report
        self.users = [user(4), user(5)]
        self.users.append(dict(user(9), app=True, admin=False))
        self.audience = {'visibility': 'public', 'workspace_users': self.users}
        self.destination = transport.Destination(self.policy, uid(5), (uid(4), uid(5)), audience=self.audience)
        self.data['viewer']['admin'] = True
        self.data['issue']['team']['visibility'] = 'public'
        self.data['workspaceUsers'] = {'pageInfo': {'hasNextPage': False}, 'nodes': [
            dict(row, organization={'id': uid(1)}) for row in self.users]}
        self.api = Mock()
        self.api.call.return_value = self.data
        self.reader = transport.LinearReader(self.api, self.destination)
        self.sink = transport.LinearSink(self.api, self.destination)

    def test_free_poll_records_delivery_and_blocks_changed_audience(self):
        self.data['comment']['body'] = json.dumps(dict(version=1, job_id='fixture',
            operation='service-status', expires=1100), sort_keys=True, separators=(',', ':'))
        calls = []
        def call(query, variables):
            calls.append(query)
            if query == transport.PUBLIC_QUERIES[transport.POLL]:
                return dict(self.data, channel={'id': uid(3), 'comments': {
                    'pageInfo': {'hasPreviousPage': False}, 'nodes': [self.data['comment']]}})
            if query == transport.POST:
                return {'commentCreate': {'success': True, 'comment': {
                    'id': uid(8), 'body': self.report, 'user': {'id': uid(5)}, 'issue': {'id': uid(3)}}}}
            return self.data
        self.api.call.side_effect = call
        diagnostic = Mock(approval_scope='a' * 64)
        diagnostic.status.return_value = {'LoadState': 'loaded', 'ActiveState': 'inactive'}
        grant = status.StatusGrant('/fixture/grant')
        record = dict(version=1, enabled=True, operation='service-status', scope_sha256='a' * 64,
                      expires=2000, **self.policy.__dict__)
        with sqlite3.connect(':memory:') as db, patch.object(status, 'protected_json', return_value=record):
            status.initialize(db)
            self.assertEqual(poll.poll_once(db, self.policy, self.reader, diagnostic, self.sink,
                                           grant, lambda: 1000), 'reported')
            self.assertEqual(db.execute('SELECT comment FROM deliveries').fetchone(), (uid(8),))
            self.data['workspaceUsers']['nodes'].append(dict(user(10), organization={'id': uid(1)}))
            self.assertEqual(poll.poll_once(db, self.policy, self.reader, diagnostic, self.sink,
                                           grant, lambda: 1060), 'blocked')
        self.assertEqual(calls.count(transport.POST), 1)
        diagnostic.status.assert_called_once()

    def test_explicit_free_mode_accepts_exact_workspace_and_issue(self):
        self.assertEqual(self.reader.fetch(uid(6)).actor, uid(4))
        query = self.api.call.call_args.args[0]
        self.assertEqual(query, transport.PUBLIC_QUERIES[transport.READ])
        self.assertIn('includeDisabled:true,includeArchived:true', query)
        self.assertIsNone(self.reader.fetch(uid(6)).unedited)

    def test_team_roster_alone_is_not_sufficient(self):
        del self.data['workspaceUsers']
        with self.assertRaises(ValueError):
            self.reader.fetch(uid(6))

    def test_add_remove_and_role_changes_block(self):
        mutations = [lambda rows: rows.append(dict(user(10), organization={'id': uid(1)})),
                     lambda rows: rows.pop()]
        for key in ('active', 'admin', 'owner', 'guest', 'app', 'canAccessAnyPublicTeam'):
            mutations.append(lambda rows, key=key: rows[0].update({key: not rows[0][key]}))
        mutations += [lambda rows: rows[0].update(archivedAt='changed'),
                      lambda rows: rows[0].update(organization={'id': uid(99)})]
        for mutate in mutations:
            changed = copy.deepcopy(self.data)
            mutate(changed['workspaceUsers']['nodes'])
            with self.assertRaises(ValueError):
                self.destination.check(changed)

    def test_new_workspace_user_blocks_even_if_team_unchanged(self):
        self.data['workspaceUsers']['nodes'].append(dict(user(10), organization={'id': uid(1)}))
        with self.assertRaises(ValueError):
            self.sink.post(uid(3), self.report)
        self.api.call.assert_called_once()  # destination read only; no mutation

    def test_partial_or_unknown_roster_fails_closed(self):
        changes = [lambda d: d['workspaceUsers']['pageInfo'].update(hasNextPage=True),
                   lambda d: d['workspaceUsers']['nodes'].append(d['workspaceUsers']['nodes'][0]),
                   lambda d: d['viewer'].update(admin=False),
                   lambda d: d['workspaceUsers']['nodes'][0].pop('app'),
                   lambda d: d['workspaceUsers']['nodes'][0].update(active=1)]
        for change in changes:
            changed = copy.deepcopy(self.data)
            change(changed)
            with self.assertRaises(ValueError):
                self.destination.check(changed)

    def test_visibility_and_sharing_changes_still_block(self):
        for visibility in ('private', 'restricted', None):
            changed = copy.deepcopy(self.data)
            changed['issue']['team']['visibility'] = visibility
            with self.assertRaises(ValueError):
                self.destination.check(changed)
        self.data['issue']['sharedAccess']['isShared'] = True
        with self.assertRaises(ValueError):
            self.destination.check(self.data)

    def test_default_private_policy_does_not_silently_accept_public(self):
        destination = transport.Destination(self.policy, uid(5), (uid(4), uid(5)))
        with self.assertRaises(ValueError):
            destination.check(self.data)
        for audience in ({'visibility': 'public'}, {'visibility': 'public', 'workspace_users': []},
                         dict(self.audience, visibility='anything')):
            with self.assertRaises(ValueError):
                transport.Destination(self.policy, uid(5), (uid(4), uid(5)), audience=audience)

    def test_report_remains_fixed_and_preflight_uses_complete_roster(self):
        result = {'commentCreate': {'success': True, 'comment': {
            'id': uid(8), 'body': self.report, 'user': {'id': uid(5)}, 'issue': {'id': uid(3)}}}}
        self.api.call.side_effect = [self.data, result]
        self.assertTrue(self.sink.post(uid(3), self.report))
        self.assertEqual(self.api.call.call_args_list[0].args[0], transport.PUBLIC_QUERIES[transport.CHECK])
        self.api.reset_mock()
        with self.assertRaises(ValueError):
            self.sink.post(uid(3), 'private log')
        self.api.call.assert_not_called()
