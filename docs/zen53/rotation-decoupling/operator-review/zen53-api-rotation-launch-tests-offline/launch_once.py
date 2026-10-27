"""One-shot ZEN-53 scope launcher for a separately approved VPS diagnostic.

This is an OFFLINE candidate. Importing this module never runs systemd. The
operator must verify the published archive and current host preconditions before
using main on the VPS. A failed launch consumes the prepared nonce.
"""
import hashlib
import json
import os
import re
import resource
import stat
import subprocess
import sys

BASE = '/var/lib'
NONCE = TOOL = STAGE = LAUNCH_ROOT = MANIFEST = UNIT = MANIFEST_SHA = None
APPROVED_TOOL_ID = APPROVED_STAGE_ID = APPROVED_MANIFEST_ID = APPROVED_SEAL_SHA = None
SYSTEMD_RUN = '/usr/bin/systemd-run'
SYSTEMD_RUN_SHA = 'de28cc5757d3789efa5f6fed5659893410469f68b42175d4531f5b34d3a09e16'
PYTHON = '/usr/bin/python3.12'
ARCHIVE_SHA = '918b7ce69929f375b588a5f4e691e35ed63a7d845fd4ef780d09a03c084e3abc'
INVENTORY_SHA = '98550b8a7fa5842cb2b8afbbd710722d8dfa78c36373c05d1a197caaa543b744'
TOOL_PINS = {
    'admission.py': '0b6b00bfa8cff5f767a445fffe016f99c2f2d1e440d747d647e043459ef93838',
    'capture_once.py': '52864425678d8a986cd07109266a2b1fbd1962ff7da5016393a522ebefc38578',
    'controller.py': 'abc7ffd74b63900a345c0c961c092a784af792716ddf6d97f7774c419ed306cb',
    'evidence.py': '4f78dd1e76482a6c5faef85d52fca4477da117187c21e98f7ef36b5234bc091e',
    'operator_entry.py': 'd7402cc58c1871294fedc85d3c484ed7ca5858ca598d78d8f409a5cf903cd173',
    'owner_adapter.py': '83622ce50dfd253ee796004e2e8799a88d9686a8df0d9fb74d8a85386251d9c6',
    'owner_context_v4.py': '01bc98232685f7672ad229d7b185e0da7631e0a8af6f581492f1f5e868f63430',
    'restricted_launcher.py': '8955df787d86d7f4bea6776cb3b1a7a598933baa73bdce8c29a63c07b1f3a364',
    'runtime_dependencies.json': '5d22624d4f42ac8040fc73a9f13bf710eacce17a9cbfab8fc6e5f6808a2cd758',
    'runtime_source_manifest.json': '521b445d1c477b271de5a5421bb1f62776f5d9cb91bdff911bac866235f2778e',
}
ORIGIN = '8:0'
BLOCK_NODE = '/dev/sda'
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
NONCE32 = re.compile(r'[0-9a-f]{32}\Z')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
LOG_LIMIT = 65536
LAUNCH_DEADLINE = 375  # scope 360s + stop 3s + bounded observation margin
OUTPUT_FILE_LIMIT = 16 * 1024 * 1024
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = (os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
              os.O_CLOEXEC | os.O_NOATIME)


def need(value, code):
    if not value:
        raise ValueError(code)


