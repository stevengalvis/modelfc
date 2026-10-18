"""Installed root-only supervisor. Candidate code is executed only by worker unit."""
import array
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import pwd
import select
import socket
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path('/usr/lib/modelfc-suite')
STATE = Path('/var/lib/modelfc-suite')
DEPLOY_LOCK = Path('/var/lib/modelfc-deploy/deploy.lock')
GUARD = 'modelfc-suite-guard.service'
WORKER = 'modelfc-suite-worker.service'
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', HOME='/nonexistent')


class Rejected(Exception):
    pass


def command(args, timeout=20, check=True):
    # Fixed trusted commands only; neither candidate paths nor text become shell code.
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=ENV, timeout=timeout, check=check,
                          text=True).stdout


def properties(unit, *names):
    text = command(['/usr/bin/systemctl', 'show', unit, '--all', *sum((['-p', n] for n in names), [])])
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    if set(values) != set(names): raise Rejected('MANAGER_UNKNOWN')
    return values


def no_job(unit):
    label = re.sub(r'[^a-zA-Z0-9]', lambda m: '_%02x' % ord(m[0]), unit)
    value = json.loads(command(['/usr/bin/busctl', '--system', '--json=short', 'get-property',
        'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/' + label,
        'org.freedesktop.systemd1.Unit', 'Job']))
    return value == {'type': '(uo)', 'data': [0, '/']}


def quiescent(unit):
    value = properties(unit, 'ActiveState', 'MainPID', 'ControlPID', 'ControlGroup', 'Job')
    # systemd show prints empty Job when no queued job exists on 255.
    if value['ActiveState'] not in ('inactive', 'failed') or value['MainPID'] != '0' or value['ControlPID'] != '0' or not no_job(unit):
        return False
    group = value['ControlGroup']
    if not group: return True
    if group != '/system.slice/' + unit: raise Rejected('CGROUP_UNKNOWN')
    directory = Path('/sys/fs/cgroup') / group.lstrip('/')
    try:
        return dict(line.split() for line in (directory/'cgroup.events').read_text().splitlines()).get('populated') == '0'
    except FileNotFoundError:
        return not directory.exists()


def deployment_quiescent():
    # Existing controller recovery belongs to that controller, never this runner.
    for path in (DEPLOY_LOCK.parent/'service-pending.json', DEPLOY_LOCK.parent/'test-request.json',
                 Path('/run/modelfc-acquisition/request.json')):
        if path.exists() or path.is_symlink(): raise Rejected('DEPLOYMENT_RECOVERY_REQUIRED')
    units = json.loads(command(['/usr/bin/systemctl', 'list-units', '--all', '--type=service',
                                '--output=json', '--no-pager', 'modelfc-postmerge-*']))
    if not isinstance(units, list): raise Rejected('MANAGER_UNKNOWN')
    for entry in units:
        unit = entry['unit']
        if not re.fullmatch(r'modelfc-postmerge-[a-zA-Z0-9@_.:-]+\.service', unit):
            raise Rejected('MANAGER_UNKNOWN')
        if not quiescent(unit): raise Rejected('DEPLOYMENT_RECOVERY_REQUIRED')


def typed_property(unit, interface, name):
    label = re.sub(r'[^a-zA-Z0-9]', lambda m: '_%02x' % ord(m[0]), unit)
    return json.loads(command(['/usr/bin/busctl', '--system', '--json=short', 'get-property',
        'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/' + label,
        'org.freedesktop.systemd1.' + interface, name]))


