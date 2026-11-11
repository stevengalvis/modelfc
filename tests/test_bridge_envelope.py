import json
import unittest

from ops.bounded_worker.bridge_envelope import Policy, validate


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(123, 55, ('docs/example.md',), publication_authorized=True)
        self.meta = {'actor_id': 123, 'thread': 55, 'comment_id': 8, 'last_edited_at': None}
        self.obj = {'version': 1, 'job_id': 'job1', 'base': 'a' * 40,
                    'paths': ['docs/example.md'], 'prompt': 'Update docs', 'expires': 200,
                    'timeout': 90, 'output': 262144, 'diff': 4096, 'publish': True}

    def body(self):
        return json.dumps(self.obj, sort_keys=True, separators=(',', ':')).encode()

    def test_valid(self):
        task, digest = validate(self.body(), self.meta, self.policy, 100)
        self.assertEqual(task.task_id, 'job1')
        self.assertEqual(len(digest), 64)

    def test_invalid_metadata(self):
        for key, value in [('actor_id', 124), ('thread', 56), ('comment_id', True),
                           ('last_edited_at', '2026-01-01T00:00:00Z')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(self.body(), {**self.meta, key: value}, self.policy, 100)
        with self.assertRaises(ValueError):
            validate(self.body(), {}, self.policy, 100)

    def test_invalid_envelopes(self):
        for key, value in [('paths', ['docs/../secret']), ('expires', 100),
                           ('timeout', 91), ('diff', 4097), ('publish', False),
                           ('job_id', '../x')]:
            old = self.obj[key]
            self.obj[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(self.body(), self.meta, self.policy, 100)
            self.obj[key] = old
        for body in (b'{}', b'{"a":1,"a":2}', self.body() + b' ', b'x' * 16385):
            with self.assertRaises(ValueError):
                validate(body, self.meta, self.policy, 100)


if __name__ == '__main__':
    unittest.main()
