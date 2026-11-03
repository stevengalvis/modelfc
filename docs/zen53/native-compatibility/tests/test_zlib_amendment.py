"""Exact one-library amendment; no downloaded or executable fixture needed."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import materialize
import zlib_amendment as z
import test_materialize as fixtures
from v4_fixture import complete_synthetic


class ZlibAmendment(unittest.TestCase):
    def inputs(self):
        return (json.loads((ROOT/'historical/pins.json').read_bytes()),
                json.loads((ROOT/'historical/DEPENDENCIES.json').read_bytes()))

    def test_only_one_hash_changes_and_historical_bytes_are_preserved(self):
        pins, deps = self.inputs()
        before_pins, before_deps = copy.deepcopy(pins), copy.deepcopy(deps)
        z.amend(pins, deps)
        self.assertEqual(pins['files'][z.ROUTE], z.NEW)
        self.assertEqual(deps['dependencies'][z.ROUTE]['sha256'], z.NEW)
        pins['files'][z.ROUTE] = z.OLD
        deps['dependencies'][z.ROUTE]['sha256'] = z.OLD
        self.assertEqual(pins, before_pins)
        self.assertEqual(deps, before_deps)
        recovery = json.loads((ROOT/'recovery/RECOVERY.json').read_bytes())
        before = copy.deepcopy(recovery)
        z.amend_recovery(recovery)
        for row in (recovery['manifest'][z.TARGET], recovery['dependencies'][z.ROUTE]):
            self.assertEqual(row['sha256'], z.NEW)
            self.assertEqual(row['historical_source_metadata']['sha256'], z.OLD)
            row['sha256'] = z.OLD
        self.assertEqual(recovery, before)

    def test_wrong_or_already_amended_inputs_do_not_mutate(self):
        for field, bad in (('sha256', z.NEW), ('sha256', '0'*64),
                           ('source', '/tmp/arbitrary'), ('source_uid', 1001),
                           ('source_mode', '0o755'), ('mode', '0o555')):
            pins, deps = self.inputs()
            deps['dependencies'][z.ROUTE][field] = bad
            before = copy.deepcopy((pins,deps))
            with self.subTest(field=field,bad=bad), self.assertRaisesRegex(ValueError,'ZLIB_HISTORICAL_PIN'):
                z.amend(pins,deps)
            self.assertEqual((pins,deps),before)
        pins, deps = self.inputs(); pins['files'][z.ROUTE] = z.NEW
        with self.assertRaisesRegex(ValueError,'ZLIB_HISTORICAL_PIN'): z.amend(pins,deps)

    def test_provenance_tampering_blocks_all_amendment_paths(self):
        pins,deps=self.inputs(); before=copy.deepcopy((pins,deps))
        manifest=json.loads((ROOT/'historical/PROPOSED_ARTIFACT_SHA256.json').read_bytes())
        recovery=json.loads((ROOT/'recovery/RECOVERY.json').read_bytes())
        with patch.object(z.Path,'read_bytes',return_value=b'altered proof'):
            for call in (lambda:z.amend(pins,deps),lambda:z.amend_manifest(manifest),lambda:z.amend_recovery(recovery)):
                with self.assertRaisesRegex(ValueError,'ZLIB_PROVENANCE_PIN'):call()
        self.assertEqual((pins,deps),before)

    def test_bridge_and_materializer_agree_on_new_pin_without_bundled_fallback(self):
        historical={p.name:p.read_bytes() for p in (ROOT/'historical').iterdir()}
        objects={p.name:p.read_bytes() for p in (ROOT/'bridge_objects').iterdir()}
        plan=bridge.build(historical,objects,'/tmp/zen53-synthetic-stage-aaaaaaaaaaaaaaaa',
                          (77,101),(77,102),complete_synthetic())
        manifest=json.loads(plan['files']['PROPOSED_ARTIFACT_SHA256.json'])
        self.assertEqual(manifest[z.TARGET],z.NEW)
        self.assertEqual(json.loads(plan['files']['pins.json'])['files'][z.ROUTE],z.NEW)
        self.assertEqual(json.loads(plan['files']['DEPENDENCIES.json'])['dependencies'][z.ROUTE]['sha256'],z.NEW)
        self.assertNotIn(z.TARGET,plan['overrides'])
        self.assertNotIn(z.NEW,plan['objects'])
        # Exercise actual compiler against the existing synthetic finalizer fixture.
        fixture=fixtures.Materialize();fixture.historical=historical;fixture.objects=objects
        final=fixture.final_fixture()
        bundled={p.name:p.read_bytes() for p in (ROOT/'recovery/objects').iterdir()}
        targets,_=materialize.compile_targets((ROOT/'recovery/RECOVERY.json').read_bytes(),final,bundled)
        self.assertEqual(targets[z.TARGET]['source']['sha256'],z.NEW)
        self.assertEqual(targets[z.TARGET]['source']['resolved_source'],z.ROUTE)
        stale=copy.deepcopy(final)
        records=json.loads(stale['files']['DEPENDENCIES.json']);records['dependencies'][z.ROUTE]['sha256']=z.OLD
        stale['files']['DEPENDENCIES.json']=bridge.json_bytes(records)
        with self.assertRaises(ValueError):materialize.compile_targets((ROOT/'recovery/RECOVERY.json').read_bytes(),stale,bundled)

    def test_stale_manifest_and_recovery_are_rejected(self):
        manifest=json.loads((ROOT/'historical/PROPOSED_ARTIFACT_SHA256.json').read_bytes())
        manifest[z.TARGET]=z.NEW
        with self.assertRaisesRegex(ValueError,'ZLIB_MANIFEST_PIN'):z.amend_manifest(manifest)
        recovery=json.loads((ROOT/'recovery/RECOVERY.json').read_bytes())
        recovery['manifest'][z.TARGET]['sha256']=z.NEW
        with self.assertRaisesRegex(ValueError,'ZLIB_RECOVERY_PIN'):z.amend_recovery(recovery)
