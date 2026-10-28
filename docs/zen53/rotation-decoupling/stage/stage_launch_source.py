"""Trusted read-only admission and exactly one fixed native invocation.
Not executed during preparation. No alternate helper or unsandboxed fallback.
"""
import os
import sys
import time
import select
import json
import stat
import hashlib

ENV = {"PATH": "/usr/bin:/bin", "HOME": "/empty-home", "CODEX_HOME": "/empty-home",
       "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/run/zen53-bookkeeping", "TERM": "dumb",
       "NO_COLOR": "1", "RUST_LOG": "off", "PYTHONDONTWRITEBYTECODE": "1"}
NATIVE = ["/opt/codex/bin/codex", "--no-daemon",
          "-c", 'approval_policy="never"',
          "-c", 'web_search="disabled"',
          "-c", 'features.network_proxy=false',
          "-c", 'history.persistence="none"',
          "sandbox", "--include-managed-config", "--permission-profile", "zen53_diagnostic",
          "-C", "/diagnostic", "--", "/usr/bin/python3", "-I", "-S", "-B", "/diagnostic/report.py"]


# BEGIN ZEN53 provisioning protocol (no module-level operations).
PROVISION_VERSION = 1
PROVISION_WAIT_NS = 5_000_000_000
RELEASE_LEASE_NS = 1_000_000_000


def require(value, code):
    if not value:
        raise RuntimeError(code)


def object_id(info):
    return [info.st_dev, info.st_ino]


def directory_info(info, uid, mode, gid=0):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == mode, "PROVISION_DIRECTORY_METADATA")


def open_directory(parent, name):
    require(type(name) is str and name not in ("", ".", "..") and "/" not in name,
            "PROVISION_COMPONENT")
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=parent)


def bounded_read(fd, limit=4096):
    data = bytearray()
    while True:
        part = os.read(fd, min(4096, limit + 1 - len(data)))
        if not part:
            return bytes(data)
        data.extend(part)
        require(len(data) <= limit, "PROVISION_READ_BOUND")


def read_at(parent, name, limit=4096):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        return bounded_read(fd, limit)
    finally:
        os.close(fd)


def canonical_record(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def read_record(parent, name, missing=False):
    try:
        fd = os.open(name + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        if missing:
            return None
        raise
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o444 and info.st_nlink == 2,
                "PROVISION_RECORD_METADATA")
        require(object_id(os.stat("." + name + ".sealed", dir_fd=parent, follow_symlinks=False))
                == object_id(info), "PROVISION_RECORD_ALIAS")
        raw = bounded_read(fd)
        value = json.loads(raw)
        require(canonical_record(value) == raw, "PROVISION_RECORD_ENCODING")
        require(object_id(os.stat(name + ".json", dir_fd=parent, follow_symlinks=False))
                == object_id(info), "PROVISION_RECORD_REPLACED")
        return value
    finally:
        os.close(fd)


def exact_keys(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()), "PROVISION_RECORD_SCHEMA")


