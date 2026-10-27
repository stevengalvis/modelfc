"""Pure ZEN-53 new-stage metadata builder and read-only sealed-source checks.

This module never creates a stage, launches a unit, or reads host /proc. A later
approved preparer must supply real, verified fresh identities and seal outputs.
"""
import ast
import copy
import hashlib
import json
import os
import re
import stat
import owner_binding_v4 as owner_context
import zlib_amendment

HISTORICAL_SHA256 = {
    'DEPENDENCIES.json': 'd68773386095a4b7b909e1ec44fa5cf8297dce52fb7e2f916af10c931a520809',
    'EMPTY_BINDINGS.json': '0437c5ba36ebb07203678664fa99fa7cfbbb70149dc06055d9c340585351eb31',
    'PROPOSED_ARTIFACT_SHA256.json': '5cfe9a32d5403609c837d757ac4cffc613c861cedb41adfd085d94ccae655872',
    'diagnostic.service': 'cec73fc80e3b57e68fa7eb39a734e288636947440bb856f42568588c2d4adbbb',
    'launch.py': '14a8ec5d0b1eb043566ef6c59d3186fe6f06090cc1515bc65a6c43b838d3e29c',
    'pins.json': '83b82f0ecf44e3651cb9e53a7083dbc1886c1ff66b6138409e072d6fe9baba11',
    'report.py': 'ec4c7e553178e61677a961ff1e0c0a88103ffe3d95cfef7dd43cef2acf38aa22',
    'requirements.toml': '2567e3290e66d17e3128f6741f64392dc6f90917bb95d0becec34db945fb12bb',
    'supervisor.py': 'e7bb99e9a0af9606725af949373e9f0d8caf6bf3cad2c7c3935a6d74e1e9e76f',
}
NEW_REPORT = 'f63c070d3c9f4b07a977fe9dda5fc4de0860a5e8510356e9ade123d5ffb2a09e'
NEW_SUPERVISOR = '5a38ed8fbc2abd8cb9808f7fb92e630ec375161656defc586b0b26979b505c46'
OS_PY = '316d1b7307fd851bded3423c9d437e0a383c725d993f0fcff2e8b749fe560b62'
OLD_STAGE = '/tmp/zen53-empty-bind-preparation-c4dc2b6bff2d4cafe744ca2134c852a0'
STAGE_PATTERN = re.compile(r'/tmp/zen53-[a-z0-9-]{12,96}\Z')
BUNDLED = {
    '/diagnostic/launch.py': 'launch.py',
    '/diagnostic/report.py': 'report.py',
    '/etc/codex/requirements.toml': 'requirements.toml',
    '/usr/lib/python3.12/os.py': 'os.py',
}
MANIFEST_CHANGED_KEYS = frozenset({
    zlib_amendment.TARGET,
    'launch.py', 'rootfs/diagnostic/launch.py',
    'report.py', 'rootfs/diagnostic/report.py',
    'supervisor.py', 'rootfs/diagnostic/pins.json',
    'DEPENDENCIES.json', 'EMPTY_BINDINGS.json', 'diagnostic.service',
})


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2) + '\n').encode('ascii')


def load_json(data):
    return json.loads(data.decode('ascii'))


def pair(value):
    require(type(value) is tuple and len(value) == 2 and
            all(type(n) is int and 0 < n < 2**63 for n in value), 'IDENTITY_PAIR')
    return list(value)


