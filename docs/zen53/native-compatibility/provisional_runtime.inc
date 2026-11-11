# Shared fixed-report proof. Definitions only; injected into authenticated sources.
PROOF_PREFIX = 'ZEN53_PROVISIONAL_V1 '
PROOF_PATHS = ('root', 'cwd', 'fd/0', 'fd/1', 'fd/2', 'ns/mnt', 'ns/user')


def proof_need(value):
    if not value:
        raise RuntimeError('PROVISIONAL_PROOF_INVALID')


def proof_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def proof_pairs(items):
    result = {}
    for key, value in items:
        proof_need(key not in result)
        result[key] = value
    return result


def proof_keys(value, names):
    proof_need(type(value) is dict and set(value) == set(names.split()))


def proof_int(value, minimum=0):
    proof_need(type(value) is int and minimum <= value < 2**64)


def proof_hash(value):
    proof_need(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value))


def proof_pair(value):
    proof_need(type(value) is list and len(value) == 2)
    for item in value:
        proof_int(item, 1)


def proof_parse(line):
    proof_need(type(line) is bytes and len(line) <= 8192 and line.startswith(PROOF_PREFIX.encode())
               and line.endswith(b'\n') and line.count(b'\n') == 1)
    raw = line[len(PROOF_PREFIX):-1]
    frame = json.loads(raw, object_pairs_hook=proof_pairs,
                       parse_constant=lambda value: proof_need(False))
    proof_need(proof_bytes(frame) == raw)
    proof_keys(frame, 'version observation report')
    proof_need(type(frame['version']) is int and frame['version'] == 1)
    proof_need(type(frame['report']) is list and 1 <= len(frame['report']) <= 64 and
               all(type(s) is str and len(s) <= 256 and s.isascii() and
                   all(32 <= ord(c) <= 126 for c in s) for s in frame['report']))
    proof_observation(frame['observation'])
    proof_report(frame['report'])
    return frame


def proof_identity(value):
    proof_keys(value, 'pid start uid gid invocation group')
    for name in ('pid', 'start', 'uid', 'gid'):
        proof_int(value[name], 1)
    proof_need(type(value['invocation']) is str and len(value['invocation']) == 32 and
               all(c in '0123456789abcdef' for c in value['invocation']) and
               value['group'] == '/system.slice/zen53-mincompat.service')


def proof_proc(value):
    proof_keys(value, 'identity mount')
    proof_pair(value['identity'])
    row = value['mount']
    proof_keys(row, 'device root type options super')
    proof_need(row['root'] == '/' and row['type'] == 'proc')
    proof_need(type(row['device']) is str and len(row['device']) < 32 and
               len(row['device'].split(':')) == 2 and
               all(p.isascii() and p.isdecimal() for p in row['device'].split(':')))
    for name in ('options', 'super'):
        proof_need(type(row[name]) is str and 0 < len(row[name]) <= 256 and
                   row[name].isascii() and all(32 < ord(c) < 127 for c in row[name]))
    proof_need('hidepid=invisible' in row['super'].split(',') or 'hidepid=2' in row['super'].split(','))


def proof_observation(value):
    proof_keys(value, 'nonce provision outer proc namespaces maps outcomes topology_sha256 fd_checked topology_checked')
    proof_hash(value['nonce']); proof_hash(value['provision']); proof_hash(value['topology_sha256'])
    proof_identity(value['outer']); proof_proc(value['proc'])
    proof_keys(value['namespaces'], 'pid mnt net user')
    for number in value['namespaces'].values():
        proof_int(number, 1)
    proof_need(type(value['maps']) is list and len(value['maps']) == 2)
    for row in value['maps']:
        proof_need(type(row) is list and len(row) == 3)
        for number in row:
            proof_int(number)
        proof_need(row[2] == 1 and row[0] > 0)
    proof_need(type(value['outcomes']) is list and len(value['outcomes']) == 7)
    for path, item in zip(PROOF_PATHS, value['outcomes']):
        proof_keys(item, 'path errno')
        proof_need(item['path'] == path and type(item['errno']) is int and item['errno'] in (1, 2, 13))
    proof_need(value['fd_checked'] is True and value['topology_checked'] is True)


