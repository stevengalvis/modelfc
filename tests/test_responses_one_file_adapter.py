"""Offline immutable fixtures/fake transport and runtime; no candidate execution."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
from ops.bounded_worker.responses_usage_pilot import one_file_adapter as a
from ops.bounded_worker.responses_usage_pilot import one_file_runner as runner
from ops.bounded_worker.responses_usage_pilot.one_file_fixtures.trusted_harness import compare, validate_function
from ops.bounded_worker.responses_usage_pilot.one_file_runner import run_once
from ops.bounded_worker.responses_usage_pilot.one_file_transport import OneFileTransport, parse_response, transport_observation

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    source = (ROOT / a.SOURCE_PATH).read_bytes()
    tests = (ROOT / (a.FIXTURES + 'trusted_tests.py')).read_bytes()
    harness = (ROOT / (a.FIXTURES + 'trusted_harness.py')).read_bytes()
    manifest = c.Manifest('required-text-fixture', 'stevengalvis/modelfc', a.BASE,
        a.SOURCE_PATH, 'required_text', a.SOURCE_HASH, a.FIXTURES + 'trusted_tests.py',
        a.TEST_HASH, a.FIXTURES + 'trusted_harness.py', a.HARNESS_HASH, a.TEST_NAMES,
        a.INSTRUCTIONS, 'b' * 64, 'c' * 64, 'runtime-fixture',
        'credential-ref:responses-pilot-saved-key', max_calls=1)
    candidate = source.replace(
        b'    if value is None or not value.strip():\n',
        b'    stripped = "" if value is None else value.strip()\n    if not stripped:\n')
    # The restricted grammar deliberately permits this one conditional expression.
    candidate = candidate.replace(b'    return value.strip()\n', b'    return stripped\n')
    return manifest, (source, tests, harness), candidate


def response(candidate):
    return json.dumps({'status': 'completed', 'model': 'gpt-6-luna', 'service_tier': 'default',
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': candidate.decode()}]}],
        'usage': {'input_tokens': 200, 'output_tokens': 300, 'total_tokens': 500}}).encode()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.m, self.blobs, self.candidate = fixture()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'store'
        self.store.mkdir(mode=0o700)
        patcher = patch.object(c, 'STORE', self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gate = c.CanonicalAdmission()
        self.addCleanup(lambda: self.gate.close())
        self.events = []

    def reserve(self):
        self.gate.register(self.m, *self.blobs)
        return self.gate.reserve(self.m, *self.blobs)

    def runtime(self, manifest, source, tests, harness, candidate, reservation, target,
                identity_recorder):
        self.events.append('runtime')
        self.assertTrue(identity_recorder('d' * 64))
        return {'status': 'passed', 'cleanup_verified': True, 'failed': [],
                'container_id': 'd' * 64, 'sandbox_name': reservation['sandbox_name'],
                'image_id': manifest.image_id, 'diagnostic': {'fake': 'execution'},
                'cleanup_evidence': {'fake': 'independent cleanup'}}

    def factory(self, *args):
        self.events.append('credentials')
        row = self.gate.db.execute('SELECT number FROM calls').fetchone()
        self.assertEqual(row, (1,))
        outer = self
        class FakeTransport:
            def __call__(self):
                outer.events.append('provider')
                return response(outer.candidate)
            def close(self):
                outer.events.append('closed')
        return FakeTransport()

    def run_task(self, **overrides):
        def ready(manifest, target):
            self.events.append('ready')
            return True
        args = dict(gate=self.gate, target=object(), transport_factory=self.factory,
                    readiness=ready, runtime=self.runtime)
        args.update(overrides)
        return run_once(self.m, *self.blobs, **args)

    def test_final_lf_derivation_is_narrow_and_idempotent(self):
        derived, lineage = runner.derive_candidate(self.m, self.candidate[:-1])
        self.assertEqual(derived, self.candidate)
        self.assertTrue(lineage['applied'])
        self.assertEqual(lineage['policy'], c.CANDIDATE_DERIVATION_POLICY)
        again, unchanged = runner.derive_candidate(self.m, derived)
        self.assertIs(again, derived)
        self.assertFalse(unchanged['applied'])
        self.assertEqual(unchanged['raw_hash'], unchanged['derived_hash'])
        self.assertEqual(a.review_patch(self.m, *self.blobs, derived),
                         a.review_patch(self.m, *self.blobs, self.candidate))
        with self.assertRaisesRegex(ValueError, '^candidate_bound$'):
            a.validate_candidate(self.m, *self.blobs, self.candidate[:-1])

    def test_derivation_bounds_and_invalid_input(self):
        for raw in (b'', None, '', bytearray(b'x'), b'x\r', b'x\r\n'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, '^candidate_bound$'):
                runner.derive_candidate(self.m, raw)
        bound = replace(self.m, max_source_bytes=len(self.candidate))
        self.assertEqual(runner.derive_candidate(bound, self.candidate[:-1])[0], self.candidate)
        self.assertEqual(runner.derive_candidate(bound, self.candidate)[0], self.candidate)
        with self.assertRaisesRegex(ValueError, '^candidate_bound$'):
            runner.derive_candidate(bound, b'x' * len(self.candidate))
        with self.assertRaisesRegex(ValueError, '^candidate_bound$'):
            runner.derive_candidate(bound, self.candidate + b'\n')

    def test_normalization_does_not_relax_strict_validation(self):
        cases = (
            (self.blobs[0][:-1], 'single_strip_required'),
            (self.candidate.replace(b'import re', b'import os')[:-1], 'function_scope'),
            (self.candidate.replace(b'def required_text(row:', b'def required_text( row:')[:-1], 'signature_bytes'),
            (self.candidate.replace(b'return stripped', b'return row.clear()')[:-1], 'candidate_attribute'),
            (self.candidate + b'\n', 'function_scope'),
            (b'```python\n' + self.candidate + b'```', 'invalid_python'),
            (self.candidate + b'\xff', 'invalid_python'),
        )
        for raw, reason in cases:
            with self.subTest(reason=reason):
                derived, _ = runner.derive_candidate(self.m, raw)
                with self.assertRaisesRegex(ValueError, '^' + reason + '$'):
                    a.validate_candidate(self.m, *self.blobs, derived)

    def test_missing_lf_retains_raw_and_tests_diffs_receipts_derived(self):
        expected = self.candidate
        self.candidate = expected[:-1]
        retained = []
        def record(raw):
            retained.append(raw)
            return True
        def runtime(*args, **kwargs):
            self.assertEqual(args[4], expected)
            self.assertEqual(retained, [self.candidate])
            return self.runtime(*args, **kwargs)
        with patch.object(runner, 'validate_candidate', wraps=a.validate_candidate) as validate, \
                patch.object(runner, 'review_patch', wraps=a.review_patch) as diff:
            result = self.run_task(candidate_recorder=record, runtime=runtime)
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(validate.call_args.args[-1], expected)
        self.assertEqual(diff.call_args.args[-1], expected)
        self.assertEqual(result['patch'], a.review_patch(self.m, *self.blobs, expected))
        self.assertEqual(result['candidate_hash'], c.digest(self.candidate))
        self.assertEqual(result['candidate_bytes'], len(self.candidate))
        lineage = result['candidate_derivation']
        self.assertEqual(lineage['raw_hash'], c.digest(self.candidate))
        self.assertEqual(lineage['derived_hash'], c.digest(expected))
        self.assertEqual(lineage['raw_bytes'], len(self.candidate))
        self.assertEqual(lineage['derived_bytes'], len(expected))
        receipt = json.loads(self.gate.db.execute('SELECT receipt FROM calls').fetchone()[0])
        self.assertEqual(receipt['candidate_hash'], c.digest(expected))
        self.assertEqual(receipt['candidate_bytes'], len(expected))
        self.assertEqual(receipt['candidate_derivation'], lineage)
        receipt['test_results'] = tuple(tuple(row) for row in receipt['test_results'])
        for change in ({'policy': 'other'}, {'applied': 1}, {'raw_hash': 'x'},
                       {'derived_hash': 'f' * 64}, {'raw_bytes': True},
                       {'raw_bytes': len(expected)}, {'derived_bytes': len(expected) + 1},
                       {'raw_hash': c.digest(expected)}, {'extra': 'field'}):
            altered = dict(lineage, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(c.Receipt(**receipt), candidate_derivation=altered).validate(
                    self.m, receipt['sandbox_name'])

    def test_append_over_bound_retains_raw_without_runtime_or_receipt(self):
        self.candidate = b'x' * self.m.max_source_bytes
        retained = []
        def record(raw):
            retained.append(raw)
            return True
        result = self.run_task(candidate_recorder=record)
        self.assertEqual(retained, [self.candidate])
        self.assertTrue(result['candidate_retained'])
        self.assertEqual(result['candidate_bytes'], self.m.max_source_bytes)
        self.assertEqual(result['failure_reason'], 'candidate_bound')
        self.assertNotIn('candidate_derivation', result)
        self.assertNotIn('runtime', self.events)
        self.assertIsNone(result['patch'])
        self.assertEqual(self.gate.db.execute('SELECT receipt FROM calls').fetchone(), (None,))

    def test_normalized_receipt_failure_never_emits_patch_or_retries(self):
        self.candidate = self.candidate[:-1]
        with patch.object(self.gate, 'record', side_effect=ValueError('receipt-sentinel')), \
                patch.object(runner, 'review_patch') as diff:
            result = self.run_task()
        diff.assert_not_called()
        self.assertEqual(result['phase'], 'receipt')
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['patch'])
        self.assertNotIn('receipt-sentinel', repr(result))
        self.assertEqual(self.gate.db.execute('SELECT receipt FROM calls').fetchone(), (None,))
        self.assertEqual(self.run_task()['status'], 'blocked')
        self.assertEqual(self.events.count('provider'), 1)

    def test_normalized_static_failure_never_executes_or_records_success(self):
        self.candidate = self.blobs[0][:-1]
        result = self.run_task(candidate_recorder=lambda raw: True)
        self.assertEqual(result['failure_reason'], 'single_strip_required')
        self.assertTrue(result['candidate_derivation']['applied'])
        self.assertNotIn('runtime', self.events)
        self.assertIsNone(result['patch'])
        self.assertEqual(self.gate.db.execute('SELECT receipt FROM calls').fetchone(), (None,))

    def test_exact_bundle_and_request(self):
        prepared = a.prepare(self.m, *self.blobs)
        self.assertEqual(prepared.wire_hash, c.digest(prepared.wire))
        payload = json.loads(prepared.wire)
        self.assertEqual(payload['tools'], [])
        self.assertEqual(payload['tool_choice'], 'none')
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['truncation'], 'disabled')
        self.assertNotIn('credential', prepared.wire.decode())
        reservation = self.reserve()
        self.assertEqual(a.bind_first_reservation(self.m, prepared, reservation, *self.blobs), prepared)
        for changed in (dict(reservation, number=True), dict(reservation, sandbox_name='other'),
                        dict(reservation, request_hash='f' * 64)):
            with self.assertRaises(ValueError):
                a.bind_first_reservation(self.m, prepared, changed, *self.blobs)

    def test_scope_and_bundle_replacements_rejected(self):
        for change in ({'base_sha': 'e' * 40}, {'instructions': 'other'}, {'max_calls': 2},
                       {'function': 'parse_whole_number'}, {'test_names': ('test_other',)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                a.prepare(replace(self.m, **change), *self.blobs)
        for index in range(3):
            blobs = list(self.blobs)
            blobs[index] += b'\n'
            with self.assertRaises(ValueError):
                a.prepare(self.m, *blobs)
        with self.assertRaises(ValueError):
            a.prepare(replace(self.m, max_request_bytes=20), *self.blobs)
        with self.assertRaises(ValueError):
            a.prepare(replace(self.m, max_input_tokens=20), *self.blobs)

    def test_candidate_and_patch_never_applied(self):
        original = (ROOT / a.SOURCE_PATH).read_bytes()
        self.assertEqual(a.validate_candidate(self.m, *self.blobs, self.candidate), c.digest(self.candidate))
        diff = a.review_patch(self.m, *self.blobs, self.candidate)
        self.assertIn(b'--- a/' + a.SOURCE_PATH.encode(), diff)
        self.assertEqual((ROOT / a.SOURCE_PATH).read_bytes(), original)

    def test_outer_byte_and_signature_rejections(self):
        for candidate in (self.candidate + b'print("pass")\n', self.candidate.replace(b'import re', b'import os'),
                          self.candidate.replace(b'field: str', b'field:str'),
                          self.candidate.replace(b'def required_text(', b'@print\ndef required_text('),
                          self.blobs[0], self.blobs[0].replace(b'return value.strip()', b'return value.strip() # change')):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                a.validate_candidate(self.m, *self.blobs, candidate)

    def test_dangerous_function_grammar_rejected(self):
        header = 'def required_text(row: dict[str, str | None], field: str) -> str:\n'
        for body in ('import os', 'return row.clear()', 'return value.__class__',
                     'exec(field)', 'while True: pass', 'row[field] = "x"',
                     'ValueError = field', 'return field.strip()', 'return value.strip(field)',
                     'return (lambda: field)()', 'return value.strip() + "x"',
                     'return f"{field!r}"', 'return value.strip()\n    return value.strip()'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_function(header + '    ' + body + '\n')

    def test_trusted_comparison_fake_function(self):
        # Hand-written fake, not compiled/generated source.
        def fake(row, field):
            value = row[field]
            if value is None:
                raise ValueError(f'{field} is required')
            value = value.strip()
            if not value:
                raise ValueError(f'{field} is required')
            return value
        self.assertEqual(compare(fake), tuple((name, True) for name in a.TEST_NAMES))
        self.assertFalse(all(passed for _, passed in compare(lambda row, field: 'PASS')))
        def mutate(row, field):
            row.clear()
            return 'PASS'
        self.assertFalse(any(passed for _, passed in compare(mutate)))

    def test_success_order_and_durable_receipt(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'pass', result)
        self.assertIsNotNone(result['patch'])
        self.assertEqual(self.events, ['ready', 'credentials', 'provider', 'runtime', 'closed'])
        row = self.gate.db.execute('SELECT receipt, container_id FROM calls').fetchone()
        self.assertEqual(row[1], 'd' * 64)
        self.assertEqual(json.loads(row[0])['outcome'], 'pass')
        self.assertEqual(self.run_task()['status'], 'blocked')
        self.assertEqual(self.events.count('provider'), 1)

    def test_candidate_rejection_retains_before_validation_without_runtime(self):
        self.candidate = self.candidate.replace(b'\n', b'\r\n')
        retained = []
        def record(candidate):
            self.events.append('retained')
            retained.append(candidate)
            return True
        result = self.run_task(candidate_recorder=record)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['phase'], 'candidate')
        self.assertEqual(result['failure_reason'], 'candidate_bound')
        self.assertEqual(result['candidate_hash'], c.digest(self.candidate))
        self.assertEqual(result['candidate_bytes'], len(self.candidate))
        self.assertTrue(result['candidate_retained'])
        self.assertEqual(retained, [self.candidate])
        self.assertEqual(self.events, ['ready', 'credentials', 'provider', 'retained', 'closed'])
        self.assertEqual(self.gate.db.execute('SELECT receipt, container_id FROM calls').fetchall(),
                         [(None, None)])
        self.assertIsNone(result['patch'])
        self.assertEqual(self.run_task()['status'], 'blocked')
        self.assertEqual(self.events.count('provider'), 1)

    def test_candidate_failure_labels_are_closed_and_do_not_format_exceptions(self):
        for reason in runner.CANDIDATE_FAILURES:
            with self.subTest(reason=reason):
                self.assertEqual(runner._candidate_failure(ValueError(reason)), reason)
        class Untrusted(ValueError):
            def __str__(self):
                raise AssertionError('must not format')
        for exc in (ValueError('secret-exception-sentinel'), ValueError('candidate_bound', 'secret'),
                    ValueError(['candidate_bound']), TypeError('candidate_bound'),
                    Untrusted('candidate_bound'), KeyboardInterrupt('secret')):
            self.assertEqual(runner._candidate_failure(exc), 'candidate_validation_failed')

    def test_unknown_validation_error_is_redacted_and_candidate_not_returned(self):
        with patch.object(runner, 'validate_candidate', side_effect=ValueError('secret-error')):
            result = self.run_task()
        self.assertEqual(result['failure_reason'], 'candidate_validation_failed')
        self.assertFalse(result['candidate_retained'])
        self.assertNotIn('secret-error', repr(result))
        self.assertNotIn(self.candidate.decode(), repr(result))
        self.assertNotIn('runtime', self.events)

    def test_retention_failure_blocks_validator_runtime_and_retry(self):
        def record(candidate):
            raise OSError('secret-path-sentinel')
        with patch.object(runner, 'validate_candidate') as validator, \
                patch.object(runner, 'derive_candidate') as derive:
            result = self.run_task(candidate_recorder=record)
        validator.assert_not_called()
        derive.assert_not_called()
        self.assertEqual(result['phase'], 'candidate_retention')
        self.assertEqual(result['failure_reason'], 'candidate_retention_failed')
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['candidate_retained'])
        self.assertNotIn('secret-path-sentinel', repr(result))
        self.assertEqual(self.events, ['ready', 'credentials', 'provider', 'closed'])
        self.assertIsNone(result['patch'])
        self.assertEqual(self.run_task()['status'], 'blocked')

    def test_retention_acknowledgement_must_be_true(self):
        result = self.run_task(candidate_recorder=lambda candidate: 1)
        self.assertEqual(result['failure_reason'], 'candidate_retention_failed')
        self.assertFalse(result['candidate_retained'])
        self.assertNotIn('runtime', self.events)

    def test_retention_precedes_validation_and_verified_pass(self):
        def record(candidate):
            self.events.append('retained')
            self.assertEqual(candidate, self.candidate)
            return True
        result = self.run_task(candidate_recorder=record)
        self.assertEqual(result['status'], 'pass')
        self.assertTrue(result['candidate_retained'])
        self.assertNotIn('failure_reason', result)
        self.assertEqual(self.events, ['ready', 'credentials', 'provider', 'retained', 'runtime', 'closed'])

    def test_malformed_response_never_reaches_candidate_recorder(self):
        with patch.object(runner, 'parse_response', side_effect=ValueError('response-sentinel')):
            with patch.object(runner, 'validate_candidate') as validator:
                recorder = unittest.mock.Mock()
                result = self.run_task(candidate_recorder=recorder)
        recorder.assert_not_called()
        validator.assert_not_called()
        self.assertEqual(result['phase'], 'response')
        self.assertNotIn('candidate_hash', result)
        self.assertNotIn('failure_reason', result)

    def test_readiness_blocks_provider_and_reservation(self):
        result = self.run_task(readiness=lambda *args: False)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.events, [])
        self.assertEqual(self.gate.db.execute('SELECT count(*) FROM calls').fetchone(), (0,))

    def test_provider_uncertainty_never_retries(self):
        def failed_factory(*args):
            raise TimeoutError
        self.assertEqual(self.run_task(transport_factory=failed_factory)['status'], 'unknown')
        self.gate.close()
        self.gate = c.CanonicalAdmission()
        self.assertEqual(self.run_task()['status'], 'blocked')
        self.assertNotIn('provider', self.events)

    def test_cleanup_failure_or_wrong_identity_no_patch_or_receipt(self):
        for change in ({'cleanup_verified': False}, {'container_id': 'e' * 64},
                       {'status': 'blocked'}, {'failed': ['unknown']}, {'diagnostic': {}}):
            with self.subTest(change=change):
                # Separate task stores in this fixture only, never production reset.
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'store'; path.mkdir(mode=0o700)
                    with patch.object(c, 'STORE', path):
                        gate = c.CanonicalAdmission()
                        try:
                            def runtime(*args, **kwargs):
                                result = self.runtime(*args, **kwargs); result.update(change); return result
                            result = self.run_task(gate=gate, transport_factory=lambda *args: FakeResponse(response(self.candidate)), runtime=runtime)
                            self.assertEqual(result['status'], 'unknown')
                            self.assertIsNone(result['patch'])
                            self.assertIsNone(gate.db.execute('SELECT receipt FROM calls').fetchone()[0])
                        finally:
                            gate.close()

    def test_binding_write_once_restart_and_mismatch(self):
        reservation = self.reserve()
        self.gate.bind_container(self.m, 1, reservation['sandbox_name'], 'd' * 64)
        self.gate.close(); self.gate = c.CanonicalAdmission()
        for name, identity in ((reservation['sandbox_name'], 'd' * 64), ('wrong', 'e' * 64)):
            with self.assertRaises(ValueError):
                self.gate.bind_container(self.m, 1, name, identity)
        with self.assertRaises(ValueError):
            self.gate.reserve(self.m, *self.blobs)

    def test_v1_rejection_does_not_modify_database(self):
        self.gate.close()
        db = self.store / 'canonical.sqlite3'
        connection = sqlite3.connect(db)
        connection.execute('PRAGMA user_version=1'); connection.close()
        before = db.read_bytes()
        with self.assertRaises(ValueError):
            c.CanonicalAdmission()
        self.assertEqual(db.read_bytes(), before)

    def test_response_validation(self):
        self.assertEqual(parse_response(self.m, response(self.candidate))[0], self.candidate)
        for change in ({'model': 'wrong'}, {'status': 'incomplete'},
                       {'usage': {'input_tokens': True, 'output_tokens': 0, 'total_tokens': 1}},
                       {'usage': {'input_tokens': 9000, 'output_tokens': 0, 'total_tokens': 9000}}):
            obj = json.loads(response(self.candidate)); obj.update(change)
            with self.assertRaises(ValueError):
                parse_response(self.m, json.dumps(obj).encode())
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'x' * 65537):
            with self.assertRaises(ValueError):
                parse_response(self.m, raw)

    def test_known_failure_records_without_patch_or_second_call(self):
        def runtime(*args, **kwargs):
            result = self.runtime(*args, **kwargs)
            result.update(status='tests_failed', failed=['test_trim'])
            return result
        result = self.run_task(runtime=runtime)
        self.assertEqual(result['status'], 'test_failure')
        self.assertIsNone(result['patch'])
        self.assertEqual(self.run_task()['status'], 'blocked')
        self.assertEqual(self.events.count('provider'), 1)

    def test_binding_survives_process_exit_before_attach(self):
        reservation = self.reserve()
        script = """import json,os,sys