def patch_launcher(raw, home, vartmp):
    text = raw.decode('ascii')
    calls = [node for node in ast.walk(ast.parse(text)) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'need'
             and len(node.args) == 2 and isinstance(node.args[1], ast.Constant)
             and node.args[1].value == 'EMPTY_BIND_PIN_SCHEMA']
    require(len(calls) == 1, 'LAUNCH_SCHEMA_SITE')
    compare = calls[0].args[0]
    require(isinstance(compare, ast.Compare) and len(compare.comparators) == 1
            and isinstance(compare.comparators[0], ast.Dict), 'LAUNCH_SCHEMA_SHAPE')
    schema = compare.comparators[0]
    require([key.value for key in schema.keys] == ['/empty-home', '/var/tmp']
            and all(isinstance(value, ast.List) and len(value.elts) == 2
                    for value in schema.values), 'LAUNCH_SCHEMA_TARGETS')
    nodes = [element for value in schema.values for element in value.elts]
    require(all(isinstance(node, ast.Constant) and type(node.value) is int for node in nodes)
            and [node.value for node in nodes] == [2049, 128952, 2049, 129326],
            'LAUNCH_OLD_IDENTITY')
    starts, offset = [], 0
    for line in text.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
    edits = [(starts[n.lineno - 1] + n.col_offset,
              starts[n.end_lineno - 1] + n.end_col_offset, str(value))
             for n, value in zip(nodes, home + vartmp)]
    for begin, end, value in sorted(edits, reverse=True):
        text = text[:begin] + value + text[end:]
    return text.encode('ascii')


