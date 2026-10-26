"""Fresh ZEN-53 stage construction and a data-only launch manifest.

This module never starts systemd, Bubblewrap, a collector or a scope. Its
filesystem writer is intentionally separate from operator_entry.main. A host
operator must first authenticate the published archive and explicitly approve
new root-owned objects before invoking this builder on a VPS.
"""
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import time

# -I removes the script directory from sys.path. Import only modules from the
# separately authenticated, protected package directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import operator_entry
import restricted_launcher as restricted

RECOVERY_SHA256 = 'bc8f681b770a804f3cb044245865cb2ee7df47c95b9bcbd719620f9d498d41f4'
TOOL_FILES = ('admission.py', 'capture_once.py', 'controller.py', 'evidence.py',
              'operator_entry.py', 'owner_adapter.py', 'owner_context_v4.py',
              'restricted_launcher.py', 'runtime_dependencies.json',
              'runtime_source_manifest.json')
CURRENT_PROJECT_SOURCES = {
    '/diagnostic/launch.py': 'stage_launch_source.py',
    '/diagnostic/pins.json': 'stage_pins_source.json',
}
NONCE = re.compile(r'[0-9a-f]{32}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
SOURCE_FLAGS = (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW |
                os.O_NOATIME | os.O_CLOEXEC)
MAX_TOTAL = 384_000_000


def need(ok, code):
    if not ok:
        raise ValueError(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True) + '\n').encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
            info.st_mode, info.st_size, info.st_nlink, info.st_mtime_ns,
            info.st_ctime_ns)


