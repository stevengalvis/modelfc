"""Disposable regression driver; never invokes installed validator/config/cache.

Requires an existing unprivileged Podman-capable identity and a local OCI archive
with the deliberately stale baked harness. No registry calls or account setup.
Example: sudo python3 -B tests/run_rootless_harness_regression.py --user EXISTING \
  --archive STALE_IMAGE.oci --image sha256:... --work NEW_PRIVATE_WORK \
  --trusted-parent ROOT_OWNED_NONWRITABLE_PARENT
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile
import threading
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('user','archive','image','work','trusted-parent'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--dependency-site', type=Path,
        help='Optional existing development site-packages: copy ONLY Pydantic and its dependencies into a disposable test image; never changes the activation image')
    args=parser.parse_args()
    assert os.geteuid()==0, 'root creates ONLY disposable root-owned mount fixtures'
    user=pwd.getpwnam(args.user);assert user.pw_uid!=0
    repo=Path(__file__).resolve().parents[1]
    parent=Path(args.trusted_parent).absolute()
    for p in (parent,*parent.parents):
        s=p.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
    work=Path(args.work).absolute()
    assert len(str(work/'runroot'))<=50, 'Podman requires a runroot path <=50 characters'
    work.mkdir(mode=0o755)  # must not already exist
    fixture=Path(tempfile.mkdtemp(prefix='.zen48-harness-test-',dir=parent));fixture.chmod(0o755)
    harness=fixture/'validate_capture.py';original=(repo/'ops/vps/validate_capture.py').read_bytes()
    harness.write_bytes(original);harness.chmod(0o555)
    for name in ('home','storage','runroot','work','race','tmp'):
        p=work/name;p.mkdir(mode=0o700);os.chown(p,user.pw_uid,user.pw_gid)
    env={'PATH':'/usr/bin:/bin','HOME':str(work/'home'),'USER':args.user,'LANG':'C',
         'XDG_RUNTIME_DIR':f'/run/user/{user.pw_uid}','TMPDIR':str(work/'tmp'),
         'PYTHONPATH':str(repo/'src')}
    engine=['/usr/bin/podman','--root',str(work/'storage'),'--runroot',str(work/'runroot'),
            '--storage-driver=vfs','--events-backend=none','--tmpdir',str(work/'tmp')]
    prefix=['/usr/sbin/runuser','-u',args.user,'--','/usr/bin/env','-i']
    def invoke(command,**kwargs):
        return subprocess.run(prefix+[k+'='+v for k,v in env.items()]+command,cwd=repo,**kwargs)
    stop=threading.Event();error=[]
    def mutate():
        try:
            while not stop.is_set() and not (work/'race/replace').exists():time.sleep(.02)
            if stop.is_set():return
            replacement=fixture/'replacement';replacement.write_text('replacement must never be selected');replacement.chmod(0o555)
            os.replace(replacement,harness);(work/'race/replaced').touch();os.chown(work/'race/replaced',user.pw_uid,user.pw_gid)
            while not stop.is_set() and not (work/'race/restore').exists():time.sleep(.02)
            replacement.write_bytes(original);replacement.chmod(0o555);os.replace(replacement,harness)
            (work/'race/restored').touch();os.chown(work/'race/restored',user.pw_uid,user.pw_gid)
        except BaseException as exc:error.append(type(exc).__name__)
    thread=threading.Thread(target=mutate,daemon=True)
    try:
        with (work/'image-load.log').open('w') as log:
            loaded=invoke(engine+['load','--input',str(Path(args.archive).absolute())],stdout=log,stderr=subprocess.STDOUT,timeout=180)
        assert loaded.returncode==0, 'isolated image load failed; inspect image-load.log'
        if args.dependency_site:
            # This fixture demonstrates mount execution independently of the
            # activation image's missing dependency. It is NOT installed acceptance.
            context=work/'build';context.mkdir(mode=0o755)
            packages=context/'packages';packages.mkdir(mode=0o755)
            for name in ('pydantic','pydantic_core','annotated_types','typing_extensions.py','typing_inspection'):
                source=args.dependency_site/name
                assert source.exists() and not source.is_symlink(), name
                if source.is_dir():shutil.copytree(source,packages/name,ignore=shutil.ignore_patterns('__pycache__'))
                else:shutil.copyfile(source,packages/name)
            # The existing image is CPython 3.12; fail the build/import if incompatible.
            (context/'Containerfile').write_text('FROM '+args.image+'\nCOPY packages /usr/local/lib/python3.12/site-packages/\nRUN python3 -I -c "import pydantic"\n')
            with (work/'image-build.log').open('w') as log:
                built=invoke(engine+['build','--network=none','--pull=never','--tag','localhost/zen48-stale-fixture',str(context)],stdout=log,stderr=subprocess.STDOUT,timeout=180)
            assert built.returncode==0, 'disposable fixture image build failed; inspect image-build.log'
            inspected=invoke(engine+['image','inspect','localhost/zen48-stale-fixture','--format','{{.Id}}'],capture_output=True,text=True,check=True)
            args.image='sha256:'+inspected.stdout.strip().removeprefix('sha256:')
        print(json.dumps({'test_image':args.image,'dependency_fixture':bool(args.dependency_site)}),flush=True)
        env.update(MODELFC_TEST_PODMAN=json.dumps(engine),MODELFC_TEST_IMAGE=args.image,
                   MODELFC_TEST_TRUSTED=str(fixture),MODELFC_TEST_WORK=str(work/'work'),
                   MODELFC_TEST_RACE=str(work/'race'))
        thread.start()
        with (work/'real-container.log').open('w') as log:
            result=invoke(['/usr/bin/python3','-B','-m','unittest',
                'tests.test_validator_harness_mount.RealRootlessHarnessTests','-v'],stdout=log,stderr=subprocess.STDOUT,timeout=360)
        print((work/'real-container.log').read_text())
        assert not error, error
        print(json.dumps({'rootless_uid':user.pw_uid,'isolated_engine':True,
                          'production_mounts':False,'network':'none','result':'PASS' if result.returncode==0 else 'FAIL'}))
        return result.returncode
    finally:
        stop.set()
        if thread.is_alive():thread.join(timeout=3)
        # Only this driver's newly-created root fixture; no installed paths.
        shutil.rmtree(fixture)


if __name__=='__main__':raise SystemExit(main())
