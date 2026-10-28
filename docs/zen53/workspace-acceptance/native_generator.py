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
WORKSPACE_MOUNT_CHECKS = (
    'WORKSPACE_MOUNT_CONTRACT', 'WORKSPACE_MOUNT_IDENTITY_SCHEMA',
    'WORKSPACE_MOUNT_VISIBLE', 'WORKSPACE_MOUNT_OPTIONS', 'WORKSPACE_MOUNT_SUBMOUNT',
    'WORKSPACE_MOUNT_ALIAS', 'WORKSPACE_MOUNT_OPEN', 'WORKSPACE_MOUNT_STAT',
    'WORKSPACE_MOUNT_METADATA', 'WORKSPACE_MOUNT_DEVICE', 'WORKSPACE_MOUNT_VFS_READ',
    'WORKSPACE_MOUNT_READONLY', 'WORKSPACE_MOUNT_FDINFO_READ', 'WORKSPACE_MOUNT_FDINFO',
    'WORKSPACE_MOUNT_NAMED_STAT', 'WORKSPACE_MOUNT_REPLACED', 'WORKSPACE_MOUNT_CLOSE',
)


def workspace_need(value, code):
    if not value:
        raise RuntimeError(code)


def workspace_checked(operation, code):
    try:
        return operation()
    except Exception:
        raise RuntimeError(code) from None


def workspace_code_tree(code):
    result = (code,)
    for child in code.co_consts:
        if type(child) is type(code):
            result += workspace_code_tree(child)
    return result


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


def workspace_topology(mounts, visible, inner=False):
    rows = [m for m in mounts if m['destination'] == '/workspace-fixture']
    active = [m for m in rows if m['id'] in visible]
    workspace_need(len(active) == 1 and len(rows) == (2 if inner else 1), 'WORKSPACE_MOUNT_VISIBLE')
    row = active[0]
    workspace_need('rw' in row['options'].split(',') and not row['propagation'], 'WORKSPACE_MOUNT_OPTIONS')
    allowed = {row['id']}
    masks = [m for m in mounts if m['destination'].startswith('/workspace-fixture/')]
    if inner:
        covered = [m for m in rows if m['id'] != row['id']][0]
        workspace_need(row['parent'] == covered['id'] and covered['id'] not in visible
                       and covered['device'] == row['device'] and covered['root'] == row['root']
                       and covered['type'] == row['type'] and covered['source'] == row['source']
                       and covered['super'] == row['super']
                       and 'ro' in covered['options'].split(',') and not covered['propagation'],
                       'WORKSPACE_MOUNT_OPTIONS')
        roots = [m for m in mounts if m['id'] == covered['parent'] and m['id'] in visible
                 and m['destination'] == '/' and 'ro' in m['options'].split(',')]
        workspace_need(len(roots) == 1, 'WORKSPACE_MOUNT_VISIBLE')
        allowed.add(covered['id'])
        names = {'/workspace-fixture/' + name for name in ('.git', '.agents', '.codex', '.aws')}
        workspace_need(len(masks) == 4 and {m['destination'] for m in masks} == names,
                       'WORKSPACE_MOUNT_SUBMOUNT')
        devices = set()
        for mask in masks:
            options = set(mask['options'].split(','))
            super_fields = mask['super'].split(',')
            workspace_need(options <= {'ro', 'nosuid', 'nodev', 'noatime', 'relatime', 'strictatime', 'nodiratime'}
                           and len(options & {'noatime', 'relatime', 'strictatime'}) <= 1
                           and mask['source'] == 'tmpfs'
                           and [part for part in super_fields if part.split('=', 1)[0] == 'mode'] == ['mode=555'],
                           'WORKSPACE_MOUNT_OPTIONS')
            workspace_need(mask['id'] in visible and mask['parent'] == row['id']
                           and mask['root'] == '/' and mask['type'] == 'tmpfs'
                           and {'ro', 'nosuid', 'nodev'} <= set(mask['options'].split(','))
                           and not mask['propagation'], 'WORKSPACE_MOUNT_SUBMOUNT')
            workspace_need(mask['device'] not in devices and mask['device'] != row['device']
                           and sum(m['device'] == mask['device'] for m in mounts) == 1,
                           'WORKSPACE_MOUNT_ALIAS')
            devices.add(mask['device'])
    else:
        workspace_need(not masks, 'WORKSPACE_MOUNT_SUBMOUNT')
    workspace_need(not any(m['id'] not in allowed and m['device'] == row['device'] and
               (m['root'] == row['root'] or m['root'].startswith(row['root'].rstrip('/') + '/')
                or row['root'].startswith(m['root'].rstrip('/') + '/')) for m in mounts), 'WORKSPACE_MOUNT_ALIAS')
    return row, masks


