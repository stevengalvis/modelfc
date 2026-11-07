import json
import unittest
from ops.bounded_worker.agents_preflight import inspect_host


class PreflightTests(unittest.TestCase):
    def probe(self, **changes):
        args = {'read_text': lambda path: '100' if path.endswith('max_user_namespaces') else
                'Name:\tsecret-host-identity\nNoNewPrivs:\t1\nSeccomp:\t2\n',
                'exists': lambda path: True, 'which': lambda name: '/fake/' + name,
                'uid': lambda: 1000, 'platform': 'linux'}
        args.update(changes)
        return inspect_host(**args)

    def test_all_prerequisites_still_not_live_ready(self):
        result = self.probe()
        self.assertEqual(result['missing_or_unknown'], [])
        self.assertFalse(result['ready_for_live'])
        self.assertNotIn('secret-host-identity', json.dumps(result))
        self.assertTrue(result['still_requires_verification'])

    def test_root_rejected(self):
        self.assertIn('nonroot_identity', self.probe(uid=lambda: 0)['missing_or_unknown'])

    def test_missing_tools_and_paths(self):
        result = self.probe(exists=lambda _: False, which=lambda _: None)
        self.assertIn('pinned_executor_path_exists', result['missing_or_unknown'])
        self.assertIn('bubblewrap_on_path', result['missing_or_unknown'])

    def test_unreadable_metadata_is_unknown(self):
        def fail(_):
            raise PermissionError('do-not-return-this')
        result = self.probe(read_text=fail)
        self.assertIsNone(result['checks']['user_namespaces_configured'])
        self.assertNotIn('do-not-return-this', json.dumps(result))

    def test_invalid_metadata_is_unknown(self):
        for raw in ('not-an-int', '-1', 'x' * 65537):
            with self.subTest(size=len(raw)):
                self.assertIsNone(self.probe(read_text=lambda _: raw)['checks']['user_namespaces_configured'])

    def test_nonlinux_and_zero_user_namespaces_fail(self):
        result = self.probe(platform='other', read_text=lambda _: '0')
        self.assertFalse(result['checks']['linux'])
        self.assertFalse(result['checks']['user_namespaces_configured'])


if __name__ == '__main__':
    unittest.main()