class FileSource:
    """Bounded no-follow reader; tests replace it with small synthetic files."""
    def __init__(self, owner_uid=0, clock=time.monotonic, root_uid=0):
        self.owner_uid = owner_uid
        self.root_uid = root_uid
        self.clock = clock
        self.deadline = clock() + 180
        self.total = 0

    def _time(self):
        need(self.clock() < self.deadline, 'BUILD_SOURCE_DEADLINE')

    def _parent(self, path, expected_uid):
        need(type(path) is str and path.startswith('/') and
             posixpath.normpath(path) == path and '\x00' not in path and
             len(path) <= 4096 and len(path.split('/')) <= 66,
             'BUILD_SOURCE_PATH')
        parts = path.split('/')[1:]
        need(parts and all(part not in ('', '.', '..') for part in parts),
             'BUILD_SOURCE_PATH')
        fd = os.open('/', DIR_FLAGS)
        try:
            prefix = ''
            for part in parts[:-1]:
                self._time()
                next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                prefix += '/' + part
                info = os.fstat(fd)
                mode = stat.S_IMODE(info.st_mode)
                sticky_tmp = (prefix == '/tmp' and
                              info.st_uid == self.root_uid and mode == 0o1777)
                need(stat.S_ISDIR(info.st_mode) and
                     (sticky_tmp or (info.st_uid in
                                     (self.root_uid, expected_uid) and
                                     not mode & 0o022)), 'BUILD_SOURCE_ANCESTOR')
            return fd, parts[-1]
        except BaseException:
            os.close(fd)
            raise

    def _parent_still_named(self, source, expected_uid, held_fd):
        current, _ = self._parent(source, expected_uid)
        try:
            before, after = os.fstat(held_fd), os.fstat(current)
            need((before.st_dev, before.st_ino, before.st_uid,
                  before.st_gid, before.st_mode) ==
                 (after.st_dev, after.st_ino, after.st_uid,
                  after.st_gid, after.st_mode), 'BUILD_SOURCE_PARENT_CHANGED')
        finally:
            os.close(current)

    def copy(self, source, destination_fd, expected_sha, *, maximum,
             expected_uid, expected_gid, expected_mode):
        need(type(maximum) is int and 0 <= maximum <= 313_000_000 and
             type(expected_uid) is int and type(expected_gid) is int and
             type(expected_mode) is int and
             HASH.fullmatch(expected_sha) is not None, 'BUILD_SOURCE_CONTRACT')
        directory, name = self._parent(source, expected_uid)
        try:
            fd = os.open(name, SOURCE_FLAGS, dir_fd=directory)
            try:
                before = os.fstat(fd)
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                     (before.st_uid, before.st_gid) ==
                     (expected_uid, expected_gid) and
                     stat.S_IMODE(before.st_mode) == expected_mode and
                     before.st_size <= maximum and
                     _identity(before) == _identity(named),
                     'BUILD_SOURCE_METADATA')
                hashed = hashlib.sha256()
                count = 0
                while count <= maximum:
                    self._time()
                    chunk = os.read(fd, min(1048576, maximum + 1 - count))
                    if not chunk:
                        break
                    count += len(chunk)
                    self.total += len(chunk)
                    need(self.total <= MAX_TOTAL, 'BUILD_SOURCE_TOTAL')
                    hashed.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_fd, view)
                        need(written > 0, 'BUILD_SHORT_WRITE')
                        view = view[written:]
                after = os.fstat(fd)
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                need(count == before.st_size and count <= maximum and
                     hashed.hexdigest() == expected_sha and
                     _identity(before) == _identity(after) == _identity(named),
                     'BUILD_SOURCE_CHANGED')
                self._parent_still_named(source, expected_uid, directory)
                return {'path': source, 'sha256': expected_sha, 'bytes': count,
                        'device': before.st_dev, 'inode': before.st_ino,
                        'uid': before.st_uid, 'gid': before.st_gid,
                        'mode': stat.S_IMODE(before.st_mode)}
            finally:
                os.close(fd)
        finally:
            os.close(directory)

    def read_small(self, source, expected_sha, *, maximum, expected_uid,
                   expected_gid, expected_mode):
        need(type(maximum) is int and 0 < maximum <= 1048576 and
             HASH.fullmatch(expected_sha) is not None,
             'BUILD_SMALL_SOURCE_CONTRACT')
        directory, name = self._parent(source, expected_uid)
        try:
            fd = os.open(name, SOURCE_FLAGS, dir_fd=directory)
            try:
                before = os.fstat(fd)
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                     (before.st_uid, before.st_gid) ==
                     (expected_uid, expected_gid) and
                     stat.S_IMODE(before.st_mode) == expected_mode and
                     before.st_size <= maximum and
                     _identity(before) == _identity(named),
                     'BUILD_SMALL_SOURCE_META')
                chunks = []
                count = 0
                while count <= maximum:
                    self._time()
                    part = os.read(fd, min(65536, maximum + 1 - count))
                    if not part:
                        break
                    count += len(part)
                    self.total += len(part)
                    need(self.total <= MAX_TOTAL, 'BUILD_SOURCE_TOTAL')
                    chunks.append(part)
                data = b''.join(chunks)
                after = os.fstat(fd)
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                need(count == before.st_size and count <= maximum and
                     _identity(before) == _identity(after) == _identity(named)
                     and digest(data) == expected_sha,
                     'BUILD_SMALL_SOURCE_CHANGED')
                self._parent_still_named(source, expected_uid, directory)
                return data
            finally:
                os.close(fd)
        finally:
            os.close(directory)


