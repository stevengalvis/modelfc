"""Disposable GitHub VM acceptance only; real systemd/proc/sockets, fixture ASGI."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

ROOT = Path('/srv/modelfc')
CONTROL = Path('/opt/modelfc-deploy')
UNIT = Path('/etc/systemd/system/modelfc-corner-api.service')
RULE = Path('/etc/sudoers.d/modelfc-readiness-ci')
LAUNCH = Path('/usr/local/bin/modelfc-readiness-ci-launch.py')
STATE = Path('/run/modelfc-readiness-ci.json')
USERS = ('modelfc-deploy', 'modelfc-api')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'HOME': '/nonexistent'}
HELPER = '2c462f7b33bc605a9e45a2497d31522a4d332272f776e7d8ac51145d4dd1c5d0'
BASE = '5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5'
CANDIDATE = '2a82ca7502724c207d4d4bd6af25f03f72ba687c868667366e6dee23f26b2db8'
FIXED = ['/usr/bin/python3', '-I', '-B', str(CONTROL/'api_readiness.py')]
ASGI = '''import json
async def app(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            event = await receive()
            if event['type'] == 'lifespan.startup':
                await send({'type':'lifespan.startup.complete'})
            elif event['type'] == 'lifespan.shutdown':
                await send({'type':'lifespan.shutdown.complete'}); return
    elif scope['type'] == 'http':
        body=json.dumps({'api_version':'v1','models':[],'competitions':[]}).encode()
        await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
        await send({'type':'http.response.body','body':body})
'''
LAUNCHER = '''import os
from pathlib import Path
root=Path.cwd()
python=str(root/'.venv/bin/python')
args=[python,'-B','-P','-s','-m','uvicorn','modelfc.corner_api:app','--host','127.0.0.1','--port','8000','--workers','1','--limit-concurrency','16','--timeout-keep-alive','5']
os.execve(python,args,{'PATH':'/usr/bin:/bin','LANG':'C','HOME':'/nonexistent'})
'''
SERVICE = '''[Unit]
Description=Disposable readiness acceptance fixture
[Service]
Type=simple
User=modelfc-api
Group=modelfc-api
WorkingDirectory=/srv/modelfc/current
ExecStart=/usr/bin/python3 -I -B /usr/local/bin/modelfc-readiness-ci-launch.py
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectProc=default
ProcSubset=all
CapabilityBoundingSet=
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_UNIX
IPAddressDeny=any
IPAddressAllow=localhost
UMask=0077
TasksMax=32
MemoryMax=256M
CPUQuota=100%
RuntimeMaxSec=4min
TimeoutStopSec=5s
KillMode=control-group
Restart=no
StandardOutput=null
StandardError=null
'''


def run(args, *, data=None, timeout=20, check=True, env=None):
    return subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=check, cwd='/', env=env or ENV)


def require(condition):
    if not condition: raise ValueError('acceptance rejected')


def write(path, data, mode):
    with path.open('xb') as stream: stream.write(data)
    path.chmod(mode)


def known(path, expected):
    raw=path.read_bytes(); require(hashlib.sha256(raw).hexdigest()==expected); return raw


def cleanup():
    if not STATE.exists(): return
    require(not STATE.is_symlink() and STATE.stat().st_uid==0)
    state=json.loads(STATE.read_text()); require(state=={'schema':1,'fixture':'modelfc-readiness-ci'})
    # Only a marker written after all fixed paths/accounts were proven absent owns these.
    run(['/usr/bin/systemctl','stop',UNIT.name],check=False)
    for path in (UNIT, RULE, LAUNCH):
        if path.exists() or path.is_symlink(): path.unlink()
    run(['/usr/bin/systemctl','daemon-reload'])
    run(['/usr/bin/systemctl','reset-failed',UNIT.name],check=False)
    for user in reversed(USERS):
        try: pwd.getpwnam(user)
        except KeyError: continue
        run(['/usr/sbin/userdel',user])
        run(['/usr/sbin/groupdel',user],check=False)
    for path in (ROOT,CONTROL):
        if path.is_symlink(): raise ValueError('unexpected symlink')
        if path.exists(): shutil.rmtree(path)
    STATE.unlink()


def probe(sha, *, data=None, command=None, timeout=20):
    nonce=secrets.token_hex(32)
    request=json.dumps({'sha':sha,'nonce':nonce}).encode() if data is None else data
    if command is None:
        command=['/usr/sbin/runuser','-u','modelfc-deploy','--','/usr/bin/python3','-I','-B',str(CONTROL/'deploy_main.py')]
    result=run(command,data=request,timeout=timeout,check=False,
               env=dict(ENV,SSH_ORIGINAL_COMMAND='api-readiness stevengalvis/modelfc'))
    require(len(result.stdout)<=4096)
    value=json.loads(result.stdout) if result.stdout else None
    if value and value.get('status')=='PASS':
        require(result.returncode==0 and value['requested_sha']==sha and value['serving_sha']==sha
                and value['nonce']==nonce and value['helper_sha256']==HELPER
                and value['identity_stable'] is True and value['capabilities_http_200'] is True)
    return result,value


def diagnose(sha):
    """Failure-only read-only checks from the exact installed helper; no mocks."""
    result={}
    path=CONTROL/'api_readiness.py'
    known(path,HELPER)
    spec=importlib.util.spec_from_file_location('installed_readiness_diagnostic',path)
    helper=importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    safe={'wrong process','wrong entrypoint','invalid service boundary','not running',
          'listener not owned by api','wrong current','invalid protected path',
          'wrong marker','wrong detached HEAD','api not ready','helper mode','not installed helper'}
    for label,call in (('installation',helper.installation_revision),
                       ('release',lambda:helper.release(sha)),
                       ('process',lambda:helper.process_identity(helper.release(sha))),
                       ('http',helper.read_ready)):
        try: call(); result[label]='PASS'
        except Exception as error:
            result[label]=str(error) if str(error) in safe else type(error).__name__
    raw=run(['/usr/bin/systemctl','show',UNIT.name,'--no-pager','-p','ExecMainStatus','-p','ActiveState'],check=False).stdout.decode()
    for line in raw.splitlines():
        key,value=line.split('=',1)
        if key=='ExecMainStatus' and value.isdecimal(): result[key]=int(value)
        if key=='ActiveState' and value in {'active','inactive','failed','activating','deactivating'}: result[key]=value
    return result


def wait_ready(sha):
    until=time.monotonic()+20
    while time.monotonic()<until:
        result,value=probe(sha)
        if result.returncode==0 and value and value.get('status')=='PASS': return value
        time.sleep(.2)
    raise TimeoutError('service readiness')


def release(sha, deps):
    target=ROOT/'releases'/(sha+'-'+'a'*12)
    target.mkdir(); (target/'.git').mkdir()
    write(target/'.git/HEAD',(sha+'\n').encode(),0o644)
    write(target/'.git/modelfc-deployed-sha',sha.encode(),0o444)
    # Reuse already installed hash-locked runtime; no root network/package command.
    shutil.copytree(deps,target/'.venv',symlinks=True)
    site=next((target/'.venv/lib').glob('python*/site-packages'))
    package=site/'modelfc'; package.mkdir()
    write(package/'__init__.py',b'',0o644)
    write(package/'corner_api.py',ASGI.encode(),0o644)
    owner=pwd.getpwnam('modelfc-deploy').pw_uid
    for path in (target,target/'.git',target/'.git/HEAD',target/'.git/modelfc-deployed-sha'):
        os.chown(path,owner,owner)
    return target


def accept(source, deps, sha, checks):
    require(os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true')
    require(Path('/proc/1/comm').read_text().strip()=='systemd')
    require(Path('/run/systemd/system').is_dir())
    run(['/usr/bin/systemctl','show','--property=Version','--value'])
    require(Path('/sys/fs/cgroup/cgroup.controllers').is_file())
    checks.append('real_systemd_preflight')
    for path in (ROOT,CONTROL,UNIT,RULE,LAUNCH,STATE): require(not path.exists() and not path.is_symlink())
    for user in USERS:
        try: pwd.getpwnam(user)
        except KeyError: continue
        raise ValueError('fixture account already exists')
    write(STATE,b'{"schema":1,"fixture":"modelfc-readiness-ci"}',0o600)
    for user in USERS:
        run(['/usr/sbin/useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin',user])
    ROOT.mkdir(mode=0o755); (ROOT/'releases').mkdir(mode=0o755); CONTROL.mkdir(mode=0o755)
    owner=pwd.getpwnam('modelfc-deploy').pw_uid
    os.chown(ROOT,owner,owner); os.chown(ROOT/'releases',owner,owner)
    write(CONTROL/'api_readiness.py',known(source/'ops/vps/api_readiness.py',HELPER),0o555)
    write(CONTROL/'deploy_main.py',known(source/'tests/fixtures/vps/deploy_main_5c1b.py.txt',BASE),0o555)
    run(['/usr/bin/patch','--batch','--fuzz=0',str(CONTROL/'deploy_main.py')],data=(source/'ops/vps/patches/deploy-main-5c1b-readiness.patch').read_bytes())
    known(CONTROL/'deploy_main.py',CANDIDATE); (CONTROL/'deploy_main.py').chmod(0o555)
    denied=run(['/usr/sbin/runuser','-u','modelfc-deploy','--','/usr/bin/sudo','-n','--']+FIXED,data=b'{}',check=False)
    require(denied.returncode!=0); checks.append('sudo_denied_without_grant')
    write(RULE,('modelfc-deploy ALL=(root) NOPASSWD: NOSETENV: '+' '.join(FIXED)+'\n').encode(),0o440)
    # Hosted image includes may use safe 0644 rather than visudo's default 0440.
    # Validate complete syntax without changing image policy; enforce protected
    # ownership/non-writability and exact default metadata on our own new rule.
    run(['/usr/sbin/visudo','-c','-O','-P','-f',str(RULE)])
    checks.append('sudo_fragment_valid')
    for policy_path in [Path('/etc/sudoers'),*Path('/etc/sudoers.d').iterdir()]:
        if policy_path.parent.name=='sudoers.d' and ('.' in policy_path.name or policy_path.name.endswith('~')): continue
        metadata=policy_path.lstat()
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid==0 and not metadata.st_mode & 0o022)
    run(['/usr/sbin/visudo','-c','-f','/etc/sudoers'])
    checks.append('sudo_complete_policy_valid')
    for label,args in (('extra_argv',FIXED+['extra']),('other_python',['/usr/bin/python3','-c','pass']),('service_control',['/usr/bin/systemctl','restart',UNIT.name])):
        require(run(['/usr/sbin/runuser','-u','modelfc-deploy','--','/usr/bin/sudo','-n','-l','--']+args,check=False).returncode!=0)
        checks.append('sudo_denied_'+label)
    checks.append('sudo_exact_command_only')
    first=release(sha,deps); (ROOT/'current').symlink_to(first)
    checks.append('fixture_release_prepared')
    write(LAUNCH,LAUNCHER.encode(),0o555); write(UNIT,SERVICE.encode(),0o644)
    run(['/usr/bin/systemctl','daemon-reload']); run(['/usr/bin/systemctl','start',UNIT.name])
    first_proof=wait_ready(sha)
    require(first_proof['pid']>1 and first_proof['start_ticks']>0)
    checks.append('real_service_proc_socket_ready')
    for data in (b'{}',b'x'*257,b'{"sha":"a","sha":"b"}',json.dumps({'sha':sha,'nonce':'bad'}).encode()):
        result,value=probe(sha,data=data); require(result.returncode!=0 and value['status']=='FAIL')
    checks.append('malformed_input_rejected')
    # Real incomplete stdin: no EOF is sent until the fixed helper deadline returns.
    process=subprocess.Popen(['/usr/sbin/runuser','-u','modelfc-deploy','--','/usr/bin/sudo','-n','--']+FIXED,
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV)
    try:
        process.wait(timeout=5)
        value=json.loads(process.stdout.read(4097)); require(process.returncode!=0 and value['status']=='FAIL')
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close(); process.stderr.close()
    checks.append('real_stdin_deadline')
    wrong='f'*40 if sha!='f'*40 else 'e'*40
    result,value=probe(wrong); require(result.returncode!=0 and value['status']=='FAIL')
    checks.append('wrong_selected_sha_rejected')
    second=release(wrong,deps)
    (ROOT/'current').unlink(); (ROOT/'current').symlink_to(second)
    result,value=probe(wrong); require(result.returncode!=0 and value['status']=='FAIL')
    checks.append('pointer_without_process_adoption_rejected')
    run(['/usr/bin/systemctl','restart',UNIT.name]); second_proof=wait_ready(wrong)
    require(second_proof['invocation_id']!=first_proof['invocation_id']
            and (second_proof['pid'],second_proof['start_ticks'])!=(first_proof['pid'],first_proof['start_ticks']))
    checks.append('real_rotation_changes_process_generation')
    run(['/usr/bin/systemctl','stop',UNIT.name])
    result,value=probe(wrong); require(result.returncode!=0 and value['status']=='FAIL')
    checks.append('stopped_service_rejected')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cleanup',action='store_true')
    parser.add_argument('--source',type=Path)
    parser.add_argument('--deps',type=Path)
    parser.add_argument('--sha')
    args=parser.parse_args()
    require(os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true')
    if args.cleanup:
        cleanup(); print('{"cleanup":"PASS"}'); return 0
    require(args.source and args.deps and re.fullmatch('[0-9a-f]{40}',args.sha or ''))
    checks=[]; status='FAIL'; stage='acceptance'; cleaned=False; failure=None; diagnostic=None
    try:
        accept(args.source.resolve(),args.deps.resolve(),args.sha,checks)
        status='PASS'
    except (OSError,ValueError,KeyError,StopIteration,subprocess.SubprocessError,TimeoutError) as error:
        failure={'type':type(error).__name__,'errno':getattr(error,'errno',None)}
        if 'fixture_release_prepared' in checks:
            try: diagnostic=diagnose(args.sha)
            except Exception: diagnostic={'status':'UNAVAILABLE'}
    finally:
        signal.alarm(0)
        try: cleanup(); cleaned=True
        except (OSError,ValueError,KeyError,subprocess.SubprocessError): status='FAIL'; stage='cleanup'
    print(json.dumps({'schema':1,'sha':args.sha,'status':status,'stage':stage,
                      'checks':checks,'cleanup':cleaned,'failure':failure,'diagnostic':diagnostic,'fixture':'real-systemd-uvicorn-synthetic-asgi'},sort_keys=True,separators=(',',':')))
    return 0 if status=='PASS' and cleaned else 1


if __name__=='__main__':
    def stopped(*_): raise TimeoutError('bounded acceptance')
    signal.signal(signal.SIGALRM,stopped); signal.signal(signal.SIGTERM,stopped)
    signal.alarm(240)
    raise SystemExit(main())
