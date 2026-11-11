"""Real local Git/data-only checks. No model, container, candidate import or host change."""
import base64
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import agents_checkout_verifier as verifier
from ops.bounded_worker import agents_linear_adapter as adapter
from ops.bounded_worker.bridge_checkout import Checkout
from ops.bounded_worker.linear_coding_intake import _canonical
from ops.bounded_worker.task import Task


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); source = self.root / 'source'; source.mkdir()
        def git(*args, cwd=source):
            return subprocess.check_output(['/usr/bin/git', *args], cwd=cwd, stderr=subprocess.DEVNULL)
        git('init', '-q', '-b', 'main'); git('config', 'user.email', 'fixture@example.invalid')
        git('config', 'user.name', 'Fixture')
        (source / 'sample.py').write_bytes(b'def answer():\n    return 0\n')
        (source / 'trusted.py').write_bytes(b'fixed trusted input\n')
        git('add', '.'); git('commit', '-qm', 'fixture')
        base = git('rev-parse', 'HEAD').strip().decode('ascii')
        self.mirror = self.root / 'mirror.git'; git('clone', '--bare', '--', str(source), str(self.mirror))
        self.checkouts = self.root / 'checkouts'; self.checkouts.mkdir(mode=0o700)
        self.evidence = self.root / 'evidence'; self.evidence.mkdir(mode=0o700)
        # Only privilege/ownership admission is mocked; clone, snapshots and apply are real.
        for target in ('ops.bounded_worker.bridge_checkout.protected_tree',
                       'ops.bounded_worker.bridge_checkout.protected_dir',
                       'ops.bounded_worker.agents_checkout_verifier.protected_dir'):
            mock = patch(target, side_effect=lambda path, mode=None: Path(path)); mock.start(); self.addCleanup(mock.stop)
        self.checkout = Checkout(self.mirror, self.checkouts)
        self.task = Task('task', 'stevengalvis/modelfc', base, 'Update answer', ('sample.py',), 90, 65536, 4096)
        self.approval = adapter.APIApproval(adapter.task_digest(self.task), 'test-model', 100, 'b'*64, '3'*64, 300, True)
        self.attempt = adapter._attempt(self.task, self.approval)
        self.binding = SimpleNamespace(attempt_id=self.attempt, session_id='session', environment_id='environment')
        self.plan = verifier.VerificationPlan(adapter.task_digest(self.task), 'b'*64, 'c'*64, 'd'*64, 'e'*64,
                                             ('test_answer',), 'isolated-candidate-v1', 20, 4096, True)
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        adapter.initialize_adapter(self.db)
        self.db.execute('CREATE TABLE linear_coding_jobs (task TEXT, phase TEXT)')
        self.db.execute('INSERT INTO linear_coding_jobs VALUES (?,?)', (self.task.task_id, 'launching'))
        self.db.execute('INSERT INTO linear_agents_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (self.task.task_id, self.attempt, adapter.task_digest(self.task),
             _canonical(asdict(self.approval)), 'reserved', 'session', 'environment', 'turn',
             'completed', 1, 1, 2, None, None)); self.db.commit()
        self.plan_patch = patch.object(verifier, 'load_plan', side_effect=lambda _: self.plan)
        self.plan_patch.start(); self.addCleanup(self.plan_patch.stop)
        self.subject = verifier.CheckoutVerifier(self.db, self.checkout, self.evidence, '/protected/plan')
        self.files = self.subject.prepare(self.task, self.attempt)

    def export(self, changes=None, **identity):
        files = self.files | (changes or {'sample.py': b'def answer():\n    return 42\n'})
        obj = dict(version=1, task_sha256=adapter.task_digest(self.task), attempt_id=self.attempt,
                   baseline_sha256=self.subject.baseline, files=[dict(path=name, mode=0o644,
                   content_base64=base64.b64encode(raw).decode('ascii')) for name, raw in sorted(files.items())])
        return _canonical(obj | identity)

    def stage(self, raw=None):
        return self.subject.stage_candidate(raw or self.export(), self.binding, 'turn')

    def receipt(self):
        return dict(version=1, stage_sha256=verifier._digest(_canonical(self.subject.stage)),
            verification_plan_sha256=self.plan.sha256, test_attempt_id='f'*64,
            test_image_sha256='d'*64, test_acceptance_sha256='e'*64, harness_sha256='c'*64,
            container_id='1'*64, assertion_protocol='isolated-candidate-v1', network='none',
            results={'test_answer': 'passed'}, exit_code=0, stdout_eof=True, stderr_eof=True,
            cleanup_verified=True, elapsed_ms=300, output_bytes=20, evidence_sha256='2'*64)

    def finish(self, change=None):
        receipt = self.receipt() | (change or {})
        with patch.object(verifier, 'protected_json', return_value=receipt):
            return self.subject.finish('/protected/test-receipt', self.binding, 'turn')

    def test_real_clean_checkout_patch_apply_and_independent_receipt(self):
        stage = self.stage()
        self.assertNotIn('tests_passed', stage)
        self.assertEqual((self.subject.work / 'sample.py').read_bytes(), b'def answer():\n    return 42\n')
        result = self.finish()
        self.assertIs(type(result), adapter.VerifiedResult); self.assertTrue(result.tests_passed)
        self.assertEqual(result.diff_sha256, stage['diff_sha256'])
        self.assertEqual(result.changed_paths, ('sample.py',))
        self.assertTrue((self.subject.evidence / 'verified-result.json').exists())

    def test_missing_final_lf_preserves_raw_and_tests_derived_exact_bytes(self):
        raw = self.export({'sample.py': b'def answer():\n    return 42'})
        stage = self.stage(raw)
        lineage = stage['lineage']['sample.py']
        self.assertTrue(lineage['appended_final_lf'])
        self.assertEqual(lineage['raw_bytes'] + 1, lineage['derived_bytes'])
        self.assertNotEqual(lineage['raw_sha256'], lineage['derived_sha256'])
        self.assertEqual((self.subject.evidence / 'candidate-export.json').read_bytes(), raw)
        self.assertEqual((self.subject.work / 'sample.py').read_bytes(), b'def answer():\n    return 42\n')
        self.assertTrue(self.finish().tests_passed)

    def test_carriage_returns_are_preserved_not_silently_normalized(self):
        stage = self.stage(self.export({'sample.py': b'def answer():\r\n    return 42\r\n'}))
        self.assertFalse(stage['lineage']['sample.py']['appended_final_lf'])
        self.assertEqual((self.subject.work / 'sample.py').read_bytes(), b'def answer():\r\n    return 42\r\n')

    def test_candidate_source_is_never_imported_or_executed(self):
        marker = self.root / 'must-not-exist'
        raw = self.export({'sample.py': f'raise RuntimeError("untrusted"); open({str(marker)!r}, "w").write("bad")\n'.encode()})
        self.stage(raw)
        self.assertFalse(marker.exists())
        self.assertFalse((self.subject.evidence / 'verified-result.json').exists())

    def test_changed_nonallowlisted_file_rejected(self):
        with self.assertRaises(ValueError): self.stage(self.export({'trusted.py': b'changed\n'}))
        self.assertTrue(self.subject.failed)

    def test_added_and_missing_files_rejected(self):
        raw = json.loads(self.export()); raw['files'].append(dict(path='zz.py', mode=0o644, content_base64=''))
        with self.assertRaises(ValueError): self.stage(_canonical(raw))

    def test_duplicate_paths_rejected(self):
        raw = json.loads(self.export()); raw['files'].append(raw['files'][-1])
        with self.assertRaises(ValueError): self.stage(_canonical(raw))

    def test_export_identity_mismatch(self):
        with self.assertRaises(ValueError): self.stage(self.export(attempt_id='f'*64))

    def test_unsafe_paths_rejected(self):
        for path in ('../escape', '/absolute', '.git/config', 'x//y', '.aws/key'):
            with self.subTest(path=path):
                raw = json.loads(self.export()); raw['files'][0]['path'] = path
                with self.assertRaises(ValueError): verifier.parse_export(_canonical(raw), self.task, self.attempt, self.subject.baseline)

    def test_export_wrong_type_mode_and_encoding_rejected(self):
        for key, value in (('mode', True), ('mode', 0o755), ('content_base64', '%%'), ('content_base64', 'YQ===')):
            with self.subTest(key=key, value=value):
                raw = json.loads(self.export()); raw['files'][0][key] = value
                with self.assertRaises(ValueError): verifier.parse_export(_canonical(raw), self.task, self.attempt, self.subject.baseline)

    def test_duplicate_json_keys_rejected(self):
        raw = self.export().replace(b'{', b'{"version":1,', 1)
        with self.assertRaises(ValueError): self.stage(raw)

    def test_binary_changed_candidate_rejected_and_raw_retained(self):
        raw = self.export({'sample.py': b'\x00bad\n'})
        with self.assertRaises(ValueError): self.stage(raw)
        self.assertEqual((self.subject.evidence / 'candidate-export.json').read_bytes(), raw)

    def test_noop_is_not_manufactured_success(self):
        with self.assertRaises(ValueError): self.stage(self.export(dict(self.files)))

    def test_no_cloud_cleanup_no_candidate_read(self):
        self.db.execute('UPDATE linear_agents_attempts SET cleanup=0'); self.db.commit()
        with self.assertRaises(ValueError): self.stage()
        self.assertFalse((self.subject.evidence / 'candidate-export.json').exists())

    def test_changed_api_approval_or_nonlaunching_job_cannot_verify(self):
        self.db.execute("UPDATE linear_coding_jobs SET phase='completed'"); self.db.commit()
        with self.assertRaises(ValueError): self.stage()

    def test_cloud_approval_corruption_rejected(self):
        self.db.execute('UPDATE linear_agents_attempts SET policy=?',
                        (_canonical(asdict(replace(self.approval, enabled=False))),)); self.db.commit()
        with self.assertRaises(ValueError): self.stage()

    def test_returned_stage_is_not_mutable_verifier_authority(self):
        returned = self.stage()
        returned['lineage']['sample.py']['raw_sha256'] = 'f'*64
        self.assertNotEqual(returned, self.subject.stage)
        self.assertTrue(self.finish().tests_passed)

    def test_wrong_cloud_turn_rejected(self):
        self.db.execute("UPDATE linear_agents_attempts SET turn='wrong'"); self.db.commit()
        with self.assertRaises(ValueError): self.stage()

    def test_changed_test_plan_keeps_hold(self):
        self.plan = replace(self.plan, enabled=False)
        with self.assertRaises(ValueError): self.stage()

    def test_dirty_independent_checkout_rejected(self):
        (self.subject.work / 'trusted.py').write_bytes(b'changed\n')
        with self.assertRaises(ValueError): self.stage()

    def test_symlink_in_independent_checkout_rejected(self):
        path = self.subject.work / 'sample.py'; path.unlink(); path.symlink_to(self.root / 'source/sample.py')
        with self.assertRaises(ValueError): self.stage()

    def test_no_test_receipt_is_blocked_not_test_failure(self):
        self.stage()
        with patch.object(verifier, 'protected_json', side_effect=FileNotFoundError), self.assertRaises(FileNotFoundError):
            self.subject.finish('/missing', self.binding, 'turn')
        self.assertFalse((self.subject.evidence / 'verified-result.json').exists())

    def test_known_independent_test_failure_is_distinct(self):
        self.stage()
        result = self.finish(dict(results={'test_answer': 'failed'}, exit_code=1))
        self.assertFalse(result.tests_passed)

    def test_missing_test_or_skip_cannot_be_success(self):
        self.stage()
        with self.assertRaises(ValueError): self.finish(dict(results={}))

    def test_receipt_corruption_cases(self):
        self.stage()
        good = self.receipt()
        cases = [dict(stage_sha256='f'*64), dict(verification_plan_sha256='f'*64), dict(container_id='bad'),
            dict(assertion_protocol='unittest-exit-only'), dict(network='bridge'), dict(stdout_eof=False),
            dict(stderr_eof=False), dict(cleanup_verified=False), dict(elapsed_ms=20001), dict(output_bytes=4097),
            dict(exit_code=0, results={'test_answer': 'failed'}), dict(results={'test_answer': 'skipped'}),
            dict(test_image_sha256='f'*64), dict(harness_sha256='f'*64)]
        for change in cases:
            with self.subTest(change=change):
                # Independent fixture validation attempts; no prior receipt is published.
                self.subject.failed = False
                with patch.object(verifier, 'protected_json', return_value=good | change), self.assertRaises(ValueError):
                    self.subject.finish('/protected/test-receipt', self.binding, 'turn')
        self.assertFalse((self.subject.evidence / 'verified-result.json').exists())

    def test_checkout_mutation_during_test_receipt_read_is_rejected(self):
        self.stage(); receipt = self.receipt()
        def read(_):
            (self.subject.work / 'sample.py').write_bytes(b'changed later\n')
            return receipt
        with patch.object(verifier, 'protected_json', side_effect=read), self.assertRaises(ValueError):
            self.subject.finish('/protected/test-receipt', self.binding, 'turn')

    def test_second_candidate_and_reopened_attempt_cannot_replace_evidence(self):
        self.stage()
        with self.assertRaises(ValueError): self.stage()
        second = verifier.CheckoutVerifier(self.db, self.checkout, self.evidence, '/protected/plan')
        with self.assertRaises(FileExistsError): second.prepare(self.task, self.attempt)

    def test_shared_linear_dispatch_reaches_real_checkout_verifier_offline(self):
        from tests import test_agents_linear_adapter as api_fixture
        from tests.test_linear_pickup import target_request, sink_for
        from ops.bounded_worker import linear_coding_intake as intake
        from ops.bounded_worker import linear_coding_runtime as runtime
        fixture = api_fixture.AdapterTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.req = target_request('agents-api', base=self.task.base_revision, paths=['sample.py'], output=65536)
        fixture.task = intake._task(fixture.req)[1]
        fixture.approval = replace(fixture.approval, request_sha256=intake.binding(fixture.req),
                                  base=self.task.base_revision, paths=('sample.py',))
        fixture.policy = replace(fixture.policy, task_sha256=adapter.task_digest(fixture.task))
        fixture.reader.fetch.return_value = fixture.req; fixture.sink = sink_for(fixture.req)
        self.plan = replace(self.plan, task_sha256=adapter.task_digest(fixture.task))
        with runtime.receipt_store(fixture.root) as db:
            checked = verifier.CheckoutVerifier(db, self.checkout, self.evidence, '/protected/plan')
            fixture.prepared.prepare.side_effect = checked.prepare
            def independent(task, attempt, binding, turn):
                candidate = checked.original | {'sample.py': b'def answer():\n    return 42\n'}
                export = _canonical(dict(version=1, task_sha256=adapter.task_digest(task), attempt_id=attempt,
                    baseline_sha256=checked.baseline, files=[dict(path=name, mode=0o644,
                    content_base64=base64.b64encode(raw).decode('ascii')) for name, raw in sorted(candidate.items())]))
                checked.stage_candidate(export, binding, turn)
                previous = self.subject; self.subject = checked
                try: receipt = self.receipt()
                finally: self.subject = previous
                with patch.object(verifier, 'protected_json', return_value=receipt):
                    return checked.finish('/protected/test-receipt', binding, turn)
            fixture.prepared.verify.side_effect = independent
            self.assertEqual(fixture.dispatch(db)['status'], 'completed')
            self.assertTrue(intake._safe(db))
            self.assertEqual(runtime.deliver(db, 'one', fixture.sink), 'posted')
            self.assertEqual(fixture.dispatch(db)['status'], 'duplicate')
            self.assertEqual((checked.work / 'sample.py').read_bytes(), candidate_text := b'def answer():\n    return 42\n')
            self.assertEqual(checked.derived, {'sample.py': candidate_text})
        fixture.fixture.connection.create_session.assert_called_once()
        fixture.fixture.connection.submit_task.assert_called_once()


class PlanTests(unittest.TestCase):
    def test_strict_protected_plan(self):
        plan = verifier.VerificationPlan('a'*64, 'b'*64, 'c'*64, 'd'*64, 'e'*64,
                                        ('test_answer',), 'isolated-candidate-v1', 20, 4096)
        value = asdict(plan) | {'version': 1}; value['test_names'] = list(value['test_names'])
        with patch.object(verifier, 'protected_json', return_value=value):
            self.assertEqual(verifier.load_plan('/protected'), plan)
        for changes in (dict(test_names=[]), dict(enabled=1), dict(max_seconds=61), dict(max_output_bytes=True),
                        dict(test_names=['test_answer', 'test_answer']), dict(assertion_protocol='unittest-exit-only')):
            with self.subTest(changes=changes), patch.object(verifier, 'protected_json', return_value=value | changes):
                with self.assertRaises(ValueError): verifier.load_plan('/protected')


if __name__ == '__main__': unittest.main()
