"""Version-four two-file bind ownership observation contract.

This validates a supplied *current* dual-context snapshot. It neither reads a
host nor authenticates the collector. Historical observation context stays
UNKNOWN; a real stage requires a separately reviewed trusted capture.
"""
import hashlib
import json
import re

REPORT_URL = 'https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384364441'
REVIEWED_COLLECTOR_SHA256 = '5ea2e4269204b72b4dab7d1fbda3a5d1a94e76f6db08756a5a15c77f00a8b604'
EXPECTED = {
    '/opt/codex/bin/codex': {
        'source': '/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl/bin/codex',
        'sha256': '628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1',
        'size': 294415048, 'mode': '0o755', 'device_inode': [2049, 434465],
    },
    '/opt/codex/codex-package.json': {
        'source': '/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl/codex-package.json',
        'sha256': 'f05ebff6db98bd613dd5654461985090c6416b41ad67902243d83a53b305ac05',
        'size': 205, 'mode': '0o644', 'device_inode': [2049, 434467],
    },
}


def need(ok, code):
    if not ok:
        raise ValueError(code)


def number(value, low=0, high=2**63):
    return type(value) is int and low <= value < high


def pair(value):
    return type(value) is list and len(value) == 2 and all(number(n, 1) for n in value)


def mapping(value):
    return (type(value) is list and len(value) == 1 and
            type(value[0]) is list and len(value[0]) == 3 and
            all(number(n, 0, 2**32) for n in value[0]))


