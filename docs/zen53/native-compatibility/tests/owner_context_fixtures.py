"""Synthetic schema fixtures only. These are never current host observations."""
import copy
import owner_context


def complete_synthetic():
    host_ns = {'user_ns': 111, 'mount_ns': 222, 'pid_ns': 555,
               'uid_map': [[0, 0, 4294967295]],
               'gid_map': [[0, 0, 4294967295]],
               'overflow_uid': 65534, 'overflow_gid': 65534,
               'pid1_user_ns': 111, 'pid1_mount_ns': 222, 'pid1_pid_ns': 555}
    restricted_ns = {'user_ns': 333, 'mount_ns': 444, 'pid_ns': 666,
                     'uid_map': [[0, 0, 1]], 'gid_map': [[0, 0, 1]],
                     'overflow_uid': 65534, 'overflow_gid': 65534,
                     'pid1_user_ns': 333, 'pid1_mount_ns': 444,
                     'pid1_pid_ns': 666}
    files = {}
    for i, (target, expected) in enumerate(owner_context.EXPECTED.items()):
        view = {'uid': 1001, 'gid': 1001, 'mode': expected['mode'],
                'size': expected['size'], 'sha256': expected['sha256'],
                'device_inode': expected['device_inode'],
                'named_device_inode': expected['device_inode'],
                'mtime_ns': 101 + i, 'ctime_ns': 201 + i, 'nlink': 1,
                'mount_id': 301, 'mount_unique_id': 701,
                'idmapped': False, 'alias_count': 0}
        projected = copy.deepcopy(view)
        projected.update(uid=65534, gid=65534, mount_id=401,
                         mount_unique_id=801)
        files[target] = {'source': expected['source'], 'sha256': expected['sha256'],
                         'size': expected['size'], 'host': view,
                         'restricted': projected}
    return {'version': 3, 'historical_context': 'UNKNOWN',
            'source_report': owner_context.REPORT_URL,
            'evidence_sha256': 'a' * 64,
            'boot_id': '11111111-1111-1111-1111-111111111111',
            'host_namespace': host_ns,
            'capture_control': {'collector_sha256': owner_context.REVIEWED_COLLECTOR_SHA256,
                                'control_record_sha256': 'c' * 64,
                                'sender_ordering_record_sha256': 'd' * 64,
                                'host_exit_code': 0, 'restricted_exit_code': 0,
                                'held_source_descriptors': True,
                                'finish_acknowledged': True,
                                'finish_code': 'FINISH_ACK',
                                'host_monotonic_ns': [10, 20, 30]},
            'restricted_namespace': restricted_ns, 'files': files}