def build(historical, object_bytes, stage_path, home_identity, vartmp_identity,
          owner_evidence):
    """Return exact proposed bytes; no filesystem access or side effects."""
    require(type(stage_path) is str and STAGE_PATTERN.fullmatch(stage_path) is not None
            and stage_path != OLD_STAGE, 'FRESH_STAGE_PATH')
    home, vartmp = pair(home_identity), pair(vartmp_identity)
    require(home[0] == vartmp[0] and home[1] != vartmp[1], 'BIND_IDENTITY_COLLISION')
    require(type(historical) is dict and set(historical) == set(HISTORICAL_SHA256)
            and all(type(historical[name]) is bytes and sha(historical[name]) == expected
                    for name, expected in HISTORICAL_SHA256.items()), 'HISTORICAL_BYTES')
    require(type(object_bytes) is dict and all(type(key) is str and type(value) is bytes
            and re.fullmatch('[0-9a-f]{64}', key) and sha(value) == key
            for key, value in object_bytes.items()), 'OBJECT_HASH')
    for key in (NEW_REPORT, NEW_SUPERVISOR, OS_PY, HISTORICAL_SHA256['requirements.toml']):
        require(key in object_bytes, 'REQUIRED_OBJECT')
    files = dict(historical)
    files['report.py'] = object_bytes[NEW_REPORT]
    files['supervisor.py'] = object_bytes[NEW_SUPERVISOR]
    files['launch.py'] = patch_launcher(historical['launch.py'], home, vartmp)
    pins = load_json(historical['pins.json'])
    deps = load_json(historical['DEPENDENCIES.json'])
    zlib_amendment.amend(pins, deps)
    require(pins['empty_directories'] == {'/empty-home': [2049, 128952],
            '/var/tmp': [2049, 129326]}, 'HISTORICAL_BIND_PINS')
    pins['empty_directories'] = {'/empty-home': home, '/var/tmp': vartmp}
    pins['files']['/diagnostic/launch.py'] = sha(files['launch.py'])
    pins['files']['/diagnostic/report.py'] = NEW_REPORT
    files['pins.json'] = json_bytes(pins)
    bindings = load_json(historical['EMPTY_BINDINGS.json'])
    require(set(bindings) == {'/empty-home', '/var/tmp'}, 'HISTORICAL_BIND_KEYS')
    for target, child, identity in (('/empty-home', 'empty-home', home),
                                    ('/var/tmp', 'empty-vartmp', vartmp)):
        require(bindings[target]['source'] == OLD_STAGE + '/' + child,
                'HISTORICAL_BIND_SOURCE')
        bindings[target]['source'] = stage_path + '/' + child
        bindings[target]['device_inode'] = identity
    files['EMPTY_BINDINGS.json'] = json_bytes(bindings)
    unit = historical['diagnostic.service'].decode('ascii')
    require(unit.count(OLD_STAGE) == 6, 'HISTORICAL_UNIT_PATHS')
    files['diagnostic.service'] = unit.replace(OLD_STAGE, stage_path).encode('ascii')
    require(len(deps['dependencies']) == 43 and len(deps['symlinks']) == 9,
            'HISTORICAL_DEPENDENCY_COUNT')
    owner_digest = owner_context.amend_dependencies(deps, owner_evidence)
    content = {'launch.py': files['launch.py'], 'report.py': files['report.py'],
               'requirements.toml': historical['requirements.toml'], 'os.py': object_bytes[OS_PY]}
    routes = {}
    for target, name in BUNDLED.items():
        record = deps['dependencies'][target]
        expected = sha(content[name])
        require(record['sha256'] == (HISTORICAL_SHA256[name] if name != 'os.py' else OS_PY),
                'HISTORICAL_DEPENDENCY_HASH')
        require(type(record['source']) is str and type(record['resolved_source']) is str
                and record['source'].startswith('/') and record['resolved_source'].startswith('/'),
                'HISTORICAL_SOURCE_PROVENANCE')
        uri = 'bundle:objects/' + expected
        # These are acquisition instructions, not observations of a future host.
        # A preparer must replace the URI with the verified sealed absolute path,
        # record its observed metadata, and recompute the dependency/manifest pins.
        record.update(source=uri, resolved_source=uri, sha256=expected,
                      source_uid=None, source_gid=None, source_mode=None,
                      source_observation='PENDING_PREPARATION')
        routes[target] = {'uri': uri, 'sha256': expected, 'size': len(content[name]),
                          'required_owner': [0, 0], 'required_mode': '0444'}
    files['DEPENDENCIES.json'] = json_bytes(deps)
    manifest = load_json(historical['PROPOSED_ARTIFACT_SHA256.json'])
    require(len(manifest) == 122, 'HISTORICAL_MANIFEST_COUNT')
    source_for_key = {
        'launch.py': 'launch.py', 'rootfs/diagnostic/launch.py': 'launch.py',
        'report.py': 'report.py', 'rootfs/diagnostic/report.py': 'report.py',
        'supervisor.py': 'supervisor.py', 'rootfs/diagnostic/pins.json': 'pins.json',
        'DEPENDENCIES.json': 'DEPENDENCIES.json',
        'EMPTY_BINDINGS.json': 'EMPTY_BINDINGS.json', 'diagnostic.service': 'diagnostic.service',
    }
    zlib_amendment.amend_manifest(manifest)
    require(set(source_for_key) | {zlib_amendment.TARGET} == MANIFEST_CHANGED_KEYS, 'MANIFEST_CHANGE_SCHEMA')
    for key, name in source_for_key.items():
        require(key in manifest, 'MANIFEST_MISSING_KEY')
        manifest[key] = sha(files[name])
    require({key for key, value in load_json(historical['PROPOSED_ARTIFACT_SHA256.json']).items()
             if manifest[key] != value} == MANIFEST_CHANGED_KEYS, 'MANIFEST_CHANGE_SET')
    files['PROPOSED_ARTIFACT_SHA256.json'] = json_bytes(manifest)
    generated_objects = {sha(data): data for data in (files[name] for name in
                         ('launch.py', 'report.py', 'supervisor.py', 'pins.json',
                          'DEPENDENCIES.json', 'EMPTY_BINDINGS.json', 'diagnostic.service'))}
    generated_objects.update({key: object_bytes[key] for key in
                              (OS_PY, HISTORICAL_SHA256['requirements.toml'])})
    override = {key: {'uri': 'bundle:objects/' + manifest[key], 'sha256': manifest[key]}
                for key in sorted(source_for_key)}
    contract = {'version': 1, 'state': 'NEW_STAGE_ONLY', 'rootfs/root':
                {'uid': 0, 'gid': 0, 'mode': '0750', 'empty': True,
                 'nofollow': True, 'mount': False, 'inode': 'OBSERVE_AT_PREPARATION'}}
    return {'files': files, 'objects': generated_objects, 'routes': routes,
            'overrides': override, 'directory_contract': contract,
            'owner_context_sha256': owner_digest,
            'stage_path': stage_path, 'bind_identities':
            {'/empty-home': home, '/var/tmp': vartmp}}


