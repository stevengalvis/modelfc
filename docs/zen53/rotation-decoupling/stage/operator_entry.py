"""One-shot entrypoint proposal. Never invoke without separate VPS approval.

This file performs no work on import. The future operator must first place
this entire verified package in a sealed root-owned directory and enter the
exact non-delegated transient scope described in OPERATOR_PROCEDURE.md.
"""
import json
import os
import posixpath
import re
import resource
import stat
import sys
import sysconfig


_DIRECT_DISK = r'(?:sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+)'


def _sysfs_attribute(path):
    """Read one small sysfs identity attribute without following its final link."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        value = os.read(fd, 65)
        after = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or
                len(value) > 64):
            raise ValueError('ENTRY_SYSFS_IDENTITY')
        return value.decode('ascii').strip()
    finally:
        os.close(fd)


def _verify_direct_block_topology(device, sysroot='/sys'):
    """Return the originating disk ID/name for a verified direct source."""
    link = sysroot + '/dev/block/' + device
    if not stat.S_ISLNK(os.lstat(link).st_mode):
        raise ValueError('ENTRY_SYSFS_IDENTITY')
    target = os.path.realpath(link)
    prefix = sysroot + '/devices/pci'
    if not target.startswith(prefix):
        raise ValueError('ENTRY_COMPLEX_BLOCK_DEVICE')
    match = re.fullmatch(
        re.escape(sysroot) + r'/devices/pci[0-9a-fA-F]{4}:[0-9a-fA-F]{2}/.+/block/(' +
        _DIRECT_DISK + r')(?:/([^/]+))?', target)
    if match is None:
        raise ValueError('ENTRY_COMPLEX_BLOCK_DEVICE')
    disk, partition = match.groups()
    if _sysfs_attribute(target + '/dev') != device:
        raise ValueError('ENTRY_SYSFS_IDENTITY')
    if partition is None:
        disk_path = target
        if os.path.lexists(target + '/partition'):
            raise ValueError('ENTRY_SYSFS_IDENTITY')
    else:
        suffix = 'p' if disk.startswith('nvme') else ''
        name = re.fullmatch(re.escape(disk) + suffix + r'([1-9][0-9]*)',
                            partition)
        if name is None:
            raise ValueError('ENTRY_SYSFS_IDENTITY')
        number = _sysfs_attribute(target + '/partition')
        if number != name[1]:
            raise ValueError('ENTRY_SYSFS_IDENTITY')
        disk_path = os.path.dirname(target)
        if os.path.lexists(target + '/slaves'):
            raise ValueError('ENTRY_COMPLEX_BLOCK_DEVICE')
    disk_device = _sysfs_attribute(disk_path + '/dev')
    if re.fullmatch(r'[0-9]+:[0-9]+', disk_device) is None:
        raise ValueError('ENTRY_SYSFS_IDENTITY')
    if (partition is not None and disk_device == device or
            os.path.lexists(disk_path + '/partition')):
        raise ValueError('ENTRY_SYSFS_IDENTITY')
    major, minor = map(int, disk_device.split(':'))
    node = '/dev/' + disk
    disk_node = os.lstat(node)
    if (not stat.S_ISBLK(disk_node.st_mode) or disk_node.st_uid != 0 or
            disk_node.st_nlink != 1 or
            disk_node.st_rdev != os.makedev(major, minor)):
        raise ValueError('ENTRY_ORIGIN_BLOCK_NODE')
    for canonical_link in (sysroot + '/dev/block/' + disk_device,
                           sysroot + '/block/' + disk):
        if (not stat.S_ISLNK(os.lstat(canonical_link).st_mode) or
                os.path.realpath(canonical_link) != disk_path):
            raise ValueError('ENTRY_SYSFS_IDENTITY')
    slaves = disk_path + '/slaves'
    if not stat.S_ISDIR(os.lstat(slaves).st_mode) or os.listdir(slaves):
        raise ValueError('ENTRY_COMPLEX_BLOCK_DEVICE')
    if (os.path.realpath(link) != target or
            _sysfs_attribute(target + '/dev') != device or
            _sysfs_attribute(disk_path + '/dev') != disk_device):
        raise ValueError('ENTRY_SYSFS_IDENTITY')
    if (any(not stat.S_ISLNK(os.lstat(item).st_mode) or
            os.path.realpath(item) != disk_path for item in
            (sysroot + '/dev/block/' + disk_device,
             sysroot + '/block/' + disk)) or
            not stat.S_ISDIR(os.lstat(slaves).st_mode) or os.listdir(slaves)):
        raise ValueError('ENTRY_SYSFS_IDENTITY')
    disk_node_after = os.lstat(node)
    if (not stat.S_ISBLK(disk_node_after.st_mode) or
            (disk_node.st_dev, disk_node.st_ino, disk_node.st_rdev,
             disk_node.st_uid, disk_node.st_gid, disk_node.st_mode,
             disk_node.st_nlink, disk_node.st_ctime_ns) !=
            (disk_node_after.st_dev, disk_node_after.st_ino,
             disk_node_after.st_rdev, disk_node_after.st_uid,
             disk_node_after.st_gid, disk_node_after.st_mode,
             disk_node_after.st_nlink, disk_node_after.st_ctime_ns)):
        raise ValueError('ENTRY_ORIGIN_BLOCK_NODE')
    return disk_device, node, '/dev/' + (partition or disk)

# -I/-P remove the script directory from import search. The operator must
# independently seal and hash this root-owned package before invocation.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import admission
import capture_once
import restricted_launcher as restricted


def verify_backing(paths, devices, mountinfo):
    """Keep source st_dev separate from effective io.max disk identities."""
    if type(mountinfo) is not str or len(mountinfo) > 1048576:
        raise ValueError('ENTRY_MOUNTINFO')
    mounts = []
    for line in mountinfo.splitlines():
        halves = line.split(' - ')
        if len(halves) != 2:
            raise ValueError('ENTRY_MOUNTINFO')
        fields, right = halves[0].split(), halves[1].split()
        if len(fields) < 6 or len(right) < 3 or not re.fullmatch(
                r'[0-9]+:[0-9]+', fields[2]):
            raise ValueError('ENTRY_MOUNTINFO')
        point = re.sub(r'\\([0-7]{3})',
                       lambda match: chr(int(match[1], 8)), fields[4])
        if not point.startswith('/') or posixpath.normpath(point) != point:
            raise ValueError('ENTRY_MOUNTINFO')
        mount_root = re.sub(r'\\([0-7]{3})',
                            lambda match: chr(int(match[1], 8)), fields[3])
        if not mount_root.startswith('/') or posixpath.normpath(mount_root) != mount_root:
            raise ValueError('ENTRY_MOUNTINFO')
        # Retain the complete covering record for each path. A set of source
        # devices alone cannot detect one path moving between two partitions
        # that are already present in that set.
        mounts.append((point, fields[2], right[0], right[1], mount_root,
                       line))
    for virtual, expected in (('/run', 'tmpfs'), ('/proc', 'proc'),
                              ('/sys', 'sysfs')):
        exact = [item for item in mounts if item[0] == virtual]
        if len(exact) != 1 or exact[0][2] != expected:
            raise ValueError('ENTRY_VIRTUAL_MOUNT')
    dev_parent = os.stat('/dev', follow_symlinks=False)
    if (not stat.S_ISDIR(dev_parent.st_mode) or dev_parent.st_uid != 0 or
            stat.S_IMODE(dev_parent.st_mode) & 0o022):
        raise ValueError('ENTRY_BLOCK_SOURCE_PARENT')
    if any(item[0].startswith('/run/') and item[2] not in ('tmpfs', 'ramfs')
           for item in mounts):
        raise ValueError('ENTRY_RUN_BLOCK_MOUNT')
    if any(item[0] in ('/sys/dev', '/sys/dev/block', '/sys/block', '/sys/devices') or
           item[0].startswith(('/sys/dev/block/', '/sys/block/', '/sys/devices/pci'))
           for item in mounts):
        raise ValueError('ENTRY_SYSFS_MOUNT')
    observed = []
    for path in paths:
        resolved = os.path.realpath(path)
        if not resolved.startswith('/') or '..' in resolved.split('/'):
            raise ValueError('ENTRY_SOURCE_PATH')
        candidates = [item for item in mounts if
                      resolved == item[0] or
                      resolved.startswith(item[0].rstrip('/') + '/')]
        if not candidates:
            raise ValueError('ENTRY_MOUNT_COVERAGE')
        candidates.sort(key=lambda item: len(item[0]), reverse=True)
        chosen = candidates[0]
        if (chosen[0] == resolved or chosen[4] != '/' or
            (len(candidates) > 1 and len(candidates[0][0]) == len(candidates[1][0]))) or any(
                item[0].startswith(resolved.rstrip('/') + '/') for item in mounts):
            raise ValueError('ENTRY_NESTED_MOUNT')
        value = os.stat(path).st_dev
        device = str(os.major(value)) + ':' + str(os.minor(value))
        if chosen[1] != device or chosen[2] not in ('ext4', 'xfs'):
            raise ValueError('ENTRY_BLOCK_ATTRIBUTION')
        source = chosen[3]
        if not source.startswith('/dev/'):
            raise ValueError('ENTRY_BLOCK_SOURCE')
        source_stat = os.stat(source, follow_symlinks=False)
        if (not stat.S_ISBLK(source_stat.st_mode) or
                source_stat.st_uid != 0 or source_stat.st_nlink != 1 or
                str(os.major(source_stat.st_rdev)) + ':' +
                str(os.minor(source_stat.st_rdev)) != device):
            raise ValueError('ENTRY_BLOCK_SOURCE')
        source_identity = (source_stat.st_dev, source_stat.st_ino,
                           source_stat.st_rdev, source_stat.st_mode,
                           source_stat.st_uid, source_stat.st_gid,
                           source_stat.st_nlink, source_stat.st_ctime_ns)
        observed.append((path, resolved, chosen, device, source_identity))
    origins = {}
    origin_nodes = {}
    for device in sorted({item[3] for item in observed}):
        # A partition has no slaves directory of its own. Authenticate its
        # sysfs identity and check the whole disk; never interpret ENOENT as
        # proof of an empty dependency list. io.max targets the verified disk,
        # while mount/source identity remains the observed filesystem device.
        disk_device, disk_node, source_node = _verify_direct_block_topology(device)
        if any(item[0] == node or item[0].startswith(node + '/')
               for item in mounts for node in (disk_node, source_node)):
            raise ValueError('ENTRY_ORIGIN_MOUNT')
        for path, resolved, chosen, observed_device, identity in observed:
            if observed_device != device:
                continue
            if chosen[3] != source_node:
                raise ValueError('ENTRY_BLOCK_SOURCE_ALIAS')
            current = os.stat(source_node, follow_symlinks=False)
            current_identity = (current.st_dev, current.st_ino,
                                current.st_rdev, current.st_mode,
                                current.st_uid, current.st_gid,
                                current.st_nlink, current.st_ctime_ns)
            if current_identity != identity:
                raise ValueError('ENTRY_BLOCK_SOURCE_CHANGED')
        origins[device] = disk_device
        if (disk_device in origin_nodes and
                origin_nodes[disk_device] != disk_node):
            raise ValueError('ENTRY_ORIGIN_NODE_CONFLICT')
        origin_nodes[disk_device] = disk_node
    if (type(devices) is not tuple or not devices or
            len(set(devices)) != len(devices) or
            set(devices) != set(origins.values())):
        raise ValueError('ENTRY_IO_DEVICE')
    return {'origins': origins, 'origin_nodes': origin_nodes,
            'paths': tuple((*item, origins[item[3]]) for item in observed)}


def _backing_paths(stage, tool):
    return (stage, tool, restricted.RELEASE, restricted.BWRAP,
            capture_once.PYTHON, '/usr', '/usr/bin', '/usr/lib',
            '/usr/lib/x86_64-linux-gnu', '/etc', '/lib', '/lib64',
            sysconfig.get_path('stdlib'),
            *(path for path, *_ in admission.LOCKS),
            *(os.path.dirname(path) for path in admission.PENDING
              if not path.startswith('/run/')))


def _verified_backing(paths, devices):
    with open('/proc/self/mountinfo', encoding='ascii') as stream:
        mountinfo = stream.read(1048577)
    return verify_backing(paths, devices, mountinfo)


def configuration(argv):
    if (len(argv) < 3 or len(argv) > 18 or
            not capture_once.STAGE_PATTERN.fullmatch(argv[1]) or
            any(re.fullmatch(r"[0-9]+:[0-9]+", value) is None
                for value in argv[2:]) or len(set(argv[2:])) != len(argv[2:])):
        raise ValueError("ENTRY_ARGUMENTS")
    stage = argv[1]
    devices = tuple(argv[2:])
    name = stage.rsplit("/", 1)[1]
    scope = "/system.slice/" + name + ".scope"
    tool = '/var/lib/zen53-owner-tool-' + name.rsplit('-', 1)[1]
    if os.path.abspath(os.path.dirname(__file__)) != tool:
        raise ValueError('ENTRY_TOOL_PATH')
    # Cover the tool, the two collector executables, Python's library tree,
    # loader/library/config roots and both actual data trees. A non-block or
    # ambiguous backing mount cannot be translated into an io.max promise.
    backing = _verified_backing(_backing_paths(stage, tool), devices)
    return stage, scope, devices, backing


def main(argv=None):
    try:
        arguments = list(sys.argv if argv is None else argv)
        # This explicit flag is deliberately absent from the predecessor
        # procedure. A later trust decision must approve it and the installed
        # controller/user-manager observations before a live invocation.
        cooperative = (len(arguments) >= 4 and
                       arguments[-1] == '--cooperative-service-owners')
        if cooperative:
            arguments.pop()
        # Process-local limits only; no system-wide settings or unit edits.
        for key, maximum in ((resource.RLIMIT_NOFILE, 128),
                             (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
                             (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(key, (maximum, maximum))
        stage, scope, devices, backing = configuration(arguments)
        tool = '/var/lib/zen53-owner-tool-' + stage.rsplit('-', 1)[1]
        def backing_recheck():
            return _verified_backing(_backing_paths(stage, tool), devices) == backing
        guard = admission.Admission(
            scope, devices, backing_recheck=backing_recheck,
            cooperative_policy=(admission.COOPERATIVE_LOCK_POLICY
                                if cooperative else None))
        spawner = admission.BoundedSpawner(guard)
        result = capture_once.capture(stage, admission_guard=guard,
                                      spawn_host=spawner, spawn_restricted=spawner)
        if result["status"] != "PROVISIONAL_SCHEMA_VALID_ONLY":
            raise ValueError("ENTRY_RESULT")
        print(json.dumps({"status": result["status"],
                          "snapshot_sha256": result["snapshot_sha256"],
                          "controller_marker": result["controller_marker"]},
                         sort_keys=True))
        return 0
    except BaseException:
        # Paths, host observations and exception text stay in private evidence.
        print("ZEN53_OWNER_CAPTURE_BLOCKED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