def bind(approved):
    """Bind to an already authenticated protected approval, never CLI pins."""
    global NONCE, TOOL, STAGE, LAUNCH_ROOT, MANIFEST, UNIT, MANIFEST_SHA
    global APPROVED_TOOL_ID, APPROVED_STAGE_ID, APPROVED_MANIFEST_ID, APPROVED_SEAL_SHA
    need(type(approved) is dict and approved.get('profile') ==
         'zen53-python-diagnostic-v1', 'LAUNCH_BINDING')
    nonce, manifest_sha = approved['nonce'], approved['manifest_sha256']
    need(type(nonce) is str and NONCE32.fullmatch(nonce) is not None and
         type(manifest_sha) is str and HEX64.fullmatch(manifest_sha) is not None,
         'LAUNCH_BINDING')
    need(NONCE is None or (NONCE == nonce and MANIFEST_SHA == manifest_sha),
         'LAUNCH_BINDING_REUSE')
    NONCE, MANIFEST_SHA = nonce, manifest_sha
    APPROVED_TOOL_ID = tuple(approved['tool_identity'])
    APPROVED_STAGE_ID = tuple(approved['stage_identity'])
    APPROVED_MANIFEST_ID = tuple(approved['manifest_identity'])
    APPROVED_SEAL_SHA = approved['manifest_seal_sha256']
    TOOL = BASE + '/zen53-owner-tool-' + nonce
    STAGE = BASE + '/zen53-owner-capture-' + nonce
    LAUNCH_ROOT = BASE + '/zen53-owner-launch-' + nonce
    MANIFEST = TOOL + '/LAUNCH_MANIFEST.json'
    UNIT = 'zen53-owner-capture-' + nonce + '.scope'


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True) + '\n').encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'LAUNCH_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def verified_file(path, expected_hash, maximum, mode, identity=None, *,
                  owner_uid=0, owner_gid=0):
    """Root-owned, single-link no-follow byte read with stable ancestors."""
    need(type(path) is str and path.startswith('/') and '..' not in path.split('/')
         and '//' not in path and '\x00' not in path and path != '/',
         'LAUNCH_PATH')
    parts = path.split('/')[1:]
    parent = os.open('/', DIR_FLAGS)
    held = []
    try:
        for part in parts[:-1]:
            child = os.open(part, DIR_FLAGS, dir_fd=parent)
            try:
                info = os.fstat(child)
                need(stat.S_ISDIR(info.st_mode) and
                     info.st_uid in (0, owner_uid) and
                     info.st_gid in (0, owner_gid)
                     and not stat.S_IMODE(info.st_mode) & 0o022,
                     'LAUNCH_ANCESTOR')
            except BaseException:
                os.close(child)
                raise
            held.append((parent, part, info.st_dev, info.st_ino,
                         info.st_uid, info.st_gid, info.st_mode,
                         info.st_ctime_ns))
            parent = child
        fd = os.open(parts[-1], FILE_FLAGS, dir_fd=parent)
        try:
            before = os.fstat(fd)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            key = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                             s.st_mode, s.st_size, s.st_nlink, s.st_mtime_ns,
                             s.st_ctime_ns)
            need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                 before.st_uid == owner_uid and before.st_gid == owner_gid and
                 stat.S_IMODE(before.st_mode) == mode and
                 before.st_size <= maximum and key(before) == key(named) and
                 (identity is None or (before.st_dev, before.st_ino) == identity),
                 'LAUNCH_FILE_META')
            data = bytearray()
            while len(data) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            need(len(data) == before.st_size and len(data) <= maximum and
                 key(before) == key(after) == key(named) and
                 digest(data) == expected_hash, 'LAUNCH_FILE_CHANGED')
            for (ancestor_fd, name, device, inode, uid, gid, stmode,
                 ctime_ns) in held:
                now = os.stat(name, dir_fd=ancestor_fd, follow_symlinks=False)
                need(stat.S_ISDIR(now.st_mode) and
                     (now.st_dev, now.st_ino, now.st_uid, now.st_gid,
                      now.st_mode, now.st_ctime_ns) ==
                     (device, inode, uid, gid, stmode, ctime_ns),
                     'LAUNCH_ANCESTOR_CHANGED')
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
        for ancestor_fd, *_ in held:
            os.close(ancestor_fd)


def expected_command():
    return [PYTHON, '-I', '-S', '-B', TOOL + '/operator_entry.py', STAGE,
            ORIGIN, '--cooperative-service-owners']


def checked_manifest(raw, tool_id, stage_id):
    need(type(raw) is bytes and len(raw) <= 262144 and
         digest(raw) == MANIFEST_SHA, 'LAUNCH_MANIFEST_BYTES')
    envelope = json.loads(raw, object_pairs_hook=unique_pairs)
    need(type(envelope) is dict and set(envelope) ==
         {'manifest', 'manifest_sha256'} and
         type(envelope['manifest_sha256']) is str and
         HEX64.fullmatch(envelope['manifest_sha256']) is not None and
         digest(canonical(envelope['manifest'])) == envelope['manifest_sha256'] and
         envelope['manifest_sha256'] == APPROVED_SEAL_SHA,
         'LAUNCH_MANIFEST_SEAL')
    item = envelope['manifest']
    need(item['status'] == 'PREPARED_NOT_EXECUTED' and item['nonce'] == NONCE and
         item['tool'] == TOOL and item['stage'] == STAGE and
         item['scope'] == '/system.slice/' + UNIT and
         item['command_argv'] == expected_command() and
         item['approved_archive_sha256'] == ARCHIVE_SHA and
         item['inventory_sha256'] == INVENTORY_SHA and
         item['tool_identity'] == list(tool_id) and
         item['stage_identity'] == list(stage_id) and
         item['scope_limits'] == {
             'RuntimeMaxSec': 360, 'RuntimeRandomizedExtraSec': 0,
             'TimeoutStopSec': 3, 'KillMode': 'control-group',
             'SendSIGKILL': True, 'FinalKillSignal': 'SIGKILL',
             'Delegate': False, 'CPUQuota': '50%',
             'MemoryMaxBytes': 384*1024*1024, 'MemorySwapMaxBytes': 0,
             'TasksMax': 32, 'schedule_margin_seconds': 90} and
         item['io_targets'] == [{'origin': ORIGIN, 'block_node': BLOCK_NODE,
                                'rbps': 33554432, 'wbps': 1048576,
                                'riops': 1024, 'wiops': 1024}],
         'LAUNCH_MANIFEST_CONTRACT')
    return item