def canonical(raw):
    need(type(raw) is dict, 'OWNER_CONTEXT_SCHEMA')
    return (json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def validate(raw, historical_dependencies):
    """Return snapshot digest after checking exact host and projected owners.

    The caller must authenticate its raw observation procedure and hold file
    descriptors stable across collection; schema validation alone cannot do so.
    """
    need(type(raw) is dict and set(raw) == {'version', 'historical_context',
         'source_report', 'evidence_sha256', 'boot_id', 'host_namespace',
         'restricted_namespace', 'capture_control', 'files'}, 'OWNER_CONTEXT_SCHEMA')
    need(type(raw['version']) is int and raw['version'] == 4 and
         raw['historical_context'] == 'UNKNOWN'
         and raw['source_report'] == REPORT_URL, 'OWNER_CONTEXT_PROVENANCE')
    need(type(raw['evidence_sha256']) is str and
         re.fullmatch('[0-9a-f]{64}', raw['evidence_sha256']) and
         raw['evidence_sha256'] != '0' * 64, 'OWNER_CONTEXT_EVIDENCE')
    need(type(raw['boot_id']) is str and re.fullmatch(
         '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
         raw['boot_id']), 'OWNER_CONTEXT_BOOT')
    host = raw['host_namespace']
    restricted = raw['restricted_namespace']
    ns_keys = {'user_ns', 'mount_ns', 'pid_ns', 'uid_map', 'gid_map',
               'overflow_uid', 'overflow_gid', 'pid1_user_ns',
               'pid1_mount_ns', 'pid1_pid_ns'}
    need(type(host) is dict and type(restricted) is dict and
         set(host) == ns_keys and set(restricted) == ns_keys, 'OWNER_NAMESPACE_SCHEMA')
    for ns in (host, restricted):
        need(all(number(ns[k], 1) for k in ('user_ns', 'mount_ns', 'pid_ns')) and
             all(number(ns[k], 0, 2**32) for k in ('overflow_uid', 'overflow_gid'))
             and all(number(ns[k], 1) for k in
                     ('pid1_user_ns', 'pid1_mount_ns', 'pid1_pid_ns')) and
             mapping(ns['uid_map']) and mapping(ns['gid_map']),
             'OWNER_NAMESPACE_NUMERIC')
    # /proc/1 is the init of the local PID namespace. It is not a route to
    # host PID 1 from an isolated PID namespace. Anchor the trusted host
    # collection to its own local PID 1; require a distinct, internally
    # consistent local PID 1 in the restricted collection.
    need(host['pid1_user_ns'] == host['user_ns'] and
         host['pid1_mount_ns'] == host['mount_ns'] and
         host['pid1_pid_ns'] == host['pid_ns'] and
         restricted['pid1_user_ns'] == restricted['user_ns'] and
         restricted['pid1_mount_ns'] == restricted['mount_ns'] and
         restricted['pid1_pid_ns'] == restricted['pid_ns'] and
         host['uid_map'] == host['gid_map'] == [[0, 0, 4294967295]] and
         restricted['uid_map'] == restricted['gid_map'] == [[0, 0, 1]] and
         restricted['user_ns'] != host['user_ns'] and
         restricted['mount_ns'] != host['mount_ns'] and
         restricted['pid_ns'] != host['pid_ns'] and
         host['overflow_uid'] == host['overflow_gid'] ==
         restricted['overflow_uid'] == restricted['overflow_gid'] == 65534,
         'OWNER_NAMESPACE_MAPPING')
    control = raw['capture_control']
    need(type(control) is dict and set(control) == {
         'collector_sha256', 'control_record_sha256',
         'sender_ordering_record_sha256', 'host_exit_code',
         'restricted_exit_code', 'held_source_descriptors',
         'finish_acknowledged', 'finish_code', 'host_monotonic_ns'},
         'OWNER_CAPTURE_SCHEMA')
    need(all(type(control[k]) is str and re.fullmatch('[0-9a-f]{64}', control[k])
             and control[k] != '0' * 64 for k in
             ('collector_sha256', 'control_record_sha256',
              'sender_ordering_record_sha256')) and
         control['collector_sha256'] == REVIEWED_COLLECTOR_SHA256 and
         type(control['host_exit_code']) is int and control['host_exit_code'] == 0 and
         type(control['restricted_exit_code']) is int and
         control['restricted_exit_code'] == 0 and
         control['held_source_descriptors'] is True and
         control['finish_acknowledged'] is True and
         control['finish_code'] == 'FINISH_ACK' and
         type(control['host_monotonic_ns']) is list and
         len(control['host_monotonic_ns']) == 3 and
         all(number(t, 1) for t in control['host_monotonic_ns']) and
         all(a < b for a, b in zip(control['host_monotonic_ns'],
                                  control['host_monotonic_ns'][1:])),
         'OWNER_CAPTURE_INCOMPLETE')
    need(type(historical_dependencies) is dict and type(raw['files']) is dict and
         set(raw['files']) == set(EXPECTED), 'OWNER_TARGET_SET')
    for target, expected in EXPECTED.items():
        old = historical_dependencies[target]
        need(old['source'] == old['resolved_source'] == expected['source'] and
             old['sha256'] == expected['sha256'] and
             old['mode'] == ('0o555' if target.endswith('/bin/codex') else '0o444') and
             (old['source_uid'], old['source_gid']) == (65534, 65534) and
             old['source_mode'] == expected['mode'], 'OWNER_HISTORICAL_PIN')
        file = raw['files'][target]
        need(type(file) is dict and set(file) == {'source', 'sha256', 'size',
             'host', 'restricted'}, 'OWNER_FILE_SCHEMA')
        need(file['source'] == expected['source'] and
             file['sha256'] == expected['sha256'] and
             number(file['size'], 1) and file['size'] == expected['size'],
             'OWNER_FILE_IDENTITY')
        views = (file['host'], file['restricted'])
        keys = {'uid', 'gid', 'mode', 'size', 'sha256', 'device_inode',
                'named_device_inode', 'mtime_ns', 'ctime_ns', 'nlink',
                'mount_id', 'mount_unique_id', 'idmapped', 'alias_count',
                'mount_read_only', 'mountpoint', 'mount_root'}
        need(all(type(view) is dict and set(view) == keys for view in views),
             'OWNER_VIEW_SCHEMA')
        for view in views:
            need(all(number(view[key], 0, 2**32) for key in ('uid', 'gid')) and
                 view['mode'] == expected['mode'] and
                 number(view['size'], 1) and view['size'] == expected['size'] and
                 view['sha256'] == expected['sha256'] and
                 pair(view['device_inode']) and pair(view['named_device_inode']) and
                 view['device_inode'] == view['named_device_inode'] ==
                 expected['device_inode'] and
                 number(view['mtime_ns'], 1) and number(view['ctime_ns'], 1) and
                 number(view['nlink'], 1) and number(view['mount_id'], 1) and
                 number(view['mount_unique_id'], 1) and
                 view['idmapped'] is False and type(view['alias_count']) is int and
                 view['alias_count'] == 0 and
                 type(view['mount_read_only']) is bool and
                 type(view['mountpoint']) is str and
                 type(view['mount_root']) is str,
                 'OWNER_VIEW_IDENTITY')
        need(file['restricted']['mount_read_only'] is True and
             file['restricted']['mountpoint'] == expected['source'] and
             file['restricted']['mount_root'] == expected['source'] and
             file['host']['mountpoint'] != expected['source'],
             'OWNER_FILE_BIND')
        need((file['host']['uid'], file['host']['gid']) == (1001, 1001) and
             (file['restricted']['uid'], file['restricted']['gid']) == (65534, 65534),
             'OWNER_EXACT_HOST_AND_PROJECTION')
        need(all(file['host'][key] == file['restricted'][key]
                 for key in ('mode', 'size', 'sha256', 'device_inode',
                             'mtime_ns', 'ctime_ns', 'nlink')),
             'OWNER_CROSS_CONTEXT_DRIFT')
    targets = list(EXPECTED)
    host_first = raw['files'][targets[0]]['host']
    host_second = raw['files'][targets[1]]['host']
    restricted_first = raw['files'][targets[0]]['restricted']
    restricted_second = raw['files'][targets[1]]['restricted']
    need((host_first['mount_id'], host_first['mount_unique_id']) ==
         (host_second['mount_id'], host_second['mount_unique_id']) and
         restricted_first['mount_id'] != restricted_second['mount_id'] and
         restricted_first['mount_unique_id'] != restricted_second['mount_unique_id'] and
         all(raw['files'][target]['host']['mount_unique_id'] !=
             raw['files'][target]['restricted']['mount_unique_id']
             for target in targets), 'OWNER_MOUNT_INCONSISTENT')
    return hashlib.sha256(canonical(raw)).hexdigest()


def amend_dependencies(deps, raw):
    """Create new current-host acquisition pins, never mutate historical bytes."""
    digest = validate(raw, deps['dependencies'])
    for target in EXPECTED:
        record = deps['dependencies'][target]
        record['source_uid'] = 1001
        record['source_gid'] = 1001
        record['source_owner_namespace'] = 'INITIAL_HOST_CURRENT_OBSERVATION'
        record['source_owner_observation_sha256'] = digest
        record['source_device_inode'] = raw['files'][target]['host']['device_inode']
        record['source_mount_id'] = raw['files'][target]['host']['mount_id']
    return digest
