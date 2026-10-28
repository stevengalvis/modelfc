"""Offline checks of the authenticated zlib refresh; no host source reads."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

import restricted_launcher as restricted
import stage_builder as builder


ROOT = Path(__file__).resolve().parent
OLD = '86200da370f20476a2507e9097a789b5ef97269b4ca8d5e164ad82dab9d99892'
NEW = 'f61677c7bdb7c2aa2a243ea781bfd16afcb361cede7b4ef76127a9d8eb9a5242'
ROUTE = '/usr/lib/x86_64-linux-gnu/libz.so.1.3'
TARGET = 'rootfs' + ROUTE


def raw(name):
    return (ROOT / name).read_bytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def proof_ok(record):
    """Check the immutable *documented* package identity, not host state."""
    return (record['installed_package'] == 'zlib1g:amd64' and
            record['installed_version'] == '1:1.3.dfsg-3.1ubuntu2.3' and
            record['package_sha256'] ==
            '376249c8de139568221dbb242318cc8cb8f02299e3a62b7432948e1754fbddf9' and
            record['package_member_sha256'] == NEW and
            record['package_member_bytes'] == 113000 and
            record['historical_sha256'] == OLD)


class ZlibRefreshTests(unittest.TestCase):
    def test_current_plan_and_historical_observation_are_distinct(self):
        proof = json.loads(raw('ZLIB_REFRESH_PROVENANCE.json'))
        self.assertTrue(proof_ok(proof))
        manifest = json.loads(raw('runtime_source_manifest.json'))
        deps = json.loads(raw('runtime_dependencies.json'))['dependencies']
        recovery = json.loads(raw('recovery_contract.json'))
        pins = json.loads(raw('stage_pins_source.json'))['files']
        self.assertEqual((manifest[TARGET], deps[ROUTE]['sha256'],
                          recovery['manifest'][TARGET]['sha256'],
                          recovery['dependencies'][ROUTE]['sha256'],
                          pins[ROUTE]), (NEW,) * 5)
        self.assertEqual(manifest['rootfs/diagnostic/pins.json'],
                         digest(raw('stage_pins_source.json')))
        for row in (recovery['manifest'][TARGET],
                    recovery['dependencies'][ROUTE]):
            self.assertEqual(row['historical_source_metadata']['sha256'], OLD)
        inventory = raw('INVENTORY.json')
        plan = builder.make_plan(
            str(ROOT), '/fixture/recovery', inventory,
            raw('recovery_contract.json'), raw('runtime_source_manifest.json'),
            raw('runtime_dependencies.json'),
            expected_inventory_sha=digest(inventory),
            source_owner_uid=os.geteuid(), source_owner_gid=os.getegid())
        self.assertEqual(plan['regular'][ROUTE]['sha256'], NEW)
        self.assertEqual(plan['regular'][ROUTE]['source'], ROUTE)
        self.assertEqual(plan['regular'][ROUTE]['source_mode'], 0o644)

    def test_old_or_wrong_package_record_rejected(self):
        proof = json.loads(raw('ZLIB_REFRESH_PROVENANCE.json'))
        for key, wrong in (('installed_version',
                            '1:1.3.dfsg-3.1ubuntu2.2'),
                           ('installed_package', 'zlib1g:i386'),
                           ('package_sha256', '0' * 64),
                           ('package_member_sha256', OLD),
                           ('package_member_bytes', 113001)):
            with self.subTest(key=key):
                changed = dict(proof, **{key: wrong})
                self.assertFalse(proof_ok(changed))

    def test_predecessor_and_mutated_manifests_fail_before_copy(self):
        manifest = raw('runtime_source_manifest.json')
        deps = raw('runtime_dependencies.json')
        recovery = raw('recovery_contract.json')
        for target, original in ((manifest, TARGET), (deps, ROUTE)):
            self.assertIn(NEW.encode(), target)
            stale = target.replace(NEW.encode(), OLD.encode(), 1)
            with self.subTest(original=original), self.assertRaisesRegex(
                    ValueError, 'PROJECTION_SOURCE_PINS'):
                restricted.projection_contract(
                    stale if target is manifest else manifest,
                    stale if target is deps else deps)
        stale_recovery = recovery.replace(
            ('      "sha256": "' + NEW + '"').encode(),
            ('      "sha256": "' + OLD + '"').encode(), 1)
        with self.assertRaisesRegex(ValueError, 'BUILD_CONTRACT_PINS'):
            builder.make_plan(str(ROOT), '/fixture/recovery',
                              raw('INVENTORY.json'), stale_recovery,
                              manifest, deps,
                              expected_inventory_sha=digest(raw('INVENTORY.json')))

    def test_unpinned_source_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryFile() as out:
            source = Path(tmp) / 'libz.so.1.3'
            source.write_bytes(b'wrong-version-or-arbitrary-library')
            source.chmod(0o444)
            reader = builder.FileSource(os.geteuid(), root_uid=os.stat('/').st_uid)
            with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_CHANGED'):
                reader.copy(str(source), out.fileno(), NEW, maximum=32_000_000,
                            expected_uid=os.geteuid(),
                            expected_gid=os.getegid(), expected_mode=0o444)


if __name__ == '__main__':
    unittest.main()