def build_scope_argv(item):
    """Exact systemd 255 system scope; never parse/execute shell text."""
    need(item['command_argv'] == expected_command(), 'LAUNCH_COMMAND')
    properties = (
        'RuntimeMaxSec=360s', 'RuntimeRandomizedExtraSec=0',
        'TimeoutStopSec=3s', 'KillMode=control-group',
        'SendSIGKILL=yes', 'FinalKillSignal=SIGKILL', 'Delegate=no',
        'CPUQuota=50%', 'MemoryMax=402653184', 'MemorySwapMax=0',
        'TasksMax=32',
        'IOReadBandwidthMax=/dev/sda 33554432',
        'IOWriteBandwidthMax=/dev/sda 1048576',
        'IOReadIOPSMax=/dev/sda 1024',
        'IOWriteIOPSMax=/dev/sda 1024',
    )
    return (SYSTEMD_RUN, '--system', '--scope', '--unit=' + UNIT,
            '--slice=system.slice', '--no-ask-password',
            '--expand-environment=no',
            *(('--property=' + value) for value in properties),
            '--', *expected_command())


def _directory(path, identity=None, *, empty=False, owner_uid=0,
               owner_gid=0):
    parts = path.split('/')[1:]
    need(path.startswith('/') and parts and all(
        part not in ('', '.', '..') for part in parts), 'LAUNCH_DIR_PATH')
    fd = os.open('/', DIR_FLAGS)
    try:
        for part in parts:
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            prefix = os.fstat(fd)
            need(prefix.st_uid in (0, owner_uid) and
                 prefix.st_gid in (0, owner_gid) and
                 not stat.S_IMODE(prefix.st_mode) & 0o022,
                 'LAUNCH_DIR_ANCESTOR')
        s = os.fstat(fd)
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == owner_uid and
             s.st_gid == owner_gid and
             stat.S_IMODE(s.st_mode) == 0o700 and
             (identity is None or (s.st_dev, s.st_ino) == identity),
             'LAUNCH_DIR_META')
        if empty:
            need(not os.listdir(fd), 'LAUNCH_DIR_NOT_EMPTY')
        return s.st_dev, s.st_ino
    finally:
        os.close(fd)


def read_prepared(systemd_run_sha, archive_path, package_root):
    need(NONCE is not None, 'LAUNCH_BINDING')
    need(type(systemd_run_sha) is str and HEX64.fullmatch(systemd_run_sha),
         'LAUNCH_SYSTEMD_PIN')
    need(systemd_run_sha == SYSTEMD_RUN_SHA, 'LAUNCH_SYSTEMD_PIN')
    tool_id = _directory(TOOL, APPROVED_TOOL_ID)
    stage_id = _directory(STAGE, APPROVED_STAGE_ID)
    need(set(os.listdir(TOOL)) == set(TOOL_PINS) | {'LAUNCH_MANIFEST.json'},
         'LAUNCH_TOOL_SET')
    need(set(os.listdir(STAGE)) == {'projection', 'capture_ownership_v3.py',
                                   'host-evidence', 'controller-evidence'},
         'LAUNCH_STAGE_SET')
    for name, sha in TOOL_PINS.items():
        verified_file(TOOL + '/' + name, sha, 65536, 0o444)
    for name in ('host-evidence', 'controller-evidence'):
        _directory(STAGE + '/' + name, empty=True)
    # These acquisition paths are not recorded in the public stage report.
    # Require explicit paths and verify their bytes; never guess a prior nonce.
    verified_file(archive_path, ARCHIVE_SHA, 1048576, 0o444)
    verified_file(package_root + '/INVENTORY.json', INVENTORY_SHA, 262144, 0o444)
    verified_file(SYSTEMD_RUN, systemd_run_sha, 1048576, 0o755)
    raw = verified_file(MANIFEST, MANIFEST_SHA, 262144, 0o600,
                        APPROVED_MANIFEST_ID)
    item = checked_manifest(raw, tool_id, stage_id)
    need(_directory(TOOL, tool_id) == tool_id and
         _directory(STAGE, stage_id) == stage_id, 'LAUNCH_DIR_META')
    return item