def proof_snapshot(value):
    proof_keys(value, 'identity proc namespaces targets nonce provision')
    proof_identity(value['identity']); proof_proc(value['proc'])
    proof_hash(value['nonce']); proof_hash(value['provision'])
    proof_keys(value['namespaces'], 'pid mnt net user')
    for number in value['namespaces'].values():
        proof_int(number, 1)
    proof_need(type(value['targets']) is list and len(value['targets']) == 7)
    for path, target in zip(PROOF_PATHS, value['targets']):
        proof_keys(target, 'path metadata')
        proof_need(target['path'] == path and type(target['metadata']) is list and len(target['metadata']) == 3)
        for number in target['metadata']:
            proof_int(number)


def proof_verify(line, before, after):
    frame = proof_parse(line)
    proof_snapshot(before); proof_snapshot(after)
    proof_need(before == after)
    observed = frame['observation']
    proof_need(observed['nonce'] == before['nonce'] and observed['provision'] == before['provision']
               and observed['outer'] == before['identity'])
    # Held procfs roots identify the same instance and root, hence the same PID
    # addressing namespace. Mount IDs/options may differ across RO bind mounts.
    proof_need(observed['proc']['identity'] == before['proc']['identity'] and
               observed['proc']['mount']['device'] == before['proc']['mount']['device'] and
               observed['proc']['mount']['super'] == before['proc']['mount']['super'])
    proof_need('ro' in observed['proc']['mount']['options'].split(','))
    proof_need(all(observed['namespaces'][n] != before['namespaces'][n]
                   for n in ('pid', 'mnt', 'net', 'user')))
    outer = before['identity']
    proof_need([row[1] for row in observed['maps']] in ([0, 0], [outer['uid'], outer['gid']]))
    values = proof_report(frame['report'])
    proof_need(values['apparmor'] == 'bwrap//&unpriv_bwrap (enforce)' and
               values['status.NoNewPrivs'] == '1' and values['status.Seccomp'] == '2' and
               all(int(values['status.' + key], 16) == 0 for key in
                   ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')) and
               all(values['bookkeeping.' + key] == '1' for key in
                   ('readonly', 'no_aliases', 'no_inherited_fds')) and
               values['bookkeeping.tmpdir'] == '/run/zen53-bookkeeping' and
               values['bookkeeping.proc_mode'] == 'inherited')
    for name, number in observed['namespaces'].items():
        proof_need(values['namespace.' + name] == name + ':[' + str(number) + ']')
    for index, name in enumerate(('Uid', 'Gid')):
        proof_need(values['status.' + name].split() == [str(observed['maps'][index][0])] * 4)
    nspid = values['status.NSpid'].split()
    proof_need(2 <= len(nspid) <= 8 and all(p.isascii() and p.isdecimal() and int(p) > 0 for p in nspid)
               and values['status.Pid'] == nspid[0])
    return {'version': 1, 'state': 'LIVE_TARGET_INACCESSIBLE',
            'frame_sha256': hashlib.sha256(line).hexdigest(),
            'reference_sha256': hashlib.sha256(proof_bytes(before)).hexdigest()}


def proof_metadata(fd):
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino, info.st_mode]


def proof_namespaces(procfd):
    result = {}
    for name in ('pid', 'mnt', 'net', 'user'):
        raw = os.readlink('ns/' + name, dir_fd=procfd)
        proof_need(raw.startswith(name + ':[') and raw.endswith(']') and raw[len(name)+2:-1].isdecimal())
        result[name] = int(raw[len(name)+2:-1])
    return result


def proof_proc_binding(fd, raw, pid):
    mounts = parse_mounts(raw)
    visible, _ = mount_visibility(mounts)
    rows = [m for m in mounts if m['id'] in visible and m['destination'] == '/proc']
    proof_need(len(rows) == 1)
    prefix = '/proc/' + str(pid)
    # Reject every overlay at/below the numeric process route, even hidden rows.
    proof_need(not any(m['destination'] == prefix or m['destination'].startswith(prefix + '/') for m in mounts))
    info = os.fstat(fd)
    named = os.stat('/proc', follow_symlinks=False)
    proof_need((info.st_dev, info.st_ino, info.st_mode) == (named.st_dev, named.st_ino, named.st_mode)
               and stat.S_ISDIR(info.st_mode))
    row = rows[0]
    proof_need(row['device'] == str(os.major(info.st_dev)) + ':' + str(os.minor(info.st_dev)))
    result = {'identity': [info.st_dev, info.st_ino],
              'mount': {k: row[k] for k in ('device', 'root', 'type', 'options', 'super')}}
    proof_proc(result)
    return result


