import copy
import json
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_transport as transport
from tests import test_linear_transport as fixtures

uid = fixtures.uid


class PaginationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.LinearTransportTests()
        fixture.setUp()
        self.fixture = fixture
        self.api = Mock()
        self.reader = transport.LinearReader(self.api, fixture.destination)

    def page(self, ids, previous=False, cursor='cursor'):
        data = copy.deepcopy(self.fixture.data)
        nodes = []
        for ident in ids:
            node = copy.deepcopy(data['comment'])
            node['id'] = uid(ident)
            nodes.append(node)
        data['channel'] = {'id': uid(3), 'comments': {
            'nodes': nodes, 'pageInfo': {'hasPreviousPage': previous,
                                      'startCursor': cursor if nodes else None}}}
        return data

    def test_101_comments_complete_oldest_page_first(self):
        head = self.page(range(101, 201), True, 'head')
        tail = self.page([100], False, 'tail')
        self.api.call.side_effect = [head, tail, head]
        result = self.reader.candidates()
        self.assertEqual([r.comment for r in result], [uid(i) for i in range(100, 201)])
        self.assertEqual([c.args[1]['before'] for c in self.api.call.call_args_list],
                         [None, 'head', None])

    def test_exact_cap_complete(self):
        pages = [self.page(range(300, 400), True, 'a'),
                 self.page(range(200, 300), True, 'b'),
                 self.page(range(100, 200), False, 'c')]
        self.api.call.side_effect = pages + [pages[0]]
        self.assertEqual(len(self.reader.candidates()), transport.MAX_CANDIDATES)
        self.assertEqual(self.api.call.call_count, 4)

    def test_overflow_fails_without_partial_result(self):
        self.api.call.side_effect = [self.page([100+i], True, str(i)) for i in range(3)]
        with self.assertRaisesRegex(ValueError, 'channel_page_limit'):
            self.reader.candidates()
        self.assertEqual(self.api.call.call_count, 3)

    def test_repeated_cursor_and_overlap_fail(self):
        for tail in (self.page([101], True, 'a'), self.page([100], False, 'b')):
            self.api.reset_mock()
            self.api.call.side_effect = [self.page([100], True, 'a'), tail]
            with self.assertRaises(ValueError):
                self.reader.candidates()
            self.assertEqual(self.api.call.call_count, 2)

    def test_changed_head_fails(self):
        head = self.page([101], True, 'a')
        self.api.call.side_effect = [head, self.page([100]), self.page([102], True, 'new')]
        with self.assertRaisesRegex(ValueError, 'channel_changed'):
            self.reader.candidates()

    def test_later_audience_change_fails(self):
        tail = self.page([100])
        tail['issue']['team']['visibility'] = 'public'
        self.api.call.side_effect = [self.page([101], True), tail]
        with self.assertRaisesRegex(ValueError, 'destination_mismatch'):
            self.reader.candidates()

    def test_empty_continuation_and_invalid_page_info_fail(self):
        for info in ({'hasPreviousPage': False, 'startCursor': None},
                     {'hasPreviousPage': True, 'startCursor': None},
                     {'hasPreviousPage': 1, 'startCursor': 'a'},
                     {'hasPreviousPage': True, 'startCursor': ''},
                     {'hasPreviousPage': True, 'startCursor': 'x'*1025},
                     {'hasPreviousPage': True}):
            tail = self.page([])
            tail['channel']['comments']['pageInfo'] = info
            head = self.page([101], True)
            self.api.call.side_effect = [head, tail, head]
            with self.subTest(info=info), self.assertRaises(ValueError):
                self.reader.candidates()

    def test_empty_mailbox_valid(self):
        self.api.call.return_value = self.page([])
        self.assertEqual(self.reader.candidates(), [])
        self.api.call.assert_called_once()

    def test_page_failure_propagates_without_retry(self):
        self.api.call.side_effect = [self.page([101], True), TimeoutError()]
        with self.assertRaises(TimeoutError):
            self.reader.candidates()
        self.assertEqual(self.api.call.call_count, 2)

    def test_119_comments_preserve_v2_identity_and_utc_creation(self):
        head = self.page(range(119, 219), True, 'head')
        tail = self.page(range(100, 119), False, 'tail')
        for page in (head, tail):
            for node in page['channel']['comments']['nodes']:
                node['body'] = json.dumps({'version': 2, 'operation': 'coding-task'})
                node['createdAt'] = '2026-11-10T16:09:39Z'
        self.api.call.side_effect = [head, tail, head]
        result = self.reader.candidates()
        self.assertEqual(len(result), 119)
        self.assertEqual([r.comment for r in result], [uid(i) for i in range(100, 219)])
        for request in result:
            self.assertEqual((request.workspace, request.team, request.issue, request.actor),
                             (uid(1), uid(2), uid(3), uid(4)))
            self.assertEqual(request.created_at, 1794326979)
            self.assertEqual(request.body, head['channel']['comments']['nodes'][0]['body'].encode())

    def test_v2_non_utc_timestamp_does_not_become_candidate(self):
        page = self.page([100])
        node = page['channel']['comments']['nodes'][0]
        node['body'] = '{"operation":"coding-task","version":2}'
        for stamp in ('2026-11-10T16:09:39', '2026-11-10T16:09:39+01:00', 'invalid'):
            node['createdAt'] = stamp
            self.api.call.return_value = page
            self.assertEqual(self.reader.candidates(), [])

    def test_cursor_cycles_and_invalid_cursor_bytes_block(self):
        for cursor in ('', None, [], 'a\nb', 'é', 'x' * 1025):
            head = self.page([100], True)
            head['channel']['comments']['pageInfo']['startCursor'] = cursor
            self.api.call.side_effect = [head]
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                self.reader.candidates()
        self.api.call.side_effect = [self.page([102], True, 'a'),
                                    self.page([101], True, 'b'), self.page([100], False, 'a')]
        with self.assertRaisesRegex(ValueError, 'repeated_cursor'):
            self.reader.candidates()

    def test_oversized_page_and_bad_comment_ids_block(self):
        for ids in (range(100, 201), [100, 100]):
            self.api.call.return_value = self.page(ids)
            with self.assertRaises(ValueError):
                self.reader.candidates()
        page = self.page([100])
        page['channel']['comments']['nodes'][0]['id'] = 'bad'
        self.api.call.return_value = page
        with self.assertRaisesRegex(ValueError, 'invalid_linear_id'):
            self.reader.candidates()

    def test_timeout_cannot_return_partial_results_or_fetch_again(self):
        self.api.call.return_value = self.page([100], True)
        with patch.object(transport.time, 'monotonic', side_effect=[0, 0, 40]):
            with self.assertRaisesRegex(ValueError, 'channel_time_limit'):
                self.reader.candidates()
        self.api.call.assert_called_once()

    def test_public_audience_rechecked_on_every_page_and_head_verification(self):
        from tests.test_linear_workspace_audience import WorkspaceAudienceTests
        fixture = WorkspaceAudienceTests()
        fixture.setUp()
        self.reader.destination = fixture.destination
        # Use the public fixture's explicit roster and authenticated admin.
        def public(page):
            for key in ('workspaceUsers', 'viewer', 'issue'):
                page[key] = copy.deepcopy(fixture.data[key])
            return page
        head = public(self.page([101], True, 'head'))
        tail = public(self.page([100], False, 'tail'))
        self.api.call.side_effect = [head, tail, head]
        self.assertEqual(len(self.reader.candidates()), 2)
        self.assertTrue(all(c.args[0] == transport.PUBLIC_QUERIES[transport.POLL]
                            for c in self.api.call.call_args_list))
        tail['workspaceUsers']['nodes'][0]['active'] = False
        self.api.call.side_effect = [head, tail]
        with self.assertRaisesRegex(ValueError, 'workspace_audience_changed'):
            self.reader.candidates()

    def test_legacy_poller_accepts_complete_119_candidates_and_runs_only_one(self):
        from tests.test_linear_poll import LinearPollTests
        fixture = LinearPollTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        head = self.page(range(119, 219), True, 'head')
        tail = self.page(range(100, 119), False, 'tail')
        head['channel']['comments']['nodes'][-1] = copy.deepcopy(fixture.data['comment'])
        pages = iter((head, tail, head))
        fixture.api.call.side_effect = lambda query, variables: (
            next(pages) if query == transport.POLL else fixture.call(query, variables))
        self.assertEqual(fixture.run_poll(), 'reported')
        fixture.diagnostic.status.assert_called_once()
        self.assertEqual(fixture.post_count, 1)
