import unittest

from ops.bounded_worker.bridge_status import post_once
from tests.test_bridge_dispatch import DispatchTests


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = DispatchTests('test_launch_at_most_once')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.dispatch.once()
        class Remote:
            def __init__(self):
                self.calls = []
                self.fail = False
            def post_status(self, thread, body):
                self.calls.append((thread, body))
                if self.fail:
                    raise TimeoutError()
        self.remote = Remote()

    def test_status_is_fixed_and_deduplicated(self):
        result = post_once(self.fixture.tmp.name, 55, self.remote)
        self.assertEqual(result['outcome'], 'awaiting_publication')
        self.assertEqual(post_once(self.fixture.tmp.name, 55, self.remote)['status'], 'idle')
        body = self.remote.calls[0][1]
        self.assertNotIn('Update docs', body)
        self.assertNotIn(self.fixture.tmp.name, body)
        self.assertEqual(len(self.remote.calls), 1)

    def test_uncertain_post_not_retried(self):
        self.remote.fail = True
        self.assertEqual(post_once(self.fixture.tmp.name, 55, self.remote)['status'], 'blocked')
        self.assertEqual(post_once(self.fixture.tmp.name, 55, self.remote)['status'], 'blocked')
        self.assertEqual(len(self.remote.calls), 1)
