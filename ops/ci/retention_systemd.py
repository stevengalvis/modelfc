"""Retention acceptance on a fresh disposable hosted VM, never on a VPS."""
import argparse
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[2]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

budget = load('retention_fixture_helpers', SOURCE / 'ops/ci/test_progress_systemd.py')
ENV = budget.ENV
require = budget.require
guard = budget.guard
UNIT, API = budget.UNIT, budget.API
ROOTS = budget.ROOTS
PARENT = Path('/root/dev')
UNITS = {unit: Path('/etc/systemd/system') / unit for unit in (UNIT, API)}
DROPINS = [Path(base) / (unit + '.d') for base in ('/etc/systemd/system', '/run/systemd/system',
           '/usr/lib/systemd/system', '/lib/systemd/system') for unit in UNITS]
MARKER = Path('/run/modelfc-retention-ci.json')
CGROUPS = Path('/sys/fs/cgroup')
CANDIDATE = '9d3e94dac40fae1285f13fe522efe38bce559554d58356078c2ce874cf3a3be5'
INPUT = '26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c'
UNIT_HASH = '4833e637436a6fdd7bdc1c7b895c29c602082b7d99740a65da7931728c5e97fa'
API_BYTES = b'[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nRuntimeMaxSec=30min\nKillMode=control-group\nTimeoutStopSec=30\n'
MODES = ('normal', 'longstderr', 'malformed', 'flood', 'timeout')


def command(args, *, timeout=30, **kwargs):
    return subprocess.run(args, env=ENV, capture_output=True, text=True, check=True,
                          timeout=timeout, **kwargs).stdout.strip()


def invocation():
    run = os.environ.get('GITHUB_RUN_ID', '')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
    require(re.fullmatch(r'[1-9][0-9]{0,19}', run) and re.fullmatch(r'[1-9][0-9]{0,5}', attempt), 'INVOCATION_REQUIRED')
    return [run, attempt]


def exists(path):
    return os.path.lexists(path)


def identity(path):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode), 'RESOURCE_SYMLINK')
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def state(unit):
    keys = ('LoadState', 'ActiveState', 'MainPID', 'ControlPID', 'ControlGroup', 'FragmentPath', 'DropInPaths')
    rows = command(['systemctl', 'show', unit, '--all', *[x for k in keys for x in ('-p', k)]])
    values = dict(line.split('=', 1) for line in rows.splitlines())
    require(set(values) == set(keys), 'UNIT_PROPERTIES')
    return values


def save(ledger):
    require(identity(MARKER) == ledger['marker'], 'MARKER_CHANGED')
    # Root-owned 0600 marker, bounded fixed resource names; never test writable.
    MARKER.write_text(json.dumps(ledger, sort_keys=True) + '\n')


def account_absent():
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup('modelfc-deploy')
        except KeyError:
            continue
        return False
    return True


def claim():
    guard()
    binding = invocation()
    require(not exists(MARKER), 'MARKER_COLLISION')
    require(all(not exists(p) for p in [*ROOTS, *UNITS.values(), *DROPINS]), 'PATH_COLLISION')
    require(not PARENT.is_symlink(), 'PARENT_SYMLINK')
    require(account_absent(), 'ACCOUNT_COLLISION')
    for unit in UNITS:
        fields = state(unit)
        require(fields['LoadState'] == 'not-found' and fields['ActiveState'] == 'inactive'
                and fields['FragmentPath'] == '' and fields['ControlGroup'] == ''
                and fields['DropInPaths'] == '', 'UNIT_COLLISION')
    with MARKER.open('x') as stream:
        os.chmod(MARKER, 0o600)
        ledger = dict(schema=1, invocation=binding, marker=identity(MARKER), paths={}, account=None, groups={},
                      parent_preexisting=exists(PARENT))
        stream.write(json.dumps(ledger) + '\n')
    return ledger


def own(ledger, path):
    ledger['paths'][str(path)] = identity(path)
    save(ledger)


