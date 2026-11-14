"""Explicit root executor for the fixed calc workflow; never activated on import.

Configuration is an independently pinned root-owned artifact, not GitHub input.
No defaults for host identities, tool hashes, source inputs or release baseline.
The root supervisor is NEVER assigned an outer timeout or process-group kill.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import types

from . import protected_inputs as protected
from .dispatch import ExecutionEvidence
from .executor_observer import Observer
from .model_process import ModelConfig, run_model

NATIVE = 'docs/zen53/native-compatibility/'
WORKSPACE = 'docs/zen53/workspace-acceptance/'
REPAIR = 'docs/zen53/repair-loop/'
REQUIRED = ({NATIVE+name for name in ('bridge.py','materialize.py','owner_binding_v4.py','owner_context_v4.py',
    'zlib_amendment.py','proc_bracket.py','proc_bracket_runtime.inc','provisional.py','provisional_runtime.inc',
    'harness.py','harness_runtime.inc','NATIVE_ZLIB_PROVENANCE.json','candidate/supervisor.py')} |
    {WORKSPACE+name for name in ('materializer.py','native_generator.py','workspace_store.py','payload.py','profile.toml','failure_context.py')} |
    {REPAIR+name for name in ('contract.py','coordinator.py','pipeline.py','runtime.py','usage.py')} |
    {'docs/zen53/model-patch/'+name for name in ('integration.py','capture.py','bridge.py')})


def sha(raw):return hashlib.sha256(raw).hexdigest()
def need(ok):protected.need(ok)


class SnapshotLoader:
    """Exact authenticated source bytes, never ambient module names or .pyc."""
    def __init__(self, snapshots):
        self.snapshots=snapshots
        self.owned={}
        self.importlib=types.SimpleNamespace(util=self)

    def spec_from_file_location(self,name,path):
        path=str(path);need(path in self.snapshots and path.endswith('.py'))
        return types.SimpleNamespace(name=name,origin=path,loader=self)

    def module_from_spec(self,spec):
        value=types.ModuleType(spec.name);value.__file__=spec.origin
        return value

    def exec_module(self,value):
        need(value.__file__ in self.snapshots)
        exec(compile(self.snapshots[value.__file__],value.__file__,'exec'),value.__dict__)
        if 'importlib' in value.__dict__:value.__dict__['importlib']=self.importlib

    def load(self,name,path):
        path=str(path)
        if name in sys.modules:
            need(name in self.owned and sys.modules[name] is self.owned[name]
                 and self.owned[name].__file__==path)
            return self.owned[name]
        value=self.module_from_spec(self.spec_from_file_location(name,path))
        sys.modules[name]=value
        try:self.exec_module(value)
        except BaseException:
            sys.modules.pop(name,None)
            raise
        self.owned[name]=value
        return value


def source_modules(root,snapshots):
    loader=SnapshotLoader(snapshots)
    native=Path(root)/NATIVE;workspace=Path(root)/WORKSPACE
    for name in ('owner_context_v4','owner_binding_v4','zlib_amendment','proc_bracket','provisional','harness','bridge'):
        loader.load(name,native/(name+'.py'))
    materialize=loader.load('executor_native_materialize',native/'materialize.py')
    for name in ('workspace_store','native_generator'):
        loader.load(name,workspace/(name+'.py'))
    workspace_module=loader.load('executor_workspace_materializer',workspace/'materializer.py')
    pipeline=loader.load('executor_repair_pipeline',Path(root)/REPAIR/'pipeline.py')
    return sys.modules['bridge'],materialize,workspace_module,pipeline


def configuration(record, expected_revision):
    raw,_=protected.read_pinned(record,262144)
    config=protected.strict_json(raw,262144)
    need(type(config) is dict and set(config)=={'version','revision','release_root','sources','tools','model',
         'historical','objects','owner_evidence','recovery','bundled','stages','reports','production_sha256'})
    need(type(config['version']) is int and config['version']==1 and config['revision']==expected_revision
         and type(expected_revision) is str and re.fullmatch('[0-9a-f]{40}',expected_revision))
    need(config['release_root']==str(Path(__file__).resolve().parents[2]))
    need(type(config['sources']) is dict and set(config['sources'])==REQUIRED)
    for name,entry in config['sources'].items():
        need(entry['path']==config['release_root']+'/'+name)
        protected.read_pinned(entry,4*1024*1024)
    need(type(config['tools']) is dict and set(config['tools'])=={'codex','python','systemctl','busctl'})
    need(protected.hexhash(config['production_sha256']))
    need(type(config['model']) is dict and set(config['model'])=={'uid','gid','home','codex_home','workdirs','cli_version'})
    model=config['model'];need(model['cli_version']=='0.164.0')
    need(all(type(model[k]) is int and 0<model[k]<2**32-1 for k in ('uid','gid')))
    account=pwd.getpwuid(model['uid'])
    need(account.pw_gid==model['gid'] and account.pw_dir==model['home'])
    need(type(model['workdirs']) is list and len(model['workdirs'])==2 and len(set(model['workdirs']))==2)
    need(type(config['stages']) is list and len(config['stages'])==2 and len(set(config['stages']))==2)
    need(all(type(path) is str and re.fullmatch('/tmp/zen53-[a-z0-9-]{12,96}',path) for path in config['stages']))
    need(type(config['reports']) is list and len(config['reports'])==2 and len(set(config['reports']))==2)
    for group in ('historical','objects','bundled'):
        need(type(config[group]) is dict and config[group])
    return config


def read_at(parent,name,maximum,mode,expected=None):
    need(type(name) is str and name and '/' not in name and name not in ('.','..'))
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0 and before.st_nlink==1
             and stat.S_IMODE(before.st_mode)==mode and 0<=before.st_size<=maximum)
        raw=bytearray()
        while len(raw)<=maximum:
            data=os.read(fd,min(65536,maximum+1-len(raw)))
            if not data:break
            raw.extend(data)
        need(len(raw)==before.st_size and protected.identity(before)==protected.identity(os.fstat(fd))
             ==protected.identity(os.stat(name,dir_fd=parent,follow_symlinks=False)))
        need(expected is None or sha(raw)==expected)
        return bytes(raw),protected.identity(before)
    finally:os.close(fd)


def save_at(parent,name,raw):
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
    try:
        offset=0
        while offset<len(raw):
            count=os.write(fd,raw[offset:]);need(count>0);offset+=count
        os.fchmod(fd,0o600);os.fsync(fd)
    finally:os.close(fd)
    os.fsync(parent)


def stage_layout(path,bridge):
    # Exclusive creation in the exact existing sticky-/tmp contract; do not relax
    # the root-only protected input reader to accommodate writable ancestors.
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    tmp=os.open('/tmp',flags);stage=objects=None
    try:
        info=os.fstat(tmp)
        need(info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o1777)
        name=path.rsplit('/',1)[1]
        need(bridge.STAGE_PATTERN.fullmatch(path) and path!=bridge.OLD_STAGE)
        os.mkdir(name,0o700,dir_fd=tmp)
        stage=os.open(name,flags,dir_fd=tmp);os.fchmod(stage,0o700)
        for child,mode in (('objects',0o700),('empty-home',0o555),('empty-vartmp',0o555)):
            os.mkdir(child,mode,dir_fd=stage)
            fd=os.open(child,flags,dir_fd=stage)
            try:os.fchmod(fd,mode);os.fsync(fd)
            finally:os.close(fd)
        objects=os.open('objects',flags,dir_fd=stage)
        bridge.verify_stage_objects_binding(path,objects)
        os.fsync(stage);os.fsync(tmp)
        result=(stage,objects);stage=objects=None
        return result
    finally:
        for fd in (objects,stage,tmp):
            if fd is not None:os.close(fd)


def held_stage_binding(stage,stage_fd,objects_fd,bridge):
    binding=bridge.verify_stage_objects_binding(stage,objects_fd)
    info=os.fstat(stage_fd)
    need(binding[2][:5]==(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid))


def verify_tree(stage_fd,manifest):
    need(type(manifest) is dict and len(manifest)==124)
    for path,digest in manifest.items():
        need(type(path) is str and not path.startswith('/') and all(p not in ('','.','..') for p in path.split('/'))
             and protected.hexhash(digest))
        parent=os.dup(stage_fd)
        try:
            parts=path.split('/')
            for name in parts[:-1]:
                new=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
                old,parent=parent,new;os.close(old)
                info=os.fstat(parent);need(info.st_uid==info.st_gid==0 and not info.st_mode&0o022)
            info=os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)
            need(not info.st_mode&0o022)
            read_at(parent,parts[-1],320*1024*1024,stat.S_IMODE(info.st_mode),digest)
        finally:os.close(parent)


def independent_readback(stage_fd,source,preparation,proposal,result,store_source):
    """Reopen fixed files/canaries using protected published identity, no chown."""
    names={'require','object_id','bounded_read','canonical_record','read_record','exact_keys','valid_nonce','valid_identity'}
    nodes=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names]
    need(len(nodes)==len(names))
    env={'os':os,'stat':stat,'json':json}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<authenticated-provision-reader>','exec'),env)
    dirs=[];store=None
    try:
        parent=stage_fd
        for name in ('rootfs','diagnostic','readiness'):
            fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            dirs.append(fd);parent=fd
            info=os.fstat(fd);need(info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o555)
        provision=env['read_record'](dirs[-1],'provision')
        env['exact_keys'](provision,'version nonce identity parent child')
        need(type(provision['version']) is int and provision['version']==1)
        env['valid_identity'](provision['identity'])
        proof=result['workspace_proof']
        need(provision['nonce']==proof['nonce'] and sha(env['canonical_record'](provision))==proof['provision'])
        module=types.ModuleType('independent_fixed_store')
        exec(compile(store_source,'<authenticated-fixed-store>','exec'),module.__dict__)
        candidate=proposal['replacement'].encode('ascii')
        # The separately validated literal-family candidate is data, not new code.
        need(candidate in tuple(module.WS_BEFORE.replace(b' - ',b' '+op+b' ') for op in (b'+',b'-',b'*')))
        module.WS_AFTER=candidate
        contract=preparation['workspace_contract'];module.ws_contract(contract)
        store=module.WorkspaceStore(stage_fd,dirs[0],(0,0))
        for name in module.WS_DIRS:store._open(name,True)
        for name in module.WS_FILES:store._open(name)
        expected=json.loads(json.dumps(contract))
        identity=provision['identity']
        for name in ('workspace','workspace/calc.py','workspace/test_calc.py'):
            expected['entries'][name]['identity'][3:5]=[identity['uid'],identity['gid']]
        store.contract=contract;store.provisioned_contract=expected
        store.transaction=identity;store.phase='PROVISIONED'
        observed=store.postverify(identity)
        need(observed['workspace_sha256']==result['workspace_readback']['workspace_sha256'])
    finally:
        errors=[]
        if store is not None:
            try:store.close()
            except Exception:errors.append(True)
        for fd in reversed(dirs):
            try:os.close(fd)
            except OSError:errors.append(True)
        need(not errors)


class ConcreteExecutor:
    """Construct only after a separately approved configured execution scope.

    Construction authenticates all inputs before any model invocation. Calls are
    for already durable coordinator reservations; this object never reserves,
    retries, alters an old stage, posts a result, or issues an authorization grant.
    """
    def __init__(self,config_record,expected_revision,authority_check):
        need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0)
        need(callable(authority_check));self.authority_check=authority_check
        self.authority_check()
        self.config=configuration(config_record,expected_revision)
        self.tool_fds={};self.used=set()
        try:
            for name,entry in self.config['tools'].items():
                fd,raw,_=protected.open_pinned(entry,128*1024*1024)
                self.tool_fds[name]=fd
                need(raw.startswith(b'\x7fELF') and entry['mode']&0o111)
            snapshots={entry['path']:protected.read_pinned(entry,4*1024*1024)[0] for entry in self.config['sources'].values()}
            self.bridge,self.materialize,self.workspace,self.pipeline=source_modules(self.config['release_root'],snapshots)
            self.inputs={group:{name:protected.read_pinned(entry)[0] for name,entry in self.config[group].items()}
                         for group in ('historical','objects','bundled')}
            self.evidence=protected.strict_json(protected.read_pinned(self.config['owner_evidence'])[0],2*1024*1024)
            self.recovery=protected.read_pinned(self.config['recovery'])[0]
            need(sha(self.recovery)==self.workspace.RECOVERY_SHA)
            # Byte-map authentication independent of model content. Host-specific
            # dependency pins are additionally checked from the recovery routes.
            need(set(self.inputs['historical'])==set(self.bridge.HISTORICAL_SHA256))
            need(all(sha(raw)==self.bridge.HISTORICAL_SHA256[name] for name,raw in self.inputs['historical'].items()))
            need(all(protected.hexhash(name) and sha(raw)==name for group in ('objects','bundled')
                     for name,raw in self.inputs[group].items()))
            recovery=protected.strict_json(self.recovery,4*1024*1024)
            self.materialize.zlib_amendment.amend_recovery(recovery)
            need(set(self.inputs['bundled'])=={route['sha256'] for route in recovery['manifest'].values() if route['kind']=='bundled'})
            observer_bytes,_=protected.read_pinned(self.config['sources'][NATIVE+'candidate/supervisor.py'],4*1024*1024)
            self.observer=Observer(observer_bytes,sha(observer_bytes),{name:self.tool_fds[name] for name in ('systemctl','busctl')},
                                   self.config['production_sha256'])
            for index in (0,1):
                model=self.config['model']
                from .model_process import validate
                validate(ModelConfig(self.tool_fds['codex'],model['uid'],model['gid'],model['home'],model['codex_home'],model['workdirs'][index]),b'preflight')
                report=protected.open_root_directory(self.config['reports'][index])
                try:need(stat.S_IMODE(os.fstat(report).st_mode)==0o700 and os.listdir(report)==[])
                finally:os.close(report)
                need(not os.path.lexists(self.config['stages'][index]))
        except BaseException:
            self.close();raise

    def close(self):
        failed=False
        while self.tool_fds:
            _,fd=self.tool_fds.popitem()
            try:os.close(fd)
            except OSError:failed=True
        need(not failed)

    def __call__(self,reserved):
        need(type(reserved) is dict and set(reserved)=={'reservation','feedback'})
        reservation=reserved['reservation'];c=self.pipeline.module('contract')
        c.bindings({key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')})
        need(c.digest(reserved['feedback'])==reservation['feedback_sha256'])
        index=reservation['attempt']-1;need(index not in self.used);self.used.add(index)
        stage=self.config['stages'][index];report=protected.open_root_directory(self.config['reports'][index])
        stage_fd=objects_fd=evidence_fd=None
        try:
            need(os.listdir(report)==[])
            save_at(report,'reservation.json',c.canonical(reserved))
            # Reauthenticate source paths before loading preparation helpers again.
            for entry in self.config['sources'].values():protected.read_pinned(entry,4*1024*1024)
            stage_fd,objects_fd=stage_layout(stage,self.bridge)
            baseline=self.observer.admit(stage)
            def pair(name):
                info=os.stat(name,dir_fd=stage_fd,follow_symlinks=False)
                return (info.st_dev,info.st_ino)
            plan=self.bridge.build(self.inputs['historical'],self.inputs['objects'],stage,pair('empty-home'),pair('empty-vartmp'),self.evidence)
            dependencies=protected.strict_json(plan['files']['DEPENDENCIES.json'],262144)['dependencies']
            sink=os.open('/dev/null',os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            try:
                for record in dependencies.values():
                    if record.get('source_observation')!='PENDING_PREPARATION':
                        self.materialize.copy_pinned_source(record,sink)
            finally:os.close(sink)
            prompt=c.canonical({'instruction':'Repair only the synthetic function. Do not use tools, files or commands. Return JSON only with version, loop_id, attempt, request_id, feedback_sha256, base_sha256, path, replacement. Canonical two-line function permits only operator +, - or *. No other edits.',
                'source':c.BEFORE.decode(),'tests':['assert add(2, 3) == 5','assert add(-2, 2) == 0'],
                'expected':{key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')},
                'base_sha256':c.BASE_SHA256,'feedback':reserved['feedback']})
            model=self.config['model']
            self.authority_check()
            capture=run_model(ModelConfig(self.tool_fds['codex'],model['uid'],model['gid'],model['home'],model['codex_home'],model['workdirs'][index]),prompt)
            save_at(report,'stdout.bin',capture.stdout);save_at(report,'stderr.bin',capture.stderr)
            save_at(report,'capture.json',c.canonical({'request_id':reservation['request_id'],'stdout_sha256':sha(capture.stdout),
                'stderr_sha256':sha(capture.stderr),'exit_code':capture.exit_code,'timedout':capture.timedout,'overflow':capture.overflow}))
            need(not capture.overflow and not capture.timedout and capture.exit_code==0)
            stdout,_=read_at(report,'stdout.bin',65536,0o600,sha(capture.stdout))
            prepared=self.pipeline.prepare_capture(stdout,capture.exit_code,capture.timedout,reservation,stage,
                self.inputs['historical'],self.inputs['objects'],self.evidence,self.recovery,self.inputs['bundled'],
                self.bridge,self.materialize,workspace_materializer=self.workspace)
            preparation=prepared['preparation']
            read_at(stage_fd,'ARTIFACT_SHA256.json',262144,0o444,preparation['manifest_sha256'])
            read_at(stage_fd,'MATERIALIZATION_COMPLETE.json',262144,0o444,preparation['completion_sha256'])
            manifest=protected.strict_json(read_at(stage_fd,'ARTIFACT_SHA256.json',262144,0o444)[0],262144)
            source,_=read_at(stage_fd,'supervisor.py',2*1024*1024,0o444,manifest['supervisor.py'])
            held_stage_binding(stage,stage_fd,objects_fd,self.bridge)
            verify_tree(stage_fd,manifest)
            need(self.observer.admit(stage)==baseline)
            save_at(report,'worker-start.json',c.canonical({'stage_id':reservation['stage_id'],'stage':stage,
                'stage_identity':[os.fstat(stage_fd).st_dev,os.fstat(stage_fd).st_ino],
                'manifest_sha256':preparation['manifest_sha256'],'supervisor_sha256':sha(source)}))
            # Root supervisor inherits no OAuth/home, provider token or model env.
            # Deliberately NO timeout/start_new_session/kill/terminate path: a
            # protective hold may remain indefinitely and must not be bypassed.
            self.authority_check()
            child=subprocess.Popen([f"/proc/self/fd/{self.tool_fds['python']}",'-I','-B',stage+'/supervisor.py','--approved-launch'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},close_fds=True,
                pass_fds=(self.tool_fds['python'],))
            code=child.wait()  # No exception handler kills or retries this process.
            need(code==0)
            held_stage_binding(stage,stage_fd,objects_fd,self.bridge)
            verify_tree(stage_fd,manifest)
            read_at(stage_fd,'supervisor.py',2*1024*1024,0o444,sha(source))
            evidence_fd=os.open('evidence',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=stage_fd)
            info=os.fstat(evidence_fd);need(info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700)
            raw,info=read_at(evidence_fd,'result.json',2*1024*1024,0o600)
            envelope=protected.strict_json(raw,2*1024*1024)
            need(type(envelope) is dict and set(envelope)=={'result','events'})
            cleanup=self.observer.verify_cleanup(stage,baseline)
            proposal=self.pipeline.module('contract').validate_proposal(prepared['proposal_raw'],
                {key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')})
            store_source=protected.read_pinned(self.config['sources'][WORKSPACE+'workspace_store.py'],4*1024*1024)[0]
            independent_readback(stage_fd,source,preparation,proposal,envelope['result'],store_source)
            # The strict pipeline additionally binds candidate/capture/task/proof/
            # fixed readback. Authentication here comes from protected held bytes.
            self.pipeline.receipt_from_result(prepared['proposal_raw'],prepared['capture_sha256'],reservation,
                preparation,envelope['result'],prepared['usage'])
            save_at(report,'authenticated-result.json',raw)
            return ExecutionEvidence(stdout,capture.exit_code,capture.timedout,preparation,envelope['result'],cleanup)
        finally:
            failed=False
            for fd in (evidence_fd,objects_fd,stage_fd,report):
                if fd is not None:
                    try:os.close(fd)
                    except OSError:failed=True
            need(not failed)


def receive_configured_once(approval,state_dir,loop_fd,transport,config_record,expected_revision,
                            *,clock,cancelled):
    """Concrete opt-in root receiver integration; still no daemon or live activation."""
    from .dispatch import receive_once
    executor=None;started=False
    try:
        need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0)
        def authority():
            approval.check(clock(),expected_revision)
            need(cancelled() is False)
        authority()
        executor=ConcreteExecutor(config_record,expected_revision,authority)
        started=True
        return receive_once(approval,state_dir,loop_fd,transport,executor,expected_revision,
                            clock=clock,cancelled=cancelled)
    except (Exception,KeyboardInterrupt):
        return 'uncertain' if started else 'rejected'
    finally:
        if executor is not None:
            try:executor.close()
            except Exception:return 'uncertain'
