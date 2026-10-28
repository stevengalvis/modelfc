"""Exact-site source generation only; never provisions or launches a native unit.

Input is the generated native bridge's files mapping. The caller separately
supplies pins.workspace, regenerates dependency/manifest hashes and unit binds.
"""
import ast
import hashlib
from pathlib import Path


def change(source, old, new):
    if source.count(old) != 1:
        raise ValueError('WORKSPACE_GENERATION_SITE')
    return source.replace(old, new, 1)


COMMON = r'''
def workspace_record(value, observation):
    proof_keys(value, 'version nonce provision contract_sha256 payload_sha256 tests_passed probes')
    proof_need(type(value['version']) is int and value['version'] == 1)
    for key in ('nonce', 'provision', 'contract_sha256', 'payload_sha256'):
        proof_hash(value[key])
    proof_need(value['nonce'] == observation['nonce'] and value['provision'] == observation['provision'])
    proof_need(value['payload_sha256'] == WORKSPACE_PAYLOAD_HASH)
    proof_need(type(value['tests_passed']) is int and value['tests_passed'] == 2)
    tokens = ('outside_write', 'traversal_write', 'symlink_write', 'secret_read', 'ipv4_socket', 'ipv6_socket')
    proof_need(type(value['probes']) is list and len(value['probes']) == len(tokens))
    for token, item in zip(tokens, value['probes']):
        proof_keys(item, 'probe errno')
        errors = (1, 13, 30) if token.endswith('write') else (1, 13) if token == 'secret_read' else (1, 13, 97)
        proof_need(item['probe'] == token and type(item['errno']) is int and item['errno'] in errors)


def workspace_contract_at(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        proof_need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 131072)
        raw = bytearray()
        while len(raw) <= 131072:
            part = os.read(fd, 131073 - len(raw))
            if not part:
                break
            raw.extend(part)
        proof_need(len(raw) == before.st_size)
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        fields = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid','st_gid','st_nlink')
        proof_need(tuple(getattr(before,k) for k in fields) == tuple(getattr(after,k) for k in fields)
                   == tuple(getattr(named,k) for k in fields))
        pins = json.loads(raw, object_pairs_hook=proof_pairs)
        return pins['workspace']
    finally:
        os.close(fd)


def workspace_mount(mounts, visible, owners):
    contract = workspace_contract_at('/diagnostic/pins.json')
    expected = contract['entries']['workspace']['identity'][:2]
    proof_pair(expected)
    rows = [m for m in mounts if m['destination'] == '/workspace-fixture']
    proof_need(len(rows) == 1 and rows[0]['id'] in visible)
    row = rows[0]
    proof_need('rw' in row['options'].split(',') and not row['propagation'])
    proof_need(not any(m['destination'].startswith('/workspace-fixture/') for m in mounts))
    proof_need(not any(m['id'] != row['id'] and m['device'] == row['device'] and
               (m['root'] == row['root'] or m['root'].startswith(row['root'].rstrip('/') + '/')
                or row['root'].startswith(m['root'].rstrip('/') + '/')) for m in mounts))
    fd = os.open('/workspace-fixture', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        proof_need([info.st_dev, info.st_ino] == expected and stat.S_IMODE(info.st_mode) == 0o700
                   and (info.st_uid,info.st_gid) == tuple(owners))
        proof_need(row['device'] == str(os.major(info.st_dev)) + ':' + str(os.minor(info.st_dev)))
        proof_need(not os.fstatvfs(fd).f_flag & os.ST_RDONLY)
        with open('/proc/self/fdinfo/' + str(fd), encoding='ascii') as stream:
            raw = stream.read(4097)
        proof_need(len(raw) <= 4096 and [line.split(':',1)[1].strip() for line in raw.splitlines()
                   if line.startswith('mnt_id:')] == [str(row['id'])])
        named = os.stat('/workspace-fixture', follow_symlinks=False)
        proof_need((named.st_dev,named.st_ino,named.st_mode,named.st_uid,named.st_gid) ==
                   (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid))
        return row['id']
    finally:
        os.close(fd)


def workspace_canaries(mounts, visible, root_owners, workspace_id):
    contract = workspace_contract_at('/diagnostic/pins.json')
    def metadata(info, directory):
        row = [info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid]
        return row if directory else row + [info.st_nlink,info.st_size]
    def check(path, key, directory, mount_id):
        expected = list(contract['entries'][key]['identity'])
        expected[3:5] = root_owners
        fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            proof_need(metadata(before,directory) == expected)
            with open('/proc/self/fdinfo/' + str(fd), encoding='ascii') as stream:
                raw = stream.read(4097)
            proof_need(len(raw) <= 4096 and [line.split(':',1)[1].strip() for line in raw.splitlines()
                       if line.startswith('mnt_id:')] == [str(mount_id)])
            proof_need(metadata(os.stat(path,follow_symlinks=False),directory) == expected
                       and metadata(os.fstat(fd),directory) == expected)
        finally:
            os.close(fd)
    for directory, leaf in (('/readonly-fixture','outside.txt'),('/secret-fixture','canary')):
        proof_need(not any(m['destination'] == directory or m['destination'].startswith(directory + '/') for m in mounts))
        # These files are seeded into the authenticated rootfs, never separately mounted.
        covers = [m for m in mounts if m['id'] in visible and m['destination'] == '/']
        proof_need(len(covers) == 1 and 'ro' in covers[0]['options'].split(','))
        check(directory, 'rootfs' + directory, True, covers[0]['id'])
        check(directory + '/' + leaf, 'rootfs' + directory + '/' + leaf, False, covers[0]['id'])
    proof_need(contract['entries']['workspace/escape']['target'] == '/readonly-fixture/outside.txt')
    check('/workspace-fixture/escape','workspace/escape',False,workspace_id)
    proof_need(os.readlink('/workspace-fixture/escape') == '/readonly-fixture/outside.txt')
'''


