"""Offline rejection tests; no fixture is accepted as a host observation."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import owner_context
from owner_context_fixtures import complete_synthetic


class OwnerContext(unittest.TestCase):
    def setUp(self):
        self.raw = complete_synthetic()
        self.old_bytes = (ROOT / 'historical/DEPENDENCIES.json').read_bytes()
        self.deps = json.loads(self.old_bytes)
        self.target = '/opt/codex/bin/codex'

    def check_rejected(self, edit, code=None):
        raw = copy.deepcopy(self.raw)
        edit(raw)
        with self.assertRaisesRegex((ValueError, KeyError, TypeError), code or 'OWNER_'):
            owner_context.validate(raw, self.deps['dependencies'])

    def test_exact_two_only_and_historical_bytes_unchanged(self):
        changed = copy.deepcopy(self.deps)
        digest = owner_context.amend_dependencies(changed, self.raw)
        self.assertEqual(digest, owner_context.validate(self.raw, self.deps['dependencies']))
        self.assertEqual((ROOT / 'historical/DEPENDENCIES.json').read_bytes(), self.old_bytes)
        for target in set(self.deps['dependencies']) - set(owner_context.EXPECTED):
            self.assertEqual(self.deps['dependencies'][target], changed['dependencies'][target])
        for target in owner_context.EXPECTED:
            record = changed['dependencies'][target]
            self.assertEqual((record['source_uid'], record['source_gid']), (1001, 1001))
            self.assertEqual(record['source_owner_observation_sha256'], digest)

    def test_missing_pending_and_contradictory_context_fail_closed(self):
        pending = json.loads((ROOT / 'REPORTED_CONTEXT_PENDING.json').read_text())
        with self.assertRaises(ValueError):
            owner_context.validate(pending, self.deps['dependencies'])
        self.check_rejected(lambda x: x.pop('evidence_sha256'))
        self.check_rejected(lambda x: x.update(evidence_sha256='0' * 64))
        self.check_rejected(lambda x: x.update(historical_context='INITIAL_HOST'))
        self.check_rejected(lambda x: x['host_namespace'].update(uid_map=[[0, 1000, 1]]))
        self.check_rejected(lambda x: x['restricted_namespace'].update(overflow_uid=0))
        self.check_rejected(lambda x: x['host_namespace'].update(pid1_user_ns=333))
        self.check_rejected(lambda x: x['restricted_namespace'].update(mount_ns=222))
        self.check_rejected(lambda x: x.update(version=1))
        self.check_rejected(lambda x: x.update(version=2))
        self.check_rejected(lambda x: x['host_namespace'].update(pid1_mount_ns=444))
        self.check_rejected(lambda x: x['host_namespace'].update(pid1_pid_ns=666))
        self.check_rejected(lambda x: x['restricted_namespace'].update(pid1_user_ns=111))
        self.check_rejected(lambda x: x['restricted_namespace'].update(pid1_mount_ns=222))
        self.check_rejected(lambda x: x['restricted_namespace'].update(pid1_pid_ns=555))
        self.check_rejected(lambda x: x['restricted_namespace'].update(pid_ns=555))

    def test_reported_local_pid1_anchor_is_valid_but_failed_bracket_is_not(self):
        raw = copy.deepcopy(self.raw)
        raw['host_namespace'].update(user_ns=4026531837, mount_ns=4026531841,
                                     pid_ns=4026531836, pid1_user_ns=4026531837,
                                     pid1_mount_ns=4026531841, pid1_pid_ns=4026531836)
        raw['restricted_namespace'].update(user_ns=4026532437, mount_ns=4026532438,
                                           pid_ns=4026532498, pid1_user_ns=4026532437,
                                           pid1_mount_ns=4026532438, pid1_pid_ns=4026532498)
        # Namespace facts are compatible. This remains synthetic and cannot
        # authenticate the capture. The actual report's exit 2 must block.
        self.assertEqual(len(owner_context.validate(raw, self.deps['dependencies'])), 64)
        raw['capture_control']['host_exit_code'] = 2
        raw['capture_control']['finish_acknowledged'] = False
        with self.assertRaisesRegex(ValueError, 'OWNER_CAPTURE_INCOMPLETE'):
            owner_context.validate(raw, self.deps['dependencies'])

    def test_missing_or_incomplete_finish_record_rejected(self):
        changes = [
            lambda x: x.pop('capture_control'),
            lambda x: x['capture_control'].update(host_exit_code=2),
            lambda x: x['capture_control'].update(restricted_exit_code=1),
            lambda x: x['capture_control'].update(finish_acknowledged=False),
            lambda x: x['capture_control'].update(finish_code='FINISH_EOF'),
            lambda x: x['capture_control'].update(held_source_descriptors=False),
            lambda x: x['capture_control'].update(host_monotonic_ns=[10, 20, 20]),
            lambda x: x['capture_control'].update(host_monotonic_ns=[10, 20, 30, 40]),
            lambda x: x['capture_control'].update(host_monotonic_ns=[10, True, 30]),
            lambda x: x['capture_control'].update(control_record_sha256='0' * 64),
            lambda x: x['capture_control'].pop('sender_ordering_record_sha256'),
            lambda x: x['capture_control'].update(collector_sha256='a' * 64),
        ]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                self.check_rejected(change)

    def test_actual_host_owner_or_gid_change_rejected_even_if_projected_same(self):
        for field, value in (('uid', 1002), ('gid', 1002), ('uid', 65534), ('gid', 65534)):
            with self.subTest(field=field, value=value):
                self.check_rejected(lambda x: x['files'][self.target]['host'].update({field: value}))
        self.check_rejected(lambda x: x['files'][self.target]['restricted'].update(uid=1001))

    def test_content_metadata_path_inode_mount_and_alias_rejected(self):
        changes = [
            lambda x: x['files'][self.target].update(source='/tmp/alias'),
            lambda x: x['files'][self.target]['host'].update(sha256='f' * 64),
            lambda x: x['files'][self.target]['host'].update(mode='0o777'),
            lambda x: x['files'][self.target]['host'].update(size=1),
            lambda x: x['files'][self.target]['host'].update(device_inode=[2049, 434466]),
            lambda x: x['files'][self.target]['host'].update(named_device_inode=[2049, 434466]),
            lambda x: x['files'][self.target]['host'].update(mtime_ns=99),
            lambda x: x['files'][self.target]['restricted'].update(ctime_ns=99),
            lambda x: x['files'][self.target]['host'].pop('mount_id'),
            lambda x: x['files'][self.target]['restricted'].update(mount_id=0),
            lambda x: x['files'][self.target]['restricted'].update(mount_unique_id=0),
            lambda x: x['files'][self.target]['host'].update(mount_unique_id=702),
            lambda x: x['files'][self.target]['host'].update(mount_id=302),
            lambda x: x['files'][self.target]['host'].update(idmapped=True),
            lambda x: x['files'][self.target]['restricted'].update(alias_count=1),
            lambda x: x['files'][self.target]['restricted'].update(alias_count=False),
            lambda x: x['files'].pop('/opt/codex/codex-package.json'),
        ]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                self.check_rejected(change)

    def test_json_numeric_type_confusion_rejected(self):
        changes = [
            lambda x: x.update(version=True),
            lambda x: x['host_namespace'].update(uid_map=[[0.0, 0, 4294967295]]),
            lambda x: x['restricted_namespace'].update(gid_map=[[0, False, 1]]),
            lambda x: x['files'][self.target]['host'].update(uid=1001.0),
            lambda x: x['files'][self.target]['restricted'].update(gid=65534.0),
            lambda x: x['files'][self.target]['host'].update(device_inode=[2049.0, 434465]),
            lambda x: x['files'][self.target]['host'].update(named_device_inode=[2049, 434465.0]),
            lambda x: x['files'][self.target].update(size=294415048.0),
            lambda x: x['files'][self.target]['host'].update(size=294415048.0),
        ]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                self.check_rejected(change)

    def test_historical_pin_tampering_rejected(self):
        for field, value in (('source_uid', 1001), ('source_gid', 1001),
                             ('sha256', 'a' * 64), ('mode', '0o444')):
            altered = copy.deepcopy(self.deps)
            altered['dependencies'][self.target][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,
                                                                    'OWNER_HISTORICAL_PIN'):
                owner_context.validate(self.raw, altered['dependencies'])

    def test_bridge_requires_evidence_and_rejects_stale_result(self):
        historical = {p.name: p.read_bytes() for p in (ROOT / 'historical').iterdir() if p.is_file()}
        objects = {p.name: p.read_bytes() for p in (ROOT / 'bridge_objects').iterdir() if p.is_file()}
        from v4_fixture import complete_synthetic as v4_synthetic
        current = v4_synthetic()
        stage = '/tmp/zen53-synthetic-stage-aaaaaaaaaaaaaaaa'
        with self.assertRaises(TypeError):
            bridge.build(historical, objects, stage, (77, 101), (77, 102))
        plan = bridge.build(historical, objects, stage, (77, 101), (77, 102), current)
        bad = copy.deepcopy(current)
        bad['snapshot']['files'][self.target]['host']['uid'] = 1002
        with self.assertRaisesRegex(ValueError, 'OWNER_EXACT_HOST_AND_PROJECTION'):
            bridge.verify_result(plan, historical, objects, stage, (77, 101),
                                 (77, 102), bad)
        stale = copy.deepcopy(current)
        stale['snapshot']['evidence_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'OWNER_BINDING_MISMATCH'):
            bridge.verify_result(plan, historical, objects, stage, (77, 101),
                                 (77, 102), stale)


if __name__ == '__main__':
    unittest.main()