def workspace_masks(masks, owners):
    for mask in masks:
        fd = workspace_checked(lambda: os.open(mask['destination'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC), 'WORKSPACE_MOUNT_OPEN')
        try:
            info = workspace_checked(lambda: os.fstat(fd), 'WORKSPACE_MOUNT_STAT')
            workspace_need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o555
                           and (info.st_uid, info.st_gid) == tuple(owners), 'WORKSPACE_MOUNT_METADATA')
            workspace_need(mask['device'] == str(os.major(info.st_dev)) + ':' + str(os.minor(info.st_dev)), 'WORKSPACE_MOUNT_DEVICE')
            budget = workspace_checked(lambda: os.fstatvfs(fd), 'WORKSPACE_MOUNT_VFS_READ')
            required = os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV
            workspace_need(budget.f_flag & required == required, 'WORKSPACE_MOUNT_READONLY')
            def read_fdinfo():
                with open('/proc/self/fdinfo/' + str(fd), encoding='ascii') as stream:
                    return stream.read(4097)
            raw = workspace_checked(read_fdinfo, 'WORKSPACE_MOUNT_FDINFO_READ')
            workspace_need(len(raw) <= 4096 and [line.split(':',1)[1].strip() for line in raw.splitlines()
                           if line.startswith('mnt_id:')] == [str(mask['id'])], 'WORKSPACE_MOUNT_FDINFO')
            workspace_need(workspace_checked(lambda: os.listdir(fd), 'WORKSPACE_MOUNT_SUBMOUNT') == [], 'WORKSPACE_MOUNT_SUBMOUNT')
            named = workspace_checked(lambda: os.stat(mask['destination'], follow_symlinks=False), 'WORKSPACE_MOUNT_NAMED_STAT')
            after = workspace_checked(lambda: os.fstat(fd), 'WORKSPACE_MOUNT_STAT')
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')
            workspace_need(tuple(getattr(info, k) for k in fields) == tuple(getattr(named, k) for k in fields)
                           == tuple(getattr(after, k) for k in fields), 'WORKSPACE_MOUNT_REPLACED')
        finally:
            workspace_checked(lambda: os.close(fd), 'WORKSPACE_MOUNT_CLOSE')


def workspace_mount(mounts, visible, owners, inner=False):
    context = workspace_context_begin(mounts, visible)
    try:
        return workspace_mount_checked(mounts, visible, owners, inner=inner)
    except BaseException as error:
        workspace_context_attach(error, context)
        raise


def workspace_mount_checked(mounts, visible, owners, inner=False):
    contract = workspace_checked(lambda: workspace_contract_at('/diagnostic/pins.json'), 'WORKSPACE_MOUNT_CONTRACT')
    expected = workspace_checked(lambda: contract['entries']['workspace']['identity'][:2], 'WORKSPACE_MOUNT_IDENTITY_SCHEMA')
    workspace_checked(lambda: proof_pair(expected), 'WORKSPACE_MOUNT_IDENTITY_SCHEMA')
    row, masks = workspace_topology(mounts, visible, inner)
    workspace_masks(masks, owners)
    fd = workspace_checked(lambda: os.open('/workspace-fixture', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC), 'WORKSPACE_MOUNT_OPEN')
    try:
        info = workspace_checked(lambda: os.fstat(fd), 'WORKSPACE_MOUNT_STAT')
        workspace_need([info.st_dev, info.st_ino] == expected and stat.S_IMODE(info.st_mode) == 0o700
                   and (info.st_uid,info.st_gid) == tuple(owners), 'WORKSPACE_MOUNT_METADATA')
        workspace_need(row['device'] == str(os.major(info.st_dev)) + ':' + str(os.minor(info.st_dev)), 'WORKSPACE_MOUNT_DEVICE')
        budget = workspace_checked(lambda: os.fstatvfs(fd), 'WORKSPACE_MOUNT_VFS_READ')
        workspace_need(not budget.f_flag & os.ST_RDONLY, 'WORKSPACE_MOUNT_READONLY')
        def read_fdinfo():
            with open('/proc/self/fdinfo/' + str(fd), encoding='ascii') as stream:
                return stream.read(4097)
        raw = workspace_checked(read_fdinfo, 'WORKSPACE_MOUNT_FDINFO_READ')
        workspace_need(len(raw) <= 4096 and [line.split(':',1)[1].strip() for line in raw.splitlines()
                   if line.startswith('mnt_id:')] == [str(row['id'])], 'WORKSPACE_MOUNT_FDINFO')
        named = workspace_checked(lambda: os.stat('/workspace-fixture', follow_symlinks=False), 'WORKSPACE_MOUNT_NAMED_STAT')
        workspace_need((named.st_dev,named.st_ino,named.st_mode,named.st_uid,named.st_gid) ==
                   (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid), 'WORKSPACE_MOUNT_REPLACED')
        return row['id']
    finally:
        workspace_checked(lambda: os.close(fd), 'WORKSPACE_MOUNT_CLOSE')


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