def verify_result(candidate, historical, object_bytes, stage_path,
                  home_identity, vartmp_identity, owner_evidence):
    """Recompute all bytes and routes; no stale legacy acquisition may pass."""
    expected = build(historical, object_bytes, stage_path,
                     home_identity, vartmp_identity, owner_evidence)
    require(type(candidate) is dict and set(candidate) == set(expected), 'BRIDGE_RESULT_SCHEMA')
    require(candidate == expected, 'BRIDGE_RESULT_DRIFT')
    for target, route in candidate['routes'].items():
        record = load_json(candidate['files']['DEPENDENCIES.json'])['dependencies'][target]
        require(record['source'] == record['resolved_source'] == route['uri']
                and route['uri'] == 'bundle:objects/' + route['sha256']
                and route['sha256'] in candidate['objects']
                and sha(candidate['objects'][route['sha256']]) == route['sha256'],
                'BRIDGE_SOURCE_ROUTE')
    return True


def verify_sealed_object(objects_fd, digest):
    """Read one prepared root-owned object by descriptor; reject stale/symlink paths."""
    require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'OBJECT_NAME')
    parent = os.fstat(objects_fd)
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == parent.st_gid == 0
            and stat.S_IMODE(parent.st_mode) == 0o700, 'OBJECT_PARENT_METADATA')
    fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=objects_fd)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                and stat.S_IMODE(before.st_mode) == 0o444 and before.st_nlink == 1
                and 0 < before.st_size <= 500000, 'OBJECT_METADATA')
        chunks = bytearray()
        while len(chunks) <= 500000:
            part = os.read(fd, min(65536, 500001 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        require(len(chunks) == before.st_size and sha(chunks) == digest, 'OBJECT_BYTES')
        after = os.fstat(fd)
        named = os.stat(digest, dir_fd=objects_fd, follow_symlinks=False)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
                  'st_mode', 'st_uid', 'st_gid', 'st_nlink')
        require(tuple(getattr(before, key) for key in fields) ==
                tuple(getattr(after, key) for key in fields) ==
                tuple(getattr(named, key) for key in fields), 'OBJECT_REPLACED')
        return bytes(chunks), (before.st_dev, before.st_ino)
    finally:
        os.close(fd)


def verify_root_directory(rootfs_fd):
    """Check inode metadata/emptiness only; mount topology needs separate verification."""
    fd = os.open('root', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=rootfs_fd)
    try:
        before = os.fstat(fd)
        require(stat.S_ISDIR(before.st_mode) and before.st_uid == before.st_gid == 0
                and stat.S_IMODE(before.st_mode) == 0o750 and before.st_nlink == 2
                and os.listdir(fd) == [], 'ROOT_DIRECTORY_CONTRACT')
        after = os.fstat(fd)
        named = os.stat('root', dir_fd=rootfs_fd, follow_symlinks=False)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                  'st_mtime_ns', 'st_ctime_ns')
        require(tuple(getattr(before, key) for key in fields) ==
                tuple(getattr(after, key) for key in fields) and
                tuple(getattr(before, key) for key in fields) ==
                tuple(getattr(named, key) for key in fields),
                'ROOT_DIRECTORY_CONTRACT')
        return (before.st_dev, before.st_ino)
    finally:
        os.close(fd)