class ProofOuter:
    def __init__(self):
        self.owned = []
        self.before = None

    def own(self, fd):
        self.owned.append(fd)
        os.set_inheritable(fd, False)
        return fd

    def begin(self):
        self.worker = provision_worker
        self.pidfd = self.own(os.pidfd_open(os.getpid(), 0))
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        self.rootfd = self.own(os.open('/proc', flags))
        self.procfd = self.own(os.open(str(os.getpid()), flags, dir_fd=self.rootfd))
        proof_need(proof_metadata(self.procfd) == proof_metadata(self.worker.procfd))
        self.targets = [self.own(os.open(p, os.O_PATH | os.O_CLOEXEC, dir_fd=self.procfd)) for p in PROOF_PATHS]
        self.before = self.sample()

    def sample(self):
        proof_need(not select.select([self.pidfd], [], [], 0)[0])
        ident = parse_proc_identity(self.procfd, self.worker.identity['invocation'], self.worker.identity['group'])
        proof_need(ident == self.worker.identity and ident['pid'] == os.getpid())
        raw = read_at(self.procfd, 'mountinfo', 131072).decode('ascii')
        proc = proof_proc_binding(self.rootfd, raw, ident['pid'])
        targets = []
        for name, held in zip(PROOF_PATHS, self.targets):
            fd = os.open(name, os.O_PATH | os.O_CLOEXEC, dir_fd=self.procfd)
            try:
                proof_need(proof_metadata(fd) == proof_metadata(held))
                targets.append({'path': name, 'metadata': proof_metadata(fd)})
            finally:
                os.close(fd)
        proof_need(not select.select([self.pidfd], [], [], 0)[0])
        return {'identity': ident, 'proc': proc, 'namespaces': proof_namespaces(self.procfd),
                'targets': targets, 'nonce': self.worker.attempt['nonce'],
                'provision': hashlib.sha256(canonical_record(self.worker.provision)).hexdigest()}

    def finish(self, output):
        lines = output.splitlines(keepends=True)
        frames = [line for line in lines if line.startswith(PROOF_PREFIX.encode())]
        proof_need(len(frames) == 1 and all(line == frames[0] or line == EXPECTED_ALIAS_WARNING for line in lines))
        after = self.sample()
        certificate = proof_verify(frames[0], self.before, after)
        emit('native_proof', certificate=certificate, frame=frames[0].decode('ascii'), before=self.before, after=after)
        return certificate

    def close(self):
        errors = []
        while self.owned:
            try:
                os.close(self.owned.pop())
            except OSError as error:
                errors.append(error)
        if errors:
            raise errors[0]


def proof_collect(pid, raw, attempt, provision, release, mappings, namespaces):
    fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        proc = proof_proc_binding(fd, raw, pid)
        outcomes = []
        for path in PROOF_PATHS:
            try:
                target = os.open(str(pid) + '/' + path, os.O_PATH | os.O_CLOEXEC, dir_fd=fd)
            except OSError as error:
                proof_need(type(error.errno) is int and error.errno in (1, 2, 13))
                outcomes.append({'path': path, 'errno': error.errno})
            else:
                os.close(target)
                proof_need(False)
        after = text('/proc/self/mountinfo', 131072)
        proof_need(after == raw and proof_proc_binding(fd, after, pid) == proc)
        return {'nonce': attempt['nonce'], 'provision': release['provision_sha256'],
                'outer': provision['identity'], 'proc': proc,
                'namespaces': {k: int(v[len(k)+2:-1]) for k, v in namespaces.items()},
                'maps': [list(row) for row in mappings], 'outcomes': outcomes,
                'topology_sha256': hashlib.sha256(raw.encode('ascii')).hexdigest(),
                'fd_checked': False, 'topology_checked': False}
    finally:
        os.close(fd)