SUPERVISOR = r'''
workspace_store = None
workspace_rootfd = None
workspace_verified = False


def workspace_prepare(transaction):
    global workspace_store, workspace_rootfd
    proof_need(workspace_store is None and workspace_rootfd is None)
    contract = workspace_contract_at(str(BASE / 'rootfs/diagnostic/pins.json'))
    workspace_rootfd = os.open('rootfs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                               dir_fd=transaction.io.stagefd)
    workspace_store = workspace_storage['open_verified'](transaction.io.stagefd, workspace_rootfd, contract)
    workspace_store.provision(transaction.identity)


def workspace_finish(raw, transaction):
    global workspace_verified
    proof_need(workspace_store is not None and not workspace_verified)
    records = [json.loads(line, object_pairs_hook=proof_pairs) for line in raw.splitlines()]
    attestations = [item for item in records if item.get('phase') == 'native_proof']
    proof_need(len(attestations) == 1)
    frame = proof_parse(attestations[0]['frame'].encode('ascii'))
    proof_need(frame['workspace']['contract_sha256'] ==
               hashlib.sha256(proof_bytes(workspace_store.contract)).hexdigest())
    workspace_record(frame['workspace'], {'nonce': transaction.attempt['nonce'],
                     'provision': hashlib.sha256(canonical_record(transaction.provision)).hexdigest()})
    result['workspace_proof'] = frame['workspace']
    workspace_verified = True


def workspace_cleanup():
    global workspace_rootfd
    try:
        if workspace_store is not None:
            result['workspace_readback'] = workspace_store.postverify(provision_transaction.identity)
            if workspace_verified and result.get('cleanup') == 'PASS' and result.get('status') == 'WORKSPACE_READBACK_PENDING':
                result['status'] = 'FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE'
    except BaseException as error:
        result['workspace_readback'] = {'state': 'UNKNOWN', 'error_class': type(error).__name__}
        result['cleanup'] = 'BLOCKED:WORKSPACE_READBACK'
        result['status'] = 'BLOCKED'
    finally:
        for action in (lambda: workspace_store.close() if workspace_store is not None else None,
                       lambda: os.close(workspace_rootfd) if workspace_rootfd is not None else None):
            try:
                action()
            except BaseException:
                result['cleanup'] = 'BLOCKED:WORKSPACE_DESCRIPTOR_CLEANUP'
                result['status'] = 'BLOCKED'
        workspace_rootfd = None
'''