def valid_nonce(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def valid_identity(value):
    exact_keys(value, "pid start uid gid invocation group")
    require(all(type(value[k]) is int and 0 < value[k] < 2**32 - 1
                for k in ("pid", "uid", "gid")) and type(value["start"]) is int
            and value["start"] > 0 and type(value["invocation"]) is str
            and len(value["invocation"]) == 32
            and all(c in "0123456789abcdef" for c in value["invocation"])
            and value["group"] == "/system.slice/zen53-mincompat.service", "PROVISION_IDENTITY_SCHEMA")


def valid_attempt(value):
    exact_keys(value, "version nonce parent created_ns deadline_ns")
    require(type(value["version"]) is int and value["version"] == PROVISION_VERSION
            and valid_nonce(value["nonce"]) and type(value["parent"]) is list
            and len(value["parent"]) == 2 and all(type(n) is int and n > 0 for n in value["parent"])
            and type(value["created_ns"]) is int and value["created_ns"] > 0
            and type(value["deadline_ns"]) is int
            and value["deadline_ns"] == value["created_ns"] + PROVISION_WAIT_NS,
            "PROVISION_ATTEMPT_SCHEMA")


def parse_proc_identity(procfd, invocation, group):
    # procfd is a pinned /proc/PID directory, never a reusable pathname.
    raw = read_at(procfd, "stat").decode("ascii")
    pid = int(raw.split(" ", 1)[0])
    fields = raw.rsplit(") ", 1)[1].split()
    require(fields[0] not in ("Z", "X", "x"), "PROVISION_PROCESS_DEAD")
    status = dict(line.split(":", 1) for line in read_at(procfd, "status", 16384).decode("ascii").splitlines()
                  if ":" in line)
    uid = [int(v) for v in status["Uid"].split()]
    gid = [int(v) for v in status["Gid"].split()]
    require(len(uid) == len(gid) == 4 and len(set(uid)) == len(set(gid)) == 1,
            "PROVISION_PROCESS_CREDENTIALS")
    require(read_at(procfd, "cgroup").decode("ascii") == "0::" + group + "\n",
            "PROVISION_CGROUP")
    value = dict(pid=pid, start=int(fields[19]), uid=uid[0], gid=gid[0],
                 invocation=invocation, group=group)
    valid_identity(value)
    return value
# END ZEN53 shared protocol.


def unique_release_request(raw):
    require(type(raw) is bytes and len(raw) <= 65536, "PROVISION_LOG_BOUND")
    requests = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue  # An incomplete tail cannot authorize release.
        try:
            item = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if type(item) is dict and item.get("phase") == "release_request":
            require(canonical_record(item) == line, "PROVISION_REQUEST_ENCODING")
            requests.append(item)
    require(len(requests) <= 1, "PROVISION_DUPLICATE_REQUEST")
    return requests[0] if requests else None

class ProvisionWorker:
    """Read-only launcher-side protocol. No creation, chown or write operations."""
    def __init__(self):
        self.readyfd = self.tmpfd = self.procfd = self.childfd = None
        self.owned = []
        self.attempt = self.provision = self.release_record = None
        self.identity = None

    def own(self, fd):
        self.owned.append(fd)
        return fd

    def inventory(self):
        allowed = {"." + n + ".sealed" for n in ("attempt", "provision", "release")}
        allowed |= {n + ".json" for n in ("attempt", "provision", "release")}
        require(set(os.listdir(self.readyfd)) <= allowed, "PROVISION_READINESS_EXTRA")
        directory_info(os.fstat(self.readyfd), 0, 0o555)
        require(object_id(os.stat("diagnostic", dir_fd=self.rootfd, follow_symlinks=False))
                == object_id(os.fstat(self.diagfd)) and
                object_id(os.stat("readiness", dir_fd=self.diagfd, follow_symlinks=False))
                == object_id(os.fstat(self.readyfd)), "PROVISION_READINESS_REPLACED")
        require(os.fstatvfs(self.readyfd).f_flag & os.ST_RDONLY, "PROVISION_READINESS_WRITABLE")

    def initialize(self):
        root = self.own(os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
        diagnostic = self.own(open_directory(root, "diagnostic"))
        directory_info(os.fstat(diagnostic), 0, 0o555)
        self.diagfd = diagnostic
        self.rootfd = root
        self.readyfd = self.own(open_directory(diagnostic, "readiness"))
        self.tmpfd = self.own(open_directory(root, "tmp"))
        self.procfd = self.own(os.open("/proc/" + str(os.getpid()), os.O_RDONLY | os.O_DIRECTORY
                                      | os.O_NOFOLLOW | os.O_CLOEXEC))
        self.identity = parse_proc_identity(self.procfd, os.environ.get("INVOCATION_ID", ""),
                                           "/system.slice/zen53-mincompat.service")
        require(self.identity["pid"] == os.getpid() and self.identity["uid"] == os.geteuid()
                and self.identity["gid"] == os.getegid(), "PROVISION_SELF_IDENTITY")
        self.inventory()
        self.attempt = read_record(self.readyfd, "attempt")
        valid_attempt(self.attempt)
        now = time.monotonic_ns()
        require(self.attempt["created_ns"] <= now < self.attempt["deadline_ns"], "PROVISION_ATTEMPT_STALE")
        self.provision = self.wait_record("provision", self.attempt["deadline_ns"])
        exact_keys(self.provision, "version nonce identity parent child")
        valid_identity(self.provision["identity"])
        require(type(self.provision["version"]) is int and self.provision["version"] == 1
                and self.provision["nonce"] == self.attempt["nonce"]
                and self.provision["identity"] == self.identity
                and self.provision["parent"] == self.attempt["parent"]
                and type(self.provision["child"]) is list and len(self.provision["child"]) == 2
                and all(type(n) is int and n > 0 for n in self.provision["child"]),
                "PROVISION_MISMATCH")
        self.childfd = self.own(open_directory(self.tmpfd, "codex-daemon-" + str(self.identity["uid"])))
        self.check_tree()

    def wait_record(self, name, deadline):
        while time.monotonic_ns() < deadline:
            self.inventory()
            value = read_record(self.readyfd, name, missing=True)
            if value is not None:
                return value
            time.sleep(0.01)
        raise RuntimeError("PROVISION_WAIT_TIMEOUT")

    def check_tree(self):
        self.inventory()
        require(read_record(self.readyfd, "attempt") == self.attempt
                and read_record(self.readyfd, "provision") == self.provision,
                "PROVISION_RECORD_CHANGED")
        require(parse_proc_identity(self.procfd, self.identity["invocation"], self.identity["group"])
                == self.identity, "PROVISION_SELF_CHANGED")
        directory_info(os.fstat(self.tmpfd), 0, 0o555)
        child = os.fstat(self.childfd)
        directory_info(child, self.identity["uid"], 0o700, self.identity["gid"])
        name = "codex-daemon-" + str(self.identity["uid"])
        require(child.st_gid == self.identity["gid"]
                and object_id(os.fstat(self.tmpfd)) == self.provision["parent"]
                and object_id(os.stat("/tmp", follow_symlinks=False)) == self.provision["parent"]
                and object_id(child) == self.provision["child"]
                and object_id(os.stat(name, dir_fd=self.tmpfd, follow_symlinks=False)) == self.provision["child"]
                and os.listdir(self.tmpfd) == [name] and os.listdir(self.childfd) == [],
                "PROVISION_TMP_BIND_IDENTITY")
        require(os.fstatvfs(self.tmpfd).f_flag & os.ST_RDONLY
                and os.fstatvfs(self.childfd).f_flag & os.ST_RDONLY, "PROVISION_TMP_WRITABLE")

    def request_release(self):
        self.check_tree()
        # Trigger only. Root re-derives identity and guard state independently.
        print(canonical_record({"phase": "release_request", "nonce": self.attempt["nonce"]})
              .decode("ascii"), end="", flush=True)
        deadline = min(time.monotonic_ns() + PROVISION_WAIT_NS,
                       self.attempt["created_ns"] + 20_000_000_000)
        self.release_record = self.wait_record("release", deadline)
        self.check_release()

    def check_release(self):
        value = read_record(self.readyfd, "release")
        exact_keys(value, "version nonce identity provision_sha256 issued_ns expires_ns")
        valid_identity(value["identity"])
        require(value == self.release_record and type(value["version"]) is int and value["version"] == 1
                and value["nonce"] == self.attempt["nonce"] and value["identity"] == self.identity
                and value["provision_sha256"] == hashlib.sha256(canonical_record(self.provision)).hexdigest()
                and type(value["issued_ns"]) is int and type(value["expires_ns"]) is int
                and 0 < value["expires_ns"] - value["issued_ns"] <= RELEASE_LEASE_NS
                and self.attempt["created_ns"] <= value["issued_ns"] <= time.monotonic_ns()
                < value["expires_ns"], "PROVISION_RELEASE_INVALID_OR_EXPIRED")

    def before_native(self):
        self.check_tree()
        self.check_release()  # Last operation before existing native fork.

    def close(self):
        for fd in reversed(self.owned):
            os.close(fd)
        self.owned.clear()

provision_worker = None

def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def emit(phase, **data):
    value = json.dumps(dict(phase=phase, monotonic_ns=time.monotonic_ns(), **data),
                       separators=(",", ":"))
    need(len(value) <= 32768, "METADATA_BOUND")
    print(value, flush=True)


# Offline-reviewed candidate helpers; no module-level system operations.

def mount_path(value):
    # Reject ambiguous/noncanonical encodings rather than normalize them silently.
    need(type(value) is str and value.startswith('/'), 'MOUNT_PATH')
    i = 0
    decoded = ''
    escapes = {'040': ' ', '011': '\t', '012': '\n', '134': '\\'}
    while i < len(value):
        if value[i] == '\\':
            need(value[i + 1:i + 4] in escapes, 'MOUNT_ESCAPE')
            decoded += escapes[value[i + 1:i + 4]]
            i += 4
        else:
            decoded += value[i]
            i += 1
    need(not any(ord(c) < 32 or ord(c) == 127 for c in decoded), 'MOUNT_PATH_CONTROL')
    need(decoded == '/' or (not decoded.endswith('/') and
         all(part not in ('', '.', '..') for part in decoded.split('/')[1:])), 'MOUNT_PATH')
    return decoded


def beneath(path, parent):
    return path == parent or parent == '/' or path.startswith(parent + '/')


def parse_mounts(raw):
    need(type(raw) is str and 0 < len(raw) <= 131072, 'MOUNTINFO_BOUND')
    lines = raw.splitlines()
    need(0 < len(lines) <= 256, 'MOUNT_COUNT')
    mounts = []
    ids = set()
    for line in lines:
        need(line.count(' - ') == 1, 'MOUNT_SCHEMA')
        left, right = line.split(' - ')
        fields, extra = left.split(), right.split()
        need(len(fields) >= 6 and len(extra) == 3, 'MOUNT_SCHEMA')
        need(all(v.isascii() and v.isdecimal() and 0 < int(v) < 2**64
                 for v in fields[:2]), 'MOUNT_ID')
        ident, parent = map(int, fields[:2])
        need(ident not in ids, 'MOUNT_DUPLICATE_ID')
        ids.add(ident)
        device = fields[2].split(':')
        need(len(device) == 2 and all(v.isascii() and v.isdecimal() and
             0 <= int(v) < 2**32 for v in device), 'MOUNT_DEVICE')
        options, super_options = fields[5].split(','), extra[2].split(',')
        need(len(options) == len(set(options)) and all(options) and
             len(super_options) == len(set(super_options)) and all(super_options), 'MOUNT_OPTIONS')
        need(len({'ro', 'rw'} & set(options)) == 1 and
             len({'ro', 'rw'} & set(super_options)) == 1, 'MOUNT_MODE')
        tags = {}
        for field in fields[6:]:
            tag, sep, value = field.partition(':')
            need(tag not in tags, 'MOUNT_PROPAGATION_SCHEMA')
            need((tag == 'unbindable' and not sep) or
                 (tag in ('shared', 'master', 'propagate_from') and sep and
                  value.isascii() and value.isdecimal() and int(value) > 0),
                 'MOUNT_PROPAGATION_SCHEMA')
            tags[tag] = value
        mounts.append({'id': ident, 'parent': parent, 'device': fields[2],
                       'root': mount_path(fields[3]), 'destination': mount_path(fields[4]),
                       'options': fields[5], 'type': extra[0], 'source': extra[1],
                       'super': extra[2], 'propagation': tags})
    return mounts


def mount_visibility(mounts):
    # Tree relationships, not numeric ID order or input line order, determine covers.
    by_id = {m['id']: m for m in mounts}
    children = {ident: [] for ident in by_id}
    roots = []
    for mount in mounts:
        ident, parent = mount['id'], mount['parent']
        if parent not in by_id or parent == ident:
            need(mount['destination'] == '/', 'MOUNT_ORPHAN')
            roots.append(ident)
        else:
            need(beneath(mount['destination'], by_id[parent]['destination']), 'MOUNT_PARENT_PATH')
            children[parent].append(ident)
    need(len(roots) == 1, 'MOUNT_ROOT_AMBIGUOUS')
    seen = set()
    def inspect(ident, active):
        need(ident not in active and ident not in seen, 'MOUNT_CYCLE')
        seen.add(ident)
        locations = [by_id[child]['destination'] for child in children[ident]]
        need(len(locations) == len(set(locations)), 'MOUNT_SIBLING_STACK_AMBIGUOUS')
        for child in children[ident]:
            inspect(child, active | {ident})
    inspect(roots[0], set())
    need(len(seen) == len(mounts), 'MOUNT_DISCONNECTED_OR_CYCLE')
    visible, hidden = set(), {}
    def hide(ident, cover):
        hidden[ident] = cover
        for child in children[ident]:
            hide(child, cover)
    def visit(ident):
        here = by_id[ident]['destination']
        same = [child for child in children[ident] if by_id[child]['destination'] == here]
        if same:
            cover = same[0]
            hidden[ident] = cover
            for child in children[ident]:
                if child != cover:
                    hide(child, cover)
            visit(cover)
            return
        visible.add(ident)
        for child in children[ident]:
            path = by_id[child]['destination']
            covers = [other for other in children[ident] if other != child and
                      beneath(path, by_id[other]['destination'])]
            if covers:
                # Nearest prefix has its own proven cover chain if also shadowed.
                cover = max(covers, key=lambda other: len(by_id[other]['destination']))
                hide(child, cover)
            else:
                visit(child)
    visit(roots[0])
    need(visible | set(hidden) == set(by_id) and not visible & set(hidden), 'MOUNT_VISIBILITY')
    return visible, hidden


def validate_mount_policy(mounts, visible):
    bookkeeping_mount(mounts, visible)
    destinations = {m['destination'] for m in mounts if m['id'] in visible}
    need('/' in destinations and '/proc' in destinations, 'REQUIRED_MOUNT')
    for mount in mounts:
        if mount['id'] in visible:
            writable = 'rw' in mount['options'].split(',')
            need(not writable or (mount['type'], mount['destination']) in
                 (('proc', '/proc'), ('devpts', '/dev/pts'), ('tmpfs', BOOKKEEPING)), 'UNEXPECTED_WRITABLE_MOUNT')
    # Do not assume PrivateMounts or MountFlags alone disables incoming propagation.
    # Hidden lower rows remain evidence and are not a propagation exemption.
    need(all(not m['propagation'] for m in mounts), 'MOUNT_PROPAGATION_NOT_PRIVATE')


def corroboration_targets(mounts, visible, hidden):
    # Read-only deep descendants can be DAC-inaccessible (e.g. debugfs/tracing).
    # Corroborate every security-critical point and effective cover of hidden rows;
    # do not silently catch errors or claim graph-only RO rows were kernel-opened.
    critical = {'/', '/proc', '/proc/sys', '/sys', '/sys/fs/cgroup', '/dev', '/dev/pts',
                '/dev/shm', '/dev/mqueue', '/dev/hugepages', '/run',
                '/run/host/.os-release-stage', '/run/systemd/incoming',
                '/tmp', '/var/tmp', '/empty-home', '/usr/bin/bwrap'}
    targets = {m['id'] for m in mounts if m['id'] in visible and
               (m['destination'] in critical or 'rw' in m['options'].split(','))}
    for cover in hidden.values():
        followed = set()
        while cover in hidden:
            need(cover not in followed, 'MOUNT_COVER_CYCLE')
            followed.add(cover)
            cover = hidden[cover]
        need(cover in visible, 'MOUNT_COVER_UNPROVEN')
        targets.add(cover)
    return targets


def corroborate_mounts(mounts, targets):
    # O_PATH opens metadata only, never file contents; fdinfo supplies kernel mnt_id.
    for mount in mounts:
        if mount['id'] not in targets:
            continue
        descriptor = os.open(mount['destination'], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(descriptor)
            need(not stat.S_ISLNK(info.st_mode), 'MOUNT_SYMLINK')
            with open('/proc/self/fdinfo/' + str(descriptor)) as stream:
                fdinfo = stream.read(4097)
            need(len(fdinfo) <= 4096, 'MOUNT_FDINFO_BOUND')
            values = [line.split(':', 1)[1].strip() for line in fdinfo.splitlines()
                      if line.startswith('mnt_id:')]
            need(len(values) == 1 and values[0].isascii() and values[0].isdecimal()
                 and int(values[0]) == mount['id'], 'MOUNT_VISIBLE_ID_MISMATCH')
            need((os.major(info.st_dev), os.minor(info.st_dev)) ==
                 tuple(map(int, mount['device'].split(':'))), 'MOUNT_VISIBLE_DEVICE_MISMATCH')
        finally:
            os.close(descriptor)


def attest_private_tmpfs(mounts, visible, pins):
    need(pins == {'/run': {'max_bytes': 65536, 'max_inodes': 32},
                  '/dev/shm': {'max_bytes': 65536, 'max_inodes': 32}}, 'TMPFS_PIN_SCHEMA')
    devices = []
    for path, limits in pins.items():
        matched = [m for m in mounts if m['id'] in visible and m['destination'] == path]
        need(len(matched) == 1, 'TMPFS_MOUNT_REQUIRED')
        mount = matched[0]
        need(mount['type'] == mount['source'] == 'tmpfs' and mount['root'] == '/' and
             {'ro', 'nosuid', 'nodev', 'noexec'} <= set(mount['options'].split(',')) and
             not mount['propagation'], 'PRIVATE_RO_TMPFS')
        devices.append(mount['device'])
        need(sum(m['device'] == mount['device'] for m in mounts) == 1, 'TMPFS_DEVICE_REUSED')
        info = os.stat(path, follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
             and stat.S_IMODE(info.st_mode) == 0o755, 'TMPFS_ROOT_METADATA')
        budget = os.statvfs(path)
        need(budget.f_flag & os.ST_RDONLY and
             0 < budget.f_frsize * budget.f_blocks <= limits['max_bytes'] and
             0 < budget.f_files <= limits['max_inodes'], 'TMPFS_BUDGET')
        if path == '/dev/shm':
            need(os.listdir(path) == [] and not any(m['id'] in visible and
                 m['destination'].startswith(path + '/') for m in mounts), 'SHM_NOT_EMPTY')
        else:
            attest_run_children(mounts, visible, mount)
    need(len(devices) == len(set(devices)), 'TMPFS_DEVICE_REUSED')


def attest_run_children(mounts, visible, run_mount):
    # Exact source-derived systemd v255 layout, not an exemption for arbitrary /run.
    expected = {
        '/run/host/.os-release-stage': '/systemd/propagate/.os-release-stage',
        '/run/systemd/incoming': '/systemd/propagate/zen53-mincompat.service'}
    children = [m for m in mounts if m['id'] in visible and
                m['destination'].startswith('/run/')]
    need({m['destination'] for m in children} == set(expected) | {BOOKKEEPING}, 'RUN_MOUNT_LAYOUT')
    children = [m for m in children if m['destination'] != BOOKKEEPING]
    for mount in children:
        need(mount['parent'] == run_mount['id'] and
             mount['type'] == mount['source'] == 'tmpfs' and
             mount['root'] == expected[mount['destination']] and
             {'ro', 'nosuid', 'nodev', 'noexec'} <= set(mount['options'].split(',')) and
             not mount['propagation'], 'RUN_CHILD_MOUNT')
    layouts = {'/run': ['host', 'systemd', 'zen53-bookkeeping'],
               '/run/host': ['.os-release-stage', 'os-release'],
               '/run/systemd': ['incoming'],
               '/run/host/.os-release-stage': ['os-release']}
    for path, entries in layouts.items():
        info = os.stat(path, follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
             not info.st_mode & 0o022 and sorted(os.listdir(path)) == entries,
             'RUN_DIRECTORY_LAYOUT')
    link = '/run/host/os-release'
    info = os.lstat(link)
    need(stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0 and
         os.readlink(link) == '.os-release-stage/os-release', 'RUN_OS_RELEASE_LINK')
    info = os.stat('/run/host/.os-release-stage/os-release', follow_symlinks=False)
    need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and
         not info.st_mode & 0o022 and info.st_nlink == 1 and 0 < info.st_size <= 16384,
         'RUN_OS_RELEASE_METADATA')
    # Do not recursively inspect this root-only leaf or read its contents.
    info = os.stat('/run/systemd/incoming', follow_symlinks=False)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
         stat.S_IMODE(info.st_mode) == 0o600, 'RUN_INCOMING_METADATA')


BOOKKEEPING = '/run/zen53-bookkeeping'
BOOKKEEPING_POLICY = {'version': 1, 'path': BOOKKEEPING, 'bytes': 1048576,
                      'inodes': 64, 'uid': 0, 'gid': 0, 'mode': 0o1777}


def bookkeeping_mount(mounts, visible, inner=False):
    matches = [m for m in mounts if m['destination'] == BOOKKEEPING]
    need(len(matches) == 1 and matches[0]['id'] in visible, 'BOOKKEEPING_MOUNT_REQUIRED')
    mount = matches[0]
    options = set(mount['options'].split(','))
    required = {'ro' if inner else 'rw', 'nosuid', 'nodev', 'noexec'}
    need(required <= options and not options - required - {'relatime', 'strictatime', 'noatime'},
         'BOOKKEEPING_OPTIONS')
    need(mount['root'] == '/' and mount['type'] == mount['source'] == 'tmpfs'
         and not mount['propagation'], 'BOOKKEEPING_FILESYSTEM')
    need(sum(m['device'] == mount['device'] for m in mounts) == 1,
         'BOOKKEEPING_ALIAS')
    need(not any(m['destination'].startswith(BOOKKEEPING + '/') for m in mounts),
         'BOOKKEEPING_SUBMOUNT')
    parent = [m for m in mounts if m['id'] == mount['parent']]
    need(len(parent) == 1 and parent[0]['id'] in visible
         and parent[0]['destination'] == '/run' and 'ro' in parent[0]['options'].split(','),
         'BOOKKEEPING_PARENT')
    super_options = dict((v.split('=', 1) + [''])[:2] for v in mount['super'].split(','))
    need(super_options.get('size') in ('1024k', '1048576')
         and super_options.get('nr_inodes') == '64'
         and super_options.get('mode', '1777') == '1777'
         and super_options.get('uid', '0') == super_options.get('gid', '0') == '0'
         and 'rw' in super_options and 'ro' not in super_options,
         'BOOKKEEPING_SUPERBLOCK')
    return mount


def attest_bookkeeping(mounts, visible, policy, inner=False, owners=(0, 0)):
    need(policy == BOOKKEEPING_POLICY, 'BOOKKEEPING_PIN_SCHEMA')
    mount = bookkeeping_mount(mounts, visible, inner)
    # Component no-follow descriptors prevent symlink traversal beneath /run.
    root = os.open('/run', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptor = None
    try:
        descriptor = os.open('zen53-bookkeeping', os.O_RDONLY | os.O_DIRECTORY |
                             os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
        info = os.fstat(descriptor)
        need(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == owners
             and stat.S_IMODE(info.st_mode) == 0o1777, 'BOOKKEEPING_METADATA')
        need((os.major(info.st_dev), os.minor(info.st_dev)) ==
             tuple(map(int, mount['device'].split(':'))), 'BOOKKEEPING_DEVICE')
        with open('/proc/self/fdinfo/' + str(descriptor)) as stream:
            fields = stream.read(4097)
        ids = [line.split(':', 1)[1].strip() for line in fields.splitlines()
               if line.startswith('mnt_id:')]
        need(len(fields) <= 4096 and ids == [str(mount['id'])], 'BOOKKEEPING_MOUNT_ID')
        budget = os.fstatvfs(descriptor)
        need(bool(budget.f_flag & os.ST_RDONLY) == inner and
             budget.f_frsize * budget.f_blocks == 1048576 and budget.f_files == 64,
             'BOOKKEEPING_QUOTA')
        need(budget.f_flag & os.ST_NOSUID and budget.f_flag & os.ST_NODEV
             and budget.f_flag & os.ST_NOEXEC, 'BOOKKEEPING_VFS_FLAGS')
        if not inner:
            need(os.listdir(descriptor) == [] and budget.f_bfree == budget.f_blocks
                 and budget.f_bavail == budget.f_blocks and budget.f_ffree == 63
                 and budget.f_favail == 63, 'BOOKKEEPING_NOT_FRESH')
        current = os.stat('zen53-bookkeeping', dir_fd=root, follow_symlinks=False)
        need((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino),
             'BOOKKEEPING_REPLACED')
        return {'device': mount['device'], 'inode': info.st_ino, 'mount_id': mount['id']}
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            os.close(root)


bookkeeping_identity = None


def bookkeeping_recheck():
    with open('/proc/self/mountinfo') as stream:
        raw = stream.read(131073)
    mounts = parse_mounts(raw)
    visible, hidden = mount_visibility(mounts)
    validate_mount_policy(mounts, visible)
    need(attest_bookkeeping(mounts, visible, BOOKKEEPING_POLICY) == bookkeeping_identity,
         'BOOKKEEPING_IDENTITY_CHANGED')
    with open('/proc/self/mountinfo') as stream:
        need(stream.read(131073) == raw, 'BOOKKEEPING_TOPOLOGY_CHANGED')


def admission():
    global bookkeeping_identity
    need(len(sys.argv) == 1 and os.geteuid() != 0, "WORKER_IDENTITY")
    with open("/diagnostic/pins.json") as stream:
        pins = json.load(stream)
    for path, expected in pins["files"].items():
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(descriptor)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                 and not info.st_mode & 0o222 and not info.st_mode & 0o6000,
                 "IMMUTABLE_RUNTIME_METADATA")
            h = hashlib.sha256()
            while True:
                data = os.read(descriptor, 65536)
                if not data:
                    break
                h.update(data)
            need(h.hexdigest() == expected, "RUNTIME_HASH")
        finally:
            os.close(descriptor)
    # System executable retains package mode0755; effective RO mount is required.
    bwrap = os.stat("/usr/bin/bwrap", follow_symlinks=False)
    need(stat.S_ISREG(bwrap.st_mode) and bwrap.st_uid == bwrap.st_gid == 0
         and stat.S_IMODE(bwrap.st_mode) == 0o755 and bwrap.st_nlink == 1,
         "SYSTEM_BWRAP_METADATA")
    need(hashlib.sha256(open("/usr/bin/bwrap", "rb").read()).hexdigest()
         == pins["bwrap_sha256"], "SYSTEM_BWRAP_HASH")
    with open("/proc/self/mountinfo") as stream:
        raw = stream.read(131073)
    need(len(raw) <= 131072, "MOUNTINFO_BOUND")
    mounts = parse_mounts(raw)
    visible, hidden = mount_visibility(mounts)
    targets = corroboration_targets(mounts, visible, hidden)
    for mount in mounts:
        mount["visibility"] = "visible" if mount["id"] in visible else "hidden"
        if mount["id"] in hidden:
            mount["covered_by"] = hidden[mount["id"]]
    emit("mount_admission_observation", result="OBSERVATION_NOT_PASS", mounts=mounts,
         mounts_sha256=hashlib.sha256(raw.encode()).hexdigest(), corroboration_required=sorted(targets))
    validate_mount_policy(mounts, visible)
    corroborate_mounts(mounts, targets)
    attest_private_tmpfs(mounts, visible, pins["private_tmpfs"])
    observed_bookkeeping = attest_bookkeeping(mounts, visible, pins["bookkeeping"])
    need(bookkeeping_identity is None or observed_bookkeeping == bookkeeping_identity,
         'BOOKKEEPING_IDENTITY_CHANGED')
    bookkeeping_identity = observed_bookkeeping
    need(os.environ.get('TMPDIR') == BOOKKEEPING, 'BOOKKEEPING_FIXED_ENVIRONMENT')
    for path in ("/", "/diagnostic", "/usr", "/opt", "/usr/bin/bwrap",
                 "/proc/sys", "/sys", "/sys/fs/cgroup", "/dev", "/dev/shm", "/run",
                 "/tmp", "/var/tmp", "/empty-home"):
        need(os.statvfs(path).f_flag & os.ST_RDONLY, "REQUIRED_READONLY_MOUNT")
    need(pins["empty_directories"] == {"/empty-home": [2049, 128952],
         "/var/tmp": [2049, 129326]}, "EMPTY_BIND_PIN_SCHEMA")
    need(pins["daemon_tmp"] == {"version": 1, "source": "daemon-tmp", "readiness": "/diagnostic/readiness"},
         "DAEMON_TMP_POLICY")
    provision_worker.check_tree()
    for path, expected in pins["empty_directories"].items():
        info = os.stat(path, follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and [info.st_dev, info.st_ino] == expected
             and info.st_uid == info.st_gid == 0 and not info.st_mode & 0o222
             and os.listdir(path) == [], "EMPTY_BIND_IDENTITY")
    selected = {}
    with open("/proc/self/status") as stream:
        for line in stream:
            key, _, value = line.partition(":")
            if key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp"):
                selected[key] = value.strip()
    need(selected.get("NoNewPrivs") == "1" and selected.get("Seccomp") == "2",
         "OUTER_PRIVILEGES")
    need(all(int(selected.get(key, "-1"), 16) == 0 for key in
             ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")), "OUTER_CAPABILITIES")
    # Native children receive exactly ENV and only FDs0/1/2, not PID1's environment.
    need(os.environ.get("HOME") == ENV["HOME"] and os.environ.get("CODEX_HOME") == ENV["CODEX_HOME"],
         "HOME_IDENTITY")
    with open("/proc/self/mountinfo") as stream:
        after = stream.read(131073)
    need(after == raw, "MOUNT_TOPOLOGY_CHANGED")
    emit("outer_admission", bookkeeping=observed_bookkeeping, pid=os.getpid(), uid=os.geteuid(), status=selected,
         namespaces={name: os.readlink("/proc/self/ns/" + name) for name in ("pid", "mnt", "net", "user")},
         mounts_sha256=hashlib.sha256(raw.encode()).hexdigest(),
         mount_count=len(mounts), hidden_mount_count=len(hidden),
         corroboration_verified=sorted(targets))


# Exact pinned Rust io::Error display in the fixed C.UTF-8 Linux environment.
# This is a source-derived expectation, NOT an observed runtime success.
EXPECTED_ALIAS_WARNING = (b"WARNING: proceeding, even though we could not create PATH aliases: "
                          b"Read-only file system (os error 30)\n")


class NativeOutput:
    """Bounded complete-line classification; only one exact line is exempted."""
    def __init__(self):
        self.total = 0
        self.pending = b""
        self.alias_warnings = 0

    def feed(self, chunk):
        self.total += len(chunk)
        need(self.total <= 16384, "native_OUTPUT_BOUND")
        self.pending += chunk
        observed = False
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line += b"\n"
            if line == EXPECTED_ALIAS_WARNING:
                need(self.alias_warnings == 0, "DUPLICATE_ALIAS_WARNING")
                self.alias_warnings += 1
                observed = True
                continue  # Exempt ONLY this validated, complete line.
            need(all(32 <= value <= 126 or value in (9, 10) for value in line),
                 "NATIVE_OUTPUT_MALFORMED")
            need(not line[:-1] or not EXPECTED_ALIAS_WARNING.lower().startswith(line[:-1].lower()),
                 "MALFORMED_ALIAS_WARNING")
            lower = line.lower()
            need(not any(token in lower for token in
                         (b"warn", b"error", b"denied", b"read-only", b"read only",
                          b"not permitted", b"failed", b"failure", b"fatal", b"panic",
                          b"could not")), "CODEX_STARTUP_ACCESS_OR_WRITE_FAILURE")
        return observed

    def finish(self):
        # A partial expected warning is never exempted, even at EOF.
        need(not self.pending, "NATIVE_OUTPUT_UNTERMINATED")


def fixed_command(argv, seconds, label):
    emit(label + "_start", argv=argv)
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    if label == "native":
        bookkeeping_recheck()
        provision_worker.before_native()
    pid = os.fork()
    if pid == 0:
        try:
            os.dup2(write_fd, 1)
            os.dup2(write_fd, 2)
            os.close(read_fd)
            os.closerange(3, 128)
            os.execve(argv[0], argv, ENV)
        finally:
            os._exit(125)
    os.close(write_fd)
    os.set_blocking(read_fd, False)
    end, output, status, eof = time.monotonic() + seconds, bytearray(), None, False
    native_output = NativeOutput() if label == "native" else None
    while status is None or not eof:
        # Any failure ends this launcher; whole-cgroup stop is the supervisor's duty.
        need(time.monotonic() < end, label + "_TIMEOUT")
        ready, _, _ = select.select([read_fd] if not eof else [], [], [], 0.02)
        if ready:
            chunk = os.read(read_fd, 4096)
            if chunk:
                output.extend(chunk)
                need(len(output) <= 16384, label + "_OUTPUT_BOUND")
                if label == "native":
                    try:
                        observed = native_output.feed(chunk)
                    except RuntimeError:
                        emit("native_stop_evidence", output=bytes(output[-4096:]).decode("utf-8", "replace"))
                        raise
                    if observed:
                        emit("native_alias_warning_observation",
                             result="OBSERVATION_NOT_PASS", count=1,
                             warning=EXPECTED_ALIAS_WARNING.decode("ascii").rstrip("\n"))
            else:
                eof = True
        if status is None:
            child, value = os.waitpid(pid, os.WNOHANG)
            if child:
                status = value
    os.close(read_fd)
    if native_output is not None:
        try:
            native_output.finish()
        except RuntimeError:
            emit("native_stop_evidence", output=bytes(output[-4096:]).decode("utf-8", "replace"))
            raise
    emit(label + "_finish", pid=pid, wait_status=status,
         output=bytes(output).decode("utf-8", "replace"))
    need(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, label + "_NONZERO")
    return bytes(output)


try:
    provision_worker = ProvisionWorker()
    provision_worker.initialize()
    admission()
    help_output = fixed_command(["/usr/bin/bwrap", "--help"], 5, "bwrap_help")
    need(all(flag in help_output.split() for flag in (b"--as-pid-1", b"--perms", b"--argv0")),
         "BWRAP_REQUIRED_FEATURES")
    # Recheck actual mounts/hashes immediately before the one native invocation.
    admission()
    provision_worker.request_release()
    output = fixed_command(NATIVE, 35, "native")
    need(output.count(b"ZEN53_REPORT_V1\n") == 1 and output.count(b"ZEN53_REPORT_END\n") == 1,
         "REPORT_MISSING_OR_DUPLICATE")
    emit("launcher_complete", result="REPORTER_FINISHED_NOT_ISOLATION_ACCEPTANCE")
except BaseException as error:
    # Fixed local error identifiers only; no arbitrary process/environment values.
    code = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    print(json.dumps({"phase": "stopped", "code": code[:128]}), flush=True)
    raise SystemExit(1)

finally:
    if provision_worker is not None:
        provision_worker.close()
