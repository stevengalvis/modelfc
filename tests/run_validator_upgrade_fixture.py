"""Actual root-owned filesystem/lock and private rootless-image transaction fixture.

Never targets installed validator/config/cache. Existing identity only; no access
or service changes. GitHub main is an explicit fixture adapter, not a live call.
"""
import argparse
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import tempfile
from unittest.mock import patch

SHA='1f51047af65bba30e1684973a65e1e0154af1fab'


class Interrupted(BaseException):
    pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('installer','source','old-source','store-work','trusted-parent','user'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    assert os.geteuid()==0, 'root creates ONLY disposable root-owned fixture files'
    user=pwd.getpwnam(args.user);assert user.pw_uid!=0
    store=Path(args.store_work).absolute()
    assert store.parent==Path('/tmp') and store.name.startswith('zen48-')
    engine=json.loads((store/'engine.json').read_bytes())
    assert engine==['/usr/bin/podman','--root',str(store/'storage'),'--runroot',str(store/'runroot'),
        '--storage-driver=vfs','--events-backend=none','--tmpdir',str(store/'tmp')]
    prefix=['/usr/sbin/runuser','-u',args.user,'--','/usr/bin/env','-i','PATH=/usr/bin:/bin',
        'HOME='+str(store/'home'),'USER='+args.user,'XDG_RUNTIME_DIR=/run/user/'+str(user.pw_uid)]
    spec=importlib.util.spec_from_file_location('isolated_upgrade',args.installer)
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    source=Path(args.source);old_source=Path(args.old_source)
    parent=Path(args.trusted_parent).absolute()
    for path in (parent,*parent.parents):
        s=path.lstat();assert path.is_dir() and not path.is_symlink() and s.st_uid==0 and not s.st_mode&0o022
    results=[]

    def inspect(image):
        result=subprocess.run(prefix+engine+['image','inspect',image,'--format','{{.Id}} {{.Os}} {{.Architecture}}'],capture_output=True,text=True,check=True,timeout=30)
        assert result.stdout.strip().removeprefix('sha256:')==image.removeprefix('sha256:')+' linux amd64'

    def idle():
        result=subprocess.run(prefix+engine+['ps','--filter','label=io.modelfc.validation=phase1','--format','{{.Names}}'],capture_output=True,text=True,check=True,timeout=30)
        assert not result.stdout.strip(), 'fixture store has an active validation container'

    class Fixture:
        def __enter__(self):
            self.root=Path(tempfile.mkdtemp(prefix='.zen48-upgrade-test-',dir=parent));self.root.chmod(0o755)
            self.dest=self.root/'installed';self.dest.mkdir()
            self.stage=self.root/'stage';self.stage.mkdir()
            self.runs=self.root/'runs';self.runs.mkdir(mode=0o700);os.chown(self.runs,user.pw_uid,user.pw_gid)
            lock=self.runs/'validation.lock';lock.touch(mode=0o600);os.chown(lock,user.pw_uid,user.pw_gid)
            for name in installer.FILES:
                (self.dest/name).write_bytes((old_source/name).read_bytes());(self.dest/name).chmod(0o555)
                (self.stage/name).write_bytes((source/name).read_bytes());(self.stage/name).chmod(0o644)
            (self.stage/'STAGED_SHA').write_text(SHA)
            self.config=self.root/'config.json'
            self.old_config=(json.dumps(dict(repository='stevengalvis/modelfc',image=installer.PREVIOUS_IMAGE,
                github_token_file='/fixture/not-a-credential',provider_key_file='/fixture/not-mounted',
                history_directory='/fixture/not-used'))+'\n').encode()
            self.config.write_bytes(self.old_config);self.config.chmod(0o640);os.chown(self.config,0,user.pw_gid)
            self.config_meta=(self.config.stat().st_uid,self.config.stat().st_gid,self.config.stat().st_mode&0o777)
            # Opaque bounded predecessor receipt bytes are preserved exactly;
            # schema/stale/binding policy is separately exercised by real worker tests.
            self.old_cache=b'{"fixture":"old exact bounded receipt"}\n'
            self.cache=self.runs/'release-evidence.json';self.set_cache(self.old_cache)
            self.main=SHA
            self.stack=ExitStack()
            for name,value in dict(DEST=self.dest,SOURCE=self.stage,RUNS=self.runs,CONFIG=self.config,
                    verify_image=inspect,idle=idle,main_unchanged=lambda sha:sha==self.main).items():
                self.stack.enter_context(patch.object(installer,name,value))
            return self
        def set_cache(self,raw):
            self.cache.write_bytes(raw);self.cache.chmod(0o600);os.chown(self.cache,user.pw_uid,user.pw_gid)
        def install(self):return installer.install(SHA,SHA)
        def restore(self,backup):
            with installer.locked():installer.restore_locked(backup)
        def check_old(self,cache=True):
            installer.verify_files(self.dest,installed=True,hashes=installer.PREVIOUS_FILES)
            assert self.config.read_bytes()==self.old_config
            assert (self.config.stat().st_uid,self.config.stat().st_gid,self.config.stat().st_mode&0o777)==self.config_meta
            if cache:
                assert self.cache.read_bytes()==self.old_cache
                assert self.cache.stat().st_uid==user.pw_uid and self.cache.stat().st_mode&0o777==0o600
            else:assert not self.cache.exists()
            assert not (self.runs/'validator-transition.json').exists()
            inspect(installer.PREVIOUS_IMAGE)
        def check_new(self):
            installer.verify_files(self.dest,installed=True)
            value=json.loads(self.config.read_bytes());old=json.loads(self.old_config)
            assert value=={**old,'image':installer.EXPECTED_IMAGE}
            assert (self.config.stat().st_uid,self.config.stat().st_gid,self.config.stat().st_mode&0o777)==self.config_meta
            assert not self.cache.exists() and not (self.runs/'validator-transition.json').exists()
            inspect(installer.PREVIOUS_IMAGE);inspect(installer.EXPECTED_IMAGE)
        def backup(self):
            backups=list(self.dest.glob('.zen48-backup-*'));assert len(backups)==1
            return backups[0]
        def __exit__(self,*exc):
            self.stack.close();shutil.rmtree(self.root)

    inspect(installer.PREVIOUS_IMAGE);inspect(installer.EXPECTED_IMAGE)
    with Fixture() as f:
        backup=f.install();f.check_new()
        f.set_cache(b'{"fixture":"new format receipt retained for diagnosis"}')
        f.restore(backup);f.check_old()
        assert list(backup.glob('rollback-cache-*'))
        results.append('install, image-only config transition, exact five/config/cache restore')
    with Fixture() as f:
        f.cache.unlink();backup=f.install();f.check_new();f.restore(backup);f.check_old(cache=False)
        results.append('cache absence restored')
    # Raise after every atomic module/config replacement: no catch/cleanup path
    # may pretend that an interrupted install completed successfully.
    for name in ('validate_pr.py','validate_offline_ssh.py','offline_report.py','release_evidence.py','validate_capture.py','config.json'):
        with Fixture() as f:
            actual=installer.os.replace
            def interrupt(src,dest):
                actual(src,dest)
                if Path(dest).name==name:raise Interrupted()
            try:
                with patch.object(installer.os,'replace',side_effect=interrupt):f.install()
            except Interrupted:pass
            else:raise AssertionError('interruption not reached')
            assert (f.runs/'validator-transition.json').exists()
            f.restore(f.backup());f.check_old()
    results.append('all six module/config interruption boundaries restored')
    with Fixture() as f:
        actual=installer.os.replace
        pid=os.fork()
        if pid==0:
            def die(src,dest):
                actual(src,dest)
                if Path(dest)==f.dest/'validate_pr.py':os.kill(os.getpid(),signal.SIGKILL)
            with patch.object(installer.os,'replace',side_effect=die):f.install()
            os._exit(99)
        _,status=os.waitpid(pid,0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL
        assert (f.runs/'validator-transition.json').exists()
        f.restore(f.backup());f.check_old()
        results.append('actual SIGKILL and lock release recovered')
    with Fixture() as f:
        backup=f.install();f.check_new()
        actual=installer.os.replace
        def interrupted_restore(src,dest):
            actual(src,dest)
            if Path(dest)==f.config:raise Interrupted()
        try:
            with patch.object(installer.os,'replace',side_effect=interrupted_restore):f.restore(backup)
        except Interrupted:pass
        else:raise AssertionError('rollback interruption not reached')
        assert hashlib.sha256((f.dest/'validate_pr.py').read_bytes()).hexdigest()==installer.FILES['validate_pr.py']
        assert (f.runs/'validator-transition.json').exists()
        f.restore(backup);f.check_old()
        results.append('interrupted rollback retains guarded controller and is retryable')
    with Fixture() as f:
        actual=installer.os.replace
        def main_moved(src,dest):
            actual(src,dest)
            if Path(dest)==f.dest/'validate_pr.py':
                f.main='b'*40;raise RuntimeError('fixture main moved')
        try:
            with patch.object(installer.os,'replace',side_effect=main_moved):f.install()
        except installer.InstallFailure as failure:assert failure.outcome=='DEFERRED_DO_NOT_MERGE'
        else:raise AssertionError('advanced main accepted')
        assert (f.runs/'validator-transition.json').exists()
        before={n:(f.dest/n).read_bytes() for n in installer.FILES}
        try:f.restore(f.backup())
        except ValueError:pass
        else:raise AssertionError('host-only rollback behind new main accepted')
        assert before=={n:(f.dest/n).read_bytes() for n in installer.FILES}
        # Reset only fixture main to prove recovery, never a real branch operation.
        f.main=SHA;f.restore(f.backup());f.check_old()
        results.append('advanced/unverified main defers rollback without clobbering host')
    with Fixture() as f:
        backup=f.install();f.check_new()
        (backup/'old-config.json').write_bytes(b'corrupt')
        before={n:(f.dest/n).read_bytes() for n in installer.FILES}
        try:f.restore(backup)
        except ValueError:pass
        else:raise AssertionError('corrupt configuration backup accepted')
        assert before=={n:(f.dest/n).read_bytes() for n in installer.FILES}
        results.append('corrupt configuration backup rejected before any restoration')
    with Fixture() as f:
        with installer.locked():
            try:f.install()
            except BlockingIOError:pass
            else:raise AssertionError('concurrent installer entered')
        f.check_old();assert not list(f.dest.glob('.zen48-backup-*'))
        results.append('actual existing-lock exclusion before backup/mutation')
    with Fixture() as f:
        os.setxattr(f.config,'user.zen48-fixture',b'preserve')
        try:f.install()
        except ValueError:pass
        else:raise AssertionError('extended configuration metadata discarded')
        assert os.getxattr(f.config,'user.zen48-fixture')==b'preserve'
        assert not list(f.dest.glob('.zen48-backup-*'));f.check_old()
        results.append('actual extended configuration metadata rejects before mutation')
    with Fixture() as f:
        actual=Path.unlink
        def cache_interrupted(path,*args,**kwargs):
            actual(path,*args,**kwargs)
            if path==f.cache:raise Interrupted()
        try:
            with patch.object(Path,'unlink',cache_interrupted):f.install()
        except Interrupted:pass
        else:raise AssertionError('cache interruption not reached')
        assert (f.runs/'validator-transition.json').exists()
        f.restore(f.backup());f.check_old()
        results.append('cache-unlink interruption restores prior receipt exactly')
    with Fixture() as f:
        actual=installer.os.link
        pid=os.fork()
        if pid==0:
            def journal_die(src,dest,*args,**kwargs):
                actual(src,dest,*args,**kwargs)
                if Path(dest)==f.runs/'validator-transition.json':os.kill(os.getpid(),signal.SIGKILL)
            with patch.object(installer.os,'link',side_effect=journal_die):f.install()
            os._exit(99)
        _,status=os.waitpid(pid,0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL
        marker=f.runs/'validator-transition.json';assert marker.stat().st_nlink==2
        f.restore(f.backup());f.check_old()
        results.append('actual SIGKILL at journal publication recovers two-link boundary')
    print(json.dumps({'result':'PASS','groups':len(results),'interruption_boundaries':6,
        'actual_process_death':True,'real_root_owned_files_and_fsync':True,
        'private_rootless_images':True,'production_resources':False,'checks':results},indent=2))


if __name__=='__main__':main()
