import hashlib
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from ops.bounded_worker.bridge_dispatch import Dispatcher
from ops.bounded_worker.bridge_publisher import Publisher
from ops.bounded_worker.bridge_status import post_once
from tests.test_bridge_dispatch import DispatchTests
from tests.test_bridge_patch import PatchTests


class Remote:
    def __init__(self, base):
        self.base = base
        self.calls = []
        self.failure = None
        self.url = 'https://github.com/stevengalvis/modelfc/pull/123'
    def main_head(self):
        return self.base
    def create_branch(self, branch, base):
        self.calls.append('branch')
        if self.failure == 'branch':
            raise TimeoutError()
    def create_commit(self, branch, base, contents, job, patch):
        self.calls.append('commit')
        if self.failure == 'commit':
            raise TimeoutError()
        return 'c' * 40
    def verify_commit(self, branch, base, commit_id, contents):
        return contents == {'docs/example.md': b'new\n'}
    def create_draft(self, *args):
        self.calls.append('draft')
        if self.failure in ('draft', 'draft_after_write'):
            raise TimeoutError()
        return self.url
    def find_draft(self, branch):
        return self.url if self.failure == 'draft_after_write' else None
    def verify_draft(self, branch, base, commit_id, url):
        return commit_id == 'c' * 40 and url == self.url


class PublisherTests(unittest.TestCase):
    def setUp(self):
        # Reuse the offline fixtures without running a real worker or network call.
        self.fixture = DispatchTests('test_launch_at_most_once')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.patch_fixture = PatchTests('test_apply')
        self.patch_fixture.setUp()
        self.addCleanup(self.patch_fixture.doCleanups)
        self.fixture.obj = None
        import json
        node, body, meta = self.fixture.reader.value[0]
        data = json.loads(body)
        data['base'] = self.patch_fixture.task.base_revision
        self.fixture.reader.value = [(node, json.dumps(data, sort_keys=True, separators=(',', ':')).encode(), meta)]
        self.assertEqual(self.fixture.dispatch.once()['status'], 'completed')
        patch = self.patch_fixture.patch
        digest = hashlib.sha256(patch).hexdigest()
        class Inspector:
            def inspect(self, task):
                return {'task_id': task.task_id, 'status': 'completed', 'diff_sha256': digest}
            def patch(self, task, expected):
                return patch
        self.destinations = tempfile.TemporaryDirectory()
        self.addCleanup(self.destinations.cleanup)
        class Checkout:
            def __init__(inner):
                inner.count = 0
            def prepare(inner, task):
                inner.count += 1
                target = Path(self.destinations.name) / str(inner.count)
                subprocess.check_call(['git', 'clone', '-q', '--no-hardlinks', str(self.patch_fixture.root), str(target)])
                return target
        self.remote = Remote(data['base'])
        self.publisher = Publisher(self.fixture.tmp.name, self.fixture.policy, self.fixture.reader,
                                   Inspector(), Checkout(), self.remote, clock=lambda: 100)

    def test_publishes_and_reports_existing_draft(self):
        self.assertEqual(self.publisher.publish('job1')['status'], 'published')
        self.assertEqual(self.publisher.publish('job1')['status'], 'published')
        self.assertEqual(self.remote.calls, ['branch', 'commit', 'draft'])

    def test_completed_job_publishes_then_posts_once(self):
        self.assertEqual(self.publisher.publish()['status'], 'published')
        status_remote = MagicMock()
        self.assertEqual(post_once(self.fixture.tmp.name, 55, status_remote)['outcome'], 'published')
        self.assertEqual(post_once(self.fixture.tmp.name, 55, status_remote)['status'], 'idle')
        self.assertEqual(self.publisher.publish()['status'], 'idle')
        status_remote.post_status.assert_called_once()
        self.assertEqual(self.remote.calls, ['branch', 'commit', 'draft'])

    def test_uncertain_branch_is_never_repeated(self):
        self.remote.failure = 'branch'
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.remote.calls, ['branch'])

    def test_stale_base_blocks_all_writes(self):
        self.remote.base = 'b' * 40
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.remote.calls, [])

    def test_rejected_inspection_blocks_publication(self):
        self.publisher.inspector.inspect = lambda task: {'task_id': task.task_id, 'status': 'rejected'}
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.remote.calls, [])

    def test_uncertain_commit_is_not_repeated(self):
        self.remote.failure = 'commit'
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.remote.calls, ['branch', 'commit'])

    def test_lost_pr_response_requires_operator_even_with_matching_pr(self):
        self.remote.failure = 'draft_after_write'
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        calls_before = self.fixture.reader.calls
        with patch.object(self.remote, 'find_draft', wraps=self.remote.find_draft) as lookup:
            self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
            lookup.assert_not_called()
        self.assertEqual(self.fixture.reader.calls, calls_before)
        self.assertEqual(self.remote.calls, ['branch', 'commit', 'draft'])

    def test_lost_pr_response_without_match_stays_blocked(self):
        self.remote.failure = 'draft'
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.publisher.publish('job1')['status'], 'blocked')
        self.assertEqual(self.remote.calls, ['branch', 'commit', 'draft'])