def rebuild_registry(source):
    """Rebind the existing authenticated-code diagnostic registry after insertion."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('DIAGNOSTIC_CODES', 'DIAGNOSTIC_ASSERTIONS'):
            lines[node.lineno-1:node.end_lineno] = [node.targets[0].id + ' = {}\n']
    source = ''.join(lines)
    tree = ast.parse(source)
    compiled = compile(source, '<authenticated-supervisor>', 'exec')
    refs = {'MODULE': 'sys._getframe().f_code'}
    scopes = []

    def children(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                yield child
            else:
                yield from children(child)

    def code_for(code, node):
        return next(c for c in code.co_consts if hasattr(c, 'co_name') and c.co_name == node.name and c.co_firstlineno == node.lineno)

    def register(node, ref, code):
        key = 'F%03d' % len(refs)
        refs[key] = ref
        scopes.append((node, key))
        for child in children(node):
            nested = 'next(c for c in ' + ref + '.co_consts if getattr(c, "co_name", None) == ' + repr(child.name) + ' and c.co_firstlineno == ' + str(child.lineno) + ')'
            register(child, nested, code_for(code, child))

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            register(node, node.name + '.__code__', code_for(compiled, node))
        elif isinstance(node, ast.ClassDef):
            parent = code_for(compiled, node)
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    register(child, node.name + '.' + child.name + '.__code__', code_for(parent, child))
    assertions = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('need', 'require') and len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            owners = [(n, k) for n, k in scopes if n.lineno <= node.lineno <= n.end_lineno]
            owner = min(owners, key=lambda item: item[0].end_lineno-item[0].lineno)[1] if owners else 'MODULE'
            assertions[(owner, node.lineno)] = node.args[1].value
    source = change(source, 'DIAGNOSTIC_CODES = {}', 'DIAGNOSTIC_CODES = {' + ', '.join(ref + ': ' + repr(key) for key, ref in refs.items()) + '}')
    return change(source, 'DIAGNOSTIC_ASSERTIONS = {}', 'DIAGNOSTIC_ASSERTIONS = ' + repr(assertions))


def generate(files):
    """Return amended source files; do not mutate input or invent stage identities."""
    result = dict(files)
    payload = Path(__file__).with_name('payload.py').read_bytes()
    storage = Path(__file__).with_name('workspace_store.py').read_bytes()
    common = 'WORKSPACE_PAYLOAD_HASH = ' + repr(hashlib.sha256(payload).hexdigest()) + '\n' + COMMON
    for name in ('launch.py', 'report.py', 'supervisor.py'):
        source = files[name].decode('ascii')
        source = change(source, "PROOF_PREFIX = 'ZEN53_PROVISIONAL_V1 '",
                        common + "\nPROOF_PREFIX = 'ZEN53_WORKSPACE_V2 '")
        source = change(source, "proof_keys(frame, 'version observation report')", "proof_keys(frame, 'version observation report workspace')")
        source = change(source, "proof_need(type(frame['version']) is int and frame['version'] == 1)",
                        "proof_need(type(frame['version']) is int and frame['version'] == 2)")
        source = change(source, "    proof_observation(frame['observation'])", "    proof_observation(frame['observation'])\n    workspace_record(frame['workspace'], frame['observation'])")
        if name == 'launch.py':
            source = change(source, '    bookkeeping_mount(mounts, visible)\n    destinations =',
                            '    bookkeeping_mount(mounts, visible)\n    workspace_id = workspace_mount(mounts, visible, (os.getuid(), os.getgid()))\n    workspace_canaries(mounts, visible, (0, 0), workspace_id)\n    destinations =')
            source = change(source, "need(not writable or (mount['type'], mount['destination']) in", "need(not writable or mount['id'] == workspace_id or (mount['type'], mount['destination']) in")
            source = change(source, '"zen53_diagnostic",', '"zen53_workspace_fixture",')
            source = change(source, '        certificate = proof_verify(frames[0], self.before, after)',
                            "        certificate = proof_verify(frames[0], self.before, after)\n        proof_need(proof_parse(frames[0])['workspace']['contract_sha256'] == hashlib.sha256(proof_bytes(workspace_contract_at('/diagnostic/pins.json'))).hexdigest())")
        elif name == 'report.py':
            source = change(source, "    for mount in mounts:\n        if mount['id'] in visible and 'rw'", "    workspace_id = workspace_mount(mounts, visible, (os.getuid(), os.getgid()))\n    workspace_canaries(mounts, visible, (INNER_ROOT_UID, INNER_ROOT_GID), workspace_id)\n    for mount in mounts:\n        if mount['id'] in visible and 'rw'")
            source = change(source, "need((mount['type'], mount['destination']) in", "need(mount['id'] == workspace_id or (mount['type'], mount['destination']) in")
            source = change(source, "proof_inner_record = None", "workspace_payload = {}\nexec(" + repr(payload) + ", workspace_payload)\nproof_inner_record = None")
            source = change(source, "    frame = {'version': 1, 'observation': proof_inner_record, 'report': lines}",
                "    contract_hash = hashlib.sha256(proof_bytes(workspace_contract_at('/diagnostic/pins.json'))).hexdigest()\n"
                "    observation = workspace_payload['run']({'nonce': proof_inner_record['nonce'], 'stage_sha256': contract_hash, 'payload_sha256': WORKSPACE_PAYLOAD_HASH})\n"
                "    workspace_raw = text('/proc/self/mountinfo', 131072)\n"
                "    proof_need(hashlib.sha256(workspace_raw.encode('ascii')).hexdigest() == proof_inner_record['topology_sha256'])\n"
                "    workspace_mounts = parse_mounts(workspace_raw)\n"
                "    workspace_visible, _ = mount_visibility(workspace_mounts)\n"
                "    workspace_id = workspace_mount(workspace_mounts, workspace_visible, (os.getuid(), os.getgid()))\n"
                "    workspace_canaries(workspace_mounts, workspace_visible, (INNER_ROOT_UID, INNER_ROOT_GID), workspace_id)\n"
                "    workspace = {'version': 1, 'nonce': proof_inner_record['nonce'], 'provision': proof_inner_record['provision'], 'contract_sha256': contract_hash, 'payload_sha256': WORKSPACE_PAYLOAD_HASH, 'tests_passed': observation['tests_passed'], 'probes': observation['probes']}\n"
                "    frame = {'version': 2, 'observation': proof_inner_record, 'report': lines, 'workspace': workspace}")
        else:
            source = change(source, 'provision_transaction = None', 'workspace_storage = {}\nexec(' + repr(storage) + ', workspace_storage)\n' + SUPERVISOR + '\nprovision_transaction = None')
            source = change(source, '        child = self.io.create(self.identity)', '        child = self.io.create(self.identity)\n        workspace_prepare(self)')
            source = change(source, '        eligible_until = self.io.guard()', '        proof_need(proof_bytes(workspace_store.observe()) == proof_bytes(workspace_store.provisioned_contract))\n        eligible_until = self.io.guard()')
            source = change(source, '        self.io.verify_child(self.identity)\n        now = self.io.now()', '        self.io.verify_child(self.identity)\n        proof_need(proof_bytes(workspace_store.observe()) == proof_bytes(workspace_store.provisioned_contract))\n        now = self.io.now()')
            source = change(source, '    result["native_runtime"] = "PASS"', '    workspace_finish(raw, provision_transaction)\n    result["native_runtime"] = "PASS"')
            source = change(source, '"COMPATIBILITY_PASS_NOT_CODING_ACCEPTANCE"', '"WORKSPACE_READBACK_PENDING"')
            source = change(source, '    if provision_transaction is not None:\n        result["provisioning_phase_at_cleanup"]', '    workspace_cleanup()\n    if provision_transaction is not None:\n        result["provisioning_phase_at_cleanup"]')
            source = change(source, '"ReadOnlyPaths", "ReadWritePaths", "BindReadOnlyPaths",', '"ReadOnlyPaths", "ReadWritePaths", "BindPaths", "BindReadOnlyPaths",')
            source = change(source, 'need(observed.get("ReadWritePaths") == "",', 'need(observed.get("ReadWritePaths") == "+/workspace-fixture" and\n         observed.get("BindPaths") == str(BASE / "workspace") + ":/workspace-fixture:rbind",')
            source = change(source, 'set(dependencies["dependencies"]) | {"/diagnostic/pins.json"}', 'set(dependencies["dependencies"]) | {"/diagnostic/pins.json", "/readonly-fixture/outside.txt", "/secret-fixture/canary"}')
            source = rebuild_registry(source)
        ast.parse(source)
        result[name] = source.encode('ascii')
    return result
