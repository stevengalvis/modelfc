"""Trusted ZEN-53 self-observation reporter; accepts no arguments."""
import os
import sys
import stat
import json
import hashlib

FIELDS = ("Name", "Pid", "PPid", "Uid", "Gid", "NSpid", "CapInh", "CapPrm",
          "CapEff", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp")


def text(path, maximum):
    with open(path, "r", encoding="ascii", errors="strict") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("oversized observation")
    return value


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
        require(stat.S_ISREG(info.st_mode) and info.st_uid == INNER_ROOT_UID and info.st_gid == INNER_ROOT_GID
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


def need(ok, code):
    if not ok:
        raise RuntimeError(code)

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


def check_inherited_fds():
    # listdir's own temporary descriptor may appear, then be closed before fstat.
    names = os.listdir('/proc/self/fd')
    need(len(names) <= 128 and all(n.isascii() and n.isdecimal() for n in names),
         'BOOKKEEPING_FD_INVENTORY')
    live = []
    for name in names:
        try:
            info = os.fstat(int(name))
        except OSError as error:
            need(error.errno == 9 and int(name) >= 3, 'BOOKKEEPING_FD_UNCERTAIN')
            continue
        need(int(name) < 3, 'BOOKKEEPING_INHERITED_DESCRIPTOR')
        live.append(int(name))
        if name == '0':
            need(stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == 1
                 and os.minor(info.st_rdev) == 3, 'BOOKKEEPING_STDIN')
        else:
            need(stat.S_ISFIFO(info.st_mode), 'BOOKKEEPING_STDOUT')
    need(sorted(live) == [0, 1, 2], 'BOOKKEEPING_MISSING_STDIO')


def deny_outer_aliases(pid, fresh_proc):
    # O_PATH follows proc magic links but never reads external file contents.
    # For inherited proc, a live outer launcher must be permission-denied, not
    # merely missing. Fresh proc cannot name ancestor PIDs; ENOENT is accepted.
    for suffix, probe in (('root', 'ROOT'), ('cwd', 'CWD'), ('fd/0', 'FD0'),
                          ('fd/1', 'FD1'), ('fd/2', 'FD2'),
                          ('ns/mnt', 'NS_MNT'), ('ns/user', 'NS_USER')):
        try:
            fd = os.open('/proc/' + str(pid) + '/' + suffix, os.O_PATH | os.O_CLOEXEC)
        except OSError as error:
            try:
                need(error.errno in (1, 13) or (fresh_proc and error.errno == 2),
                     'BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN')
            except RuntimeError as failure:
                number = error.errno if any(type(error) is kind for kind in FAILURE_OSERROR_TYPES) else 0
                if type(number) is not int or not 0 <= number <= 4095:
                    number = 0
                mode = 'FRESH' if fresh_proc is True else 'INHERITED' if fresh_proc is False else 'UNKNOWN'
                failure._zen53_outer_alias = (number, probe, mode)
                raise
        else:
            os.close(fd)
            raise RuntimeError('BOOKKEEPING_OUTER_PROC_ACCESS')


def inner_root_mapping():
    mappings, overflow = [], []
    for name, current in (('uid', os.geteuid()), ('gid', os.getegid())):
        raw = text('/proc/self/' + name + '_map', 256)
        rows = raw.splitlines()
        need(len(rows) == 1 and len(rows[0].split()) == 3, 'BOOKKEEPING_USER_MAPPING')
        fields = rows[0].split()
        need(all(v.isascii() and v.isdecimal() for v in fields), 'BOOKKEEPING_USER_MAPPING')
        inside, outside, count = map(int, fields)
        need(0 < inside == current < 2**32 - 1 and 0 <= outside < 2**32 - 1 and count == 1,
             'BOOKKEEPING_USER_MAPPING')
        value = text('/proc/sys/kernel/overflow' + name, 64).strip()
        need(value.isascii() and value.isdecimal() and 0 <= int(value) < 2**32 - 1
             and int(value) != inside, 'BOOKKEEPING_OVERFLOW_ID')
        mappings.append(outside)
        overflow.append(int(value))
    return tuple(mappings), tuple(overflow)


def report_bookkeeping(status, namespaces):
    global INNER_ROOT_UID, INNER_ROOT_GID
    mappings, owners = inner_root_mapping()
    INNER_ROOT_UID, INNER_ROOT_GID = owners
    need(os.environ.get('TMPDIR') == BOOKKEEPING, 'BOOKKEEPING_TMPDIR_PROPAGATION')
    need(status['NoNewPrivs'] == '1' and status['Seccomp'] == '2' and
         all(int(status[key], 16) == 0 for key in
             ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')), 'BOOKKEEPING_PRIVILEGES')
    raw = text('/proc/self/mountinfo', 131072)
    mounts = parse_mounts(raw)
    visible, hidden = mount_visibility(mounts)
    need(all(not m['propagation'] for m in mounts), 'BOOKKEEPING_PROPAGATION')
    for mount in mounts:
        if mount['id'] in visible and 'rw' in mount['options'].split(','):
            need((mount['type'], mount['destination']) in
                 (('proc', '/proc'), ('tmpfs', '/dev'), ('devpts', '/dev/pts')),
                 'BOOKKEEPING_INNER_WRITABLE_MOUNT')
    identity = attest_bookkeeping(mounts, visible, BOOKKEEPING_POLICY, inner=True, owners=owners)
    # Existing runtime root policy is retained. A readable bookkeeping mount is
    # acceptable, but it must be RO and have no second device alias or submount.
    need(os.statvfs('/').f_flag & os.ST_RDONLY, 'BOOKKEEPING_INNER_ROOT')
    fd = os.open('/diagnostic/readiness', os.O_RDONLY | os.O_DIRECTORY |
                 os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        directory_info(os.fstat(fd), owners[0], 0o555, gid=owners[1])
        need(os.fstatvfs(fd).f_flag & os.ST_RDONLY, 'BOOKKEEPING_READINESS_RW')
        attempt = read_record(fd, 'attempt')
        valid_attempt(attempt)
        provision = read_record(fd, 'provision')
        release = read_record(fd, 'release')
        exact_keys(provision, 'version nonce identity parent child')
        exact_keys(release, 'version nonce identity provision_sha256 issued_ns expires_ns')
        valid_identity(provision['identity'])
        need(type(provision['version']) is int and provision['version'] == 1 and
             type(release['version']) is int and release['version'] == 1 and
             provision['nonce'] == release['nonce'] == attempt['nonce'] and
             release['identity'] == provision['identity'] and
             release['provision_sha256'] == hashlib.sha256(canonical_record(provision)).hexdigest(),
             'BOOKKEEPING_ATTEMPT_BINDING')
        outer_pid = provision['identity']['pid']
        need(mappings in ((provision['identity']['uid'], provision['identity']['gid']), (0, 0)),
             'BOOKKEEPING_OUTER_MAPPING')
    finally:
        os.close(fd)
    # NSpid is relative to the PID namespace of this procfs mount. One element
    # plus matching proc/1 namespace establishes a fresh proc view; multiple
    # elements require explicit denial against the live outer launcher.
    pids = status['NSpid'].split()
    need(pids and all(p.isascii() and p.isdecimal() and int(p) > 0 for p in pids)
         and int(pids[-1]) == os.getpid(), 'BOOKKEEPING_PROC_IDENTITY')
    fresh = len(pids) == 1
    if fresh:
        need(os.readlink('/proc/1/ns/pid') == namespaces['pid'], 'BOOKKEEPING_PROC_NAMESPACE')
    deny_outer_aliases(outer_pid, fresh)
    check_inherited_fds()
    need(text('/proc/self/mountinfo', 131072) == raw, 'BOOKKEEPING_INNER_TOPOLOGY_CHANGED')
    return ['bookkeeping.readonly=1', 'bookkeeping.no_aliases=1',
            'bookkeeping.no_inherited_fds=1', 'bookkeeping.outer_proc_denied=1',
            'bookkeeping.tmpdir=' + BOOKKEEPING,
            'bookkeeping.proc_mode=' + ('fresh' if fresh else 'inherited'),
            'bookkeeping.device=' + identity['device'],
            'bookkeeping.inode=' + str(identity['inode'])]


def main():
    if len(sys.argv) != 1:
        raise ValueError("arguments forbidden")
    label = text("/proc/self/attr/current", 4096).strip()
    if not label or any(ord(c) < 32 or ord(c) > 126 for c in label):
        raise ValueError("invalid label")
    status = {}
    for line in text("/proc/self/status", 16384).splitlines():
        key, separator, value = line.partition(":")
        if separator and key in FIELDS:
            if key in status or not value.strip():
                raise ValueError("invalid status")
            status[key] = value.strip()
    if set(status) != set(FIELDS):
        raise ValueError("missing status")
    namespaces = {}
    for name in ("pid", "mnt", "net", "user"):
        value = os.readlink("/proc/self/ns/" + name)
        if not value.startswith(name + ":[") or not value.endswith("]"):
            raise ValueError("invalid namespace")
        if not value[len(name) + 2:-1].isdigit():
            raise ValueError("invalid namespace id")
        namespaces[name] = value
    lines = ["ZEN53_REPORT_V1", "apparmor=" + label]
    lines += ["status." + key + "=" + status[key] for key in FIELDS]
    lines += ["namespace." + key + "=" + namespaces[key] for key in namespaces]
    lines += report_bookkeeping(status, namespaces)
    output = "\n".join(lines) + "\nZEN53_REPORT_END\n"
    if len(output.encode("ascii")) > 8192:
        raise ValueError("output bound")
    sys.stdout.write(output)
    sys.stdout.flush()


# Diagnostic values are fixed vocabulary, never arbitrary exception text.
FAILURE_CHECKS = frozenset((
    'BOOKKEEPING_ALIAS',
    'BOOKKEEPING_ATTEMPT_BINDING',
    'BOOKKEEPING_DEVICE',
    'BOOKKEEPING_FD_INVENTORY',
    'BOOKKEEPING_FD_UNCERTAIN',
    'BOOKKEEPING_FILESYSTEM',
    'BOOKKEEPING_INHERITED_DESCRIPTOR',
    'BOOKKEEPING_INNER_ROOT',
    'BOOKKEEPING_INNER_TOPOLOGY_CHANGED',
    'BOOKKEEPING_INNER_WRITABLE_MOUNT',
    'BOOKKEEPING_METADATA',
    'BOOKKEEPING_MISSING_STDIO',
    'BOOKKEEPING_MOUNT_ID',
    'BOOKKEEPING_MOUNT_REQUIRED',
    'BOOKKEEPING_NOT_FRESH',
    'BOOKKEEPING_OPTIONS',
    'BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN',
    'BOOKKEEPING_OUTER_MAPPING',
    'BOOKKEEPING_OUTER_PROC_ACCESS',
    'BOOKKEEPING_OVERFLOW_ID',
    'BOOKKEEPING_PARENT',
    'BOOKKEEPING_PIN_SCHEMA',
    'BOOKKEEPING_PRIVILEGES',
    'BOOKKEEPING_PROC_IDENTITY',
    'BOOKKEEPING_PROC_NAMESPACE',
    'BOOKKEEPING_PROPAGATION',
    'BOOKKEEPING_QUOTA',
    'BOOKKEEPING_READINESS_RW',
    'BOOKKEEPING_REPLACED',
    'BOOKKEEPING_STDIN',
    'BOOKKEEPING_STDOUT',
    'BOOKKEEPING_SUBMOUNT',
    'BOOKKEEPING_SUPERBLOCK',
    'BOOKKEEPING_TMPDIR_PROPAGATION',
    'BOOKKEEPING_USER_MAPPING',
    'BOOKKEEPING_VFS_FLAGS',
    'MOUNTINFO_BOUND',
    'MOUNT_COUNT',
    'MOUNT_CYCLE',
    'MOUNT_DEVICE',
    'MOUNT_DISCONNECTED_OR_CYCLE',
    'MOUNT_DUPLICATE_ID',
    'MOUNT_ESCAPE',
    'MOUNT_ID',
    'MOUNT_MODE',
    'MOUNT_OPTIONS',
    'MOUNT_ORPHAN',
    'MOUNT_PARENT_PATH',
    'MOUNT_PATH',
    'MOUNT_PATH_CONTROL',
    'MOUNT_PROPAGATION_SCHEMA',
    'MOUNT_ROOT_AMBIGUOUS',
    'MOUNT_SCHEMA',
    'MOUNT_SIBLING_STACK_AMBIGUOUS',
    'MOUNT_VISIBILITY',
    'PROVISION_ATTEMPT_SCHEMA',
    'PROVISION_CGROUP',
    'PROVISION_COMPONENT',
    'PROVISION_DIRECTORY_METADATA',
    'PROVISION_IDENTITY_SCHEMA',
    'PROVISION_PROCESS_CREDENTIALS',
    'PROVISION_PROCESS_DEAD',
    'PROVISION_READ_BOUND',
    'PROVISION_RECORD_ALIAS',
    'PROVISION_RECORD_ENCODING',
    'PROVISION_RECORD_METADATA',
    'PROVISION_RECORD_REPLACED',
    'PROVISION_RECORD_SCHEMA',
))

FAILURE_CLASSES = (
    (RuntimeError, 'RuntimeError'),
    (ValueError, 'ValueError'),
    (TypeError, 'TypeError'),
    (KeyError, 'KeyError'),
    (IndexError, 'IndexError'),
    (OverflowError, 'OverflowError'),
    (AssertionError, 'AssertionError'),
    (MemoryError, 'MemoryError'),
    (RecursionError, 'RecursionError'),
    (UnicodeDecodeError, 'UnicodeDecodeError'),
    (json.JSONDecodeError, 'JSONDecodeError'),
    (OSError, 'OSError'),
    (FileNotFoundError, 'FileNotFoundError'),
    (PermissionError, 'PermissionError'),
    (NotADirectoryError, 'NotADirectoryError'),
    (IsADirectoryError, 'IsADirectoryError'),
    (FileExistsError, 'FileExistsError'),
    (InterruptedError, 'InterruptedError'),
    (BlockingIOError, 'BlockingIOError'),
    (BrokenPipeError, 'BrokenPipeError'),
    (SystemExit, 'SystemExit'),
    (KeyboardInterrupt, 'KeyboardInterrupt'),
)
FAILURE_OSERROR_TYPES = (
    OSError, BlockingIOError, ChildProcessError, ConnectionError,
    BrokenPipeError, ConnectionAbortedError, ConnectionRefusedError,
    ConnectionResetError, FileExistsError, FileNotFoundError, InterruptedError,
    IsADirectoryError, NotADirectoryError, PermissionError, ProcessLookupError,
    TimeoutError,
)
FAILURE_PROBES = ('ROOT', 'CWD', 'FD0', 'FD1', 'FD2', 'NS_MNT', 'NS_USER')

FAILURE_REPORTER_CODES = tuple(code for function in (
    text,
    require,
    object_id,
    directory_info,
    open_directory,
    bounded_read,
    read_at,
    canonical_record,
    read_record,
    exact_keys,
    valid_nonce,
    valid_identity,
    valid_attempt,
    parse_proc_identity,
    need,
    mount_path,
    beneath,
    parse_mounts,
    mount_visibility,
    bookkeeping_mount,
    attest_bookkeeping,
    check_inherited_fds,
    deny_outer_aliases,
    inner_root_mapping,
    report_bookkeeping,
    main,
) for code in (function.__code__,) + tuple(
    value for value in function.__code__.co_consts
    if type(value) is type(function.__code__)))


def failure_line(error):
    """ASCII <= 384 bytes; fixed tokens and numeric data only, no str(error)."""
    kind = type(error)
    name = next((label for allowed, label in FAILURE_CLASSES if kind is allowed), "UNKNOWN")
    known = name != "UNKNOWN"
    number = error.errno if known and isinstance(error, OSError) else 0
    if type(number) is not int or not 0 <= number <= 4095:
        number = 0
    code = "UNKNOWN"
    if kind is RuntimeError and len(error.args) == 1:
        value = error.args[0]
        if type(value) is str and value in FAILURE_CHECKS:
            code = value
    detail = ""
    if code == "BOOKKEEPING_OUTER_IDENTITY_UNCERTAIN":
        number_cause, probe, mode = 0, "UNKNOWN", "UNKNOWN"
        context = getattr(error, "_zen53_outer_alias", None)
        if type(context) is tuple and len(context) == 3:
            if type(context[0]) is int and 0 <= context[0] <= 4095:
                number_cause = context[0]
            if type(context[1]) is str and context[1] in FAILURE_PROBES:
                probe = context[1]
            if type(context[2]) is str and context[2] in ("FRESH", "INHERITED", "UNKNOWN"):
                mode = context[2]
        detail = (" cause_errno=" + str(number_cause) + " probe=" + probe + " proc=" + mode)
    # Innermost eight owned frames retain need/require AND their callers.
    # External library/test frames are excluded by exact code identity.
    lines = []
    trace = error.__traceback__
    while trace is not None:
        if any(trace.tb_frame.f_code is owned for owned in FAILURE_REPORTER_CODES):
            number_line = trace.tb_lineno
            if type(number_line) is int and 1 <= number_line <= 999999:
                lines.append(number_line)
                lines = lines[-8:]
        trace = trace.tb_next
    line = ("ZEN53_REPORT_REQUIRED_OBSERVATION_FAILED class=" + name +
            " errno=" + str(number) + " check=" + code + detail + " lines=" +
            (",".join(str(value) for value in lines) if lines else "0") + "\n")
    if len(line) > 384 or not line.isascii():
        return "ZEN53_REPORT_REQUIRED_OBSERVATION_FAILED class=UNKNOWN errno=0 check=UNKNOWN lines=0\n"
    return line


try:
    main()
except BaseException as error:
    try:
        line = failure_line(error)
    except BaseException:
        line = "ZEN53_REPORT_REQUIRED_OBSERVATION_FAILED class=UNKNOWN errno=0 check=UNKNOWN lines=0\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    finally:
        raise SystemExit(1)
