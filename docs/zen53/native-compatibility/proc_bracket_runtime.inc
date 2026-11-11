"""Injected launcher-only observations; never changes reporter acceptance.

Executed definitions use the launcher's existing imports and pinned worker.
This file is not imported by the staged runtime or run as an entrypoint.
"""


class ProcBracket:
    def __init__(self):
        self.fd = None
        self.before = None
        self.seen = False

    def sample(self):
        worker = provision_worker
        require(self.fd is not None and not select.select([self.fd], [], [], 0)[0],
                'BRACKET_PROCESS_DEAD')
        identity = parse_proc_identity(worker.procfd, worker.identity['invocation'],
                                       worker.identity['group'])
        require(identity == worker.identity and identity['pid'] == os.getpid(),
                'BRACKET_IDENTITY')
        namespaces = {}
        for name in ('pid', 'mnt', 'net', 'user', 'time'):
            value = os.readlink('ns/' + name, dir_fd=worker.procfd)
            prefix = name + ':['
            require(value.startswith(prefix) and value.endswith(']') and
                    value[len(prefix):-1].isdecimal(), 'BRACKET_NAMESPACE')
            namespaces[name] = int(value[len(prefix):-1])
        numeric = {}
        for name in ('uid_map', 'gid_map'):
            rows = read_at(worker.procfd, name, 256).decode('ascii').splitlines()
            require(len(rows) == 1 and len(rows[0].split()) == 3 and
                    all(p.isdecimal() for p in rows[0].split()), 'BRACKET_MAPPING')
            numeric[name] = [int(p) for p in rows[0].split()]
        mounts = parse_mounts(read_at(worker.procfd, 'mountinfo', 131072).decode('ascii'))
        visible, _ = mount_visibility(mounts)
        proc = [m for m in mounts if m['id'] in visible and m['destination'] == '/proc']
        require(len(proc) == 1 and proc[0]['type'] == 'proc', 'BRACKET_PROC')
        proc = {k: proc[0][k] for k in ('id', 'device', 'root', 'options', 'super')}
        require(not select.select([self.fd], [], [], 0)[0], 'BRACKET_PROCESS_DEAD')
        return dict(identity=identity, namespaces=namespaces, mappings=numeric,
                    proc=proc, at_ns=time.monotonic_ns())

    def begin(self):
        try:
            self.fd = os.pidfd_open(os.getpid(), 0)
            os.set_inheritable(self.fd, False)
            self.before = self.sample()
        except Exception:
            self.before = None

    def observe(self, line):
        if not line.startswith(b'ZEN53_REPORT_CONTEXT ') or self.seen:
            return
        result = {'state': 'UNKNOWN', 'context_sha256': hashlib.sha256(line).hexdigest()}
        try:
            require(not self.seen and self.before is not None and len(line) <= 512,
                    'BRACKET_CONTEXT')
            fields = line.decode('ascii').strip().split(' ')
            require(len(fields) == 11 and fields[:2] == ['ZEN53_REPORT_CONTEXT', 'version=1'],
                    'BRACKET_CONTEXT')
            names = ('nspid', 'uid', 'gid', 'uidmap', 'gidmap', 'outer', 'ns', 'proc', 'clock')
            sizes = (None, 4, 4, 3, 3, 2, 4, 3, 2)
            context = {}
            for field, name, count in zip(fields[2:], names, sizes):
                key, value = field.split('=', 1)
                parts = value.split(',')
                require(key == name and 0 < len(parts) <= 8 and
                        (count is None or len(parts) == count) and
                        all(p.isdecimal() and 0 <= int(p) < 2**64 for p in parts),
                        'BRACKET_CONTEXT')
                context[name] = [int(p) for p in parts]
            before = self.before
            after = self.sample()
            require(context['outer'] == [before['identity']['pid'], before['identity']['start']]
                    and before['identity'] == after['identity'] and
                    before['namespaces'] == after['namespaces'] and
                    before['mappings'] == after['mappings'] and before['proc'] == after['proc']
                    and before['at_ns'] <= after['at_ns'] and
                    0 < context['clock'][0] <= context['clock'][1], 'BRACKET_BINDING')
            result.update(state='BOUND_OUTER_LIVE_BEFORE_FORK_AND_AFTER_CONTEXT',
                          before=before, after=after,
                          context=context,
                          meaning='OBSERVATION_ONLY_NOT_ACCEPTANCE')
        except Exception:
            pass
        finally:
            self.seen = True
        # Fixed, one-line bounded observation; no raw paths or exception strings.
        if len(json.dumps(result)) > 4096:
            result = {'state': 'UNKNOWN', 'context_sha256': result['context_sha256']}
        emit('outer_probe_bracket', **result)

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                os.close(fd)
            except OSError:
                pass  # Process exit still closes it; never mask the primary failure.


native_bracket = ProcBracket()