def make_plan(package_root, recovery_root, inventory_bytes, recovery_bytes,
              manifest_bytes, dependencies_bytes, *, expected_inventory_sha,
              source_owner_uid=0, source_owner_gid=0):
    """Pure exact-source plan. The caller authenticates archive bytes first."""
    need(all(type(x) is str and x.startswith('/') and
             posixpath.normpath(x) == x for x in (package_root, recovery_root))
         and HASH.fullmatch(expected_inventory_sha) is not None and
         digest(inventory_bytes) == expected_inventory_sha and
         digest(recovery_bytes) == RECOVERY_SHA256,
         'BUILD_CONTRACT_PINS')
    contract = restricted.projection_contract(manifest_bytes, dependencies_bytes)
    inventory = json.loads(inventory_bytes)['files']
    recovery = json.loads(recovery_bytes)
    manifest = json.loads(manifest_bytes)
    dependencies = json.loads(dependencies_bytes)['dependencies']
    need(type(inventory) is dict and set(TOOL_FILES + ('collector_v3.py',)) <=
         set(inventory) and type(recovery) is dict and
         type(recovery.get('manifest')) is dict and
         recovery.get('symlinks') == contract['symlinks'],
         'BUILD_CONTRACT_SCHEMA')
    regular = {}
    for path, pin in contract['regular'].items():
        key = 'rootfs' + path
        row = recovery['manifest'].get(key)
        need(manifest.get(key) == pin['sha256'], 'BUILD_MANIFEST_ROUTE')
        if path in CURRENT_PROJECT_SOURCES:
            name = CURRENT_PROJECT_SOURCES[path]
            need(inventory.get(name, {}).get('sha256') == pin['sha256'],
                 'BUILD_CURRENT_PROJECT_PIN')
            source = package_root + '/' + name
            uid, gid, mode = source_owner_uid, source_owner_gid, 0o444
        else:
            need(type(row) is dict and row.get('sha256') == pin['sha256'] and
                 row.get('kind') in ('bundled', 'installed'),
                 'BUILD_RECOVERY_ROUTE')
            if row['kind'] == 'bundled':
                need(row.get('source') == 'objects/' + pin['sha256'],
                     'BUILD_RECOVERY_ROUTE')
                source = recovery_root + '/objects/' + pin['sha256']
                uid, gid, mode = source_owner_uid, source_owner_gid, 0o444
            else:
                dependency = dependencies.get(path)
                need(type(dependency) is dict and
                     row.get('source') == dependency.get('source') and
                     dependency.get('sha256') == pin['sha256'] and
                     type(row.get('source')) is str and
                     row['source'].startswith('/'), 'BUILD_INSTALLED_ROUTE')
                source = row['source']
                uid = 1001 if path in ('/opt/codex/bin/codex',
                                       '/opt/codex/codex-package.json') else 0
                gid = uid
                mode = int(dependency['source_mode'], 8)
        regular[path] = {'source': source, 'sha256': pin['sha256'],
                         'target_mode': int(pin['mode'], 8),
                         'source_uid': uid, 'source_gid': gid,
                         'source_mode': mode,
                         'maximum': (313_000_000 if path == '/opt/codex/bin/codex'
                                     else 32_000_000)}
    tool = {name: {'source': package_root + '/' + name,
                   'sha256': inventory[name]['sha256'],
                   'target_mode': 0o444, 'source_uid': source_owner_uid,
                   'source_gid': source_owner_gid,
                   'source_mode': 0o444, 'maximum': 262144}
            for name in TOOL_FILES}
    collector = {'source': package_root + '/collector_v3.py',
                 'sha256': restricted.COLLECTOR_SHA256,
                 'target_mode': 0o444, 'source_uid': source_owner_uid,
                 'source_gid': source_owner_gid,
                 'source_mode': 0o444, 'maximum': 65536}
    need(inventory['collector_v3.py']['sha256'] == collector['sha256'],
         'BUILD_COLLECTOR_PIN')
    return {'projection_contract': contract, 'regular': regular,
            'tool': tool, 'collector': collector,
            'package_root': package_root,
            'expected_inventory_sha': expected_inventory_sha}


def _open_child(parent_fd, name):
    return os.open(name, DIR_FLAGS, dir_fd=parent_fd)