from pathlib import Path
from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
c.STORE=Path(sys.argv[1])
values=json.loads(sys.argv[2]); values['test_names']=tuple(values['test_names'])
m=c.Manifest(**values)
gate=c.CanonicalAdmission()
gate.bind_container(m,1,sys.argv[3],'d'*64)
os._exit(19)
"""
        process = subprocess.run([sys.executable, '-B', '-c', script, str(self.store),
                                  json.dumps(asdict(self.m)), reservation['sandbox_name']],
                                 cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 19, process.stderr)
        self.assertEqual(self.gate.db.execute('SELECT container_id FROM calls').fetchone(), ('d' * 64,))
        with self.assertRaises(ValueError):
            self.gate.bind_container(self.m, 1, reservation['sandbox_name'], 'd' * 64)
        receipt = c.Receipt('pass', 'e' * 64, 'f' * 64, 'a' * 64,
            reservation['sandbox_name'], self.m.image_id,
            tuple((name, True) for name in self.m.test_names), 1, 1, 10, 10,
            '1' * 64, '2' * 64, True)
        with self.assertRaisesRegex(ValueError, 'created_container_mismatch'):
            self.gate.record(self.m, 1, receipt)
        with self.assertRaises(ValueError):
            self.gate.reserve(self.m, *self.blobs)

    def test_transport_rejects_error_encoding_oversize_and_no_replay(self):
        reservation = self.reserve()
        for status, encoding, raw, close_error in ((429, 'identity', b'', False),
                (200, 'gzip', b'', False), (200, 'identity', b'x' * 65537, False),
                (200, 'identity', response(self.candidate), True)):
            events = []
            class Connection:
                def __init__(self, *args, **kwargs): self.status = status
                def connect(self): pass
                def request(self, *args, **kwargs): events.append('send')
                def getresponse(self): return self
                def getheader(self, key, default=None):
                    return encoding if key == 'Content-Encoding' else default
                def read(self, size): return raw
                def close(self):
                    events.append('close')
                    if close_error: raise OSError('synthetic')
            transport = OneFileTransport(self.m, *self.blobs, reservation, 'fake-key',
                lambda operation, seconds: operation(), connection_factory=Connection)
            with self.assertRaisesRegex(RuntimeError, 'provider_failed_or_uncertain'): transport()
            with self.assertRaises(ValueError): transport()
            self.assertEqual(events, ['send', 'close'])
            transport.close()

    def test_runner_retains_closed_safe_transport_failure_evidence(self):
        secret = 'SECRET_SENTINEL_NEVER_RETAIN'
        created = []
        class Connection:
            status = 429
            def __init__(self, *args, **kwargs): pass
            def connect(self): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return self
            def close(self): pass
        def factory(manifest, source, tests, harness, reservation):
            transport = OneFileTransport(manifest, source, tests, harness, reservation,
                secret, lambda operation, seconds: operation(), connection_factory=Connection)
            created.append(transport)
            return transport
        result = self.run_task(transport_factory=factory)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['phase'], 'provider')
        self.assertEqual(result['transport'], {'phase': 'headers', 'category': 'response_rejected',
            'http_status': 429, 'delivery': 'response_received', 'connection_closed': True})
        self.assertNotIn(secret, json.dumps(result))
        self.assertIsNone(created[0].key)
        # Even unexpected raw strings inserted in observation fields are dropped.
        created[0].evidence.update(phase=secret, category=secret, delivery=secret,
                                  http_status=secret, connection_closed=secret, raw=secret)
        self.assertNotIn(secret, json.dumps(transport_observation(created[0])))

    def test_raw_transport_exception_text_is_not_retained(self):
        secret = 'SECRET_EXCEPTION_SENTINEL'
        reservation = self.reserve()
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def connect(self): raise OSError(secret)
            def close(self): pass
        transport = OneFileTransport(self.m, *self.blobs, reservation, 'fake-key',
            lambda operation, seconds: operation(), connection_factory=Connection)
        with self.assertRaises(RuntimeError) as caught: transport()
        self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(transport_observation(transport), {'phase': 'connect',
            'category': 'operation_failed', 'http_status': None, 'delivery': 'not_sent',
            'connection_closed': True})
        transport.close()

    def test_http_content_length_and_chunked_framing(self):
        reservation = self.reserve()
        body = response(self.candidate)
        cases = (
            (str(len(body)), None, True),
            (' \t' + str(len(body)) + '\t ', None, True),
            (None, None, True),
            (None, 'chunked', True),
            (str(len(body) + 1), None, False),  # Valid JSON, but premature EOF.
            (str(len(body) - 1), None, False),
            ('65537', None, False),
            ('bogus', None, False),
            ('-1', None, False),
            ('1, 1', None, False),
            ('1, 2', None, False),
            ('9' * 100, None, False),
            (str(len(body)), 'chunked', False),
            (None, 'gzip, chunked', False),
        )
        for length, transfer, accepted in cases:
            with self.subTest(length=length, transfer=transfer):
                events = []
                class Connection:
                    status = 200
                    def __init__(self, *args, **kwargs): pass
                    def connect(self): pass
                    def request(self, *args, **kwargs): events.append('send')
                    def getresponse(self): return self
                    def getheader(self, key, default=None):
                        return {'Content-Length': length, 'Transfer-Encoding': transfer}.get(key, default)
                    def read(self, size): events.append('read'); return body
                    def close(self): events.append('close')
                transport = OneFileTransport(self.m, *self.blobs, reservation, 'fake-key',
                    lambda operation, seconds: operation(), connection_factory=Connection)
                if accepted:
                    self.assertEqual(transport(), body)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'provider_failed_or_uncertain'): transport()
                self.assertEqual(events.count('send'), 1)
                self.assertEqual(events[-1], 'close')
                with self.assertRaises(ValueError): transport()
                transport.close()

    def test_transport_one_shot_fixed_wire_and_close(self):
        reservation = self.reserve(); events = []
        outer = self
        class Connection:
            def __init__(self, host, port, **kwargs):
                outer.assertEqual((host, port), ('api.openai.com', 443))
            def connect(self): events.append('connect')
            def request(self, method, path, **kwargs):
                outer.assertEqual((method, path), ('POST', '/v1/responses'))
                outer.assertEqual(kwargs['body'], a.prepare(outer.m, *outer.blobs).wire)
                outer.assertEqual(self.auto_open, 0); events.append('send')
            def getresponse(self): return self
            status = 200
            def getheader(self, key, default): return default
            def read(self, size):
                outer.assertEqual(size, outer.m.max_response_bytes + 1)
                return response(outer.candidate)
            def close(self): events.append('close')
        transport = OneFileTransport(self.m, *self.blobs, reservation, 'fake-key',
                                     lambda operation, seconds: operation(), connection_factory=Connection)
        self.assertEqual(transport(), response(self.candidate))
        self.assertEqual(events, ['connect', 'send', 'close'])
        with self.assertRaises(ValueError): transport()
        transport.close(); self.assertIsNone(transport.key)


class FakeResponse:
    def __init__(self, raw): self.raw = raw
    def __call__(self): return self.raw
    def close(self): pass