def verify_stage_objects_binding(stage_path, objects_fd):
    """Nofollow-walk /tmp/new-stage/objects and bind it to objects_fd."""
    require(type(stage_path) is str and STAGE_PATTERN.fullmatch(stage_path) is not None
            and stage_path != OLD_STAGE, 'FRESH_STAGE_PATH')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fds = []
    try:
        root_fd = os.open('/', flags)
        fds.append(root_fd)
        tmp_fd = os.open('tmp', flags, dir_fd=root_fd)
        fds.append(tmp_fd)
        stage_fd = os.open(stage_path.rsplit('/', 1)[1], flags, dir_fd=tmp_fd)
        fds.append(stage_fd)
        bound_fd = os.open('objects', flags, dir_fd=stage_fd)
        fds.append(bound_fd)
        observations = [os.fstat(fd) for fd in fds]
        root, tmp, stage, bound = observations
        require(all(stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0
                    for s in observations), 'STAGE_ANCESTOR_METADATA')
        require(stat.S_IMODE(tmp.st_mode) == 0o1777 and
                stat.S_IMODE(stage.st_mode) == 0o700 and
                stat.S_IMODE(bound.st_mode) == 0o700, 'STAGE_DIRECTORY_MODE')
        supplied = os.fstat(objects_fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                  'st_mtime_ns', 'st_ctime_ns')
        require(tuple(getattr(bound, key) for key in fields) ==
                tuple(getattr(supplied, key) for key in fields),
                'STAGE_OBJECTS_BINDING')
        return tuple(tuple(getattr(s, key) for key in fields) for s in observations)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def finalize(plan, historical, object_bytes, stage_path, home_identity,
             vartmp_identity, owner_evidence, objects_fd):
    """Resolve four plan URIs only after reading their sealed objects by dirfd.

    The descriptor walk binds objects_fd to the new stage path. The caller must
    separately check mount topology. No host path is created here.
    """
    verify_result(plan, historical, object_bytes, stage_path,
                  home_identity, vartmp_identity, owner_evidence)
    binding = verify_stage_objects_binding(stage_path, objects_fd)
    result = copy.deepcopy(plan)
    deps = load_json(result['files']['DEPENDENCIES.json'])
    observations = {}
    for target, route in sorted(plan['routes'].items()):
        digest = route['sha256']
        data, identity = verify_sealed_object(objects_fd, digest)
        require(data == plan['objects'][digest], 'SEALED_OBJECT_MISMATCH')
        source = stage_path + '/objects/' + digest
        record = deps['dependencies'][target]
        require(record['source'] == record['resolved_source'] == route['uri']
                and record['source_observation'] == 'PENDING_PREPARATION',
                'PENDING_ROUTE_REQUIRED')
        record.update(source=source, resolved_source=source, source_uid=0,
                      source_gid=0, source_mode='0o444',
                      source_observation='SEALED_VERIFIED')
        observations[target] = {'source': source, 'device_inode': list(identity),
                                'sha256': digest, 'uid': 0, 'gid': 0, 'mode': '0444'}
    result['files']['DEPENDENCIES.json'] = json_bytes(deps)
    manifest = load_json(result['files']['PROPOSED_ARTIFACT_SHA256.json'])
    manifest['DEPENDENCIES.json'] = sha(result['files']['DEPENDENCIES.json'])
    result['files']['PROPOSED_ARTIFACT_SHA256.json'] = json_bytes(manifest)
    result['objects'].pop(plan['overrides']['DEPENDENCIES.json']['sha256'])
    result['objects'][manifest['DEPENDENCIES.json']] = result['files']['DEPENDENCIES.json']
    result['overrides']['DEPENDENCIES.json'] = {
        'uri': 'bundle:objects/' + manifest['DEPENDENCIES.json'],
        'sha256': manifest['DEPENDENCIES.json']}
    result['routes'] = observations
    result['state'] = 'SEALED_SOURCE_OBSERVATIONS_ONLY'
    require(verify_stage_objects_binding(stage_path, objects_fd) == binding,
            'STAGE_BINDING_CHANGED')
    return result
