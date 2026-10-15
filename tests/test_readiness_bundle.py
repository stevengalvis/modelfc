"""Reproducible installation proposal packaging, no payload execution."""
import hashlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ops/vps/patches/build_readiness_bundle.py'
spec = importlib.util.spec_from_file_location('readiness_bundle', SCRIPT)
bundle = importlib.util.module_from_spec(spec); spec.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def test_deterministic_complete_manifest_and_pinned_candidate(self):
        first = bundle.build()
        self.assertEqual(first, bundle.build())
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            entries = archive.namelist()
            self.assertEqual(len(entries), 7)
            self.assertTrue(all('/' not in name for name in entries))
            manifest = archive.read('SHA256SUMS').decode().splitlines()
            self.assertEqual(len(manifest), 6)
            names = []
            for line in manifest:
                digest, name = line.split('  ')
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
                names.append(name)
            self.assertEqual(set(names), set(entries) - {'SHA256SUMS'})
            self.assertEqual(hashlib.sha256(archive.read('deploy_main.candidate.py')).hexdigest(), bundle.CANDIDATE)
            self.assertEqual(archive.read('modelfc-api-readiness.sudoers'), bundle.RULE)
            self.assertIsNone(archive.testzip())
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16, 0o100644)

    def test_every_source_pin_rejects_drift_before_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in bundle.PINS:
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, p)
            for name in bundle.PINS:
                p = root / name; original = p.read_bytes(); p.write_bytes(original + b'\n')
                with patch.object(bundle.subprocess, 'run') as run, self.assertRaises(ValueError):
                    bundle.build(root)
                run.assert_not_called(); p.write_bytes(original)

    def test_patched_output_is_verified_and_patch_failure_propagates(self):
        with patch.object(bundle, 'CANDIDATE', '0' * 64), self.assertRaises(ValueError):
            bundle.build()
        with patch.object(bundle.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'patch')), self.assertRaises(subprocess.CalledProcessError):
            bundle.build()

    def test_documented_cli_and_standard_checksum_verification_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'proposal.zip'
            args = [sys.executable, '-I', '-B', str(SCRIPT), str(output)]
            first = subprocess.run(args, capture_output=True, timeout=15)
            self.assertEqual(first.returncode, 0, first.stderr)
            expected = hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertEqual(first.stdout.decode().strip(), 'bundle_sha256=' + expected)
            with zipfile.ZipFile(output) as archive: archive.extractall(temporary)
            verified = subprocess.run(['sha256sum', '--strict', '--check', 'SHA256SUMS'], cwd=temporary, capture_output=True, timeout=5)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            second = subprocess.run(args, capture_output=True, timeout=15)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), expected)


if __name__ == '__main__': unittest.main()
