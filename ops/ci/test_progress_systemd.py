"""Exact compatibility-candidate acceptance on disposable GitHub-hosted VMs only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import platform
import shutil
import subprocess
import sys
import time

BASE = '5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5'
CANDIDATE = '882cb40931695df85e2b0c7d72d607b5e266e2207c895a66467e080d49ee528d'
PATCH = 'efaf214ccd6d5ee8e9576de4ba8a4f17d1884062b00addd733c4d3f6040332c3'
UNIT = 'modelfc-postmerge-tests.service'
UNIT_PATH = Path('/etc/systemd/system') / UNIT
MARKER = Path('/run/modelfc-test-progress-ci.json')
ROOTS = [Path(p) for p in ('/opt/modelfc-deploy', '/srv/modelfc', '/var/lib/modelfc-deploy',
                          '/var/lib/modelfc', '/root/modelfc-state', '/root/dev/modelfc',
                          '/etc/modelfc-validator')]
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'}


def require(condition, reason='ACCEPTANCE_FAILED'):
    if not condition:
        raise RuntimeError(reason)


def command(args, **kw):
    return subprocess.run(args, env=ENV, capture_output=True, text=True, timeout=30,
                          check=True, **kw).stdout.strip()


def guard():
    require(os.geteuid() == 0 and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'NOT_DISPOSABLE_GITHUB_VM')
    require(Path('/proc/1/comm').read_text().strip() == 'systemd', 'SYSTEMD_REQUIRED')
    require(Path('/sys/fs/cgroup/cgroup.controllers').is_file(), 'CGROUP_V2_REQUIRED')


def cleanup():
    guard()
    if not MARKER.exists():
        return
    require(not MARKER.is_symlink() and MARKER.stat().st_uid == 0)
    require(json.loads(MARKER.read_text()) == {'schema': 1})
    subprocess.run(['systemctl', 'stop', UNIT], env=ENV, capture_output=True, timeout=45)
    group = command(['systemctl', 'show', UNIT, '-p', 'ControlGroup', '--value']) if UNIT_PATH.exists() else ''
    if group:
        procs = Path('/sys/fs/cgroup') / group.lstrip('/') / 'cgroup.procs'
        require(not procs.exists() or not procs.read_text().strip(), 'CLEANUP_NOT_EMPTY')
    UNIT_PATH.unlink(missing_ok=True)
    command(['systemctl', 'daemon-reload'])
    subprocess.run(['systemctl', 'reset-failed', UNIT], env=ENV, capture_output=True, timeout=30)
    for path in ROOTS:
        require(not path.is_symlink())
        if path.exists():
            shutil.rmtree(path, ignore_errors=False)
    try:
        pwd.getpwnam('modelfc-deploy')
    except KeyError:
        pass
    else:
        command(['userdel', 'modelfc-deploy'])
    MARKER.unlink()


def build(source, output):
    baseline = source / 'tests/fixtures/vps/deploy_main_5c1b.py.txt'
    patch = source / 'ops/vps/patches/deploy-main-5c1b-test-progress.patch'
    require(hashlib.sha256(baseline.read_bytes()).hexdigest() == BASE, 'BASE_HASH')
    require(hashlib.sha256(patch.read_bytes()).hexdigest() == PATCH, 'PATCH_HASH')
    target = output / 'ops/vps/deploy_main.py'
    target.parent.mkdir(parents=True)
    target.write_bytes(baseline.read_bytes())
    command(['git', 'apply', '--check', str(patch)], cwd=output)
    command(['git', 'apply', str(patch)], cwd=output)
    require(hashlib.sha256(target.read_bytes()).hexdigest() == CANDIDATE, 'CANDIDATE_HASH')
    return target


def properties():
    keys = ['PrivateNetwork','PrivateTmp','ProtectSystem','KillMode','SendSIGKILL',
            'MemoryMax','TasksMax','User','Group','NoNewPrivileges','TimeoutStartUSec',
            'TimeoutStopUSec','MainPID','ControlGroup']
    return dict(line.split('=',1) for line in command(['systemctl','show',UNIT,
                *[item for key in keys for item in ('-p',key)]]).splitlines())


def prepare(source, built):
    require(not MARKER.exists() and not UNIT_PATH.exists())
    require(all(not p.exists() and not p.is_symlink() for p in ROOTS), 'FIXTURE_PATH_COLLISION')
    try:
        pwd.getpwnam('modelfc-deploy')
    except KeyError:
        pass
    else:
        raise RuntimeError('FIXTURE_USER_COLLISION')
    # Fixed disposable ownership marker; no cleanup of pre-existing paths.
    MARKER.write_text('{"schema": 1}')
    for p in ROOTS:
        p.mkdir(parents=True, mode=0o755)
    command(['useradd','--system','--no-create-home','--shell','/usr/sbin/nologin','modelfc-deploy'])
    reports = Path('/var/lib/modelfc-deploy/reports'); reports.mkdir()
    account = pwd.getpwnam('modelfc-deploy')
    os.chown(reports, account.pw_uid, account.pw_gid)
    target = Path('/opt/modelfc-deploy/deploy_main.py')
    target.write_bytes(built.read_bytes()); target.chmod(0o555)
    # Original unit plus stronger home hiding on CI; never weaken its boundary.
    unit_path = source/'deploy/modelfc-postmerge-tests.service'
    require(hashlib.sha256(unit_path.read_bytes()).hexdigest() ==
            'e93dbf72da38fff2c5e541b9b54871a65a358e70d53b99c139661cb5015f7c92', 'UNIT_HASH')
    unit = unit_path.read_text()
    unit = unit.replace('[Install]', 'ProtectHome=yes\n\n[Install]')
    UNIT_PATH.write_text(unit)
    command(['systemctl','daemon-reload'])
    command(['systemd-analyze','verify',str(UNIT_PATH)])
    expected = dict(PrivateNetwork='yes',PrivateTmp='yes',ProtectSystem='strict',
                    KillMode='control-group',SendSIGKILL='yes',MemoryMax=str(2*1024**3),
                    TasksMax='64',User='modelfc-deploy',Group='modelfc-deploy',
                    NoNewPrivileges='yes',TimeoutStartUSec='5min 30s',TimeoutStopUSec='30s')
    actual = properties()
    require(all(actual.get(k)==v for k,v in expected.items()), 'EFFECTIVE_UNIT_MISMATCH')
    return actual


FIXTURE = r'''
import os, socket, time, unittest
from pathlib import Path
MODE = __MODE__
class Timing(unittest.TestCase):
    def test_a_boundary(self):
        self.assertNotEqual(os.geteuid(), 0)
        self.assertEqual(socket.if_nameindex(), [(1, 'lo')])
        for path in ('/var/lib/modelfc','/root/modelfc-state','/root/dev/modelfc','/etc/modelfc-validator','/home/runner'):
            self.assertFalse(os.access(path, os.R_OK))
        self.assertFalse(os.access('/var/lib/modelfc-deploy', os.W_OK))
        self.assertFalse(os.access('/srv/modelfc', os.W_OK))
        self.assertFalse(any(key in os.environ for key in ('GITHUB_TOKEN','ODDSPAPI_API_KEY','SSH_AUTH_SOCK')))
    def test_b_observation(self):
        time.sleep(1)  # Keep the unit observable even for the normal case.
        fd = unittest.TextTestRunner.resultclass.startTest.__globals__['fd']
        if MODE == 'timeout':
            pid = os.fork()
            if pid == 0:
                os.setsid()
                Path('/var/lib/modelfc-deploy/reports/descendant').write_text(str(os.getpid()))
                time.sleep(600)
                os._exit(0)
            time.sleep(600)
        if MODE == 'malformed':
            os.write(fd, b'[' * 1024 + b'\n')
        if MODE == 'flood':
            until = time.monotonic() + 3
            while time.monotonic() < until:
                try: os.write(fd, b'SYNTHETIC_SECRET\n' * 128)
                except BlockingIOError: pass
        if MODE != 'normal':
            self.fail('SYNTHETIC_SECRET')
'''


def run_case(mode):
    control=Path('/var/lib/modelfc-deploy'); reports=control/'reports'
    for p in reports.iterdir(): p.unlink()
    staging=Path('/srv/modelfc/staging'); staging.mkdir()
    (staging/'tests').mkdir()
    (staging/'tests/test_timing.py').write_text(FIXTURE.replace('__MODE__',repr(mode)))
    command(['git','init','-q',str(staging)])
    command(['git','-C',str(staging),'add','.'])
    command(['git','-C',str(staging),'-c','user.name=CI','-c','user.email=ci@example.invalid','commit','-qm','Synthetic fixture'])
    sha=command(['git','-C',str(staging),'rev-parse','HEAD'])
    release=Path('/srv/modelfc/releases')/(sha+'-0123456789ab'); release.parent.mkdir(exist_ok=True)
    staging.rename(release)
    command(['/usr/bin/python3','-m','venv','--without-pip',str(release/'.venv')])
    account=pwd.getpwnam('modelfc-deploy')
    for root,dirs,files in os.walk(release):
        os.chown(root,account.pw_uid,account.pw_gid)
        for name in files: os.chown(Path(root)/name,account.pw_uid,account.pw_gid,follow_symlinks=False)
    ns=os.stat('/proc/self/ns/net')
    (control/'test-request.json').write_text(json.dumps(dict(sha=sha,release_id=release.name,controller_netns=[ns.st_dev,ns.st_ino])))
    subprocess.run(['systemctl','reset-failed',UNIT],env=ENV,capture_output=True,timeout=30)
    started=time.monotonic()
    process=subprocess.Popen(['systemctl','start','--wait',UNIT],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    group=''
    try:
        while process.poll() is None:
            require(time.monotonic()-started < 365, 'OUTER_DEADLINE')
            current=properties()
            group=current['ControlGroup'] or group
            time.sleep(0.25)
        elapsed=time.monotonic()-started
        require(bool(group), 'NO_CGROUP_OBSERVED')
        procs=Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs'
        require(not procs.exists() or not procs.read_text().strip(),'DESCENDANTS_SURVIVED')
        descendant=reports/'descendant'
        if mode=='timeout':
            require(descendant.exists(),'DESCENDANT_NOT_CREATED')
            require(not Path('/proc',descendant.read_text()).exists(),'DESCENDANT_SURVIVED')
            require(300 <= elapsed < 330,'DEADLINE_MISMATCH')
        raw=(reports/'test-result.json').read_bytes()
        require(len(raw)<=4096 and b'SYNTHETIC_SECRET' not in raw,'REPORT_BOUNDS_OR_LEAK')
        result=json.loads(raw)
        require(result['state_boundary_enforced'] is True and result['sha']==sha)
        require((process.returncode==0)==(mode=='normal'))
        require(result['tests_status']==('PASS' if mode=='normal' else 'FAIL'))
        if mode=='normal':
            require(result['tests_run']==2 and 'diagnostics' not in result)
        else:
            info=result['diagnostics']; progress=info['progress']
            require(progress['complete'] is False and progress['observed_completed']==1)
            require(info['classification']==('TIMEOUT' if mode=='timeout' else 'TEST_FAILURE'))
            require(info['count_known']==(mode!='timeout'))
            require(result['tests_run']==(0 if mode=='timeout' else 2))
        require(not Path('/srv/modelfc/current').exists(),'PROMOTION_OCCURRED')
        return dict(mode=mode,status='PASS',elapsed_seconds=round(elapsed,3),report_bytes=len(raw),cgroup_empty=True)
    finally:
        if process.poll() is None:
            subprocess.run(['systemctl','stop',UNIT],env=ENV,capture_output=True,timeout=45)
            process.kill();process.wait()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--cleanup',action='store_true')
    args=parser.parse_args(); guard()
    if args.cleanup: cleanup(); return
    require(args.source is not None and args.output is not None)
    output=args.output.resolve(); output.mkdir()
    report=dict(status='FAIL',candidate_sha256=CANDIDATE,baseline_sha256=BASE,patch_sha256=PATCH,
                python=sys.version.split()[0],systemd=command(['systemctl','--version']).splitlines()[0],
                kernel=platform.release(),cases=[])
    try:
        candidate=build(args.source.resolve(),output/'build')
        (output/'baseline.py').write_bytes((args.source/'tests/fixtures/vps/deploy_main_5c1b.py.txt').read_bytes())
        (output/'diagnostic-only.patch').write_bytes((args.source/'ops/vps/patches/deploy-main-5c1b-test-progress.patch').read_bytes())
        report['effective_unit']=prepare(args.source.resolve(),candidate)
        for mode in ('normal','malformed','flood','timeout'):
            report['cases'].append(run_case(mode))
        report['status']='PASS'
    finally:
        try:
            cleanup(); report['cleanup']='PASS'
        except BaseException:
            report.update(status='FAIL',cleanup='FAIL')
            raise
        finally:
            (output/'acceptance.json').write_text(json.dumps(report,sort_keys=True)+'\n')
    require(report['status']=='PASS')


if __name__=='__main__': main()