def _walk(root_fd, absolute):
    need(type(absolute) is str and restricted._absolute(absolute),
         'BUILD_TARGET_PATH')
    parts = absolute.split('/')[1:]
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = _open_child(fd, part)
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _verify_target(parent_fd, name, expected_sha, expected_size, expected_mode,
                   owner_uid, owner_gid, maximum):
    fd = os.open(name, SOURCE_FLAGS, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
             (before.st_uid, before.st_gid) == (owner_uid, owner_gid) and
             stat.S_IMODE(before.st_mode) == expected_mode and
             before.st_size == expected_size and before.st_size <= maximum and
             _identity(before) == _identity(named), 'BUILD_TARGET_READBACK')
        count = 0
        hashed = hashlib.sha256()
        while count <= maximum:
            chunk = os.read(fd, min(1048576, maximum + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            hashed.update(chunk)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        need(count == expected_size and count <= maximum and
             hashed.hexdigest() == expected_sha and
             _identity(before) == _identity(after) == _identity(named),
             'BUILD_TARGET_READBACK')
    finally:
        os.close(fd)


def _file(root_fd, path, pin, reader, owner_uid, owner_gid, observations):
    parent, name = _walk(root_fd, path)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and
                 (info.st_uid, info.st_gid) == (owner_uid, owner_gid) and
                 info.st_nlink == 1,
                 'BUILD_TARGET_OWNER')
            observation = reader.copy(pin['source'], fd, pin['sha256'],
                                      maximum=pin['maximum'],
                                      expected_uid=pin['source_uid'],
                                      expected_gid=pin['source_gid'],
                                      expected_mode=pin['source_mode'])
            os.fchmod(fd, pin['target_mode'])
            os.fsync(fd)
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            need(after.st_dev == named.st_dev and after.st_ino == named.st_ino and
                 (after.st_uid, after.st_gid) == (owner_uid, owner_gid) and
                 after.st_nlink == 1 and
                 stat.S_IMODE(after.st_mode) == pin['target_mode'],
                 'BUILD_TARGET_CHANGED')
            observations[path] = observation
        finally:
            os.close(fd)
        _verify_target(parent, name, pin['sha256'], observation['bytes'],
                       pin['target_mode'], owner_uid, owner_gid,
                       pin['maximum'])
        os.fsync(parent)
    finally:
        os.close(parent)


def _empty(root_fd, path, owner_uid, owner_gid):
    parent, name = _walk(root_fd, path)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            need((info.st_uid, info.st_gid) == (owner_uid, owner_gid) and
                 info.st_size == 0 and
                 info.st_nlink == 1, 'BUILD_EMPTY_TARGET')
            os.fchmod(fd, 0o444)
            os.fsync(fd)
        finally:
            os.close(fd)
        _verify_target(parent, name, digest(b''), 0, 0o444,
                       owner_uid, owner_gid, 0)
        os.fsync(parent)
    finally:
        os.close(parent)


def _mark_consumed(parent_fd, name):
    """Best-effort fixed marker; the new path is nonreusable even if full."""
    try:
        child = _open_child(parent_fd, name)
        try:
            fd = os.open('CONSUMED_PARTIAL', os.O_WRONLY | os.O_CREAT |
                         os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=child)
            try:
                os.write(fd, b'CONSUMED_PARTIAL_NO_RETRY\n')
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(child)
        finally:
            os.close(child)
    except OSError:
        pass


def seal_manifest(value):
    raw = canonical(value)
    return {'manifest': value, 'manifest_sha256': digest(raw)}


def verify_manifest(envelope):
    need(type(envelope) is dict and set(envelope) ==
         {'manifest', 'manifest_sha256'} and
         HASH.fullmatch(envelope['manifest_sha256']) is not None and
         digest(canonical(envelope['manifest'])) == envelope['manifest_sha256'] and
         envelope['manifest'].get('status') == 'PREPARED_NOT_EXECUTED',
         'BUILD_MANIFEST_TAMPERED')
    return True


def _release_observations(observations, plan):
    for index, path in enumerate(('/opt/codex/bin/codex',
                                  '/opt/codex/codex-package.json')):
        if path not in plan['regular']:
            continue  # Synthetic miniature plans only; the real plan has both.
        record = observations.get(path)
        need(type(record) is dict and
             record['device'] == restricted.SOURCE_DEVICE and
             record['inode'] == restricted.SOURCE_INODE[index] and
             record['uid'] == record['gid'] == 1001 and
             record['bytes'] == restricted.SOURCE_SIZE[index] and
             record['sha256'] == restricted.SOURCE_SHA256[index],
             'BUILD_RELEASE_IDENTITY')


def materialize(plan, base_root, nonce, devices, *, reader=None,
                owner_uid=0, owner_gid=0, root_uid=0, backing_observer=None,
                archive_sha256):
    """Create only new stage/tool objects; never launch the manifest."""
    need(type(nonce) is str and NONCE.fullmatch(nonce) is not None and
         type(owner_uid) is int and type(owner_gid) is int and
         (os.geteuid(), os.getegid()) == (owner_uid, owner_gid) and
         type(devices) is tuple and devices and len(set(devices)) == len(devices)
         and all(type(x) is str and re.fullmatch(r'[0-9]+:[0-9]+', x)
                 for x in devices) and HASH.fullmatch(archive_sha256) is not None,
         'BUILD_ARGUMENTS')
    reader = reader if reader is not None else FileSource(owner_uid,
                                                          root_uid=root_uid)
    tool_name, stage_name = ('zen53-owner-tool-' + nonce,
                             'zen53-owner-capture-' + nonce)
    need(type(base_root) is str and base_root.startswith('/') and
         posixpath.normpath(base_root) == base_root,
         'BUILD_BASE')
    # The real operator may use /var/lib only. Tests must use disposable roots.
    import evidence
    base_fd = evidence._open_dir(base_root, owner_uid, root_uid)
    made = []
    new_identities = {}
    try:
        for name in (tool_name, stage_name):
            os.mkdir(name, 0o700, dir_fd=base_fd)
            made.append(name)
            child = _open_child(base_fd, name)
            try:
                info = os.fstat(child)
                need(stat.S_ISDIR(info.st_mode) and
                     (info.st_uid, info.st_gid) == (owner_uid, owner_gid) and
                     stat.S_IMODE(info.st_mode) == 0o700,
                     'BUILD_NEW_DIRECTORY')
                new_identities[name] = (info.st_dev, info.st_ino,
                                        info.st_uid, info.st_gid,
                                        stat.S_IMODE(info.st_mode))
            finally:
                os.close(child)
        tool_fd = _open_child(base_fd, tool_name)
        stage_fd = _open_child(base_fd, stage_name)
        observations = {}
        try:
            for name, pin in sorted(plan['tool'].items()):
                _file(tool_fd, '/' + name, pin, reader, owner_uid, owner_gid,
                      observations)
            _file(stage_fd, '/capture_ownership_v3.py', plan['collector'],
                  reader, owner_uid, owner_gid, observations)
            for name in ('host-evidence', 'controller-evidence', 'projection'):
                os.mkdir(name, 0o700, dir_fd=stage_fd)
            projection_fd = _open_child(stage_fd, 'projection')
            try:
                contract = plan['projection_contract']
                dirs = sorted(restricted.expected_directories(contract),
                              key=lambda x: (x.count('/'), x))
                for path in dirs:
                    if path == '/':
                        continue
                    parent, name = _walk(projection_fd, path)
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent)
                    finally:
                        os.close(parent)
                for path, pin in sorted(plan['regular'].items()):
                    _file(projection_fd, path, pin, reader, owner_uid, owner_gid,
                          observations)
                for path, target in sorted(contract['symlinks'].items()):
                    parent, name = _walk(projection_fd, path)
                    try:
                        os.symlink(target, name, dir_fd=parent)
                        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        need(stat.S_ISLNK(info.st_mode) and
                             (info.st_uid, info.st_gid) ==
                             (owner_uid, owner_gid) and
                             os.readlink(name, dir_fd=parent) == target,
                             'BUILD_SYMLINK')
                    finally:
                        os.close(parent)
                for path, addition in sorted(contract['additions'].items()):
                    if addition['kind'] == 'bind_target_file':
                        _empty(projection_fd, path, owner_uid, owner_gid)
                    else:
                        _file(projection_fd, path, plan['collector'], reader,
                              owner_uid, owner_gid, observations)
                for path in reversed(dirs):
                    fd = os.dup(projection_fd) if path == '/' else None
                    if fd is None:
                        parent, name = _walk(projection_fd, path)
                        try:
                            fd = _open_child(parent, name)
                        finally:
                            os.close(parent)
                    try:
                        os.fchmod(fd, 0o555)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            finally:
                os.close(projection_fd)
            os.fsync(tool_fd)
            os.fsync(stage_fd)
        finally:
            os.close(tool_fd)
            os.close(stage_fd)
        stage = base_root + '/' + stage_name
        tool = base_root + '/' + tool_name
        need(evidence._still_bound(base_root, base_fd, owner_uid, root_uid),
             'BUILD_BASE_CHANGED')
        for name, expected in new_identities.items():
            info = os.stat(name, dir_fd=base_fd, follow_symlinks=False)
            need(stat.S_ISDIR(info.st_mode) and
                 (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                  stat.S_IMODE(info.st_mode)) == expected,
                 'BUILD_NEW_DIRECTORY_CHANGED')
        need(set(os.listdir(tool)) == set(TOOL_FILES) and
             set(os.listdir(stage)) == {'projection',
                 'capture_ownership_v3.py', 'host-evidence',
                 'controller-evidence'}, 'BUILD_TREE_SET')
        for name in ('host-evidence', 'controller-evidence'):
            location = stage + '/' + name
            info = os.stat(location, follow_symlinks=False)
            need(stat.S_ISDIR(info.st_mode) and
                 (info.st_uid, info.st_gid) == (owner_uid, owner_gid) and
                 stat.S_IMODE(info.st_mode) == 0o700 and
                 not os.listdir(location), 'BUILD_EVIDENCE_DIRECTORY')
        _release_observations(observations, plan)
        summary = restricted.verify_projection_tree(
            stage + '/projection', plan['projection_contract'],
            owner_uid=owner_uid, owner_gid=owner_gid,
            root_uid=root_uid)
        if backing_observer is None:
            need(base_root == '/var/lib' and owner_uid == 0,
                 'BUILD_REAL_BACKING_ONLY')
            snapshot = operator_entry._verified_backing(
                operator_entry._backing_paths(stage, tool), devices)
        else:
            snapshot = backing_observer(stage, tool, devices)
        need(type(snapshot) is dict and
             set(snapshot) == {'origins', 'origin_nodes', 'paths'} and
             set(snapshot['origin_nodes']) == set(devices) and
             set(snapshot['origins'].values()) == set(devices) and
             type(snapshot['paths']) is tuple and snapshot['paths'],
             'BUILD_BACKING_SNAPSHOT')
        nodes = snapshot['origin_nodes']
        need(all(type(nodes[d]) is str and re.fullmatch(
            r'/dev/(?:sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+)', nodes[d])
            for d in devices), 'BUILD_ORIGIN_NODE')
        scope = '/system.slice/' + stage_name + '.scope'
        command = ['/usr/bin/python3.12', '-I', '-S', '-B',
                   tool + '/operator_entry.py', stage, *devices,
                   '--cooperative-service-owners']
        payload = {
            'format': 'zen53-stage-launch-v1', 'status': 'PREPARED_NOT_EXECUTED',
            'approved_archive_sha256': archive_sha256,
            'inventory_sha256': plan['expected_inventory_sha'],
            'recovery_sha256': RECOVERY_SHA256,
            'nonce': nonce, 'tool': tool, 'stage': stage,
            'scope': scope, 'command_argv': command,
            'scope_limits': {'RuntimeMaxSec': 360,
                             'RuntimeRandomizedExtraSec': 0,
                             'TimeoutStopSec': 3, 'KillMode': 'control-group',
                             'SendSIGKILL': True, 'FinalKillSignal': 'SIGKILL',
                             'Delegate': False,
                             'CPUQuota': '50%', 'MemoryMaxBytes': 384*1024*1024,
                             'MemorySwapMaxBytes': 0, 'TasksMax': 32,
                             'schedule_margin_seconds': 90},
            'source_to_origin': snapshot,
            'io_targets': [{'origin': d, 'block_node': nodes[d],
                            'rbps': 33554432, 'wbps': 1048576,
                            'riops': 1024, 'wiops': 1024}
                           for d in devices],
            'projection_summary': summary,
            'source_observations': observations,
            'tool_identity': [os.stat(tool).st_dev, os.stat(tool).st_ino],
            'stage_identity': [os.stat(stage).st_dev, os.stat(stage).st_ino],
        }
        envelope = seal_manifest(payload)
        verify_manifest(envelope)
        tool_fd = _open_child(base_fd, tool_name)
        try:
            fd = os.open('LAUNCH_MANIFEST.json', os.O_WRONLY | os.O_CREAT |
                         os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=tool_fd)
            try:
                raw = canonical(envelope)
                need(len(raw) <= 262144, 'BUILD_MANIFEST_BOUND')
                view = memoryview(raw)
                while view:
                    written = os.write(fd, view)
                    need(written > 0, 'BUILD_MANIFEST_WRITE')
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            _verify_target(tool_fd, 'LAUNCH_MANIFEST.json', digest(raw),
                           len(raw), 0o600, owner_uid, owner_gid, 262144)
            os.fsync(tool_fd)
        finally:
            os.close(tool_fd)
        need(set(os.listdir(tool)) == set(TOOL_FILES) |
             {'LAUNCH_MANIFEST.json'}, 'BUILD_TOOL_FINAL_SET')
        os.fsync(base_fd)
        return envelope
    except BaseException:
        for name in made:
            _mark_consumed(base_fd, name)
        if made:
            os.fsync(base_fd)
        raise
    finally:
        os.close(base_fd)


