"""Synthetic collector records; never execute owner collector or Bubblewrap."""
import copy
import hashlib
import json
import os
import pathlib
import tempfile
import unittest

import evidence
import owner_adapter
import owner_context_v4 as contract


def raw(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def sh(data):
    return hashlib.sha256(data).hexdigest()


def context(user, mnt, pid, uid_map):
    ns = {'self/ns/user': f'user:[{user}]', 'self/ns/mnt': f'mnt:[{mnt}]',
          'self/ns/pid': f'pid:[{pid}]', '1/ns/user': f'user:[{user}]',
          '1/ns/mnt': f'mnt:[{mnt}]', '1/ns/pid': f'pid:[{pid}]'}
    return {'namespace_links': ns, 'uid_map_raw': uid_map,
            'gid_map_raw': uid_map, 'overflow_uid_raw': '65534\n',
            'overflow_gid_raw': '65534\n',
            'boot_id_raw': '11111111-1111-1111-1111-111111111111\n'}


def capture(label, uid, mount_id, unique_id, ns):
    files = {}
    for index, (target, expected) in enumerate(contract.EXPECTED.items()):
        meta = {'uid': uid, 'gid': uid, 'mode': expected['mode'],
                'size': expected['size'], 'device': expected['device_inode'][0],
                'inode': expected['device_inode'][1],
                'mtime_ns': 101+index, 'ctime_ns': 201+index,
                'atime_ns': 301+index, 'nlink': 1}
        binding = {'descriptor': meta, 'named': copy.deepcopy(meta)}
        source = expected['source']
        file_bind = label == 'restricted'
        local_id, local_unique = (mount_id + index, unique_id + index) if file_bind else (mount_id, unique_id)
        mount = {'mount_id': local_id, 'idmapped': False,
                 'mountinfo_row': {'id': local_id,
                                   'options': ['ro'] if file_bind else ['rw'],
                                   'mountpoint': source if file_bind else '/',
                                   'root': source if file_bind else '/'},
                 'statx': {'unique_mount_id': local_unique},
                 'statmount': {'unique_mount_id': local_unique,
                               'mount_id_old': local_id,
                               'mount_attributes': 1 if file_bind else 0,
                               'returned_mask': 2}}
        files[target] = {'source': expected['source'],
                         'expected_size': expected['size'],
                         'expected_sha256': expected['sha256'],
                         'bytes_hashed': expected['size'],
                         'sha256': expected['sha256'],
                         'stable_descriptor_and_named_metadata': True,
                         'stable_mount_observation': True,
                         'binding_before': binding,
                         'binding_after': copy.deepcopy(binding),
                         'mount_before': mount,
                         'mount_after': copy.deepcopy(mount),
                         'aliases': {'alias_count': 0}}
    result = {'context_label': label, 'blockers': [], 'files': files,
              'context_before': ns, 'context_after': copy.deepcopy(ns),
              'mountinfo_before': {'sha256': 'a'*64},
              'mountinfo_after': {'sha256': 'a'*64}}
    if label == 'host-held':
        result.update(host_ready_monotonic_ns=10,
                      finish_control={'code': 'FINISH_ACK',
                                      'finish_received_monotonic_ns': 20},
                      held_descriptors_closed_monotonic_ns=30)
    return result


def fixture():
    host = capture('host-held', 1001, 301, 701,
                   context(111, 222, 555, '0 0 4294967295\n'))
    restricted = capture('restricted', 65534, 401, 801,
                         context(333, 444, 666, '0 0 1\n'))
    host_bytes, restricted_bytes = raw(host), raw(restricted)
    lines = [
        {'event': 'HOST_DESCRIPTORS_HELD',
         'host_ready_monotonic_ns': 10},
        {'event': 'HOST_FINISH_ACK',
         'finish_received_monotonic_ns': 20},
        {'event': 'HOST_CAPTURE_COMPLETE',
         'sha256': sh(host_bytes), 'blockers': []},
    ]
    stdout = b''.join(raw(line) for line in lines)
    streams = {}
    for name, data in {'host_stdout': stdout,
                       'restricted_stdout': restricted_bytes}.items():
        streams[name] = {'raw': data, 'sha256': sh(data), 'bytes': len(data)}
    events = [
        {'event': 'HOST_READY', 'at_ns': 100, 'child_monotonic_ns': 10},
        {'event': 'RESTRICTED_EXIT', 'at_ns': 200, 'exit': 0},
        {'event': 'FINISH_WRITTEN', 'at_ns': 300, 'bytes': 7,
         'sha256': sh(b'finish\n')},
        {'event': 'HOST_ACK', 'at_ns': 400, 'child_monotonic_ns': 20},
        {'event': 'HOST_COMPLETE', 'at_ns': 500,
         'capture_sha256': sh(host_bytes)},
    ]
    controller = {'outcome': 'PROTOCOL_COMPLETE', 'reason': 'NONE',
                  'host_exit': 0, 'restricted_exit': 0,
                  'streams': streams, 'events': events}
    marker = {'meaning': 'DURABLE_RECORD_ONLY_NOT_ACCEPTANCE',
              'summary_sha256': 'd'*64,
              'files': {name+'.bin': {'bytes': value['bytes'],
                                     'sha256': value['sha256']}
                        for name, value in streams.items()}}
    deps = {}
    for target, expected in contract.EXPECTED.items():
        deps[target] = {
            'source': expected['source'], 'resolved_source': expected['source'],
            'sha256': expected['sha256'], 'mode':
            '0o555' if target.endswith('/bin/codex') else '0o444',
            'source_uid': 65534, 'source_gid': 65534,
            'source_mode': expected['mode']}
    return host_bytes, controller, marker, deps


def rebind_host(host, controller, marker):
    host_bytes = raw(host)
    lines = [json.loads(line) for line in
             controller['streams']['host_stdout']['raw'].splitlines()]
    lines[2]['sha256'] = sh(host_bytes)
    stdout = b''.join(raw(line) for line in lines)
    controller['streams']['host_stdout'].update(
        raw=stdout, sha256=sh(stdout), bytes=len(stdout))
    controller['events'][4]['capture_sha256'] = sh(host_bytes)
    marker['files']['host_stdout.bin'] = {
        'bytes': len(stdout), 'sha256': sh(stdout)}
    return host_bytes


class AdapterTests(unittest.TestCase):
    def test_two_file_mount_contract_and_source_drift(self):
        host_bytes, controller, marker, deps = fixture()
        snapshot, _ = owner_adapter.build(host_bytes, controller, marker, deps)
        targets = list(contract.EXPECTED)
        mutations = (
            lambda s: s['files'][targets[1]]['restricted'].update(
                mount_id=s['files'][targets[0]]['restricted']['mount_id']),
            lambda s: s['files'][targets[1]]['restricted'].update(
                mount_unique_id=s['files'][targets[0]]['restricted']['mount_unique_id']),
            lambda s: s['files'][targets[0]]['restricted'].update(
                mount_read_only=False),
            lambda s: s['files'][targets[0]]['restricted'].update(
                mountpoint='/unreviewed'),
            lambda s: s['files'][targets[0]]['restricted'].update(
                mount_root='/unreviewed'),
            lambda s: s['files'][targets[0]]['restricted'].update(
                device_inode=[2049, 123]),
            lambda s: s['files'][targets[0]]['host'].update(uid=1000),
            lambda s: s['files'][targets[0]]['restricted'].update(alias_count=1),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(snapshot)
            mutate(candidate)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                contract.validate(candidate, deps)

    def test_adapter_rejects_rw_or_wrong_file_bind(self):
        source = capture('restricted', 65534, 401, 801,
                         context(333, 444, 666, '0 0 1\n'))
        target = next(iter(contract.EXPECTED))
        for change in (
            lambda row: row['options'].__setitem__(0, 'rw'),
            lambda row: row.update(mountpoint='/other'),
            lambda row: row.update(root='/other'),
        ):
            record = copy.deepcopy(source)
            row = record['files'][target]['mount_before']['mountinfo_row']
            change(row)
            record['files'][target]['mount_after'] = copy.deepcopy(
                record['files'][target]['mount_before'])
            with self.assertRaisesRegex(ValueError, 'OWNER_FILE_BIND'):
                owner_adapter.view(record, target, restricted=True)

    def test_complete_synthetic_path(self):
        evidence = fixture()
        snapshot, digest = owner_adapter.build(*evidence)
        self.assertEqual(contract.validate(snapshot, evidence[3]), digest)
        self.assertEqual(snapshot['capture_control']['host_monotonic_ns'],
                         [10, 20, 30])

    def test_durable_synthetic_record_to_validator(self):
        host_bytes, controller, _, deps = fixture()
        for name in ('host_stderr', 'restricted_stderr'):
            controller['streams'][name] = {'raw': b'', 'bytes': 0,
                                           'sha256': sh(b'')}
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)/'record'
            folder.mkdir(mode=0o700)
            kwargs = {'required_uid': os.geteuid(),
                      'required_gid': os.getegid(),
                      'root_uid': os.stat('/').st_uid}
            evidence.write_evidence(str(folder), controller, **kwargs)
            loaded, marker = evidence.load_evidence(str(folder), **kwargs)
            snapshot, digest = owner_adapter.build(
                host_bytes, loaded, marker, deps)
            self.assertEqual(contract.validate(snapshot, deps), digest)

    def test_fail_closed_mutations(self):
        for mutate in (
            lambda h, c, m, d: c['events'][2].update(bytes=6),
            lambda h, c, m, d: c['events'][3].update(at_ns=150),
            lambda h, c, m, d: c.update(host_exit=2),
            lambda h, c, m, d: m.update(summary_sha256=''),
            lambda h, c, m, d: c['streams']['host_stdout'].update(sha256='0'*64),
        ):
            host, controller, marker, deps = fixture()
            mutate(host, controller, marker, deps)
            with self.assertRaises((ValueError, KeyError)):
                owner_adapter.build(host, controller, marker, deps)
        for change in (
            lambda h: h['files']['/opt/codex/bin/codex']
                      ['binding_before']['descriptor'].update(uid=1000),
            lambda h: h['files']['/opt/codex/bin/codex']
                      ['aliases'].update(alias_count=1),
            lambda h: h['files']['/opt/codex/bin/codex']
                      ['mount_before'].update(mount_id=302),
            lambda h: h['context_before'].update(uid_map_raw='0 0 2\n'),
            lambda h: h['finish_control'].update(code='FINISH_EOF'),
            lambda h: (
                h['context_before']['namespace_links'].update(
                    {'self/ns/user': 'mnt:[111]'}),
                h['context_after']['namespace_links'].update(
                    {'self/ns/user': 'mnt:[111]'})),
            lambda h: (
                h['context_before']['namespace_links'].update(
                    {'1/ns/pid': 'user:[555]'}),
                h['context_after']['namespace_links'].update(
                    {'1/ns/pid': 'user:[555]'})),
        ):
            host_bytes, controller, marker, deps = fixture()
            host = json.loads(host_bytes)
            change(host)
            with self.assertRaises((ValueError, KeyError)):
                owner_adapter.build(rebind_host(host, controller, marker),
                                    controller, marker, deps)


if __name__ == '__main__':
    unittest.main()
