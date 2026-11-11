"""Trusted fixed-fixture storage; no CLI, process execution or sandbox authority.

Production entry points require root. The explicitly named fixture entry point
uses only the caller's current UID/GID and retains descriptors opened at creation.
No returned observation authenticates the caller or the supplied transaction.
"""
import hashlib
import json
import os
import stat

WS_BEFORE = b'def add(a, b):\n    return a - b\n'
WS_AFTER = b'def add(a, b):\n    return a + b\n'
WS_TEST = b'assert add(2, 3) == 5\nassert add(-2, 2) == 0\n'
WS_OUTSIDE = b'ZEN53_SYNTHETIC_OUTSIDE_UNCHANGED\n'
WS_SECRET = b'ZEN53_SYNTHETIC_SECRET_NOT_FOR_PAYLOAD\n'
WS_ESCAPE = '/readonly-fixture/outside.txt'
WS_PATCH = ('--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n'
            ' def add(a, b):\n-    return a - b\n+    return a + b\n')
WS_DIRS = {'workspace': 0o700, 'rootfs/readonly-fixture': 0o755,
           'rootfs/secret-fixture': 0o755, 'rootfs/workspace-fixture': 0o755}
WS_FILES = {'workspace/calc.py': (WS_BEFORE, 0o600),
            'workspace/test_calc.py': (WS_TEST, 0o600),
            'rootfs/readonly-fixture/outside.txt': (WS_OUTSIDE, 0o444),
            'rootfs/secret-fixture/canary': (WS_SECRET, 0o000)}


def ws_need(value):
    if not value:
        raise ValueError('WORKSPACE_FIXTURE_REJECTED')


def ws_copy(value):
    return json.loads(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))


def ws_row(info, directory=False):
    row = [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]
    return row if directory else row + [info.st_nlink, info.st_size]


def ws_root():
    ws_need(os.geteuid() == 0)


def ws_identity(value):
    ws_need(type(value) is dict and set(value) == {'pid', 'start', 'uid', 'gid', 'invocation', 'group'})
    for name in ('pid', 'start', 'uid', 'gid'):
        ws_need(type(value[name]) is int and 0 < value[name] < (2**64 if name == 'start' else 2**32))
    ws_need(type(value['invocation']) is str and len(value['invocation']) == 32 and
            all(c in '0123456789abcdef' for c in value['invocation']) and
            value['group'] == '/system.slice/zen53-mincompat.service')


def ws_contract(value):
    ws_need(type(value) is dict and set(value) == {'version', 'bases', 'entries'})
    ws_need(type(value['version']) is int and value['version'] == 1)
    ws_need(type(value['bases']) is dict and set(value['bases']) == {'stage', 'rootfs'})
    ws_need(type(value['entries']) is dict and
            set(value['entries']) == set(WS_DIRS) | set(WS_FILES) | {'workspace/escape'})
    for name, row in value['bases'].items():
        ws_need(type(row) is list and len(row) == 5 and
                all(type(n) is int and 0 <= n < 2**64 for n in row) and stat.S_ISDIR(row[2]))
    for name, item in value['entries'].items():
        extra = 'sha256' if name in WS_FILES else 'target' if name == 'workspace/escape' else None
        ws_need(type(item) is dict and set(item) == ({'identity', extra} if extra else {'identity'}))
        row = item['identity']
        ws_need(type(row) is list and len(row) == (5 if name in WS_DIRS else 7) and
                all(type(n) is int and 0 <= n < 2**64 for n in row))
        if name in WS_DIRS:
            ws_need(stat.S_ISDIR(row[2]) and stat.S_IMODE(row[2]) == WS_DIRS[name])
        elif name in WS_FILES:
            data, mode = WS_FILES[name]
            ws_need(stat.S_ISREG(row[2]) and stat.S_IMODE(row[2]) == mode and row[5] == 1
                    and row[6] == len(data) and item['sha256'] == hashlib.sha256(data).hexdigest())
        else:
            ws_need(stat.S_ISLNK(row[2]) and row[5] == 1 and row[6] == len(WS_ESCAPE)
                    and item['target'] == WS_ESCAPE)


