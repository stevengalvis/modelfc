"""Disposable Ubuntu 24.04/systemd 255 acceptance. NEVER run on a VPS."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import pwd
import socket
import subprocess
import tempfile
import time

ROOT=Path('/usr/lib/modelfc-suite')
STATE=Path('/var/lib/modelfc-suite')
CONTROL=Path('/var/lib/modelfc-deploy')
GUARD='modelfc-suite-guard.service'; WORKER='modelfc-suite-worker.service'
UNITS=Path('/etc/systemd/system')
CLAIM=Path('/run/modelfc-suite-ci-claim')
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','HOME':'/root'}


def command(*args,check=True,timeout=480):
    return subprocess.run(args,env=ENV,capture_output=True,text=True,check=check,timeout=timeout)


def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def wait_for(test,seconds=30):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if test(): return
        time.sleep(.05)
    raise AssertionError('bounded condition not reached')


def active(unit):
    return command('systemctl','show',unit,'-p','ActiveState','--value').stdout.strip()


def lock_available():
    with (CONTROL/'deploy.lock').open('rb') as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return False
        return True


def cleanup():
    if not CLAIM.is_file(): return
    if CLAIM.read_text()!='isolated-suite-ci-v1\n': raise AssertionError('claim mismatch')
    command('systemctl','stop',GUARD,WORKER,'modelfc-api.service','modelfc-postmerge-tests.service',check=False)
    command('systemctl','clean','--what=fdstore',GUARD,check=False)
    for unit in (GUARD,WORKER,'modelfc-api.service','modelfc-postmerge-tests.service'):
        (UNITS/unit).unlink(missing_ok=True)
    command('systemctl','daemon-reload')
    command('systemctl','reset-failed',GUARD,WORKER,'modelfc-api.service','modelfc-postmerge-tests.service',check=False)
    for path in (ROOT,STATE,CONTROL,Path('/srv/modelfc'),Path('/var/lib/modelfc'),Path('/etc/modelfc-validator')):
        if path.exists(): shutil.rmtree(path)
    command('userdel','modelfc-deploy',check=False)
    CLAIM.unlink()


def prepare_host():
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true': raise AssertionError('disposable GitHub VM only')
    if command('systemctl','--version').stdout.split()[1]!='255': raise AssertionError('requires actual systemd 255')
    paths=[ROOT,STATE,CONTROL,Path('/srv/modelfc'),Path('/var/lib/modelfc'),Path('/etc/modelfc-validator'),CLAIM]
    paths += [UNITS/u for u in (GUARD,WORKER,'modelfc-api.service')]
    if any(p.exists() or p.is_symlink() for p in paths): raise AssertionError('preexisting fixture paths')
    try: pwd.getpwnam('modelfc-deploy')
    except KeyError: pass
    else: raise AssertionError('preexisting deployment account')
    CLAIM.write_text('isolated-suite-ci-v1\n');CLAIM.chmod(0o600)
    command('useradd','--system','--no-create-home','--shell','/usr/sbin/nologin','modelfc-deploy')
    account=pwd.getpwnam('modelfc-deploy')
    CONTROL.mkdir(mode=0o700)
    os.chown(CONTROL,account.pw_uid,account.pw_gid)
    (CONTROL/'deploy.lock').touch(mode=0o600)
    os.chown(CONTROL/'deploy.lock',account.pw_uid,account.pw_gid)
    (CONTROL/'last-test-failure.json').write_text('preserved failure evidence\n')
    for parent in ('/var/lib/modelfc','/etc/modelfc-validator'):
        path=Path(parent);path.mkdir(mode=0o755)
        (path/'canary').write_text('HOST_SECRET_CANARY\n');(path/'canary').chmod(0o644)
    release=Path('/srv/modelfc/releases')/('d'*40+'-fixture')
    release.mkdir(parents=True)
    Path('/srv/modelfc/current').symlink_to(release)
    (UNITS/'modelfc-api.service').write_text('[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nWorkingDirectory=/srv/modelfc/current\n')
    command('systemctl','daemon-reload')
    command('systemctl','start','modelfc-api.service')


def case_source(directory,mode,source,port):
    directory.mkdir()
    command('git','init','--quiet',str(directory))
    (directory/'requirements-deploy.lock').write_bytes((source/'requirements-deploy.lock').read_bytes())
    (directory/'tests').mkdir()
    cases='''import os, socket, subprocess, time, unittest
class Boundary(unittest.TestCase):
 def test_a_boundary(self):
  self.assertNotEqual(os.geteuid(),0)
  self.assertEqual(socket.if_nameindex(),[(1,'lo')])
  self.assertFalse(os.path.exists('/run/modelfc-suite-ci-claim'))
  with socket.socket(socket.AF_UNIX) as client:
   with self.assertRaises(OSError): client.connect('/run/dbus/system_bus_socket')
  for path in ['/var/lib/modelfc/canary','/etc/modelfc-validator/canary','/var/lib/modelfc-deploy/last-test-failure.json','/srv/modelfc/current','/var/lib/modelfc-suite/results']:
   with self.assertRaises(OSError): os.open(path,os.O_RDONLY)
  with self.assertRaises(OSError): os.open(__file__,os.O_WRONLY)
  with self.assertRaises(OSError): socket.create_connection(('127.0.0.1',PORT),timeout=.2)
'''.replace('PORT',str(port))
    if mode=='failure': cases+=' def test_b_failure(self): self.fail("DO_NOT_PUBLISH_RAW_CANARY")\n'
    if mode in ('timeout','kill','stop','concurrent'):
        cases+=''' def test_b_descendant(self):
  subprocess.Popen(['/usr/bin/python3','-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(600)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  os.closerange(3,256)
  time.sleep(600)
'''
    (directory/'tests/test_boundary.py').write_text(cases)
    command('git','-C',str(directory),'add','.')
    command('git','-C',str(directory),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm',mode)
    return command('git','-C',str(directory),'rev-parse','HEAD').stdout.strip()


def run(args):
    source=Path(args.source).resolve()
    builder=load(source/'ops/suite/bundle.py');installer=load(source/'ops/suite/install.py')
    guard=load(source/'ops/suite/guard.py')
    results=[]
    prepare_host()
    before=(CONTROL/'last-test-failure.json').read_bytes()
    identity=guard.api_identity()
    with socket.socket() as listener, tempfile.TemporaryDirectory() as temporary:
        listener.bind(('127.0.0.1',0));listener.listen()
        port=listener.getsockname()[1]
        tmp=Path(temporary)
        for mode in ('pass','failure','busy','pending','abandoned','override','kill','stop','concurrent','queued','timeout','full'):
            if ROOT.exists():
                guard.recover()
                for unit in (GUARD,WORKER): (UNITS/unit).unlink()
                command('systemctl','daemon-reload')
                shutil.rmtree(ROOT);shutil.rmtree(STATE)
            if mode=='full':
                bundle=Path(args.bundle);sha=args.candidate
                digest=hashlib.sha256(bundle.read_bytes()).hexdigest()
            else:
                fixture=tmp/mode
                sha=case_source(fixture,mode,source,port)
                bundle=tmp/(mode+'.tar.gz')
                digest=builder.build(fixture,sha,args.runtime,bundle)
            installer.install(bundle,digest,sha)
            wrong=command('/usr/bin/python3','-I',str(ROOT/'guard.py'),'run','0'*40,check=False)
            assert wrong.returncode and not list((STATE/'results').iterdir())
            started=time.monotonic()
            if mode=='busy':
                with (CONTROL/'deploy.lock').open('rb') as held:
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    command('/usr/bin/python3','-I',str(ROOT/'guard.py'),'run',sha)
                    evidence=json.loads(next((STATE/'results').glob('*.json')).read_text())
                    assert evidence['reason']=='DEPLOYMENT_BUSY' and evidence['worker'] is None
            elif mode in ('pending','abandoned','override'):
                pending=CONTROL/'service-pending.json'
                abandoned=UNITS/'modelfc-postmerge-tests.service'
                if mode=='pending': pending.write_text('{"unfinished":true}\n')
                if mode=='abandoned':
                    abandoned.write_text('[Service]\nExecStart=/usr/bin/sleep infinity\n')
                    command('systemctl','daemon-reload');command('systemctl','start',abandoned.name)
                if mode=='override': command('systemctl','set-property','--runtime',WORKER,'PrivateNetwork=no')
                result=command('/usr/bin/python3','-I',str(ROOT/'guard.py'),'run',sha,check=False)
                if mode=='override':
                    assert result.returncode and guard.quiescent(WORKER)
                    command('systemctl','revert',WORKER)
                else:
                    evidence=json.loads(next((STATE/'results').glob('*.json')).read_text())
                    assert evidence['reason']=='DEPLOYMENT_RECOVERY_REQUIRED' and evidence['worker'] is None,evidence
                assert lock_available()
                if mode=='pending':
                    assert pending.read_text()=='{"unfinished":true}\n';pending.unlink()
                if mode=='abandoned':
                    assert active(abandoned.name)=='active'
                    command('systemctl','stop',abandoned.name);abandoned.unlink();command('systemctl','daemon-reload')
            elif mode=='queued':
                command('systemctl','start','--no-block',WORKER,check=False)
                wait_for(lambda:active(GUARD)=='failed')
                wait_for(lambda:guard.quiescent(WORKER))
                assert lock_available()
            elif mode in ('kill','stop','concurrent'):
                command('systemctl','start',GUARD)
                wait_for(lambda:active(WORKER)=='activating')
                wait_for(lambda:int(guard.properties(GUARD,'NFileDescriptorStore')['NFileDescriptorStore'])==1)
                time.sleep(1)
                assert not lock_available()
                if mode=='concurrent':
                    other=command('/usr/bin/python3','-I',str(ROOT/'guard.py'),'run',sha,check=False)
                    assert other.returncode and active(WORKER)=='activating'
                if mode=='stop': command('systemctl','stop','--no-block',GUARD)
                else: command('systemctl','kill','--kill-whom=main','--signal=KILL',GUARD)
                until=time.monotonic()+65
                while not guard.quiescent(WORKER):
                    assert not lock_available(),'deployment interlock released while candidate cgroup survives'
                    assert time.monotonic()<until
                    time.sleep(.01)
                assert not lock_available(),'failed guard must retain PID1 descriptor'
                guard.recover()
                assert lock_available()
            else:
                command('/usr/bin/python3','-I',str(ROOT/'guard.py'),'run',sha)
                evidence=json.loads(next((STATE/'results').glob('*.json')).read_text())
                assert evidence['candidate']==sha and evidence['cleanup']=='EMPTY_CGROUP',evidence
                assert evidence['api_unchanged'] is True,evidence
                if mode in ('pass','full'):
                    assert evidence['status']=='PASS' and lock_available(),evidence
                    assert evidence['worker']['terminal']['tests']>0
                else:
                    assert evidence['status']=='FAIL' and not lock_available(),evidence
                    if mode=='timeout':
                        assert evidence['worker']['classification']=='TIMEOUT',evidence
                        assert evidence['worker']['terminal'] is None and evidence['worker']['observed_completed']>=1
                        assert 300<=evidence['worker']['elapsed_seconds']<310
                    else: assert evidence['worker']['terminal']['failures']==1
                    preserved=next((STATE/'results').glob('*.json')).read_bytes()
                    guard.recover()
                    assert next((STATE/'results').glob('*.json')).read_bytes()==preserved
                    assert lock_available()
            assert (CONTROL/'last-test-failure.json').read_bytes()==before
            assert guard.api_identity()==identity
            reports=[json.loads(p.read_text()) for p in sorted((STATE/'results').glob('*.json'))]
            results.append(dict(case=mode,status='PASS',elapsed_seconds=round(time.monotonic()-started,3),reports=reports))
            Path(args.output).write_text(json.dumps(dict(status='IN_PROGRESS',cases=results))+'\n')
            print(json.dumps(dict(case=mode,status='PASS',elapsed_seconds=results[-1]['elapsed_seconds'])),flush=True)
    return dict(status='PASS',cases=results,candidate=args.candidate,source_sha=args.source_sha)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source');parser.add_argument('--runtime');parser.add_argument('--bundle')
    parser.add_argument('--candidate');parser.add_argument('--source-sha');parser.add_argument('--output')
    parser.add_argument('--cleanup',action='store_true')
    args=parser.parse_args()
    if args.cleanup: cleanup();raise SystemExit(0)
    result=dict(status='FAIL',reason='ACCEPTANCE_ERROR')
    try:
        result=run(args)
    finally:
        output=Path(args.output)
        if result['status']=='FAIL':
            if output.exists(): result['completed_cases']=json.loads(output.read_text()).get('cases',[])
            if (STATE/'results').exists(): result['last_reports']=[json.loads(p.read_text()) for p in (STATE/'results').glob('*.json')]
        diagnostic=command('journalctl','--no-pager','-u',GUARD,'-u',WORKER,'-n','40',check=False).stdout
        output.with_name('systemd-diagnostic.log').write_text(diagnostic)
        if result['status']=='FAIL':
            print(command('journalctl','--no-pager','-o','cat','-u',WORKER,'_PID=1','-n','12',check=False).stdout,flush=True)
        try: cleanup();result['cleanup']='PASS'
        except Exception: result['cleanup']='FAIL'
        output=Path(args.output);output.write_text(json.dumps(result,sort_keys=True)+'\n');output.chmod(0o644)