def _exclusive_marker(directory_fd):
    fd = os.open('ATTEMPT_RESERVED', os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        value = ('ZEN53_ONE_ATTEMPT ' + NONCE + '\n').encode('ascii')
        need(os.write(fd, value) == len(value), 'LAUNCH_MARKER_WRITE')
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)


def _save(directory_fd, name, raw):
    need(type(raw) is bytes and len(raw) <= LOG_LIMIT, 'LAUNCH_LOG_BOUND')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(raw)
        while view:
            n = os.write(fd, view)
            need(n > 0, 'LAUNCH_LOG_WRITE')
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)


def _new_output(directory_fd, name):
    """Open a private regular output file, never a pipe to the payload."""
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        os.fsync(directory_fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _child_file_limit():
    """Bound inherited stdout/stderr even before systemd-run execs payload."""
    resource.setrlimit(resource.RLIMIT_FSIZE,
                       (OUTPUT_FILE_LIMIT, OUTPUT_FILE_LIMIT))


def launch_once(systemd_run_sha, archive_path, package_root, *, spawn=subprocess.Popen):
    """Finite observation of one spawn; residual processes become an incident."""
    need((os.geteuid(), os.getegid()) == (0, 0), 'LAUNCH_ROOT')
    item = read_prepared(systemd_run_sha, archive_path, package_root)
    _directory(LAUNCH_ROOT, empty=True)
    fd = os.open(LAUNCH_ROOT, DIR_FLAGS)
    try:
        _exclusive_marker(fd)
        # This pre-spawn record remains a deterministic incident default even
        # if a later write fails or the host loses power before a result.
        _save(fd, 'DEFAULT_UNKNOWN.json', canonical({
            'status': 'UNKNOWN_OR_BLOCKED_NO_RETRY', 'scope': UNIT,
            'manifest_sha256': MANIFEST_SHA, 'no_retry': True}))
        argv = build_scope_argv(item)
        proc = None
        out_fd = err_fd = None
        try:
            out_fd = _new_output(fd, 'stdout')
            err_fd = _new_output(fd, 'stderr')
            _child_file_limit()
            proc = spawn(argv, cwd='/', env=dict(ENV),
                         stdin=subprocess.DEVNULL, stdout=out_fd,
                         stderr=err_fd, close_fds=True,
                         start_new_session=True, shell=False)
            try:
                code = proc.wait(timeout=LAUNCH_DEADLINE)
            except subprocess.TimeoutExpired:
                code = None
            state = ('RETURNED_ZERO_NOT_ACCEPTED' if code == 0 else
                     'INCIDENT_RESIDUAL_PROCESS_POSSIBLE' if code is None else
                     'UNKNOWN_OR_BLOCKED_NO_RETRY')
            _save(fd, 'RESULT.json', canonical({'status': state,
                     'exit_code': code, 'observation_timeout': code is None,
                     'manifest_sha256': MANIFEST_SHA, 'scope': UNIT,
                     'no_retry': True}))
            return state
        except BaseException:
            # DEFAULT_UNKNOWN remains. Closing the parent's regular-file FDs
            # does not close the child's independent descriptors or SIGPIPE it.
            raise
        finally:
            if out_fd is not None:
                os.close(out_fd)
            if err_fd is not None:
                os.close(err_fd)
    finally:
        os.close(fd)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if (len(args) != 10 or args[0::2] !=
            ['--nonce', '--manifest-sha256', '--systemd-run-sha256',
             '--archive-path', '--package-root']):
        print('ZEN53_LAUNCH_SPEC_ARGUMENTS', file=sys.stderr)
        return 2
    try:
        need(False, 'LAUNCH_DIRECT_ENTRY_DISABLED')
        status = launch_once(args[5], args[7], args[9])
        print(status)
        # A launcher return is never diagnostic containment acceptance.
        return 1
    except BaseException:
        print('ZEN53_LAUNCH_BLOCKED_OR_UNCERTAIN', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
