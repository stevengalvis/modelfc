import copy
import unittest

from ops.bounded_worker import mount_visibility_prototype as model


def mount(identity, path, options='rw', kind='ext4'):
    return {'id': identity, 'path': path, 'root': '/', 'device': '8:1',
            'options': [options], 'type': kind, 'super_options': ['rw']}


class VisibilityModelTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = [mount(1, '/', 'ro'), mount(2, '/dev'), mount(3, '/dev', 'ro'),
                         mount(4, '/dev/pts', kind='devpts'), mount(5, '/dev/hidden'),
                         mount(6, '/alias'), mount(7, '/data')]
        self.observed = {'/': 1, '/dev': 3, '/dev/pts': 4, '/dev/hidden': 3, '/alias': 6, '/data': 7}

    def test_stack_and_covered_descendant_are_distinguished_from_alias(self):
        visible = model.visible(self.snapshot, copy.deepcopy(self.snapshot), self.observed)
        self.assertEqual({m['id'] for m in visible}, {1, 3, 4, 6, 7})
        self.assertEqual(next(m for m in visible if m['id'] == 6)['options'], ['rw'])
        # A visible writable alias remains in the result even if another path is covered.
        self.assertNotIn(2, {m['id'] for m in visible})

    def test_unresolved_unknown_and_inconsistent_ids_fail_closed(self):
        for change in ({'/dev': None}, {'/dev': 999}, {'/dev': 7}, {'/dev/hidden': 2},
                       {'/dev/pts': '4'}):
            with self.subTest(change=change), self.assertRaises(model.VisibilityBlocked):
                model.visible(self.snapshot, self.snapshot, {**self.observed, **change})
        missing = dict(self.observed)
        del missing['/dev']
        with self.assertRaises(model.VisibilityBlocked):
            model.visible(self.snapshot, self.snapshot, missing)

    def test_mount_changes_and_duplicate_ids_block(self):
        for changed in (self.snapshot[:-1], self.snapshot + [mount(8, '/new')],
                        [dict(m, options=['ro']) if m['id'] == 6 else m for m in self.snapshot]):
            with self.subTest(changed=changed), self.assertRaises(model.VisibilityBlocked):
                model.visible(self.snapshot, changed, self.observed)
        with self.assertRaises(model.VisibilityBlocked):
            model.visible(self.snapshot + [mount(2, '/extra')], self.snapshot, self.observed)

    def test_three_stacks_and_covered_ancestor(self):
        entries = [mount(1, '/', 'ro'), mount(2, '/dev'), mount(3, '/dev', 'ro'),
                   mount(4, '/dev', 'ro'), mount(5, '/dev/old/child'), mount(6, '/elsewhere')]
        observed = {'/': 1, '/dev': 4, '/dev/old/child': 4, '/elsewhere': 6}
        self.assertEqual({m['id'] for m in model.visible(entries, entries, observed)}, {1, 4, 6})
        # An inaccessible or unresolved covered child is uncertainty, not proof.
        with self.assertRaises(model.VisibilityBlocked):
            model.visible(entries, entries, dict(observed, **{'/dev/old/child': None}))

    def test_order_of_records_does_not_choose_stack_top(self):
        expected = {m['id'] for m in model.visible(self.snapshot, list(reversed(self.snapshot)), self.observed)}
        self.assertEqual(expected, {1, 3, 4, 6, 7})
        switched = dict(self.observed, **{'/dev': 2, '/dev/hidden': 5})
        result = model.visible(self.snapshot, self.snapshot, switched)
        self.assertEqual({m['id'] for m in result}, {1, 2, 4, 5, 6, 7})

    def test_noncanonical_path_and_invalid_snapshot_block(self):
        for path in ('/x/../dev', '/x//y', '//dev', 'relative', '/invalid\x00path', '/' + 'x' * 4096):
            altered = [*self.snapshot, mount(9, path)]
            with self.subTest(path=path), self.assertRaises(model.VisibilityBlocked):
                model.visible(altered, altered, dict(self.observed, **{path: 9}))
        with self.assertRaises(model.VisibilityBlocked):
            model.visible([], [], {})
        bad_options = [dict(m, options=['rw', 'ro']) if m['id'] == 2 else m for m in self.snapshot]
        with self.assertRaises(model.VisibilityBlocked):
            model.visible(bad_options, bad_options, self.observed)


if __name__ == '__main__':
    unittest.main()
