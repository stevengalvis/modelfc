"""Local fixture/cleanup checks, not evidence of real systemd execution."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retention_systemd', ROOT / 'ops/ci/retention_systemd.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class RetentionSystemdTests(unittest.TestCase):
    def test_guard_rejects_local_environment_before_mutation(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, 'NOT_DISPOSABLE'):
            harness.guard()
        with patch.object(harness, 'guard', side_effect=RuntimeError('NOT_DISPOSABLE')), patch.object(
                harness, 'state') as state, self.assertRaisesRegex(RuntimeError, 'NOT_DISPOSABLE'):
            harness.claim()
        state.assert_not_called()

    def test_fixed_fixtures_compile_and_real_controller_has_no_substitution(self):
        for mode in harness.MODES:
            compile(harness.FIXTURE.replace('__MODE__', repr(mode)), mode, 'exec')
        compile(harness.CHILD, 'root-controller-fixture', 'exec')
        self.assertIn('m.tests(', harness.CHILD)
        self.assertNotIn('unittest.mock', harness.CHILD)
        self.assertNotIn('m.run_service=', harness.CHILD)
        self.assertNotIn('monotonic_ns=', harness.CHILD)
        self.assertEqual(set(harness.ENV), {'PATH', 'HOME', 'LANG'})
        self.assertNotIn('/home/runner', harness.FIXTURE)

    def test_artifact_and_unit_pins_match_reviewed_package(self):
        builder = harness.load('retention_builder_test', ROOT / 'ops/ci/build_retention.py')
        self.assertEqual(builder.PINS['input_sha256'], harness.INPUT)
        with tempfile.TemporaryDirectory() as tmp:
            artifact = builder.build(ROOT, Path(tmp) / 'controller.py')
            self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), harness.CANDIDATE)
        self.assertEqual(hashlib.sha256((ROOT / 'deploy' / harness.UNIT).read_bytes()).hexdigest(), harness.UNIT_HASH)

    def fixture(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        roots = [root / 'fixture']
        units = {name: root / name for name in (harness.UNIT, harness.API)}
        marker = root / 'marker'
        for name, value in dict(ROOTS=roots, UNITS=units, MARKER=marker, PARENT=root / 'parent',
                                DROPINS=[root / (unit + '.d') for unit in units]).items():
            self.enterContext(patch.object(harness, name, value))
        self.enterContext(patch.object(harness, 'guard'))
        self.enterContext(patch.object(harness, 'invocation', return_value=['123', '1']))
        self.enterContext(patch.object(harness, 'account_absent', return_value=True))
        inactive = dict(LoadState='not-found', ActiveState='inactive', MainPID='0', ControlPID='0',
                        ControlGroup='', FragmentPath='', DropInPaths='')
        state = self.enterContext(patch.object(harness, 'state', return_value=inactive))
        command = self.enterContext(patch.object(harness, 'command', return_value=''))
        return root, roots, units, marker, state, command, inactive

    def owned(self):
        root, roots, units, marker, state, command, inactive = self.fixture()
        ledger = harness.claim()
        roots[0].mkdir(); harness.own(ledger, roots[0])
        for unit, path in units.items():
            path.write_text('synthetic'); harness.own(ledger, path)
            ledger['groups'][unit] = '/system.slice/' + unit
        harness.save(ledger)
        state.side_effect = lambda unit: {**inactive, 'FragmentPath': str(units[unit]) if units[unit].exists() else ''}
        real_lstat = Path.lstat
        def lstat(path):
            value = real_lstat(path)
            if path == marker:
                fields = list(value); fields[4] = 0
                return os.stat_result(fields)
            return value
        self.enterContext(patch.object(Path, 'lstat', lstat))
        return roots, units, marker, state, command, inactive

    def test_collision_guard_preserves_paths_dangling_links_and_loaded_units(self):
        root, roots, units, marker, state, command, inactive = self.fixture()
        for collision in [marker, roots[0], *units.values(), *harness.DROPINS]:
            collision.symlink_to(root / 'missing')
            with self.assertRaisesRegex(RuntimeError, 'COLLISION'):
                harness.claim()
            self.assertTrue(collision.is_symlink())
            collision.unlink()
        state.return_value = {**inactive, 'LoadState': 'loaded'}
        with self.assertRaisesRegex(RuntimeError, 'UNIT_COLLISION'):
            harness.claim()
        self.assertFalse(marker.exists())
        command.assert_not_called()

    def test_account_collision_precedes_marker(self):
        root, roots, units, marker, state, command, inactive = self.fixture()
        with patch.object(harness, 'account_absent', return_value=False), self.assertRaisesRegex(RuntimeError, 'ACCOUNT_COLLISION'):
            harness.claim()
        self.assertFalse(marker.exists())
        state.assert_not_called()

    def test_cleanup_checks_both_groups_before_deletion(self):
        roots, units, marker, state, command, inactive = self.owned()
        checked = []
        def empty(group):
            self.assertTrue(all(path.exists() for path in units.values()))
            self.assertTrue(roots[0].exists())
            checked.append(group)
            return True
        with patch.object(harness, 'empty_group', side_effect=empty):
            harness.cleanup()
        self.assertEqual(set(checked), {'/system.slice/' + unit for unit in units})
        self.assertIn((['systemctl', 'stop', *units],), [call.args for call in command.call_args_list])
        self.assertFalse(marker.exists())
        self.assertFalse(roots[0].exists())
        self.assertTrue(all(not path.exists() for path in units.values()))

    def test_cleanup_stop_failure_keeps_all_resources(self):
        roots, units, marker, state, command, inactive = self.owned()
        command.side_effect = subprocess.CalledProcessError(1, 'systemctl stop')
        with self.assertRaises(subprocess.CalledProcessError):
            harness.cleanup()
        self.assertTrue(marker.exists() and roots[0].exists())
        self.assertTrue(all(path.exists() for path in units.values()))

    def test_cleanup_resets_only_failed_unit(self):
        roots, units, marker, state, command, inactive = self.owned()
        state.side_effect = lambda unit: {**inactive, 'ActiveState': 'failed' if unit == harness.UNIT else 'inactive',
                                          'FragmentPath': str(units[unit]) if units[unit].exists() else ''}
        with patch.object(harness, 'empty_group', return_value=True):
            harness.cleanup()
        resets = [call.args[0] for call in command.call_args_list if call.args[0][1] == 'reset-failed']
        self.assertEqual(resets, [['systemctl', 'reset-failed', harness.UNIT]])

    def test_reset_failure_keeps_resources_and_reports_stderr(self):
        roots, units, marker, state, command, inactive = self.owned()
        state.side_effect = lambda unit: {**inactive, 'ActiveState': 'failed', 'FragmentPath': str(units[unit])}
        def run(args, **kwargs):
            if args[1] == 'reset-failed':
                raise subprocess.CalledProcessError(1, args, stderr='manager refused reset')
        command.side_effect = run
        with patch.object(harness, 'empty_group', return_value=True), self.assertRaisesRegex(RuntimeError, 'manager refused reset'):
            harness.cleanup()
        self.assertTrue(marker.exists() and roots[0].exists() and all(p.exists() for p in units.values()))

    def test_unloaded_or_replaced_unit_during_cleanup_blocks_deletion(self):
        for race in ('unloaded', 'replaced', 'populated'):
            with self.subTest(race=race):
                case = RetentionSystemdTests()
                try:
                    roots, units, marker, state, command, inactive = case.owned()
                    def run(args, **kwargs):
                        if args[1] == 'stop':
                            if race == 'unloaded':
                                state.side_effect = lambda unit: {**inactive, 'FragmentPath': ''}
                            elif race == 'replaced':
                                path = units[harness.API]
                                replacement = path.with_suffix('.new'); replacement.write_text('foreign'); replacement.replace(path)
                    command.side_effect = run
                    with patch.object(harness, 'empty_group', return_value=race != 'populated'), self.assertRaisesRegex(
                            RuntimeError, 'UNIT_OVERRIDE|RESOURCE_CHANGED|CGROUP_NOT_EMPTY'):
                        harness.cleanup()
                    self.assertTrue(marker.exists() and roots[0].exists() and all(p.exists() for p in units.values()))
                finally:
                    case.doCleanups()

    def test_cleanup_populated_api_group_blocks_deletion(self):
        roots, units, marker, state, command, inactive = self.owned()
        with patch.object(harness, 'empty_group', side_effect=lambda group: not group.endswith(harness.API)), self.assertRaisesRegex(RuntimeError, 'CGROUP_NOT_EMPTY'):
            harness.cleanup()
        self.assertTrue(marker.exists() and roots[0].exists())
        self.assertTrue(all(path.exists() for path in units.values()))

    def test_cleanup_rejects_replaced_owned_resource_before_stop(self):
        roots, units, marker, state, command, inactive = self.owned()
        path = units[harness.API]
        replacement = path.with_suffix('.new'); replacement.write_text('unrelated')
        replacement.replace(path)
        with self.assertRaisesRegex(RuntimeError, 'RESOURCE_CHANGED'):
            harness.cleanup()
        command.assert_not_called()
        self.assertEqual(path.read_text(), 'unrelated')
        self.assertTrue(roots[0].exists())

    def test_cleanup_without_marker_never_touches_preexisting_resources(self):
        root, roots, units, marker, state, command, inactive = self.fixture()
        roots[0].mkdir()
        harness.cleanup()
        command.assert_not_called(); state.assert_not_called()
        self.assertTrue(roots[0].exists())

    def test_workflow_scope_cleanup_and_exact_head_upload(self):
        text = (ROOT / '.github/workflows/retention-systemd.yml').read_text()
        for forbidden in ('secrets.', 'push:', 'pull_request_target', 'workflow_dispatch', 'environment: production'):
            self.assertNotIn(forbidden, text)
        for required in ('contents: read', 'persist-credentials: false', 'timeout-minutes: 20',
                         'cancel-in-progress: false', 'ref: ${{ github.event.pull_request.head.sha }}',
                         '--cleanup', 'if: always()', 'retention-acceptance-${{ github.event.pull_request.head.sha }}'):
            self.assertIn(required, text)
        source = (ROOT / 'ops/ci/retention_systemd.py').read_text()
        self.assertIn('600 <= elapsed < 630', source)
        self.assertIn('retained.read_bytes() == raw', source)
        self.assertIn('process.terminate(); process.wait(timeout=15)', source)
        self.assertNotIn('ProtectHome=yes', source)

    def test_unrecorded_root_unit_or_account_blocks_cleanup_without_deletion(self):
        for kind in ('root', 'unit', 'account'):
            with self.subTest(kind=kind):
                # Separate helper instance keeps patches isolated across cases.
                case = RetentionSystemdTests()
                try:
                    root, roots, units, marker, state, command, inactive = case.fixture()
                    harness.claim()
                    real_lstat = Path.lstat
                    def lstat(path):
                        value = real_lstat(path)
                        if path == marker:
                            fields = list(value); fields[4] = 0
                            return os.stat_result(fields)
                        return value
                    if kind == 'root': roots[0].mkdir()
                    if kind == 'unit': units[harness.UNIT].write_text('unrecorded')
                    with patch.object(Path, 'lstat', lstat), patch.object(
                            harness, 'account_absent', return_value=kind != 'account'), self.assertRaisesRegex(
                            RuntimeError, 'UNRECORDED'):
                        harness.cleanup()
                    command.assert_not_called()
                    self.assertTrue(marker.exists())
                finally:
                    case.doCleanups()

    def test_corrupt_marker_cannot_report_cleanup_success(self):
        roots, units, marker, state, command, inactive = self.owned()
        marker.write_text('{')
        with self.assertRaises(ValueError):
            harness.cleanup()
        self.assertTrue(marker.exists() and roots[0].exists())
        command.assert_not_called()

    def test_unit_dropin_blocks_setup_or_cleanup(self):
        roots, units, marker, state, command, inactive = self.owned()
        state.side_effect = lambda unit: {**inactive, 'FragmentPath': str(units[unit]), 'DropInPaths': '/unexpected.conf'}
        with self.assertRaisesRegex(RuntimeError, 'UNIT_OVERRIDE'):
            harness.cleanup()
        command.assert_not_called()
        self.assertTrue(marker.exists() and roots[0].exists())

    def test_root_fixture_umask_produces_service_readable_request(self):
        import ast
        builder = harness.load('retention_permission_builder', ROOT / 'ops/ci/build_retention.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = harness.load('retention_permission_controller', builder.build(ROOT, root / 'controller.py'))
            # Execute the exact CHILD umask statement, then the real controller
            # writer. This checks actual permissions, not just fixture text.
            statement = next(n for n in ast.parse(harness.CHILD).body if isinstance(n, ast.Expr)
                             and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                             and n.value.func.attr == 'umask')
            old = os.umask(0o077)
            try:
                exec(compile(ast.Module(body=[statement], type_ignores=[]), 'fixture-umask', 'exec'), {'os': os})
                controller.write_json(root / 'request.json', {'synthetic': True})
            finally:
                os.umask(old)
            self.assertEqual((root / 'request.json').stat().st_mode & 0o777, 0o644)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_foreign_marker_collision_cannot_be_adopted_by_always_cleanup(self):
        roots, units, marker, state, command, inactive = self.owned()
        ledger = json.loads(marker.read_text())
        ledger['invocation'] = ['999', '1']
        marker.write_text(json.dumps(ledger))
        with self.assertRaisesRegex(RuntimeError, 'MARKER_COLLISION'):
            harness.claim()
        with self.assertRaisesRegex(RuntimeError, 'FOREIGN_MARKER'):
            harness.cleanup()
        command.assert_not_called()
        self.assertTrue(marker.exists() and roots[0].exists())
        self.assertTrue(all(path.exists() for path in units.values()))

    def test_invocation_requires_run_and_attempt_from_fixed_workflow_environment(self):
        for values in ({}, {'GITHUB_RUN_ID': '1'}, {'GITHUB_RUN_ID': '../private', 'GITHUB_RUN_ATTEMPT': '1'}):
            with patch.dict(os.environ, values, clear=True), self.assertRaisesRegex(RuntimeError, 'INVOCATION_REQUIRED'):
                harness.invocation()
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2'}, clear=True):
            self.assertEqual(harness.invocation(), ['123', '2'])
