"""Adversarial provisional-proof fixtures. No host/kernel/native operations."""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import provisional


def runtime():
    env = dict(json=json, hashlib=hashlib, stat=stat)
    exec(compile(provisional.RUNTIME, '<proof-fixture>', 'exec'), env)
    return env


def fixture():
    identity = dict(pid=12979, start=1347813, uid=63153, gid=63153,
                    invocation='a'*32, group='/system.slice/zen53-mincompat.service')
    mount = dict(device='0:48', root='/', type='proc', options='rw,nosuid', super='rw,hidepid=invisible')
    before = dict(identity=identity, proc=dict(identity=[48, 1], mount=mount),
                  namespaces=dict(pid=11, mnt=12, net=13, user=14),
                  targets=[dict(path=p, metadata=[48, 100+i, 16384]) for i,p in enumerate(runtime()['PROOF_PATHS'])],
                  nonce='b'*64, provision='c'*64)
    observation = dict(nonce='b'*64, provision='c'*64, outer=copy.deepcopy(identity),
        proc=copy.deepcopy(before['proc']), namespaces=dict(pid=21,mnt=22,net=23,user=24),
        maps=[[63153,0,1],[63153,0,1]], outcomes=[dict(path=p, errno=2) for p in runtime()['PROOF_PATHS']],
        topology_sha256='d'*64, fd_checked=True, topology_checked=True)
    observation['proc']['mount']['options']='ro,nosuid'
    status = dict(Name='python3.12',Pid='13297',PPid='1',Uid='63153 63153 63153 63153',
        Gid='63153 63153 63153 63153',NSpid='13297 2',CapInh='0',CapPrm='0',CapEff='0',
        CapBnd='0',CapAmb='0',NoNewPrivs='1',Seccomp='2')
    report = ['ZEN53_REPORT_V1','apparmor=bwrap//&unpriv_bwrap (enforce)']
    report += ['status.'+k+'='+v for k,v in status.items()]
    report += ['namespace.'+k+'='+k+':['+str(v)+']' for k,v in observation['namespaces'].items()]
    report += ['bookkeeping.'+k+'='+v for k,v in dict(readonly='1',no_aliases='1',no_inherited_fds='1',
        outer_proc_denied='provisional',tmpdir='/run/zen53-bookkeeping',proc_mode='inherited',device='0:99',inode='7').items()]
    report += ['ZEN53_REPORT_END']
    return dict(version=1,observation=observation,report=report),before,copy.deepcopy(before)


def encode(env,frame):
    return env['PROOF_PREFIX'].encode()+env['proof_bytes'](frame)+b'\n'


