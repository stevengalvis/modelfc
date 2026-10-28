"""Local caller-owned journal and synthetic trusted receipts; no external calls."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'docs/zen53/repair-loop'


def load(name):
    spec = importlib.util.spec_from_file_location('test_repair_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c, coordinator, usage = load('contract'), load('coordinator'), load('usage')


class RepairLoop(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        self.addCleanup(os.close, self.fd)
        self.loop_id = 'a' * 64
        self.loop = coordinator.open_loop_fixture(self.fd, self.loop_id)
        self.addCleanup(self.loop.close)

    def reserve(self, second=False):
        return self.loop.reserve(('d' if second else 'b') * 64, ('e' if second else 'c') * 64)

    def candidate(self, reserved, op):
        expected = {key: reserved['reservation'][key] for key in ('loop_id', 'attempt', 'request_id', 'feedback_sha256')}
        value = dict(version=1, **expected, base_sha256=c.BASE_SHA256,
                     path='calc.py', replacement=c.CANDIDATES[op].decode())
        return c.canonical(value), expected

    def receipt(self, reserved, op):
        raw, expected = self.candidate(reserved, op)
        proposal = c.validate_proposal(raw, expected)
        reservation = reserved['reservation']
        actual = {'+': (5, 0), '-': (-1, -4), '*': (6, -4)}[op]
        record = dict(version=1, **expected, response_sha256=proposal['response_sha256'],
                      candidate_sha256=proposal['replacement_sha256'],
                      capture_sha256=('1' if reservation['attempt'] == 1 else '2')*64,
                      stage_id=reservation['stage_id'],
                      stage_manifest_sha256=('3' if reservation['attempt'] == 1 else '4')*64,
                      native='PASS', transition='PASS', cleanup='PASS', proof='PASS', readback='PASS', task='PASS' if op == '+' else 'FAIL',
                      cases=[dict(case_id=name, expected=want, actual=got) for name, want, got in
                             zip(('add_positive', 'add_cancel'), (5, 0), actual)],
                      usage=usage.normalize(dict(input_tokens=11, cached_input_tokens=2, output_tokens=3)))
        return raw, record

    def finish(self, reserved, op):
        raw, receipt = self.receipt(reserved, op)
        return self.loop.record_result(raw, receipt['capture_sha256'], receipt)

    def test_family_validator_all_three_exact_bindings_and_hashes(self):
        reserved = self.reserve()
        for op in c.CANDIDATES:
            raw, expected = self.candidate(reserved, op)
            value = c.validate_proposal(raw, expected)
            self.assertEqual(value['response_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(value['replacement_sha256'], hashlib.sha256(c.CANDIDATES[op]).hexdigest())
            self.assertEqual(len(c.CANDIDATES[op]), len(c.BEFORE))

    def test_validator_rejects_schema_paths_code_and_stale_bindings(self):
        raw, expected = self.candidate(self.reserve(), '-')
        value = json.loads(raw)
        for key, bad in (('version', True), ('attempt', True), ('attempt', 3), ('request_id', 'f'*64),
                         ('feedback_sha256', '0'*64), ('loop_id', '0'*64), ('base_sha256', '0'*64),
                         ('path', '../calc.py'), ('path', '/calc.py'), ('path', 'cаlc.py'),
                         ('replacement', 'import os\nos.system("SECRET")'),
                         ('replacement', c.BEFORE.decode().replace('-', '/')),
                         ('replacement', c.BEFORE.decode() + '\n'), ('extra', 'SECRET')):
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                c.validate_proposal(c.canonical(value | {key: bad}), expected)
        for bad in (raw[:-1]+b',"version":1}', raw+b'{}', b'['*1500+b']'*1500,
                    raw+b' '*4096, b'\xff', b'null', b'{"version":NaN}'):
            with self.subTest(raw=bad[:30]), self.assertRaises(ValueError):
                c.validate_proposal(bad, expected)

    def test_first_pass_stops_without_repair_claim(self):
        record = self.finish(self.reserve(), '+')
        self.assertEqual(record['receipt']['task'], 'PASS')
        self.assertEqual(record['usage_aggregate']['known_total_tokens'], 14)
        self.assertNotIn('repair', record)
        with self.assertRaises(ValueError):
            self.reserve(True)

    def test_fail_then_pass_reopens_durable_feedback_and_aggregate(self):
        first = self.finish(self.reserve(), '-')
        self.loop.close()
        self.loop = coordinator.open_loop_fixture(self.fd, self.loop_id)
        self.addCleanup(self.loop.close)
        second = self.reserve(True)
        self.assertEqual(second['feedback']['cases'], first['receipt']['cases'])
        self.assertEqual(second['feedback']['result_sha256'], c.digest(first))
        self.assertEqual(second['reservation']['feedback_sha256'], c.digest(second['feedback']))
        last = self.finish(second, '+')
        self.assertEqual(last['usage_aggregate']['known_total_tokens'], 28)
        self.assertEqual(last['usage_aggregate']['attempts'], 2)
        with self.assertRaises(ValueError):
            self.loop.reserve('f'*64, '9'*64)

    def test_two_failures_stop_at_two(self):
        self.finish(self.reserve(), '*')
        self.finish(self.reserve(True), '-')
        with self.assertRaises(ValueError):
            self.loop.reserve('f'*64, '9'*64)

    def test_pending_model_or_worker_uncertainty_never_retries(self):
        self.reserve()
        other = coordinator.open_loop_fixture(self.fd, self.loop_id)
        self.addCleanup(other.close)
        with self.assertRaises(ValueError):
            other.reserve('d'*64, 'e'*64)
        self.assertEqual(len(list(self.path.glob('*.reserve.json'))), 1)

    def test_receipt_failure_unknown_task_tampering_and_bad_usage_consume_attempt(self):
        reserved = self.reserve()
        raw, receipt = self.receipt(reserved, '-')
        mutations = [('native', 'FAIL'), ('transition', 'UNKNOWN'), ('cleanup', 'UNVERIFIED'),
                     ('proof', 'UNKNOWN'), ('readback', 'FAIL'), ('task', 'PASS'), ('capture_sha256', '9'*64), ('candidate_sha256', '9'*64),
                     ('stage_id', '9'*64), ('stage_manifest_sha256', 'nothex'),
                     ('request_id', '9'*64), ('usage', {'state': 'REPORTED'})]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.loop.record_result(raw, receipt['capture_sha256'], receipt | {key: value})
        bad = copy.deepcopy(receipt)
        bad['cases'][0]['actual'] = True
        with self.assertRaises(ValueError):
            self.loop.record_result(raw, receipt['capture_sha256'], bad)
        self.assertFalse((self.path / 'attempt1.result.json').exists())
        with self.assertRaises(ValueError):
            self.reserve(True)

    def test_missing_usage_persists_unknown_not_zero(self):
        reserved = self.reserve()
        raw, receipt = self.receipt(reserved, '+')
        receipt['usage'] = usage.normalize(None)
        record = self.loop.record_result(raw, receipt['capture_sha256'], receipt)
        self.assertEqual(record['usage_aggregate']['state'], 'UNKNOWN')
        self.assertIsNone(record['usage_aggregate']['total_tokens'])

    def test_stale_request_stage_capture_and_manifest_rejected(self):
        self.finish(self.reserve(), '-')
        for request, stage in (('b'*64, 'e'*64), ('d'*64, 'c'*64)):
            with self.assertRaises(ValueError):
                self.loop.reserve(request, stage)
        second = self.reserve(True)
        raw, receipt = self.receipt(second, '+')
        for key, old in (('capture_sha256', '1'*64), ('stage_manifest_sha256', '3'*64)):
            changed = receipt | {key: old}
            with self.assertRaises(ValueError):
                self.loop.record_result(raw, changed['capture_sha256'], changed)

    def test_duplicate_result_and_concurrent_reservation_no_overwrite(self):
        other = coordinator.open_loop_fixture(self.fd, self.loop_id)
        self.addCleanup(other.close)
        reserved = self.reserve()
        with self.assertRaises(ValueError):
            other.reserve('d'*64, 'e'*64)
        self.finish(reserved, '+')
        before = (self.path / 'attempt1.result.json').read_bytes()
        with self.assertRaises(ValueError):
            self.finish(reserved, '+')
        self.assertEqual((self.path / 'attempt1.result.json').read_bytes(), before)

    def test_partial_write_and_fsync_failure_leave_uncertain_no_retry(self):
        original = os.write
        def partial(fd, data):
            original(fd, data[:5])
            raise OSError('synthetic interruption')
        with patch.object(coordinator.os, 'write', side_effect=partial), self.assertRaises(OSError):
            self.reserve()
        self.assertTrue((self.path / 'attempt1.reserve.json.pending').exists())
        with self.assertRaises(ValueError):
            self.reserve()

    def test_publication_fsync_failure_does_not_repeat_reserved_attempt(self):
        original = os.fsync
        def sync(fd):
            if fd == self.loop.fd and (self.path / 'attempt1.reserve.json').exists():
                raise OSError('uncertain durability')
            return original(fd)
        with patch.object(coordinator.os, 'fsync', side_effect=sync), self.assertRaises(OSError):
            self.reserve()
        with self.assertRaises(ValueError):
            self.reserve()

    def test_wrong_loop_unknown_entries_symlinks_and_hardlinks_rejected(self):
        with self.assertRaises(ValueError):
            coordinator.open_loop_fixture(self.fd, 'f'*64)
        target = self.path / 'loop.json'
        os.link(target, self.path / 'alias')
        with self.assertRaises(ValueError):
            self.reserve()
        (self.path / 'alias').unlink()
        target.rename(self.path / 'original')
        target.symlink_to('original')
        with self.assertRaises(ValueError):
            self.reserve()

    def test_root_guard_and_directory_mode(self):
        self.assertNotEqual(os.geteuid(), 0)
        with self.assertRaises(ValueError):
            coordinator.open_loop(self.fd, self.loop_id)
        self.path.chmod(0o750)
        with self.assertRaises(ValueError):
            self.reserve()
        with self.assertRaises(ValueError):
            coordinator.open_loop_fixture(self.fd, self.loop_id)

    def test_loop_header_bool_and_float_versions_rejected(self):
        for version in (True, 1.0):
            (self.path / 'loop.json').write_bytes(c.canonical({'version': version, 'loop_id': self.loop_id}))
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.reserve()

    def test_reopened_tampered_records_fail_closed(self):
        self.finish(self.reserve(), '-')
        path = self.path / 'attempt1.result.json'
        value = json.loads(path.read_bytes())
        value['usage_aggregate']['known_total_tokens'] += 1
        path.write_bytes(c.canonical(value))
        with self.assertRaises(ValueError):
            self.reserve(True)


if __name__ == '__main__':
    unittest.main()
