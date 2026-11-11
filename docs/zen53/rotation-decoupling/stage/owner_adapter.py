"""Pure conversion of verified v3 collector records into the pinned validator.

This checks internal consistency, never authenticates the collector process,
Bubblewrap, host filesystem or source of the supplied raw bytes.
"""
import hashlib
import json
import re

import owner_context_v4 as contract

MAX_CAPTURE = 8 * 1024 * 1024
CONTEXT_KEYS = ('namespace_links', 'uid_map_raw', 'gid_map_raw',
                'overflow_uid_raw', 'overflow_gid_raw', 'boot_id_raw')
EVENTS = ('HOST_READY', 'RESTRICTED_EXIT', 'FINISH_WRITTEN',
          'HOST_ACK', 'HOST_COMPLETE')


def need(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw, code):
    need(type(raw) is bytes and len(raw) <= MAX_CAPTURE, code)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError(code) from None
    need(type(value) is dict, code)
    return value


def number(raw):
    need(type(raw) is str and re.fullmatch(r'[0-9]+\n?', raw) is not None,
         'OWNER_RAW_NUMBER')
    return int(raw)


def link(raw, kind):
    need(type(raw) is str and
         re.fullmatch(re.escape(kind) + r':\[([0-9]+)\]', raw),
         'OWNER_NS_LINK')
    return int(raw[raw.index('[')+1:-1])


def mapping(raw):
    need(type(raw) is str, 'OWNER_RAW_MAP')
    rows = raw.strip().splitlines()
    need(len(rows) == 1, 'OWNER_RAW_MAP')
    parts = rows[0].split()
    need(len(parts) == 3 and all(part.isdecimal() for part in parts),
         'OWNER_RAW_MAP')
    return [list(map(int, parts))]


def namespace(capture):
    before, after = capture['context_before'], capture['context_after']
    need(all(before[key] == after[key] for key in CONTEXT_KEYS) and
         capture['mountinfo_before']['sha256'] ==
         capture['mountinfo_after']['sha256'], 'OWNER_CONTEXT_CHANGED')
    links = before['namespace_links']
    need(set(links) == {'self/ns/user', 'self/ns/mnt', 'self/ns/pid',
                        '1/ns/user', '1/ns/mnt', '1/ns/pid'},
         'OWNER_NS_LINKS')
    return {
        'user_ns': link(links['self/ns/user'], 'user'),
        'mount_ns': link(links['self/ns/mnt'], 'mnt'),
        'pid_ns': link(links['self/ns/pid'], 'pid'),
        'pid1_user_ns': link(links['1/ns/user'], 'user'),
        'pid1_mount_ns': link(links['1/ns/mnt'], 'mnt'),
        'pid1_pid_ns': link(links['1/ns/pid'], 'pid'),
        'uid_map': mapping(before['uid_map_raw']),
        'gid_map': mapping(before['gid_map_raw']),
        'overflow_uid': number(before['overflow_uid_raw']),
        'overflow_gid': number(before['overflow_gid_raw']),
    }


def view(capture, target, *, restricted):
    expected = contract.EXPECTED[target]
    record = capture['files'][target]
    need(record['source'] == expected['source'] and
         record['expected_size'] == expected['size'] and
         record['expected_sha256'] == expected['sha256'] and
         record['bytes_hashed'] == expected['size'] and
         record['sha256'] == expected['sha256'] and
         'read_error' not in record and
         record['stable_descriptor_and_named_metadata'] is True and
         record['stable_mount_observation'] is True and
         record['binding_before']['descriptor'] ==
         record['binding_after']['descriptor'] and
         record['binding_before']['named'] ==
         record['binding_after']['named'] and
         all(record['mount_before'][key] == record['mount_after'][key]
             for key in ('mount_id', 'mountinfo_row', 'statx',
                         'statmount', 'idmapped')) and
         record['aliases']['alias_count'] == 0,
         'OWNER_CAPTURE_SOURCE')
    binding = record['binding_before']
    descriptor, named = binding['descriptor'], binding['named']
    need(descriptor == named, 'OWNER_CAPTURE_BINDING')
    mount = record['mount_before']
    statx = mount['statx']
    statmount = mount['statmount']
    row = mount['mountinfo_row']
    need(mount['idmapped'] is False and
         type(mount['mount_id']) is int and mount['mount_id'] > 0 and
         type(statx.get('unique_mount_id')) is int and
         type(statmount.get('unique_mount_id')) is int and
         statx['unique_mount_id'] == statmount['unique_mount_id'] and
         statmount.get('mount_id_old') == mount['mount_id'] and
         type(statmount.get('returned_mask')) is int and
         statmount['returned_mask'] & 2,
         'OWNER_CAPTURE_MOUNT')
    need(type(row) is dict and type(row.get('options')) is list and
         type(row.get('mountpoint')) is str and type(row.get('root')) is str and
         type(statmount.get('mount_attributes')) is int,
         'OWNER_MOUNT_TOPOLOGY')
    readonly = 'ro' in row['options'] and bool(statmount['mount_attributes'] & 1)
    if restricted:
        need(readonly and row['mountpoint'] == expected['source'] and
             row['root'] == expected['source'], 'OWNER_FILE_BIND')
    else:
        need(row['mountpoint'] != expected['source'], 'OWNER_HOST_MOUNT')
    return {
        'uid': descriptor['uid'], 'gid': descriptor['gid'],
        'mode': descriptor['mode'], 'size': descriptor['size'],
        'sha256': record['sha256'],
        'device_inode': [descriptor['device'], descriptor['inode']],
        'named_device_inode': [named['device'], named['inode']],
        'mtime_ns': descriptor['mtime_ns'], 'ctime_ns': descriptor['ctime_ns'],
        'nlink': descriptor['nlink'], 'mount_id': mount['mount_id'],
        'mount_unique_id': statx['unique_mount_id'],
        'idmapped': mount['idmapped'],
        'mount_read_only': readonly,
        'mountpoint': row['mountpoint'],
        'mount_root': row['root'],
        'alias_count': record['aliases']['alias_count'],
    }


