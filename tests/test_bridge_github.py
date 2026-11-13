import hashlib
import unittest

from ops.bounded_worker.bridge_github import PublisherGitHub


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.api = PublisherGitHub('fixture')
        self.base, self.commit, self.tree, self.subtree = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
        self.contents = {'docs/example.md': b'new\n'}
        self.blob = hashlib.sha1(b'blob 4\0new\n').hexdigest()
        self.paths = {
            'git/ref/heads/bounded/test': {'object': {'sha': self.commit}},
            'git/commits/' + self.commit: {'parents': [{'sha': self.base}], 'tree': {'sha': self.tree}},
            'git/trees/' + self.tree: {'truncated': False, 'tree': [
                {'path': 'docs', 'type': 'tree', 'mode': '040000', 'sha': self.subtree}]},
            'git/trees/' + self.subtree: {'truncated': False, 'tree': [
                {'path': 'example.md', 'type': 'blob', 'mode': '100644', 'sha': self.blob}]},
            'compare/' + self.base + '...' + self.commit + '?per_page=100': {
                'total_commits': 1, 'status': 'ahead', 'files': [
                    {'filename': 'docs/example.md', 'status': 'modified', 'sha': self.blob}]}}
        self.api._rest = lambda method, path, data=None: self.paths[path]

    def test_verified_commit(self):
        self.assertTrue(self.api.verify_commit('bounded/test', self.base, self.commit, self.contents))

    def test_wrong_mode_or_extra_file_fails(self):
        self.paths['git/trees/' + self.subtree]['tree'][0]['mode'] = '100755'
        self.assertFalse(self.api.verify_commit('bounded/test', self.base, self.commit, self.contents))
        self.paths['git/trees/' + self.subtree]['tree'][0]['mode'] = '100644'
        self.paths['compare/' + self.base + '...' + self.commit + '?per_page=100']['files'].append(
            {'filename': '.github/workflows/evil.yml', 'status': 'modified', 'sha': self.blob})
        self.assertFalse(self.api.verify_commit('bounded/test', self.base, self.commit, self.contents))
