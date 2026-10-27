"""Make the inert ZEN53 source's offline regression suites visible to CI."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'docs/zen53/rotation-decoupling'


class Zen53OfflineTests(unittest.TestCase):
    def test_stage_and_operator_suites(self):
        for directory in (BASE/'stage',
                          BASE/'operator-review/zen53-api-rotation-operator-v4-offline',
                          BASE/'operator-review/zen53-api-rotation-launch-tests-offline'):
            with self.subTest(directory=directory.name):
                result = subprocess.run([sys.executable, '-B', '-m', 'unittest',
                                         'discover', '-s', str(directory), '-q'],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                if os.environ.get('ZEN53_PYTHON_FIXTURE'):
                    self.assertNotIn('skipped=', result.stderr)

    def test_successor_archives_and_entire_pin_chain_are_current(self):
        gzip = Path('/usr/bin/gzip')
        result = subprocess.run(
            [sys.executable, '-B', str(BASE/'packaging/build_python_closure_candidate.py'),
             '--gzip-sha256', hashlib.sha256(gzip.read_bytes()).hexdigest(),
             '--gzip-owner-uid', str(gzip.stat().st_uid),
             '--gzip-owner-gid', str(gzip.stat().st_gid)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        manifest = json.loads((BASE/'SOURCE_TREE_MANIFEST.json').read_bytes())
        self.assertIn(manifest['stage_archive']['sha256'], result.stdout)
        self.assertIn(manifest['operator_distribution']['sha256'], result.stdout)


if __name__ == '__main__':
    unittest.main()
