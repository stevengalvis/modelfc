"""Pure bridge boundary regression; no genuine capture or native process."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import owner_binding_v4
import owner_context_v4
from v4_fixture import complete_synthetic


class Integration(unittest.TestCase):
    def setUp(self):
        self.historical = {p.name: p.read_bytes() for p in (ROOT / 'historical').iterdir()}
        self.objects = {p.name: p.read_bytes() for p in (ROOT / 'bridge_objects').iterdir()}
        self.deps = json.loads(self.historical['DEPENDENCIES.json'])
        self.evidence = complete_synthetic()
        self.args = (self.historical, self.objects,
                     '/tmp/zen53-synthetic-stage-aaaaaaaaaaaaaaaa', (77, 101), (77, 102))

    def test_amendment_changes_only_two_source_observations(self):
        before = copy.deepcopy(self.deps)
        evidence_before = copy.deepcopy(self.evidence)
        digest = owner_binding_v4.amend_dependencies(self.deps, self.evidence)
        self.assertEqual(digest, self.evidence['reviewed_snapshot_sha256'])
        self.assertEqual(evidence_before, self.evidence)
        self.assertEqual(set(self.deps), set(before))
        for key in before:
            if key != 'dependencies':
                self.assertEqual(self.deps[key], before[key])
        self.assertEqual(set(self.deps['dependencies']), set(before['dependencies']))
        allowed = {'source_uid', 'source_gid', 'source_owner_namespace',
                   'source_owner_observation_sha256', 'source_device_inode', 'source_mount_id'}
        for target, old in before['dependencies'].items():
            new = self.deps['dependencies'][target]
            if target not in owner_context_v4.EXPECTED:
                self.assertEqual(new, old)
                continue
            self.assertEqual({k: v for k, v in old.items() if k not in allowed},
                             {k: v for k, v in new.items() if k not in allowed})
            self.assertEqual((new['source_uid'], new['source_gid']), (1001, 1001))
            self.assertEqual(new['source_owner_observation_sha256'], digest)

    def test_missing_extra_wrong_or_unbound_evidence_never_amends(self):
        bads = [None, {}, self.evidence['snapshot'],
                dict(self.evidence, extra=True),
                dict(self.evidence, reviewed_snapshot_sha256=None),
                dict(self.evidence, reviewed_snapshot_sha256='0' * 64)]
        changed = copy.deepcopy(self.evidence)
        changed['snapshot']['evidence_sha256'] = 'a' * 64
        bads.append(changed)
        for bad in bads:
            with self.subTest(bad=str(bad)[:60]):
                before = copy.deepcopy(self.deps)
                with self.assertRaises(ValueError):
                    owner_binding_v4.amend_dependencies(self.deps, bad)
                self.assertEqual(self.deps, before)

    def test_rebound_invalid_snapshot_still_fails_validator(self):
        for kind in ('version', 'owner', 'writable', 'alias', 'identity'):
            bad = copy.deepcopy(self.evidence)
            raw = bad['snapshot']
            target = next(iter(raw['files']))
            if kind == 'version': raw['version'] = 3
            elif kind == 'owner': raw['files'][target]['host']['uid'] = 1002
            elif kind == 'writable': raw['files'][target]['restricted']['mount_read_only'] = False
            elif kind == 'alias': raw['files'][target]['restricted']['alias_count'] = 1
            else: raw['files'][target]['host']['device_inode'][1] += 1
            bad['reviewed_snapshot_sha256'] = hashlib.sha256(owner_context_v4.canonical(raw)).hexdigest()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                bridge.build(*self.args, bad)

    def test_finalizer_rejects_changed_binding_before_any_stage_read(self):
        plan = bridge.build(*self.args, self.evidence)
        bad = copy.deepcopy(self.evidence)
        bad['snapshot']['evidence_sha256'] = 'a' * 64
        with patch.object(bridge, 'verify_stage_objects_binding') as read:
            with self.assertRaisesRegex(ValueError, 'OWNER_BINDING_MISMATCH'):
                bridge.finalize(plan, *self.args, bad, 7)
            read.assert_not_called()

    def test_preparation_plan_preserves_runtime_policy_bytes(self):
        historical_before = copy.deepcopy(self.historical)
        plan = bridge.build(*self.args, self.evidence)
        self.assertEqual(self.historical, historical_before)
        self.assertEqual(plan['files']['requirements.toml'], self.historical['requirements.toml'])
        self.assertEqual(plan['files']['diagnostic.service'].replace(
            self.args[2].encode(), bridge.OLD_STAGE.encode()), self.historical['diagnostic.service'])
        self.assertEqual(plan['files']['report.py'], self.objects[bridge.NEW_REPORT])
        self.assertEqual(plan['files']['supervisor.py'], self.objects[bridge.NEW_SUPERVISOR])
        changed = bridge.patch_launcher(self.historical['launch.py'], [77, 101], [77, 102])
        self.assertEqual(plan['files']['launch.py'], changed)
        deps = json.loads(plan['files']['DEPENDENCIES.json'])
        self.assertEqual(deps['symlinks'], self.deps['symlinks'])
        for target in set(self.deps['dependencies']) - set(bridge.BUNDLED) - set(owner_context_v4.EXPECTED) - {bridge.zlib_amendment.ROUTE}:
            self.assertEqual(deps['dependencies'][target], self.deps['dependencies'][target])
