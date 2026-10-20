"""New-artifact admission acceptance on a disposable GitHub VM, never a host installer.

The natural case takes about 30 minutes: no clock, properties, or controller
functions are mocked. The API reaches RuntimeMaxSec=30min and systemd restarts it.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHILD = r'''
import fcntl, hashlib, importlib.util, json, os, subprocess, sys, time
from pathlib import Path
spec=importlib.util.spec_from_file_location('controller','/opt/modelfc-deploy/deploy_main.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert os.geteuid()!=0
mode,sha,expected=sys.argv[1:]
stage='identity'
observed_reason='NONE'
def failed(kind,error,tb):
    print(json.dumps(dict(stage=stage,reason=error.code if isinstance(error,m.Failure) else 'ASSERTION',observed_reason=observed_reason)))
sys.excepthook=failed
assert hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()==expected
release=m.RELEASES/(sha+'-0123456789ab')
files=[m.REQUEST,m.TEST_OUTPUT,m.CONTROL/'last-test-failure.json']
def unchanged():
    assert all(p.read_bytes()==b'preserved admission evidence\n' for p in files)
with (m.CONTROL/'deploy.lock').open('a+') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for path in files:path.write_bytes(b'preserved admission evidence\n')
    selected=m.current_release()
    checks=[]
    def validate():
        global stage
        stage='boundary'
        m.boundary()
        stage='source'
        m.verify_source(release,sha)
        stage='venv'
        m.verify_venv_size(release/'.venv')
        stage='selected_release'
        assert m.current_release()==selected
        # Disposable local Git remote: never contact a provider or production.
        stage='target'
        assert m.latest_main(str(m.ROOT/'origin.git'))==sha
        checks.append(time.monotonic_ns())
    validate()
    if mode in ('missing','inactive','wrong_limit'):
        stage='rejection'
        try:m.tests(release,sha,revalidate=validate)
        except m.Failure as error:
            observed_reason=error.code
            assert error.code=='ADMISSION_PROPERTIES_INVALID'
        else:raise AssertionError('rejection admitted')
        unchanged()
        result=dict(mode=mode,status='PASS',reason='ADMISSION_PROPERTIES_INVALID')
    elif mode=='fresh':
        m.test_admission_window(revalidate=validate)
        unchanged()
        assert len(checks)==1
        result=dict(mode=mode,status='PASS')
    else:
        assert mode=='natural'
        original,age=m.admission_snapshot()
        assert age < 60_000_000_000
        # Wait for real age to cross the cutoff, without changing any API state.
        origin=time.monotonic()
        while age <= m.ADMISSION_MAX_AGE_NS:
            assert time.monotonic()-origin < 1050
            time.sleep(min(5,(m.ADMISSION_MAX_AGE_NS-age)/1e9+0.01))
            identity,age=m.admission_snapshot()
            assert identity==original
        validate()
        started=time.monotonic()
        m.test_admission_window(revalidate=validate)
        elapsed=time.monotonic()-started
        final,final_age=m.admission_snapshot()
        assert 700 < elapsed < 900
        assert final[0]!=original[0] and final[2]>original[2]
        assert final_age < 30_000_000_000 and len(checks)==3
        unchanged()
        result=dict(mode=mode,status='PASS',wait_seconds=round(elapsed,3),
                    old_age_seconds=age/1e9,new_age_seconds=final_age/1e9,
                    invocation_changed=True,revalidation_count=len(checks))
    assert m.current_release()==selected
    print(json.dumps(result))
'''


def write_candidate_source(staging):
    (staging/'.gitignore').write_text('.venv/\n')
    (staging/'fixture.txt').write_text('synthetic admission candidate\n')


def prepare_candidate(budget):
    account = pwd.getpwnam('modelfc-deploy')
    root = Path('/srv/modelfc')
    releases = root / 'releases'
    releases.mkdir()
    control = Path('/var/lib/modelfc-deploy')
    for path in (root, releases, control, control/'reports'):
        os.chown(path, account.pw_uid, account.pw_gid)
    (control/'reports').chmod(0o700)
    for path in ('/var/lib/modelfc', '/root/modelfc-state', '/root/dev/modelfc'):
        Path(path).chmod(0o700)
    staging = root/'staging'
    staging.mkdir()
    write_candidate_source(staging)
    budget.command(['git','init','-q','--initial-branch=main',str(staging)])
    budget.command(['git','-C',str(staging),'add','.'])
    budget.command(['git','-C',str(staging),'-c','user.name=CI','-c','user.email=ci@example.invalid','commit','-qm','Admission fixture'])
    sha = budget.command(['git','-C',str(staging),'rev-parse','HEAD'])
    budget.command(['git','clone','--bare',str(staging),str(root/'origin.git')])
    release = releases/(sha+'-0123456789ab')
    staging.rename(release)
    budget.command(['/usr/bin/python3','-m','venv','--without-pip',str(release/'.venv')])
    for directory, dirs, files in os.walk(root):
        os.chown(directory, account.pw_uid, account.pw_gid)
        for name in files:
            os.chown(Path(directory)/name, account.pw_uid, account.pw_gid, follow_symlinks=False)
    return sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve()
    budget = load('budget_acceptance',source/'ops/ci/test_progress_systemd.py')
    budget.guard()
    if args.cleanup:
        budget.cleanup()
        return
    budget.require(args.output is not None)
    output = args.output.resolve()
    output.mkdir()
    manifest_path=source/'ops/vps/patches/deploy-main-08f7-admission.json'
    manifest=json.loads(manifest_path.read_text())
    report=dict(status='FAIL',identities=manifest,clock='real_monotonic',
                rotation='systemd_RuntimeMaxSec_30min_Restart_always',cases=[],
                systemd=budget.command(['systemctl','--version']).splitlines()[0])
    try:
        builder=load('admission_builder',source/'ops/ci/build_admission.py')
        built=builder.build(source,output/'compatibility-controller.py')
        (output/'identities.json').write_bytes(manifest_path.read_bytes())
        (output/'admission.patch').write_bytes((source/'ops/vps/patches/deploy-main-08f7-admission.patch').read_bytes())
        report['test_unit']=budget.prepare(source,built)
        sha=prepare_candidate(budget)
        expected=manifest['output_sha256']
        def invocation():
            return budget.command(['systemctl','show',budget.UNIT,'-p','InvocationID','--value'])
        initial_test=invocation()
        def run(mode,timeout=45):
            result=subprocess.run(['/usr/sbin/runuser','-u','modelfc-deploy','--',
                '/usr/bin/python3','-I','-B','-c',CHILD,mode,sha,expected],
                env=budget.ENV,cwd="/",capture_output=True,text=True,timeout=timeout)
            if result.returncode:
                # Only fixed fixture stages and allowlisted controller reasons.
                detail=json.loads(result.stdout) if len(result.stdout)<=2048 else {}
                stages={'identity','boundary','source','venv','selected_release','target','rejection'}
                reasons={'NONE','ASSERTION','STATE_BOUNDARY_FAILED','SOURCE_INVALID','STORAGE_LIMIT_FAILED',
                         'FETCH_FAILED','INTERNAL_ERROR','ADMISSION_QUERY_FAILED','ADMISSION_PROPERTIES_INVALID'}
                if (set(detail)=={'stage','reason','observed_reason'} and detail['stage'] in stages
                        and detail['reason'] in reasons and detail['observed_reason'] in reasons):
                    report['failure']=dict(case=mode,**detail)
                    print(json.dumps(report['failure']),flush=True)
                raise RuntimeError('ADMISSION_CASE_'+mode.upper())
            budget.require(invocation()==initial_test,'TEST_SERVICE_LAUNCHED')
            report['cases'].append(json.loads(result.stdout))
        run('missing')
        api='[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nRuntimeMaxSec=30min\nRestart=always\nRestartSec=1s\n'
        budget.API_PATH.write_text(api)
        budget.command(['systemctl','daemon-reload'])
        run('inactive')
        budget.API_PATH.write_text(api.replace('30min','29min'))
        budget.command(['systemctl','daemon-reload'])
        budget.command(['systemctl','start',budget.API])
        run('wrong_limit')
        budget.command(['systemctl','stop',budget.API])
        budget.API_PATH.write_text(api)
        budget.command(['systemctl','daemon-reload'])
        (output/'synthetic-api.service').write_text(api)
        budget.command(['systemctl','start',budget.API])
        run('fresh')
        # The only lifecycle action after this start is cleanup after acceptance.
        # No shortened runtime, clock substitution, restart command, or provider.
        run('natural',timeout=1950)
        budget.require(int(budget.command(['systemctl','show',budget.API,'-p','NRestarts','--value']))==1,'ROTATION_COUNT')
        report['status']='PASS'
    finally:
        try:
            budget.cleanup()
            report['cleanup']='PASS'
        except BaseException:
            report.update(status='FAIL',cleanup='FAIL')
            raise
        finally:
            (output/'acceptance.json').write_text(json.dumps(report,sort_keys=True)+'\n')
    budget.require(report['status']=='PASS')


if __name__=='__main__':
    main()