def execution_policy(unit, script, suffix):
    expected = ['/usr/bin/python3', '-I', '-B', str(ROOT/script), *suffix]
    value = typed_property(unit, 'Service', 'ExecStart')
    rows = value.get('data')
    if value.get('type') != 'a(sasbttttuii)' or not isinstance(rows, list) or len(rows) != 1:
        raise Rejected('EXECUTION_POLICY')
    if rows[0][:3] != ['/usr/bin/python3', expected, False]: raise Rejected('EXECUTION_POLICY')
    extended = typed_property(unit, 'Service', 'ExecStartEx')
    if extended.get('type') != 'a(sasasttttuii)' or len(extended.get('data', [])) != 1 or extended['data'][0][:3] != ['/usr/bin/python3', expected, []]:
        raise Rejected('EXECUTION_POLICY')
    empty_arrays = ('ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost', 'ExecReload',
                    'Environment', 'PassEnvironment', 'UnsetEnvironment', 'SupplementaryGroups',
                    'BindPaths', 'ReadWritePaths', 'LoadCredential',
                    'LoadCredentialEncrypted', 'SetCredential', 'SetCredentialEncrypted', 'ImportCredential')
    for name in empty_arrays:
        if typed_property(unit, 'Service', name).get('data') != []: raise Rejected('EXECUTION_POLICY')
    expected_files = [[str(STATE/'worker.env'), False]] if unit == WORKER else []
    if typed_property(unit, 'Service', 'EnvironmentFiles').get('data') != expected_files:
        raise Rejected('EXECUTION_POLICY')
    expected_paths = ['-/srv', '-/var/lib/modelfc', '-/var/lib/modelfc-deploy',
                      '-/var/lib/modelfc-suite', '-/etc/modelfc-validator', '-/etc/modelfc'] if unit == WORKER else []
    if typed_property(unit, 'Service', 'InaccessiblePaths').get('data') != expected_paths:
        raise Rejected('EXECUTION_POLICY')
    nss_socket = '/run/systemd/userdb/io.systemd.DynamicUser'
    expected_binds = [[nss_socket, nss_socket, False, 0]] if unit == WORKER else []
    if typed_property(unit, 'Service', 'BindReadOnlyPaths') != dict(type='a(ssbt)', data=expected_binds):
        raise Rejected('EXECUTION_POLICY')
    expected_tmpfs = [['/run', 'ro']] if unit == WORKER else []
    if typed_property(unit, 'Service', 'TemporaryFileSystem').get('data') != expected_tmpfs:
        raise Rejected('EXECUTION_POLICY')
    expected_readonly = [str(ROOT)] if unit == WORKER else []
    if typed_property(unit, 'Service', 'ReadOnlyPaths').get('data') != expected_readonly:
        raise Rejected('EXECUTION_POLICY')


def stop_worker():
    command(['/usr/bin/systemctl', 'stop', WORKER], timeout=65, check=False)
    if not quiescent(WORKER): raise Rejected('CLEANUP_UNKNOWN')


def notify(message, fd=None):
    address = os.environ['NOTIFY_SOCKET']
    if address.startswith('@'): address = '\0' + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        ancillary = [] if fd is None else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]
        client.sendmsg([message.encode()], ancillary)


def barrier():
    reader, writer = os.pipe2(os.O_CLOEXEC)
    try:
        notify('BARRIER=1', writer)
        os.close(writer); writer = -1
        if not select.select([reader], [], [], 10)[0] or os.read(reader, 1) != b'':
            raise Rejected('NOTIFY_UNCONFIRMED')
    finally:
        os.close(reader)
        if writer >= 0: os.close(writer)