COMMON += '\n' + Path(__file__).with_name('failure_context.py').read_text('ascii')


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
            integrity = workspace_store.cleanupverify(provision_transaction.identity)
            proof_need(type(integrity) is dict and set(integrity) == {'state', 'fixture_state'}
                       and integrity['state'] == 'WORKSPACE_INTEGRITY_VERIFIED'
                       and integrity['fixture_state'] in ('BEFORE', 'AFTER'))
            result['workspace_integrity'] = integrity
            result['workspace_readback'] = {'state': 'NOT_ACCEPTED', 'fixture_state': integrity['fixture_state']}
            if (integrity['fixture_state'] == 'AFTER' and workspace_verified
                    and result.get('native_runtime') == 'PASS' and result.get('transition') == 'PASS'
                    and result.get('cleanup') == 'PASS' and result.get('status') == 'WORKSPACE_READBACK_PENDING'):
                result['workspace_readback'] = workspace_store.postverify(provision_transaction.identity)
                result['status'] = 'FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE'
            elif result.get('status') == 'WORKSPACE_READBACK_PENDING':
                result['status'] = 'BLOCKED'
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
            source = change(source, 'line = failure_context_line(error) + failure_line(error)',
                'line = workspace_failure_context_line(error, WORKSPACE_MOUNT_CHECKS, failure_line) + failure_context_line(error) + failure_line(error)')
            source = change(source, 'FAILURE_REPORTER_CODES = tuple(code for function in (',
                'FAILURE_CHECKS = FAILURE_CHECKS | frozenset(WORKSPACE_MOUNT_CHECKS)\n\n'
                'FAILURE_REPORTER_CODES = tuple(code for function in (\n'
                '    workspace_mount, workspace_mount_checked, workspace_topology, workspace_masks, workspace_contract_at, workspace_canaries, workspace_need, workspace_checked,\n'
                '    proof_need, proof_pair, proof_int, proof_pairs,')
            source = change(source,
                ') for code in (function.__code__,) + tuple(\n    value for value in function.__code__.co_consts\n    if type(value) is type(function.__code__)))',
                ') for code in workspace_code_tree(function.__code__))')
            source = change(source, "    for mount in mounts:\n        if mount['id'] in visible and 'rw'", "    workspace_id = workspace_mount(mounts, visible, (os.getuid(), os.getgid()), inner=True)\n    workspace_canaries(mounts, visible, (INNER_ROOT_UID, INNER_ROOT_GID), workspace_id)\n    for mount in mounts:\n        if mount['id'] in visible and 'rw'")
            source = change(source, "need((mount['type'], mount['destination']) in", "need(mount['id'] == workspace_id or (mount['type'], mount['destination']) in")
            source = change(source, "proof_inner_record = None", "workspace_payload = {}\nexec(" + repr(payload) + ", workspace_payload)\nproof_inner_record = None")
            source = change(source, "    frame = {'version': 1, 'observation': proof_inner_record, 'report': lines}",
                "    contract_hash = hashlib.sha256(proof_bytes(workspace_contract_at('/diagnostic/pins.json'))).hexdigest()\n"
                "    observation = workspace_payload['run']({'nonce': proof_inner_record['nonce'], 'stage_sha256': contract_hash, 'payload_sha256': WORKSPACE_PAYLOAD_HASH})\n"
                "    workspace_raw = text('/proc/self/mountinfo', 131072)\n"
                "    proof_need(hashlib.sha256(workspace_raw.encode('ascii')).hexdigest() == proof_inner_record['topology_sha256'])\n"
                "    workspace_mounts = parse_mounts(workspace_raw)\n"
                "    workspace_visible, _ = mount_visibility(workspace_mounts)\n"
                "    workspace_id = workspace_mount(workspace_mounts, workspace_visible, (os.getuid(), os.getgid()), inner=True)\n"
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