def observe(ledger, unit):
    fields = state(unit)
    require(fields['DropInPaths'] == '' and fields['FragmentPath'] == str(UNITS[unit]), 'UNIT_OVERRIDE')
    group = fields['ControlGroup']
    if group:
        require(group == '/system.slice/' + unit, 'CGROUP_IDENTITY')
        if ledger['groups'].get(unit) != group:
            ledger['groups'][unit] = group
            save(ledger)
    return fields


def empty_group(group):
    directory = CGROUPS / group.lstrip('/')
    if not directory.exists():
        return True
    if directory.is_symlink():
        return False
    events = dict(line.split() for line in (directory / 'cgroup.events').read_text().splitlines())
    return events.get('populated') == '0' and not (directory / 'cgroup.procs').read_text().strip()


def quiet(ledger, unit):
    fields = observe(ledger, unit)
    require(fields['ActiveState'] in ('inactive', 'failed') and fields['MainPID'] == '0'
            and fields['ControlPID'] == '0', 'UNIT_NOT_QUIET')
    group = ledger['groups'].get(unit)
    require(group is None or empty_group(group), 'CGROUP_NOT_EMPTY')


def cleanup():
    guard()
    if not exists(MARKER):
        return
    info = MARKER.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1 and info.st_size <= 8192, 'MARKER_BOUNDARY')
    ledger = json.loads(MARKER.read_text())
    allowed = {str(p) for p in [PARENT, *ROOTS, *UNITS.values()]}
    require(set(ledger) == {'schema', 'invocation', 'marker', 'paths', 'account', 'groups', 'parent_preexisting'}
            and ledger['schema'] == 1 and type(ledger['parent_preexisting']) is bool
            and ledger['marker'] == identity(MARKER)
            and set(ledger['paths']) <= allowed and set(ledger['groups']) <= set(UNITS)
            and all(group == '/system.slice/' + unit for unit, group in ledger['groups'].items()), 'LEDGER_INVALID')
    require(ledger['invocation'] == invocation(), 'FOREIGN_MARKER')
    managed = [*ROOTS, *UNITS.values()] + ([] if ledger['parent_preexisting'] else [PARENT])
    require(all(not exists(path) or str(path) in ledger['paths'] for path in managed), 'UNRECORDED_RESOURCE')
    if ledger['account'] is None:
        require(account_absent(), 'UNRECORDED_ACCOUNT')
    # Validate every owned resource before stopping or deleting anything.
    for name, expected in ledger['paths'].items():
        require(identity(Path(name)) == expected, 'RESOURCE_CHANGED')
    owned_units = [unit for unit, path in UNITS.items() if str(path) in ledger['paths']]
    for unit in owned_units:
        observe(ledger, unit)  # Capture both cgroups BEFORE stop clears properties.
    if owned_units:
        command(['systemctl', 'stop', *owned_units], timeout=60)  # Nonzero exit blocks deletion.
        for unit in owned_units:
            quiet(ledger, unit)
    if ledger['account'] is not None:
        user, group = pwd.getpwnam('modelfc-deploy'), grp.getgrnam('modelfc-deploy')
        require([user.pw_uid, group.gr_gid] == ledger['account'] and user.pw_gid == group.gr_gid,
                'ACCOUNT_CHANGED')
    else:
        require(account_absent(), 'UNRECORDED_ACCOUNT')
    if owned_units:
        command(['systemctl', 'reset-failed', *owned_units])
    for unit in owned_units:
        UNITS[unit].unlink()
        del ledger['paths'][str(UNITS[unit])]
        save(ledger)
    if owned_units:
        command(['systemctl', 'daemon-reload'])
        for unit in owned_units:
            fields = state(unit)
            require(fields['ActiveState'] in ('inactive', 'failed') and fields['MainPID'] == '0'
                    and fields['ControlPID'] == '0' and fields['FragmentPath'] == '', 'UNIT_REMAINS')
    for name in sorted(list(ledger['paths']), key=lambda s: len(Path(s).parts), reverse=True):
        path = Path(name)
        require(identity(path) == ledger['paths'][name], 'RESOURCE_CHANGED')
        shutil.rmtree(path)
        require(not exists(path), 'PATH_REMAINS')
        del ledger['paths'][name]
        save(ledger)
    if ledger['account'] is not None:
        command(['userdel', 'modelfc-deploy'])
        try:
            remaining = grp.getgrnam('modelfc-deploy')
        except KeyError:
            pass
        else:
            require(remaining.gr_gid == ledger['account'][1], 'GROUP_CHANGED')
            command(['groupdel', 'modelfc-deploy'])
        require(account_absent(), 'ACCOUNT_REMAINS')
        ledger['account'] = None
        save(ledger)
    require(not ledger['paths'] and account_absent() and all(not exists(path) for path in managed), 'CLEANUP_INCOMPLETE')
    MARKER.unlink()


