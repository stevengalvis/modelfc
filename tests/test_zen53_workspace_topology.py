"""Synthetic mount tables and held-descriptor observations; no mounts or native runs."""
import ast
import copy
import io
import os
import stat
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from tests.test_zen53_workspace_generator import WORKSPACE, baseline, load


def row(ident, parent, path, device='8:1', root='/fixture/workspace', options='rw', kind='ext4'):
    return dict(id=ident, parent=parent, destination=path, device=device, root=root,
                options=options, type=kind, source=kind, super='rw', propagation={})


def layered():
    rows = [row(45, 10, '/workspace-fixture', options='ro'),
            row(9, 45, '/workspace-fixture')]
    rows.extend(row(n, 9, '/workspace-fixture/' + name, '0:' + str(n), '/',
                    'ro,nosuid,nodev', 'tmpfs')
                for n, name in zip((80, 22, 4, 70), ('.git', '.agents', '.codex', '.aws')))
    for mask in rows[2:]:
        mask['super'] = 'rw,size=1024k,mode=555,uid=12345,gid=12345'
    rows.append(row(10, 1, '/', device='8:2', root='/rootfs', options='ro'))
    return rows, {10, 9, 80, 22, 4, 70}


class WorkspaceTopology(unittest.TestCase):
    def setUp(self):
        generator = load('workspace_topology_generator', WORKSPACE / 'native_generator.py')
        self.env = dict(os=os, stat=stat)
        exec(generator.COMMON, self.env)

    def test_outer_single_and_inner_exact_stack_ignore_numeric_id_order(self):
        outer = row(5, 10, '/workspace-fixture')
        self.assertEqual(self.env['workspace_topology']([outer], {5}), (outer, []))
        rows, visible = layered()
        actual, masks = self.env['workspace_topology'](list(reversed(rows)), visible, True)
        self.assertEqual(actual['id'], 9)
        self.assertEqual({m['id'] for m in masks}, visible - {9, 10})
        for inner, table, seen in ((True, [outer], {5}), (False, rows, visible)):
            with self.assertRaises(RuntimeError):
                self.env['workspace_topology'](table, seen, inner)

    def test_generated_parser_visibility_and_layered_mounts_together(self):
        generator = load('workspace_topology_complete_generator', WORKSPACE / 'native_generator.py')
        source = generator.generate(baseline())['report.py'].decode('ascii')
        names = {'need', 'mount_path', 'beneath', 'parse_mounts', 'mount_visibility'}
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<generated-mount-parser>', 'exec'), self.env)
        rows, expected_visible = layered()
        raw = ''.join("{id} {parent} {device} {root} {destination} {options} - {type} {source} {super}\n".format(**m)
                      for m in reversed(rows))
        parsed = self.env['parse_mounts'](raw)
        visible, hidden = self.env['mount_visibility'](parsed)
        self.assertEqual(visible, expected_visible)
        self.assertEqual(hidden, {45: 9})
        actual, masks = self.env['workspace_topology'](parsed, visible, True)
        self.assertEqual(actual['id'], 9)
        self.assertEqual(len(masks), 4)

    def test_layer_and_mask_adversarial_tables(self):
        for case in ('missing', 'extra', 'nested', 'wrong_parent', 'covered_rw', 'covered_device',
                     'covered_root', 'covered_visible', 'visible_ro', 'propagation', 'mask_rw',
                     'mask_suid', 'mask_device', 'mask_root', 'mask_kind', 'mask_hidden',
                     'mask_alias', 'workspace_alias', 'stack_extra', 'duplicate_mask', 'parent_root', 'root_rw', 'unknown_option',
                     'contradictory_atime', 'source', 'super_mode'):
            rows, visible = layered()
            if case == 'missing': rows.pop(2)
            elif case == 'extra': rows.append(row(100, 9, '/workspace-fixture/other'))
            elif case == 'nested': rows.append(row(100, 80, '/workspace-fixture/.git/child'))
            elif case == 'wrong_parent': rows[1]['parent'] = 10
            elif case == 'covered_rw': rows[0]['options'] = 'rw'
            elif case == 'covered_device': rows[0]['device'] = '8:2'
            elif case == 'covered_root': rows[0]['root'] = '/different'
            elif case == 'covered_visible': visible.add(45)
            elif case == 'visible_ro': rows[1]['options'] = 'ro'
            elif case == 'propagation': rows[2]['propagation'] = {'shared': '99'}
            elif case == 'mask_rw': rows[2]['options'] = 'rw,nosuid,nodev'
            elif case == 'mask_suid': rows[2]['options'] = 'ro,nodev'
            elif case == 'mask_device': rows[2]['device'] = rows[3]['device']
            elif case == 'mask_root': rows[2]['root'] = '/subtree'
            elif case == 'mask_kind': rows[2]['type'] = 'ext4'
            elif case == 'mask_hidden': visible.remove(80)
            elif case == 'mask_alias': rows.append(row(100, 10, '/alias', rows[2]['device'], '/', kind='tmpfs'))
            elif case == 'workspace_alias': rows.append(row(100, 10, '/alias', root='/'))
            elif case == 'stack_extra': rows.append(row(100, 9, '/workspace-fixture', options='ro'))
            elif case == 'duplicate_mask': rows[2]['destination'] = rows[3]['destination']
            elif case == 'parent_root': rows[0]['parent'] = 80
            elif case == 'root_rw': rows[-1]['options'] = 'rw'
            elif case == 'unknown_option': rows[2]['options'] += ',unexpected'
            elif case == 'contradictory_atime': rows[2]['options'] += ',noatime,relatime'
            elif case == 'source': rows[2]['source'] = 'other'
            else: rows[2]['super'] = 'rw,mode=755'
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                self.env['workspace_topology'](rows, visible, True)

    def mask_io(self):
        mask = layered()[0][2]
        info = NS(st_dev=os.makedev(0, 80), st_ino=123, st_mode=stat.S_IFDIR | 0o555,
                  st_uid=7, st_gid=8)
        fake = NS(**{name: getattr(os, name) for name in
                     ('O_RDONLY', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC',
                      'ST_RDONLY', 'ST_NOSUID', 'ST_NODEV', 'major', 'minor')})
        fake.open = Mock(return_value=21)
        fake.fstat = Mock(return_value=info)
        fake.stat = Mock(return_value=copy.copy(info))
        fake.fstatvfs = Mock(return_value=NS(f_flag=os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV))
        fake.listdir = Mock(return_value=[])
        fake.close = Mock()
        self.env.update(os=fake, open=lambda *a, **kw: io.StringIO('mnt_id:\t80\n'))
        return mask, info, fake

    def test_mask_held_identity_vfs_and_empty_directory(self):
        mask, _, fake = self.mask_io()
        self.env['workspace_masks']([mask], (7, 8))
        fake.close.assert_called_once_with(21)
        fake.listdir.assert_called_once_with(21)
        self.assertEqual(fake.open.call_args.args[0], '/workspace-fixture/.git')
        self.assertTrue(fake.open.call_args.args[1] & os.O_NOFOLLOW)

    def test_mask_correspondence_failures_close_owned_fd(self):
        for case in ('owner', 'mode', 'device', 'type', 'writable', 'suid', 'entries',
                     'fdinfo', 'named', 'held', 'read_error'):
            mask, info, fake = self.mask_io()
            if case == 'owner': info.st_uid = 9
            elif case == 'mode': info.st_mode = stat.S_IFDIR | 0o755
            elif case == 'device': info.st_dev = os.makedev(0, 90)
            elif case == 'type': info.st_mode = stat.S_IFREG | 0o555
            elif case == 'writable': fake.fstatvfs.return_value.f_flag &= ~os.ST_RDONLY
            elif case == 'suid': fake.fstatvfs.return_value.f_flag &= ~os.ST_NOSUID
            elif case == 'entries': fake.listdir.return_value = ['unexpected']
            elif case == 'fdinfo': self.env['open'] = lambda *a, **kw: io.StringIO('mnt_id: 90\n')
            elif case == 'named': fake.stat.return_value.st_ino += 1
            elif case == 'held':
                after = copy.copy(info)
                after.st_ino += 1
                fake.fstat.side_effect = [info, after]
            else: fake.listdir.side_effect = OSError('synthetic')
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                self.env['workspace_masks']([mask], (7, 8))
            fake.close.assert_called_once_with(21)