class WorkspaceStore:
    """Own child/file descriptors only; stage/rootfs remain owned by the caller."""
    def __init__(self, stage_fd, rootfs_fd, owner, fixture=False):
        self.stage_fd, self.rootfs_fd = stage_fd, rootfs_fd
        self.owner, self.fixture = owner, fixture
        self.fds = {}
        self.contract = None
        self.phase = 'NEW'
        self.transaction = None
        for fd, mode in ((stage_fd, 0o700), (rootfs_fd, 0o700 if fixture else 0o555)):
            info = os.fstat(fd)
            ws_need(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == owner
                    and stat.S_IMODE(info.st_mode) == mode)
        ws_need(ws_row(os.stat('rootfs', dir_fd=stage_fd, follow_symlinks=False), True)
                == ws_row(os.fstat(rootfs_fd), True))
        ws_need((os.fstat(stage_fd).st_dev, os.fstat(stage_fd).st_ino) !=
                (os.fstat(rootfs_fd).st_dev, os.fstat(rootfs_fd).st_ino))

    def _parent(self, name):
        parent, leaf = name.rsplit('/', 1) if '/' in name else ('', name)
        return (self.stage_fd if parent == '' else self.rootfs_fd if parent == 'rootfs'
                else self.fds[parent]), leaf

    def _open(self, name, directory=False):
        parent, leaf = self._parent(name)
        named = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        ws_need(stat.S_ISDIR(named.st_mode) if directory else stat.S_ISREG(named.st_mode))
        if not directory:
            ws_need(named.st_nlink == 1)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        fd = os.open(leaf, flags, dir_fd=parent)
        self.fds[name] = fd
        ws_need(ws_row(os.fstat(fd), directory) == ws_row(named, directory))
        return fd

    def _read(self, name):
        fd = self.fds[name]
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 1025)
        ws_need(len(data) <= 1024 and not os.read(fd, 1))
        return data

    def _snapshot(self, expected=None):
        stage, rootfs = os.fstat(self.stage_fd), os.fstat(self.rootfs_fd)
        bases = {'stage': ws_row(stage, True), 'rootfs': ws_row(rootfs, True)}
        ws_need(ws_row(os.stat('rootfs', dir_fd=self.stage_fd, follow_symlinks=False), True)
                == ws_row(rootfs, True))
        if expected is not None:
            ws_need(bases == expected['bases'])
        entries = {}
        for name, fd in self.fds.items():
            parent, leaf = self._parent(name)
            held = os.fstat(fd)
            named = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            ws_need(ws_row(held, name in WS_DIRS) == ws_row(named, name in WS_DIRS))
            entries[name] = {'identity': ws_row(held, name in WS_DIRS)}
            if expected is not None:
                ws_need(entries[name]['identity'] == expected['entries'][name]['identity'])
            if name in WS_FILES:
                ws_need(stat.S_ISREG(held.st_mode) and held.st_nlink == 1)
                entries[name]['sha256'] = hashlib.sha256(self._read(name)).hexdigest()
        link = os.stat('escape', dir_fd=self.fds['workspace'], follow_symlinks=False)
        entries['workspace/escape'] = {
            'identity': ws_row(link), 'target': os.readlink('escape', dir_fd=self.fds['workspace'])}
        for name, expected in (('workspace', {'calc.py', 'test_calc.py', 'escape'}),
                               ('rootfs/readonly-fixture', {'outside.txt'}),
                               ('rootfs/secret-fixture', {'canary'}),
                               ('rootfs/workspace-fixture', set())):
            ws_need(set(os.listdir(self.fds[name])) == expected)
        return {'version': 1, 'bases': bases, 'entries': entries}

    def _seed(self):
        ws_need(self.phase == 'NEW')
        self.phase = 'SEEDING'
        for name, mode in WS_DIRS.items():
            parent, leaf = self._parent(name)
            os.mkdir(leaf, mode, dir_fd=parent)
            fd = self._open(name, True)
            os.fchmod(fd, mode)
            os.fsync(parent)
        for name, (data, mode) in WS_FILES.items():
            parent, leaf = self._parent(name)
            fd = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
            self.fds[name] = fd
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                ws_need(count > 0)
                offset += count
            os.fchmod(fd, mode)
            os.fsync(fd)
            os.fsync(parent)
        os.symlink(WS_ESCAPE, 'escape', dir_fd=self.fds['workspace'])
        os.fsync(self.fds['workspace'])
        self.contract = self._snapshot()
        ws_contract(self.contract)
        ws_need(all(tuple(item['identity'][3:5]) == self.owner
                    for item in self.contract['entries'].values()))
        self.phase = 'SEEDED'
        return self

    def _verify(self, expected):
        ws_need(self.phase != 'CLOSED' and self._snapshot(expected) == expected)

    def observe(self):
        """Actual bounded metadata/hashes, never canary contents or acceptance."""
        if not self.fixture:
            ws_root()
        ws_need(self.phase in ('SEEDED', 'PROVISIONED'))
        return self._snapshot(self.provisioned_contract if self.phase == 'PROVISIONED' else self.contract)

    def provision(self, identity):
        ws_root()
        ws_need(not self.fixture)
        return self._provision(identity)

    def provision_fixture(self, identity):
        ws_need(self.fixture and (identity['uid'], identity['gid']) == self.owner)
        return self._provision(identity)

    def _provision(self, identity):
        ws_identity(identity)
        ws_need(self.phase == 'SEEDED')
        ws_contract(self.contract)
        ws_need(all(tuple(item['identity'][3:5]) == self.owner
                    for item in self.contract['entries'].values()))
        self._verify(self.contract)
        self.phase = 'PROVISIONING'
        for name in ('workspace/calc.py', 'workspace/test_calc.py', 'workspace'):
            os.fchown(self.fds[name], identity['uid'], identity['gid'])
            os.fsync(self.fds[name])
        expected = ws_copy(self.contract)
        for name in ('workspace/calc.py', 'workspace/test_calc.py', 'workspace'):
            expected['entries'][name]['identity'][3:5] = [identity['uid'], identity['gid']]
        self._verify(expected)
        self.transaction = ws_copy(identity)
        self.provisioned_contract = expected
        self.phase = 'PROVISIONED'
        return ws_copy(expected)

    def postverify(self, identity):
        ws_root()
        ws_need(not self.fixture)
        return self._postverify(identity)

    def postverify_fixture(self, identity):
        ws_need(self.fixture)
        return self._postverify(identity)

    def cleanupverify(self, identity):
        """Integrity only; an unchanged fixture does not establish task success."""
        ws_root()
        ws_need(not self.fixture)
        return self._cleanupverify(identity)

    def cleanupverify_fixture(self, identity):
        ws_need(self.fixture)
        return self._cleanupverify(identity)

    def _cleanupverify(self, identity):
        ws_identity(identity)
        ws_need(self.phase == 'PROVISIONED' and identity == self.transaction)
        before = self.provisioned_contract
        after = ws_copy(before)
        after['entries']['workspace/calc.py']['sha256'] = hashlib.sha256(WS_AFTER).hexdigest()
        # These fixed alternatives have identical metadata, including size.
        # Preserve the existing metadata-before-read guard for both states.
        ws_need(len(WS_BEFORE) == len(WS_AFTER))
        observed = self._snapshot(before)
        ws_need(observed == before or observed == after)
        return {'state': 'WORKSPACE_INTEGRITY_VERIFIED',
                'fixture_state': 'BEFORE' if observed == before else 'AFTER'}

    def _postverify(self, identity):
        ws_identity(identity)
        ws_need(self.phase == 'PROVISIONED' and identity == self.transaction)
        expected = ws_copy(self.provisioned_contract)
        expected['entries']['workspace/calc.py']['sha256'] = hashlib.sha256(WS_AFTER).hexdigest()
        expected['entries']['workspace/calc.py']['identity'][6] = len(WS_AFTER)
        self._verify(expected)
        ws_need(len(WS_PATCH.encode('ascii')) <= 1024)
        result = {'state': 'FIXED_WORKSPACE_READBACK_ONLY', 'patch': WS_PATCH,
                  'patch_sha256': hashlib.sha256(WS_PATCH.encode('ascii')).hexdigest(),
                  'workspace_sha256': hashlib.sha256(WS_AFTER + b'\0' + WS_TEST).hexdigest()}
        ws_need(len(json.dumps(result).encode('ascii')) <= 2048)
        return result

    def close(self):
        self.phase = 'CLOSED'
        failed = False
        while self.fds:
            _, fd = self.fds.popitem()
            try:
                os.close(fd)
            except OSError:
                failed = True
        ws_need(not failed)


def seed(stage_fd, rootfs_fd):
    ws_root()
    return _seed_store(stage_fd, rootfs_fd, (0, 0), False)


def seed_fixture(stage_fd, rootfs_fd):
    """Local test only: no arbitrary UID selection and no runtime authority."""
    ws_need(os.geteuid() != 0)
    return _seed_store(stage_fd, rootfs_fd, (os.geteuid(), os.getegid()), True)


def _seed_store(stage_fd, rootfs_fd, owner, fixture):
    store = WorkspaceStore(stage_fd, rootfs_fd, owner, fixture)
    try:
        return store._seed()
    except BaseException:
        store.close()
        raise


def open_verified(stage_fd, rootfs_fd, contract):
    ws_root()
    ws_contract(contract)
    ws_need(all(item['identity'][3:5] == [0, 0] for item in contract['entries'].values()))
    store = WorkspaceStore(stage_fd, rootfs_fd, (0, 0))
    try:
        for name in WS_DIRS:
            store._open(name, True)
        for name in WS_FILES:
            store._open(name)
        store.contract = ws_copy(contract)
        store._verify(store.contract)
        store.phase = 'SEEDED'
        return store
    except BaseException:
        store.close()
        raise