def prepare(source, candidate, ledger):
    require(hashlib.sha256(candidate.read_bytes()).hexdigest() == CANDIDATE, 'CANDIDATE_HASH')
    unit_bytes = (source / 'deploy' / UNIT).read_bytes()
    require(hashlib.sha256(unit_bytes).hexdigest() == UNIT_HASH, 'UNIT_HASH')
    if not PARENT.exists():
        PARENT.mkdir(); own(ledger, PARENT)
    for path in ROOTS:
        path.mkdir(); own(ledger, path)
    command(['useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'modelfc-deploy'])
    account = pwd.getpwnam('modelfc-deploy')
    ledger['account'] = [account.pw_uid, account.pw_gid]; save(ledger)
    control = Path('/var/lib/modelfc-deploy')
    os.chown(control, account.pw_uid, account.pw_gid); control.chmod(0o700)
    reports = control / 'reports'; reports.mkdir(mode=0o700)
    os.chown(reports, account.pw_uid, account.pw_gid)
    target = Path('/opt/modelfc-deploy/deploy_main.py')
    target.write_bytes(candidate.read_bytes()); target.chmod(0o555)
    for unit, data in ((UNIT, unit_bytes), (API, API_BYTES)):
        with UNITS[unit].open('xb') as stream:
            stream.write(data)
        own(ledger, UNITS[unit])
    command(['systemctl', 'daemon-reload'])
    command(['systemd-analyze', 'verify', *map(str, UNITS.values())])
    expected = dict(PrivateNetwork='yes', PrivateTmp='yes', ProtectSystem='strict', KillMode='control-group',
                    SendSIGKILL='yes', MemoryMax=str(2*1024**3), TasksMax='64', User='modelfc-deploy',
                    Group='modelfc-deploy', NoNewPrivileges='yes', TimeoutStartUSec='10min 30s', TimeoutStopUSec='30s')
    properties = budget.properties()
    require(all(properties.get(k) == v for k, v in expected.items()), 'EFFECTIVE_UNIT_MISMATCH')
    for unit in UNITS:
        observe(ledger, unit)  # Reject manager drop-ins before either fixture starts.
    command(['systemctl', 'start', API]); observe(ledger, API)
    require(API in ledger['groups'], 'API_CGROUP_NOT_OBSERVED')
    return expected


FIXTURE = r'''
import os, socket, time, unittest
from pathlib import Path
MODE = __MODE__
class Retention(unittest.TestCase):
    def test_a_boundary(self):
        self.assertNotEqual(os.geteuid(), 0)
        self.assertEqual(socket.if_nameindex(), [(1, 'lo')])
        for path in ('/var/lib/modelfc', '/root/modelfc-state', '/root/dev/modelfc', '/etc/modelfc-validator'):
            self.assertFalse(os.access(path, os.R_OK))
        self.assertFalse(os.access('/var/lib/modelfc-deploy', os.W_OK))
        self.assertFalse(os.access('/srv/modelfc', os.W_OK))
        self.assertFalse(any(k in os.environ for k in ('GITHUB_TOKEN','ODDSPAPI_API_KEY','SSH_AUTH_SOCK')))
    def test_b_outcome(self):
        time.sleep(1)
        scope = unittest.TextTestRunner.resultclass.startTest.__globals__
        if MODE == 'timeout':
            if os.fork() == 0:
                os.setsid()
                Path('/var/lib/modelfc-deploy/reports/descendant').write_text(str(os.getpid()))
                time.sleep(1200)
                os._exit(0)
            time.sleep(1200)
        if MODE in ('malformed', 'flood'):
            until = time.monotonic() + (3 if MODE == 'flood' else 0)
            while True:
                for fd in (scope['fd'], scope['outcome_fd']):
                    try: os.write(fd, b'SYNTHETIC_SECRET\n' * 128)
                    except BlockingIOError: pass
                if time.monotonic() >= until: break
        if MODE == 'longstderr':
            raise ValueError('SYNTHETIC_SECRET /private/path ' * 10000)
        if MODE != 'normal': self.fail('SYNTHETIC_SECRET')
'''

CHILD = r'''
import importlib.util, json, os, sys
from pathlib import Path
os.umask(0o022)  # Request must be readable by the service UID inside private 0700 control.
spec=importlib.util.spec_from_file_location('controller','/opt/modelfc-deploy/deploy_main.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
try:
    count=m.tests(Path(sys.argv[1]),sys.argv[2])
    value=dict(reason='OK',tests_run=count)
except m.Failure as error:
    value=dict(reason=error.code,tests_run=error.tests_run)
print(json.dumps(value))
'''


def make_release(mode, ledger):
    staging = Path('/srv/modelfc/staging'); staging.mkdir()
    (staging / 'tests').mkdir()
    (staging / 'tests/test_retention.py').write_text(FIXTURE.replace('__MODE__', repr(mode)))
    command(['git', 'init', '-q', str(staging)])
    command(['git', '-C', str(staging), 'add', '.'])
    command(['git', '-C', str(staging), '-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'Synthetic retention fixture'])
    sha = command(['git', '-C', str(staging), 'rev-parse', 'HEAD'])
    release = Path('/srv/modelfc/releases') / (sha + '-0123456789ab')
    release.parent.mkdir(exist_ok=True); staging.rename(release)
    command(['/usr/bin/python3', '-m', 'venv', '--without-pip', str(release / '.venv')])
    for root, dirs, files in os.walk(release):
        os.chown(root, *ledger['account'])
        for name in files:
            os.chown(Path(root) / name, *ledger['account'], follow_symlinks=False)
    return release, sha


def run_case(mode, ledger):
    release, sha = make_release(mode, ledger)
    started = time.monotonic()
    process = subprocess.Popen(['/usr/bin/python3', '-I', '-B', '-c', CHILD, str(release), sha],
                               env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    observed = False
    try:
        while process.poll() is None:
            require(time.monotonic() - started < 690, 'OUTER_DEADLINE')
            observed |= bool(observe(ledger, UNIT)['ControlGroup'])
            time.sleep(0.25)
        elapsed = time.monotonic() - started
        require(process.returncode == 0 and observed, 'CONTROLLER_OR_OBSERVATION_FAILED')
        result = json.loads(process.stdout.read(1025))
        quiet(ledger, UNIT)
        normal = mode == 'normal'
        require(result == dict(reason='OK' if normal else 'TESTS_FAILED', tests_run=0 if mode == 'timeout' else 2), 'CASE_RESULT')
        control = Path('/var/lib/modelfc-deploy')
        require(not exists(control / 'test-request.json') and not exists(control / 'reports/test-result.json'), 'TRANSIENT_REPORT_REMAINS')
        retained = control / 'last-test-failure.json'
        raw = b''
        if normal:
            require(not exists(retained), 'STALE_FAILURE')
        else:
            require(retained.is_file() and not retained.is_symlink(), 'FAILURE_NOT_RETAINED')
            metadata = retained.stat()
            require(metadata.st_uid == 0 and metadata.st_nlink == 1
                    and stat.S_IMODE(metadata.st_mode) == 0o644, 'RETAINED_BOUNDARY')
            require(control.stat().st_uid == ledger['account'][0]
                    and stat.S_IMODE(control.stat().st_mode) == 0o700, 'CONTROL_BOUNDARY')
            raw = retained.read_bytes()
            require(len(raw) <= 4096 and b'SYNTHETIC_SECRET' not in raw and b'/private/path' not in raw, 'REPORT_PRIVACY')
            value = json.loads(raw); info = value['diagnostics']
            require(value['sha'] == sha and value['release_id'] == release.name, 'FAILURE_BINDING')
            require(value['tests_run'] == (None if mode == 'timeout' else 2), 'COUNT_KNOWN')
            require(info['classification'] == ('TIMEOUT' if mode == 'timeout' else 'TEST_FAILURE'), 'CLASSIFICATION')
            if mode == 'longstderr':
                require(info['test_ids'] == ['test_retention.Retention.test_b_outcome'] and info['truncated'], 'ERROR_ID_LOST')
                event = info['outcomes']['events'][0]
                require(info['outcomes']['complete'] and event[:3] == ['ERROR', info['test_ids'][0], 'ValueError']
                        and bool(event[3]), 'SOURCE_FRAME_LOST')
            else:
                require(not info['outcomes']['complete'] and info['outcomes']['truncated']
                        and not info['progress']['complete'], 'LOSS_NOT_EXPLICIT')
        if mode == 'timeout':
            descendant = control / 'reports/descendant'
            require(descendant.is_file() and re.fullmatch(r'[1-9][0-9]{0,8}', descendant.read_text()), 'DESCENDANT_NOT_CREATED')
            require(not Path('/proc', descendant.read_text()).exists(), 'DESCENDANT_SURVIVED')
            require(600 <= elapsed < 630, 'REAL_DEADLINE_MISMATCH')
            descendant.unlink()
        shutil.rmtree(release)  # Synthetic candidate only; actual deploy() is not invoked.
        require(not release.exists() and (normal or retained.read_bytes() == raw), 'PERSISTENCE_LOST')
        require(not exists(Path('/srv/modelfc/current')), 'PROMOTION_OCCURRED')
        return dict(mode=mode, status='PASS', elapsed_seconds=round(elapsed, 3),
                    report_bytes=len(raw), retained_mode=None if normal else '0644',
                    retained_uid=None if normal else 0, cgroup_empty=True, persistence_after_cleanup=not normal)
    finally:
        if process.poll() is None:
            command(['systemctl', 'stop', UNIT], timeout=60); quiet(ledger, UNIT)
            process.terminate(); process.wait(timeout=15)
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args(); guard()
    if args.cleanup:
        cleanup(); return
    require(args.output is not None, 'OUTPUT_REQUIRED')
    output = args.output.resolve()
    require(not any(output == p or p in output.parents for p in ROOTS), 'OUTPUT_BOUNDARY')
    output.mkdir()
    source_sha = command(['git', '-c', 'safe.directory=' + str(args.source.resolve()),
                          '-C', str(args.source.resolve()), 'rev-parse', 'HEAD'])
    require(re.fullmatch('[0-9a-f]{40}', source_sha), 'SOURCE_SHA')
    report = dict(status='FAIL', source_sha=source_sha, invocation=invocation(), input_sha256=INPUT, candidate_sha256=CANDIDATE,
                  unit_sha256=UNIT_HASH, outer_controller_uid=os.geteuid(), cases=[], cleanup='NOT_RUN')
    def interrupted(signum, frame):
        raise RuntimeError('INTERRUPTED')
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    ledger = None
    try:
        builder = load('retention_builder', args.source / 'ops/ci/build_retention.py')
        require(builder.PINS['input_sha256'] == INPUT and builder.PINS['output_sha256'] == CANDIDATE, 'BUILD_PINS')
        candidate = builder.build(args.source.resolve(), output / 'controller.py')
        ledger = claim()
        report['effective_unit'] = prepare(args.source.resolve(), candidate, ledger)
        for mode in MODES:
            report['cases'].append(run_case(mode, ledger))
        report['status'] = 'PASS'
    finally:
        try:
            if ledger is not None:
                cleanup()
            report['cleanup'] = 'PASS' if ledger is not None else 'NO_OWNED_FIXTURE'
        except BaseException:
            report.update(status='FAIL', cleanup='FAIL')
            raise
        finally:
            (output / 'acceptance.json').write_text(json.dumps(report, sort_keys=True) + '\n')
    require(report['status'] == 'PASS')


if __name__ == '__main__':
    main()