def trusted_path(path, directory=False):
    info = path.lstat()
    wanted = stat.S_ISDIR if directory else stat.S_ISREG
    if not wanted(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise Rejected('INSTALLATION_REJECTED')
    return info


def verify_bundle():
    for path in (ROOT, ROOT.parent, STATE, STATE.parent): trusted_path(path, True)
    trusted_path(ROOT/'manifest.json')
    manifest = json.loads((ROOT/'manifest.json').read_text())
    if set(manifest) != {'version','candidate','files','host_python_sha256'} or manifest['version'] != 1 or not re.fullmatch('[0-9a-f]{40}', manifest['candidate']):
        raise Rejected('MANIFEST_REJECTED')
    if hashlib.sha256(Path('/usr/bin/python3').read_bytes()).hexdigest() != manifest['host_python_sha256']:
        raise Rejected('HOST_PYTHON_MISMATCH')
    actual = set()
    for path in ROOT.rglob('*'):
        if path.is_dir():
            trusted_path(path, True)
        else:
            trusted_path(path)
            relative = path.relative_to(ROOT).as_posix()
            if relative == 'manifest.json': continue
            actual.add(relative)
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'].get(relative):
                raise Rejected('CONTENT_MISMATCH')
    if actual != set(manifest['files']): raise Rejected('CONTENT_MISMATCH')
    if (ROOT/'source/.git/HEAD').read_text().strip() != manifest['candidate']:
        raise Rejected('CANDIDATE_MISMATCH')
    for unit in (GUARD, WORKER):
        installed = Path('/etc/systemd/system')/unit
        trusted_path(installed)
        if installed.read_bytes() != (ROOT/unit).read_bytes(): raise Rejected('UNIT_MISMATCH')
        if properties(unit, 'DropInPaths', 'FragmentPath') != dict(DropInPaths='', FragmentPath=str(installed)):
            raise Rejected('UNIT_OVERRIDE')
    guard = properties(GUARD, 'FileDescriptorStoreMax', 'FileDescriptorStorePreserve', 'NotifyAccess', 'CollectMode', 'RemainAfterExit')
    if guard != dict(FileDescriptorStoreMax='1', FileDescriptorStorePreserve='yes', NotifyAccess='main', CollectMode='inactive', RemainAfterExit='yes'):
        raise Rejected('GUARD_POLICY')
    if properties(GUARD, 'User', 'Group', 'RootDirectory', 'RootImage') != dict(User='', Group='', RootDirectory='', RootImage=''):
        raise Rejected('GUARD_POLICY')
    expected = dict(DynamicUser='yes', PrivateNetwork='yes', PrivateTmp='yes',
                    PrivateDevices='yes', NoNewPrivileges='yes', CapabilityBoundingSet='',
                    ProtectSystem='strict', ProtectHome='yes', ProtectControlGroups='yes',
                    ProtectKernelTunables='yes', ProtectKernelModules='yes', ProtectKernelLogs='yes',
                    RestrictSUIDSGID='yes', ProtectProc='invisible', KillMode='control-group',
                    SendSIGKILL='yes', MemoryMax='2147483648', TasksMax='64',
                    TimeoutStartUSec='5min 30s', TimeoutStopUSec='30s',
                    NetworkNamespacePath='', JoinsNamespaceOf='', RestrictNamespaces='yes',
                    User='modelfc-suite-worker', Group='modelfc-suite-worker',
                    RootDirectory='', RootImage='', WorkingDirectory=str(ROOT/'source'))
    if properties(WORKER, *expected) != expected: raise Rejected('WORKER_POLICY')
    execution_policy(GUARD, 'guard.py', ['supervise'])
    execution_policy(WORKER, 'worker.py', [])
    return manifest


def api_identity():
    value = properties('modelfc-api.service', 'MainPID', 'InvocationID', 'ExecMainStartTimestampMonotonic')
    if not value['MainPID'].isdigit() or int(value['MainPID']) < 1 or not value['InvocationID']:
        raise Rejected('API_NOT_RUNNING')
    value['selected'] = os.readlink('/srv/modelfc/current')
    value['process_cwd'] = os.readlink('/proc/' + value['MainPID'] + '/cwd')
    return value


def record(path, value):
    # New evidence only; never touches deployment evidence or transaction markers.
    raw = json.dumps(value, sort_keys=True).encode() + b'\n'
    if len(raw) > 16384: raise Rejected('REPORT_LIMIT')
    temporary = path.with_suffix('.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def supervise():
    run_id = uuid.uuid4().hex
    report = dict(version=1, run_id=run_id, candidate=None, status='FAIL', reason='GUARD_ERROR',
                  worker=None, cleanup='UNKNOWN', deployment_lock='NOT_ACQUIRED', api_unchanged=None)
    path = STATE/'results'/f'{run_id}.json'
    lock_fd = None
    worker_started = False
    try:
        manifest = verify_bundle()
        report['candidate'] = manifest['candidate']
        if os.environ.get('LISTEN_FDS', '0') != '0' or properties(GUARD, 'NFileDescriptorStore')['NFileDescriptorStore'] != '0':
            raise Rejected('RECOVERY_REQUIRED')
        if not quiescent(WORKER): raise Rejected('WORKER_BUSY')
        deployment_uid = pwd.getpwnam('modelfc-deploy').pw_uid
        parent = DEPLOY_LOCK.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != deployment_uid or parent.st_mode & 0o077:
            raise Rejected('DEPLOY_LOCK_REJECTED')
        lock_fd = os.open(DEPLOY_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != deployment_uid or info.st_mode & 0o022:
            raise Rejected('DEPLOY_LOCK_REJECTED')
        if DEPLOY_LOCK.lstat().st_ino != info.st_ino or DEPLOY_LOCK.lstat().st_dev != info.st_dev:
            raise Rejected('DEPLOY_LOCK_REJECTED')
        try: fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Rejected('DEPLOYMENT_BUSY') from None
        deployment_quiescent()
        # PID1 retains the same open-file description even if this process dies.
        notify('FDSTORE=1\nFDPOLL=0\nFDNAME=deployment-interlock', lock_fd)
        barrier()
        if properties(GUARD, 'NFileDescriptorStore')['NFileDescriptorStore'] != '1':
            raise Rejected('LOCK_STORE_REJECTED')
        report['deployment_lock'] = 'RETAINED'
        before = api_identity()
        report['api_before'] = before
        record(path, report)
        (STATE/'worker.env').write_text('MODELFC_SUITE_RUN_ID=' + run_id + '\n')
        notify('READY=1')
        worker_started = True
        command(['/usr/bin/systemctl', 'start', '--wait', WORKER], timeout=375, check=False)
        lifecycle = properties(WORKER, 'InvocationID', 'Result', 'ExecMainCode', 'ExecMainStatus')
        report['worker_lifecycle'] = lifecycle
        if not re.fullmatch('[0-9a-f]{32}', lifecycle['InvocationID']):
            raise Rejected('WORKER_IDENTITY_UNKNOWN')
        stop_worker()
        report['cleanup'] = 'EMPTY_CGROUP'
        output = command(['/usr/bin/journalctl', '--no-pager', '-o', 'cat', '-n', '1',
                          'SYSLOG_IDENTIFIER=modelfc-suite-worker', '_SYSTEMD_UNIT=' + WORKER,
                          '_SYSTEMD_INVOCATION_ID=' + lifecycle['InvocationID']])
        if len(output) > 16384: raise Rejected('REPORT_LIMIT')
        result = json.loads(output)
        required = {'run_id','classification','complete','terminal','observed_completed','last_progress','slowest','issues','stderr_truncated','elapsed_seconds'}
        if type(result) is not dict or set(result) != required: raise Rejected('WORKER_REPORT_REJECTED')
        if result['run_id'] != run_id: raise Rejected('WORKER_IDENTITY_UNKNOWN')
        report['worker'] = result
        report['api_after'] = api_identity()
        report['api_unchanged'] = report['api_after'] == before
        if not report['api_unchanged']: raise Rejected('API_IDENTITY_CHANGED')
        if result['classification'] != 'PASS' or result['complete'] is not True:
            raise Rejected('SUITE_NOT_PASS')
        if lifecycle['Result'] != 'success' or lifecycle['ExecMainCode'] != '1' or lifecycle['ExecMainStatus'] != '0':
            raise Rejected('WORKER_LIFECYCLE_FAILED')
        if not quiescent(WORKER): raise Rejected('CLEANUP_UNKNOWN')
        report.update(status='PASS', reason='OK')
        record(path, report)
        notify('FDSTOREREMOVE=1\nFDNAME=deployment-interlock')
        barrier()
        if properties(GUARD, 'NFileDescriptorStore')['NFileDescriptorStore'] != '0':
            raise Rejected('LOCK_RELEASE_UNKNOWN')
        report['deployment_lock'] = 'RELEASED'
        record(path, report)
        return 0
    except Exception as error:
        report.update(status='FAIL', reason=str(error) if isinstance(error, Rejected) else 'GUARD_ERROR')
        try:
            if worker_started: stop_worker()
            report['cleanup'] = 'EMPTY_CGROUP' if quiescent(WORKER) else 'UNKNOWN'
        except Exception:
            report['cleanup'] = 'UNKNOWN'
        try: record(path, report)
        except Exception: pass
        # Failure deliberately retains PID1's descriptor; only explicit recovery releases it.
        return 1
    finally:
        if lock_fd is not None: os.close(lock_fd)  # Never LOCK_UN: PID1 shares this description.


def recover():
    # Explicit test-only recovery. Never calls candidate code or deployment controller.
    command(['/usr/bin/systemctl', 'stop', GUARD], timeout=65, check=False)
    stop_worker()
    if not quiescent(GUARD) or not quiescent(WORKER): raise Rejected('CLEANUP_UNKNOWN')
    command(['/usr/bin/systemctl', 'reset-failed', GUARD, WORKER], check=False)
    command(['/usr/bin/systemctl', 'clean', '--what=fdstore', GUARD])
    if properties(GUARD, 'NFileDescriptorStore')['NFileDescriptorStore'] != '0':
        raise Rejected('LOCK_RELEASE_UNKNOWN')
    command(['/usr/bin/systemctl', 'reset-failed', GUARD, WORKER], check=False)
    return dict(status='RECOVERED', cleanup='EMPTY_CGROUP', evidence='RETAINED')


def main():
    if os.geteuid() != 0: raise Rejected('ROOT_OPERATOR_REQUIRED')
    if sys.argv[1:] == ['supervise']: return supervise()
    if sys.argv[1:] == ['recover']:
        print(json.dumps(recover())); return 0
    if len(sys.argv) == 3 and sys.argv[1] == 'run':
        manifest = verify_bundle()
        if sys.argv[2] != manifest['candidate']: raise Rejected('CANDIDATE_MISMATCH')
        fd = os.open(STATE/'admission.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd) as admission:
            try: fcntl.flock(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Rejected('BUSY') from None
            if properties(GUARD, 'ActiveState')['ActiveState'] != 'inactive' or not quiescent(WORKER):
                raise Rejected('RECOVERY_REQUIRED')
            command(['/usr/bin/systemctl', 'start', GUARD], timeout=35, check=False)
            until = time.monotonic() + 450
            while time.monotonic() < until:
                if properties(GUARD, 'MainPID')['MainPID'] == '0': break
                time.sleep(1)
            else: raise Rejected('OUTCOME_UNKNOWN')
            print(json.dumps(dict(status='FINISHED', evidence_directory=str(STATE/'results'))))
        return 0
    raise Rejected('INVALID_REQUEST')


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps(dict(status='BLOCKED', reason=str(error) if isinstance(error, Rejected) else 'GUARD_ERROR')))
        raise SystemExit(1)