def main(argv=None):
    """Future root-only preparation CLI; emits data and launches nothing."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        need(len(args) >= 8 and args[0] == 'prepare', 'BUILD_CLI_ARGUMENTS')
        (_, package, recovery, nonce, inventory_sha, archive_path,
         archive_sha, *devices) = args
        need(HASH.fullmatch(inventory_sha) is not None and
             HASH.fullmatch(archive_sha) is not None and devices,
             'BUILD_CLI_ARGUMENTS')
        reader = FileSource(0)
        reader.read_small(archive_path, archive_sha, maximum=1048576,
                          expected_uid=0, expected_gid=0,
                          expected_mode=0o444)
        inventory = reader.read_small(package + '/INVENTORY.json',
                                      inventory_sha, maximum=262144,
                                      expected_uid=0, expected_gid=0,
                                      expected_mode=0o444)
        recovery_raw = reader.read_small(package + '/recovery_contract.json',
                                         RECOVERY_SHA256, maximum=524288,
                                         expected_uid=0, expected_gid=0,
                                         expected_mode=0o444)
        manifest_raw = reader.read_small(
            package + '/runtime_source_manifest.json',
            restricted.RUNTIME_MANIFEST_SHA256, maximum=262144,
            expected_uid=0, expected_gid=0, expected_mode=0o444)
        dependencies_raw = reader.read_small(
            package + '/runtime_dependencies.json',
            restricted.RUNTIME_DEPENDENCIES_SHA256, maximum=262144,
            expected_uid=0, expected_gid=0, expected_mode=0o444)
        plan = make_plan(package, recovery, inventory, recovery_raw,
                         manifest_raw, dependencies_raw,
                         expected_inventory_sha=inventory_sha)
        result = materialize(plan, '/var/lib', nonce, tuple(devices),
                             reader=reader, archive_sha256=archive_sha)
        sys.stdout.buffer.write(canonical(result))
        return 0
    except BaseException:
        print('ZEN53_STAGE_BUILD_BLOCKED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
