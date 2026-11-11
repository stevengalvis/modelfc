"""Pure prospective orchestration tests; never call capture on a valid path."""
import unittest

import capture_once


class CapturePlanTests(unittest.TestCase):
    def test_host_command_is_exact_and_no_shell(self):
        argv = capture_once.host_argv(
            '/tmp/fresh/capture_ownership_v3.py', '/tmp/fresh/host-evidence')
        self.assertEqual(argv, (
            '/usr/bin/python3.12', '-I', '-S', '-B',
            '/tmp/fresh/capture_ownership_v3.py',
            'host-held', '/tmp/fresh/host-evidence'))
        self.assertNotIn('sh', argv)
        for bad in ('relative', '/tmp/../old', '/tmp//bad'):
            with self.assertRaises(ValueError):
                capture_once.host_argv(bad, '/tmp/fresh/host-evidence')

    def test_saved_host_prestate_must_be_unchanged(self):
        before = {'context_before': {'user': 'user:[1]'},
                  'files': {'x': {'sha256': 'a'*64}},
                  'blockers': []}
        after = {'context_before': {'user': 'user:[1]'},
                 'files': {'x': {'sha256': 'a'*64, 'binding_after': {}}},
                 'blockers': [], 'context_after': {}}
        self.assertTrue(capture_once.unchanged_prestate(before, after))
        after['files']['x']['sha256'] = 'b'*64
        self.assertFalse(capture_once.unchanged_prestate(before, after))

    def test_capture_refuses_invalid_stage_before_process(self):
        with self.assertRaises(ValueError):
            capture_once.capture('/tmp/../consumed')
        self.assertIsNotNone(capture_once.STAGE_PATTERN.fullmatch(
            '/var/lib/zen53-owner-capture-' + 'a'*32))
        self.assertIsNone(capture_once.STAGE_PATTERN.fullmatch(
            '/var/lib/zen53-owner-capture-' + 'a'*31))

    def test_missing_or_denied_envelope_stops_before_preflight(self):
        class Denied:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def check(self):
                return False
        with self.assertRaisesRegex(ValueError, 'CAPTURE_ENVELOPE_NOT_CONFIGURED'):
            capture_once.capture('/tmp/fresh')
        with self.assertRaisesRegex(ValueError, 'CAPTURE_ADMISSION_BLOCKED'):
            capture_once.capture('/tmp/fresh', admission_guard=Denied,
                                 spawn_host=lambda *a, **k: None,
                                 spawn_restricted=lambda *a, **k: None)


if __name__ == '__main__':
    unittest.main()