def proof_report(lines):
    proof_need(lines[0] == 'ZEN53_REPORT_V1' and lines[-1] == 'ZEN53_REPORT_END')
    fields = {}
    for line in lines[1:-1]:
        proof_need('=' in line)
        key, value = line.split('=', 1)
        proof_need(key not in fields)
        fields[key] = value
    status = ('Name', 'Pid', 'PPid', 'Uid', 'Gid', 'NSpid', 'CapInh', 'CapPrm',
              'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs', 'Seccomp')
    keys = {'apparmor'} | {'status.' + k for k in status} | {'namespace.' + k for k in ('pid', 'mnt', 'net', 'user')}
    keys |= {'bookkeeping.' + k for k in ('readonly', 'no_aliases', 'no_inherited_fds',
             'outer_proc_denied', 'tmpdir', 'proc_mode', 'device', 'inode')}
    proof_need(set(fields) == keys and fields['bookkeeping.outer_proc_denied'] == 'provisional')
    proof_need(fields['status.Name'] in ('python3', 'python3.12'))
    for key in ('Pid', 'PPid', 'NoNewPrivs', 'Seccomp'):
        value = fields['status.' + key]
        proof_need(value.isascii() and value.isdecimal() and str(int(value)) == value and int(value) < 2**32)
    for key, count in (('Uid', 4), ('Gid', 4), ('NSpid', None)):
        value = fields['status.' + key]
        parts = value.split(' ')
        proof_need((len(parts) == count if count else 2 <= len(parts) <= 8) and
                   all(p.isascii() and p.isdecimal() and str(int(p)) == p and int(p) < 2**32 for p in parts))
    for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        value = fields['status.' + key]
        proof_need(1 <= len(value) <= 16 and all(c in '0123456789abcdef' for c in value))
    for key in ('pid', 'mnt', 'net', 'user'):
        value = fields['namespace.' + key]
        proof_need(value.startswith(key + ':[') and value.endswith(']') and value[len(key)+2:-1].isdecimal())
    for key in ('readonly', 'no_aliases', 'no_inherited_fds'):
        proof_need(fields['bookkeeping.' + key] == '1')
    proof_need(fields['apparmor'] == 'bwrap//&unpriv_bwrap (enforce)' and
               fields['bookkeeping.tmpdir'] == '/run/zen53-bookkeeping' and
               fields['bookkeeping.proc_mode'] == 'inherited')
    device = fields['bookkeeping.device'].split(':')
    proof_need(len(device) == 2 and all(p.isascii() and p.isdecimal() for p in device) and
               fields['bookkeeping.inode'].isascii() and fields['bookkeeping.inode'].isdecimal())
    return fields


def proof_supervisor(raw, expected_binding):
    proof_keys(expected_binding, 'identity nonce provision')
    proof_identity(expected_binding['identity'])
    proof_hash(expected_binding['nonce']); proof_hash(expected_binding['provision'])
    records = [json.loads(line, object_pairs_hook=proof_pairs) for line in raw.splitlines()]
    def one(phase):
        selected = [r for r in records if type(r) is dict and r.get('phase') == phase]
        proof_need(len(selected) == 1)
        return selected[0]
    attestation = one('native_proof')
    finish = one('native_finish')
    complete = one('launcher_complete')
    proof_keys(attestation, 'phase monotonic_ns certificate frame before after')
    proof_keys(finish, 'phase monotonic_ns pid wait_status output')
    proof_keys(complete, 'phase monotonic_ns result')
    for record in (finish, attestation, complete):
        proof_int(record['monotonic_ns'], 1)
    proof_int(finish['pid'], 1)
    proof_need(records.index(finish) < records.index(attestation) < records.index(complete)
               and complete.get('result') == 'REPORTER_FINISHED_NOT_ISOLATION_ACCEPTANCE'
               and type(finish.get('wait_status')) is int and finish['wait_status'] == 0)
    line = attestation['frame'].encode('ascii')
    output = finish['output'].encode('ascii')
    frames = [item for item in output.splitlines(keepends=True) if item.startswith(PROOF_PREFIX.encode())]
    proof_need(frames == [line])
    warning = b'WARNING: proceeding, even though we could not create PATH aliases: Read-only file system (os error 30)\n'
    proof_need(all(item == line or item == warning for item in output.splitlines(keepends=True))
               and output.splitlines(keepends=True).count(warning) <= 1)
    expected = proof_verify(line, attestation['before'], attestation['after'])
    proof_need(all(attestation['before'][key] == value for key, value in expected_binding.items()))
    proof_need(proof_bytes(attestation['certificate']) == proof_bytes(expected))
    frame = proof_parse(line)
    proof_report(frame['report'])
    return frame['report']
