"""Synthetic filesystem tests; no host evidence directory is opened."""
import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("zen53_offline_evidence",
                                               ROOT / "evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
controller_spec = importlib.util.spec_from_file_location(
    "zen53_offline_controller", ROOT / "controller.py")
controller = importlib.util.module_from_spec(controller_spec)
controller_spec.loader.exec_module(controller)


def sample():
    streams = {}
    for name in evidence.STREAMS:
        raw = (name + "\n").encode()
        streams[name] = {'raw': raw, 'bytes': len(raw),
                         'sha256': hashlib.sha256(raw).hexdigest()}
    return {'outcome': 'BLOCKED', 'reason': 'SYNTHETIC', 'events': [],
            'streams': streams}


class EvidenceTests(unittest.TestCase):
    def write(self, path, record):
        return evidence.write_evidence(str(path), record,
                                       required_uid=os.geteuid(),
                                       required_gid=os.getegid(),
                                       root_uid=os.stat('/').st_uid)

    def verify(self, path, **kwargs):
        kwargs.setdefault('required_uid', os.geteuid())
        kwargs.setdefault('required_gid', os.getegid())
        kwargs.setdefault('root_uid', os.stat('/').st_uid)
        return evidence.verify_evidence(str(path), **kwargs)

    def test_complete_write_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / 'new'
            folder.mkdir(mode=0o700)
            record = sample()
            marker = self.write(folder, record)
            self.assertEqual(
                self.verify(folder,
                                         required_uid=os.geteuid(),
                                         required_gid=os.getegid()),
                marker)
            for bad in (False, 0.0):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    self.verify(folder, required_uid=bad,
                                             required_gid=os.getegid())
            self.assertEqual(marker['meaning'], 'DURABLE_RECORD_ONLY_NOT_ACCEPTANCE')
            self.assertEqual(len(list(folder.iterdir())), 6)
            summary = (folder / 'SUMMARY.json').read_bytes()
            self.assertEqual(hashlib.sha256(summary).hexdigest(),
                             marker['summary_sha256'])
            for name, item in marker['files'].items():
                self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),
                                 item['sha256'])
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600
                                for p in folder.iterdir()))
            with self.assertRaises(ValueError):
                self.write(folder, record)

    def test_stale_symlink_and_wrong_mode_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / 'target'
            target.mkdir(mode=0o700)
            (target / 'stale').write_text('keep')
            with self.assertRaises(ValueError):
                self.write(target, sample())
            self.assertEqual((target / 'stale').read_text(), 'keep')
            (target / 'stale').unlink()
            alias = root / 'alias'
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaises(OSError):
                self.write(alias, sample())
            target.chmod(0o755)
            with self.assertRaises(ValueError):
                self.write(target, sample())

    def test_bad_stream_and_partial_failure_has_no_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for variant in ('bad_hash', 'write_error'):
                with self.subTest(variant=variant):
                    folder = root / variant
                    folder.mkdir(mode=0o700)
                    record = sample()
                    if variant == 'bad_hash':
                        record['streams']['host_stdout']['sha256'] = '0' * 64
                        with self.assertRaises(ValueError):
                            self.write(folder, record)
                    else:
                        real = evidence._write_exclusive
                        count = 0
                        def fail_second(*args):
                            nonlocal count
                            count += 1
                            if count == 2:
                                raise OSError('synthetic failure')
                            return real(*args)
                        with mock.patch.object(evidence, '_write_exclusive',
                                               side_effect=fail_second):
                            with self.assertRaises(OSError):
                                self.write(folder, record)
                    self.assertFalse((folder/'COMPLETE.json').exists())
                    with self.assertRaises(ValueError):
                        self.verify(folder,
                                                 required_uid=os.geteuid(),
                                                 required_gid=os.getegid())

    def test_owner_mismatch_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / 'new'
            folder.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                evidence.write_evidence(str(folder), sample(),
                                        required_uid=os.geteuid()+1,
                                        required_gid=os.getegid(),
                                        root_uid=os.stat('/').st_uid)
            for path in ('relative', '/tmp/../bad', '/tmp//bad'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.write(path, sample())
            unsafe = pathlib.Path(tmp) / 'unsafe'
            unsafe.mkdir(mode=0o777)
            unsafe.chmod(0o777)
            child = unsafe / 'new'
            child.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                self.write(child, sample())

    def test_replaced_directory_cannot_get_completion_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            folder = root / 'new'
            folder.mkdir(mode=0o700)
            original = root / 'moved'
            real = evidence._write_exclusive
            count = 0
            def replace_after_first(*args):
                nonlocal count
                real(*args)
                count += 1
                if count == 1:
                    folder.rename(original)
                    folder.mkdir(mode=0o700)
            with mock.patch.object(evidence, '_write_exclusive',
                                   side_effect=replace_after_first):
                with self.assertRaises(ValueError):
                    self.write(folder, sample())
            self.assertFalse((original/'COMPLETE.json').exists())
            self.assertFalse((folder/'COMPLETE.json').exists())

    def test_type_confusion_oversize_and_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for index, mutation in enumerate((
                lambda r: r['streams']['host_stdout'].update(bytes=True),
                lambda r: r['streams']['host_stdout'].update(bytes=12.0),
                lambda r: r['streams']['host_stdout'].update(
                    raw=b'x' * (evidence.LIMITS['host_stdout'] + 1),
                    bytes=evidence.LIMITS['host_stdout'] + 1),
                lambda r: r['streams']['host_stdout'].update(sha256=1),
            )):
                with self.subTest(index=index):
                    folder = root / str(index)
                    folder.mkdir(mode=0o700)
                    record = sample()
                    mutation(record)
                    with self.assertRaises(ValueError):
                        self.write(folder, record)
                    self.assertFalse((folder/'COMPLETE.json').exists())
            folder = root / 'valid'
            folder.mkdir(mode=0o700)
            self.write(folder, sample())
            (folder/'host_stdout.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                self.verify(folder,
                                         required_uid=os.geteuid(),
                                         required_gid=os.getegid())
            (folder/'COMPLETE.json').write_bytes(b'{')
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                self.verify(folder,
                                         required_uid=os.geteuid(),
                                         required_gid=os.getegid())

    def test_partial_marker_and_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / 'new'
            folder.mkdir(mode=0o700)
            self.write(folder, sample())
            (folder/'COMPLETE.pending').write_bytes(b'partial')
            with self.assertRaises(ValueError):
                self.verify(folder,
                                         required_uid=os.geteuid(),
                                         required_gid=os.getegid())

    def test_fifo_substitution_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / 'new'
            folder.mkdir(mode=0o700)
            self.write(folder, sample())
            (folder/'SUMMARY.json').unlink()
            os.mkfifo(folder/'SUMMARY.json', mode=0o600)
            with self.assertRaises(ValueError):
                self.verify(folder,
                                         required_uid=os.geteuid(),
                                         required_gid=os.getegid())

    def test_synthetic_protocol_to_durable_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / 'new'
            folder.mkdir(mode=0o700)
            def child(role):
                return [sys.executable, '-I', '-S', '-B',
                        str(ROOT / 'fake_child.py'), role, 'ok']
            record = controller.run(child('host'), child('restricted'),
                                    spawn_host=subprocess.Popen,
                                    spawn_restricted=subprocess.Popen,
                                    startup_seconds=1, hold_seconds=2)
            self.assertEqual(record['outcome'], 'PROTOCOL_COMPLETE')
            marker = self.write(folder, record)
            self.assertEqual(
                self.verify(folder,
                                         required_uid=os.geteuid(),
                                         required_gid=os.getegid()),
                marker)
            self.assertEqual(
                marker['files']['host_stdout.bin']['sha256'],
                record['streams']['host_stdout']['sha256'])
            loaded, loaded_marker = evidence.load_evidence(
                str(folder), required_uid=os.geteuid(),
                required_gid=os.getegid(), root_uid=os.stat('/').st_uid)
            self.assertEqual(loaded_marker, marker)
            self.assertEqual(loaded, record)

    def test_marker_persisted_before_pending_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / 'new'
            folder.mkdir(mode=0o700)
            operations = []
            real_link, real_unlink, real_fsync = (
                evidence.os.link, evidence.os.unlink, evidence.os.fsync)
            def link(*args, **kwargs):
                operations.append('link')
                return real_link(*args, **kwargs)
            def unlink(*args, **kwargs):
                if args[0] == 'COMPLETE.pending':
                    operations.append('unlink_pending')
                return real_unlink(*args, **kwargs)
            def fsync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    operations.append('fsync_directory')
                return real_fsync(fd)
            with mock.patch.object(evidence.os, 'link', side_effect=link), (
                    mock.patch.object(evidence.os, 'unlink', side_effect=unlink)), (
                    mock.patch.object(evidence.os, 'fsync', side_effect=fsync)):
                self.write(folder, sample())
            tail = operations[operations.index('link'):]
            self.assertEqual(tail[:4], ['link', 'fsync_directory',
                                        'unlink_pending', 'fsync_directory'])


if __name__ == '__main__':
    unittest.main()
