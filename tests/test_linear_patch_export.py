import hashlib
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
from ops.bounded_worker import linear_patch_export as export
from ops.bounded_worker.linear_coding_intake import _task, binding
from tests.test_linear_coding_intake import request

PATCH = b'--- a/docs/example.md\n+++ b/docs/example.md\n@@ -1 +1 @@\n-old\n+new\n'

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.request = request()
        self.task = _task(self.request)[1]
        self.policy = dict(viewer='00000000-0000-0000-0000-000000000004', version=1, enabled=True, request_sha256=binding(self.request), paths=['docs/example.md'])
        self.state = dict(task_sha256=hashlib.sha256(export.launcher._json_bytes(asdict(self.task))).hexdigest())
        self.result = dict(task_id=self.task.task_id, status='completed', diff_sha256=hashlib.sha256(PATCH).hexdigest())
        self.read = self.enterContext(patch.object(export, 'protected_json', side_effect=lambda p: self.state if str(p).endswith('state.json') else self.policy))
        self.raw = self.enterContext(patch.object(export, 'protected_bytes', return_value=PATCH))
        self.inspect = self.enterContext(patch.object(export.launcher, 'inspect', return_value=self.result))

    def run_export(self):
        return export.prepare_export(self.request, SimpleNamespace(evidence_root='/evidence'), '/policy.json')

    def test_exact_link_and_no_test_claim(self):
        import json
        value = json.loads(self.run_export())
        self.assertEqual(value['request_comment_id'], self.request.comment)
        self.assertEqual(value['tests'], 'not recorded')
        self.assertEqual(value['patch'], PATCH.decode())
        self.assertFalse(value['empty'])

    def test_empty(self):
        self.raw.return_value = b''
        self.result['diff_sha256'] = hashlib.sha256(b'').hexdigest()
        self.assertIn(b'"empty":true', self.run_export())

    def test_disabled_no_evidence_read(self):
        self.policy['enabled'] = False
        with self.assertRaises(ValueError): self.run_export()
        self.inspect.assert_not_called()

    def test_wrong_request(self):
        self.policy['request_sha256'] = 'f'*64
        with self.assertRaises(ValueError): self.run_export()

    def test_wrong_task(self):
        self.state['task_sha256'] = 'f'*64
        with self.assertRaises(ValueError): self.run_export()

    def test_wrong_digest(self):
        self.result['diff_sha256'] = 'f'*64
        with self.assertRaises(ValueError): self.run_export()

    def test_not_completed(self):
        self.result['status'] = 'blocked'
        with self.assertRaises(ValueError): self.run_export()

    def test_changed_evidence(self):
        self.inspect.side_effect = [self.result, dict(self.result, status='blocked')]
        with self.assertRaises(ValueError): self.run_export()

    def test_scope(self):
        with self.assertRaises(ValueError): export.patch_paths(PATCH, ['other'])

    def test_bad_patches(self):
        for raw in (b'x'*4097, b'log text', PATCH+b'private log\n', PATCH.replace(b'@@ -1 +1', b'@@ -2,2 +1'), PATCH.replace(b'+new\n', b'+new\x00\n'), PATCH+PATCH, PATCH.replace(b'+++ b/', b'+++ /'), PATCH[:-1], b'\xff'):
            with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, UnicodeError)):
                export.patch_paths(raw, ['docs/example.md'])

    def test_headers_inside_hunk_are_content(self):
        raw=PATCH.replace(b'-old\n+new\n', b'--- a/fake\n+++ b/fake\n')
        self.assertEqual(export.patch_paths(raw, ['docs/example.md']), ['docs/example.md'])