class Provisional(unittest.TestCase):
    def test_complete_hidden_live_and_permission_denials(self):
        env=runtime()
        for errno in (1,2,13):
            frame,before,after=fixture()
            for item in frame['observation']['outcomes']: item['errno']=errno
            cert=env['proof_verify'](encode(env,frame),before,after)
            self.assertEqual(cert['state'],'LIVE_TARGET_INACCESSIBLE')
            self.assertEqual(cert['frame_sha256'],hashlib.sha256(encode(env,frame)).hexdigest())

    def test_wrong_bindings_or_missing_predicates_reject(self):
        mutations = {
          'pid':lambda f,b,a:f['observation']['outer'].update(pid=999),
          'pidns':lambda f,b,a:f['observation']['namespaces'].update(pid=b['namespaces']['pid']),
          'procdev':lambda f,b,a:f['observation']['proc']['identity'].__setitem__(0,49),
          'procroot':lambda f,b,a:f['observation']['proc']['mount'].update(root='/1'),
          'stale':lambda f,b,a:f['observation'].update(nonce='e'*64),
          'reuse':lambda f,b,a:a['identity'].update(start=999),
          'missingtarget':lambda f,b,a:b['targets'].pop(),
          'changedtarget':lambda f,b,a:a['targets'][0]['metadata'].__setitem__(1,999),
          'missingprobe':lambda f,b,a:f['observation']['outcomes'].pop(),
          'duplicateprobe':lambda f,b,a:f['observation']['outcomes'].__setitem__(1,copy.deepcopy(f['observation']['outcomes'][0])),
          'access':lambda f,b,a:f['observation']['outcomes'][0].update(errno=0),
          'unknownerrno':lambda f,b,a:f['observation']['outcomes'][0].update(errno=5),
          'nofd':lambda f,b,a:f['observation'].update(fd_checked=False),
          'topology':lambda f,b,a:f['observation'].update(topology_checked=False),
          'hidepolicy':lambda f,b,a:f['observation']['proc']['mount'].update(super='rw'),
          'map':lambda f,b,a:f['observation']['maps'][0].__setitem__(1,123),
          'bool':lambda f,b,a:f.update(version=True),
          'extra':lambda f,b,a:f.update(extra=True),
          'duplicate_report':lambda f,b,a:f['report'].insert(1,f['report'][1]),
        }
        for name,change in mutations.items():
            env=runtime();frame,before,after=fixture();change(frame,before,after)
            with self.subTest(name=name),self.assertRaises((RuntimeError,ValueError,TypeError)):
                env['proof_verify'](encode(env,frame),before,after)

    def test_strict_frame_encoding_and_bounds(self):
        env=runtime();frame,_,_=fixture();line=encode(env,frame)
        bads=[line[:-1],line+b'\n',line+b'x',line.replace(b'"version":1',b'"version":1,"version":1'),
              line.replace(b'"errno":2',b'"errno":true',1),line.replace(b'"errno":2',b'"errno":2.0',1),
              b'ZEN53_PROVISIONAL_V1 '+b' '*8192+b'\n',line.replace(b'{',b'{ ',1)]
        for bad in bads:
            with self.assertRaises((RuntimeError,ValueError,TypeError)):
                env['proof_parse'](bad)

    def test_supervisor_requires_unique_matching_attestation(self):
        env=runtime();frame,before,after=fixture();line=encode(env,frame)
        records=[dict(phase='native_finish',monotonic_ns=100,pid=13297,wait_status=0,output=line.decode()),
                 dict(phase='native_proof',monotonic_ns=101,frame=line.decode(),before=before,after=after,
                      certificate=env['proof_verify'](line,before,after)),
                 dict(phase='launcher_complete',monotonic_ns=102,result='REPORTER_FINISHED_NOT_ISOLATION_ACCEPTANCE')]
        binding={key:before[key] for key in ('identity','nonce','provision')}
        dump=lambda r:'\n'.join(json.dumps(v) for v in r)
        self.assertEqual(env['proof_supervisor'](dump(records),binding),frame['report'])
        for changed in (records+records[-1:],records[1:],list(reversed(records))):
            with self.assertRaises(RuntimeError): env['proof_supervisor'](dump(changed),binding)
        altered=copy.deepcopy(records);altered[1]['certificate']['frame_sha256']='0'*64
        with self.assertRaises(RuntimeError):env['proof_supervisor'](dump(altered),binding)
        for key in ('identity','nonce','provision'):
            wrong=copy.deepcopy(binding)
            if key=='identity':wrong[key]['start']+=1
            else:wrong[key]='e'*64
            with self.subTest(binding=key),self.assertRaises(RuntimeError):
                env['proof_supervisor'](dump(records),wrong)
        for kind in ('exit','extra_output','duplicate_frame','bool_certificate'):
            altered=copy.deepcopy(records)
            if kind=='exit':altered[0]['wait_status']=256
            elif kind=='extra_output':altered[0]['output']+='unrecognized text\n'
            elif kind=='duplicate_frame':altered[0]['output']+=line.decode()
            else:altered[1]['certificate']['version']=True
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):
                env['proof_supervisor'](dump(altered),binding)

    def test_generated_classifier_accepts_only_strict_frame_and_known_warning(self):
        import proc_bracket
        generated=provisional.launcher(proc_bracket.instrument((ROOT/'historical/launch.py').read_bytes()))
        tree=ast.parse(generated)
        selected=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='NativeOutput' or
                  isinstance(n,ast.FunctionDef) and n.name=='need' or isinstance(n,ast.Assign) and
                  any(isinstance(t,ast.Name) and t.id=='EXPECTED_ALIAS_WARNING' for t in n.targets)]
        env=runtime();env['native_bracket']=NS(observe=lambda line:None)
        exec(compile(ast.Module(body=selected,type_ignores=[]),'<classifier>','exec'),env)
        frame,_,_=fixture();line=encode(env,frame)
        for split in range(len(line)+1):
            output=env['NativeOutput']();output.feed(line[:split]);output.feed(line[split:]);output.finish()
        for tail in (line,b'error: synthetic\n',b'trailing'):
            output=env['NativeOutput']();output.feed(line)
            with self.assertRaises(RuntimeError):output.feed(tail);output.finish()
        output=env['NativeOutput']()
        with self.assertRaises(RuntimeError):output.feed(b'bookkeeping.outer_proc_denied=1\n')

    def test_generation_preserves_final_release_check_and_no_clock_comparison(self):
        import proc_bracket
        generated=provisional.launcher(proc_bracket.instrument((ROOT/'historical/launch.py').read_bytes())).decode()
        self.assertIn('proof_outer.begin()\n        provision_worker.before_native()\n    pid = os.fork()',generated)
        self.assertNotIn('monotonic',ast.get_source_segment(provisional.RUNTIME,next(n for n in ast.parse(provisional.RUNTIME).body if isinstance(n,ast.FunctionDef) and n.name=='proof_verify')))
        for fn,name in ((provisional.launcher,'launch.py'),(provisional.reporter,'report.py'),(provisional.supervisor,'supervisor.py')):
            raw=(ROOT/'candidate'/name).read_bytes()
            if name=='launch.py':raw=proc_bracket.instrument(raw)
            with self.assertRaises(ValueError):fn(fn(raw))
        supervisor=provisional.supervisor((ROOT/'candidate/supervisor.py').read_bytes()).decode()
        for source in ('provision_transaction.identity',"provision_transaction.attempt['nonce']",
                       'canonical_record(provision_transaction.provision)'):
            self.assertIn(source,supervisor)

    def test_reporter_normalizes_real_proc_status_tabs(self):
        env=runtime();frame,before,after=fixture()
        values=env['proof_report'](frame['report'])
        status={key[7:]:value for key,value in values.items() if key.startswith('status.')}
        status_raw='\n'.join(key+':\t'+value.replace(' ','\t') for key,value in status.items())
        output=io.StringIO()
        env.update(sys=NS(argv=['report.py'],stdout=output),FIELDS=tuple(status),
            text=lambda path,limit: values['apparmor'] if path.endswith('/attr/current') else status_raw,
            os=NS(readlink=lambda path:values['namespace.'+path.rsplit('/',1)[1]]),
            report_bookkeeping=lambda *args:[line for line in frame['report'] if line.startswith('bookkeeping.')],
            proof_inner_record=frame['observation'])
        tree=ast.parse(provisional.reporter((ROOT/'candidate/report.py').read_bytes()))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<reporter-main>','exec'),env)
        env['main']()
        self.assertEqual(env['proof_verify'](output.getvalue().encode(),before,after)['state'],'LIVE_TARGET_INACCESSIBLE')

    def outer_io(self):
        env=runtime();frame,before,_=fixture()
        identity=before['identity'];closed=[];inherited=[];fds={9:(48,12979,stat.S_IFDIR|0o555)}
        state={'dead':False,'missing':None,'changed':None,'overlay':False,'serial':20}
        def metadata(fd):
            dev,ino,mode=fds[fd]
            return NS(st_dev=dev,st_ino=ino,st_mode=mode)
        def opened(path,flags,dir_fd=None):
            if path==state['missing']:raise FileNotFoundError(path)
            if path=='/proc':value=(48,1,stat.S_IFDIR|0o555)
            elif path=='12979':value=fds[9]
            else:value=(48,200+env['PROOF_PATHS'].index(path)+(1000 if path==state['changed'] else 0),stat.S_IFREG|0o444)
            state['serial']+=1;fd=state['serial'];fds[fd]=value;return fd
        mount=dict(id=344,destination='/proc',**before['proc']['mount'])
        env.update(os=NS(O_RDONLY=0,O_DIRECTORY=1,O_NOFOLLOW=2,O_CLOEXEC=4,O_PATH=8,
            getpid=lambda:12979,pidfd_open=lambda *a:10,set_inheritable=lambda fd,v:inherited.append((fd,v)),
            open=opened,close=closed.append,fstat=metadata,
            stat=lambda *a,**kw:NS(st_dev=48,st_ino=1,st_mode=stat.S_IFDIR|0o555),
            major=lambda d:0,minor=lambda d:d,
            readlink=lambda path,**kw:path[3:]+':['+str(before['namespaces'][path[3:]])+']'),
            select=NS(select=lambda *a:([10] if state['dead'] else [],[],[])),
            provision_worker=NS(procfd=9,identity=identity,attempt={'nonce':'b'*64},provision={}),
            canonical_record=lambda value:b'provision',
            parse_proc_identity=lambda *a:dict(identity),read_at=lambda *a:b'mounts',
            parse_mounts=lambda raw:[mount]+([dict(id=345,destination='/proc/12979/fd')] if state['overlay'] else []),
            mount_visibility=lambda mounts:({344},{}))
        return env,env['ProofOuter'](),state,closed,inherited

    def test_outer_refs_close_on_failure_and_never_accept_dead_or_missing_targets(self):
        for mode in ('dead','missing','changed','overlay'):
            env,outer,state,closed,inherited=self.outer_io()
            outer.begin()
            owned=list(outer.owned)
            if mode=='dead':state['dead']=True
            elif mode=='missing':state['missing']='fd/1'
            elif mode=='changed':state['changed']='cwd'
            else:state['overlay']=True
            with self.subTest(mode=mode),self.assertRaises((RuntimeError,FileNotFoundError)):
                outer.sample()
            outer.close();outer.close()
            self.assertTrue(all(closed.count(fd)==1 for fd in owned))
            self.assertEqual(inherited,[(fd,False) for fd in owned])
        env,outer,state,closed,_=self.outer_io();state['missing']='fd/2'
        with self.assertRaises(FileNotFoundError):outer.begin()
        owned=list(outer.owned);outer.close()
        self.assertTrue(all(closed.count(fd)==1 for fd in owned))

    def test_reporter_collects_all_outcomes_closes_own_fd_and_rejects_access_or_drift(self):
        for mode in ('complete','access','unknown','drift','overlay'):
            env,_,state,closed,_=self.outer_io()
            real_open=env['os'].open;calls=[]
            def inner_open(path,flags,dir_fd=None):
                if path=='/proc':return real_open(path,flags)
                calls.append(path)
                if mode=='access':return real_open('root',flags,dir_fd=dir_fd)
                raise OSError(5 if mode=='unknown' else 2,'fixture')
            env['os'].open=inner_open
            env['text']=lambda *a:'changed' if mode=='drift' else 'mounts'
            if mode=='overlay':state['overlay']=True
            frame,before,_=fixture()
            args=(12979,'mounts',{'nonce':'b'*64},{'identity':before['identity']},
                  {'provision_sha256':'c'*64},((63153,0,1),(63153,0,1)),
                  {k:k+':['+str(v)+']' for k,v in frame['observation']['namespaces'].items()})
            if mode=='complete':
                record=env['proof_collect'](*args)
                self.assertEqual(len(calls),7)
                self.assertFalse(record['fd_checked'])
                self.assertFalse(record['topology_checked'])
            else:
                with self.assertRaises(RuntimeError):env['proof_collect'](*args)
            self.assertIn(21,closed)


if __name__=='__main__':unittest.main()