def build(host_capture_bytes, controller_record, marker,
          historical_dependencies):
    """Return (schema snapshot, digest) or reject; caller authenticates inputs."""
    need(type(controller_record) is dict and
         controller_record.get('outcome') == 'PROTOCOL_COMPLETE' and
         controller_record.get('reason') == 'NONE' and
         controller_record.get('host_exit') == 0 and
         controller_record.get('restricted_exit') == 0 and
         type(marker) is dict and marker.get('meaning') ==
         'DURABLE_RECORD_ONLY_NOT_ACCEPTANCE', 'OWNER_CONTROL_BLOCKED')
    streams = controller_record['streams']
    for name in ('host_stdout', 'restricted_stdout'):
        stream = streams[name]
        need(type(stream['raw']) is bytes and
             sha(stream['raw']) == stream['sha256'] and
             marker['files'][name + '.bin'] ==
             {'bytes': len(stream['raw']), 'sha256': stream['sha256']},
             'OWNER_STREAM_BINDING')
    events = controller_record['events']
    need(type(events) is list and [row['event'] for row in events] == list(EVENTS) and
         all(type(row['at_ns']) is int for row in events) and
         all(a['at_ns'] < b['at_ns'] for a, b in zip(events, events[1:])) and
         events[1]['exit'] == 0 and events[2]['bytes'] == 7 and
         events[2]['sha256'] == sha(b'finish\n'),
         'OWNER_SENDER_ORDER')
    host_lines = streams['host_stdout']['raw'].splitlines()
    need(len(host_lines) == 3, 'OWNER_HOST_EVENTS')
    host_events = [parse(line, 'OWNER_HOST_EVENT') for line in host_lines]
    need([row.get('event') for row in host_events] ==
         ['HOST_DESCRIPTORS_HELD', 'HOST_FINISH_ACK', 'HOST_CAPTURE_COMPLETE'] and
         host_events[0]['host_ready_monotonic_ns'] ==
         events[0]['child_monotonic_ns'] and
         host_events[1]['finish_received_monotonic_ns'] ==
         events[3]['child_monotonic_ns'] and
         host_events[2]['sha256'] == events[4]['capture_sha256'] ==
         sha(host_capture_bytes) and
         host_events[2]['blockers'] == [],
         'OWNER_HOST_EVENT_BINDING')
    host = parse(host_capture_bytes, 'OWNER_HOST_CAPTURE')
    restricted = parse(streams['restricted_stdout']['raw'],
                       'OWNER_RESTRICTED_CAPTURE')
    need(host.get('context_label') == 'host-held' and
         restricted.get('context_label') == 'restricted' and
         host.get('blockers') == restricted.get('blockers') == [] and
         host['finish_control']['code'] == 'FINISH_ACK' and
         host['host_ready_monotonic_ns'] ==
         host_events[0]['host_ready_monotonic_ns'] and
         host['finish_control']['finish_received_monotonic_ns'] ==
         host_events[1]['finish_received_monotonic_ns'] and
         host['host_ready_monotonic_ns'] <
         host['finish_control']['finish_received_monotonic_ns'] <
         host['held_descriptors_closed_monotonic_ns'] and
         host['context_before']['boot_id_raw'] ==
         restricted['context_before']['boot_id_raw'] and
         set(host['files']) == set(restricted['files']) ==
         set(contract.EXPECTED), 'OWNER_CAPTURE_COMPLETION')
    files = {}
    for target, expected in contract.EXPECTED.items():
        files[target] = {'source': expected['source'],
                         'sha256': expected['sha256'],
                         'size': expected['size'],
                         'host': view(host, target, restricted=False),
                         'restricted': view(restricted, target, restricted=True)}
    evidence_digest = sha((sha(host_capture_bytes) +
                           streams['restricted_stdout']['sha256'] +
                           marker['summary_sha256']).encode('ascii'))
    result = {
        'version': 4, 'historical_context': 'UNKNOWN',
        'source_report': contract.REPORT_URL, 'evidence_sha256': evidence_digest,
        'boot_id': host['context_before']['boot_id_raw'].strip(),
        'host_namespace': namespace(host),
        'restricted_namespace': namespace(restricted),
        'capture_control': {
            'collector_sha256': contract.REVIEWED_COLLECTOR_SHA256,
            'control_record_sha256': streams['host_stdout']['sha256'],
            'sender_ordering_record_sha256': marker['summary_sha256'],
            'host_exit_code': 0, 'restricted_exit_code': 0,
            'held_source_descriptors': True, 'finish_acknowledged': True,
            'finish_code': 'FINISH_ACK',
            'host_monotonic_ns': [
                host['host_ready_monotonic_ns'],
                host['finish_control']['finish_received_monotonic_ns'],
                host['held_descriptors_closed_monotonic_ns']],
        },
        'files': files,
    }
    return result, contract.validate(result, historical_dependencies)
