"""Exclusive root-only workspace candidate preparation, no native execution.

Native baseline modules are explicit trusted dependencies, not request inputs.
Their reviewed build/owner authentication and fixed recovery map are retained.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat

import workspace_store as store
import native_generator

RECOVERY_SHA = 'f162f46571a72c9948801d7dc38008925be5d9dd0e4c0311294a4e537943d6d1'
SOURCE_KEYS = {'requirements.toml':'requirements.toml','launch.py':'launch.py','rootfs/diagnostic/launch.py':'launch.py',
    'report.py':'report.py','rootfs/diagnostic/report.py':'report.py',
    'supervisor.py':'supervisor.py','rootfs/diagnostic/pins.json':'pins.json',
    'rootfs/etc/codex/requirements.toml':'requirements.toml',
    'DEPENDENCIES.json':'DEPENDENCIES.json','EMPTY_BINDINGS.json':'EMPTY_BINDINGS.json',
    'diagnostic.service':'diagnostic.service'}
ROUTES = {'/diagnostic/launch.py':'launch.py','/diagnostic/report.py':'report.py',
          '/etc/codex/requirements.toml':'requirements.toml','/usr/lib/python3.12/os.py':'os.py'}
CANARIES = {'rootfs/readonly-fixture/outside.txt':(store.WS_OUTSIDE,0o444),
            'rootfs/secret-fixture/canary':(store.WS_SECRET,0o000)}


def need(value):
    if not value:
        raise ValueError('WORKSPACE_MATERIALIZATION_REJECTED')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value,indent=2)+'\n').encode('ascii')


def profile_bytes():
    return Path(__file__).with_name('profile.toml').read_bytes()


def derive(base_plan, contract, os_bytes, *, source_generator=None):
    """Pure amendment of an independently recomputed baseline plan."""
    store.ws_contract(contract)
    need(set(base_plan['bind_identities']) == {'/empty-home','/var/tmp'})
    need(sha(os_bytes)==json.loads(base_plan['files']['DEPENDENCIES.json'])['dependencies']['/usr/lib/python3.12/os.py']['sha256'])
    files=copy.deepcopy(base_plan['files'])
    pins=json.loads(files['pins.json']);pins['workspace']=copy.deepcopy(contract)
    files['pins.json']=encoded(pins)
    # An explicit trusted local component, never a callable from request/model data.
    files=(native_generator.generate if source_generator is None else source_generator)(files)
    files['requirements.toml']=profile_bytes()
    unit=files['diagnostic.service'].decode('ascii')
    need('BindPaths=' not in unit and 'ReadWritePaths=' not in unit)
    unit += 'BindPaths='+base_plan['stage_path']+'/workspace:/workspace-fixture\nReadWritePaths=+/workspace-fixture\n'
    files['diagnostic.service']=unit.encode('ascii')
    pins=json.loads(files['pins.json'])
    for target,name in ROUTES.items():
        if name!='os.py':pins['files'][target]=sha(files[name])
    files['pins.json']=encoded(pins)
    deps=json.loads(files['DEPENDENCIES.json'])
    need(len(deps['dependencies'])==43 and len(deps['symlinks'])==9)
    content={**{n:files[n] for n in ('launch.py','report.py','requirements.toml')},'os.py':os_bytes}
    routes={}
    for target,name in ROUTES.items():
        data=content[name];digest=sha(data)
        record=deps['dependencies'][target]
        need(record.get('source_observation')=='PENDING_PREPARATION')
        record.update(source='bundle:objects/'+digest,resolved_source='bundle:objects/'+digest,
                      sha256=digest,source_uid=None,source_gid=None,source_mode=None)
        routes[target]={'sha256':digest,'data':data}
    files['DEPENDENCIES.json']=encoded(deps)
    manifest=json.loads(files['PROPOSED_ARTIFACT_SHA256.json'])
    need(len(manifest)==122)
    for target,name in SOURCE_KEYS.items():
        need(target in manifest);manifest[target]=sha(files[name])
    for name,(data,_) in CANARIES.items():
        need(name not in manifest);manifest[name]=sha(data)
    files['PROPOSED_ARTIFACT_SHA256.json']=encoded(manifest)
    return {'files':files,'routes':routes,'workspace_contract':copy.deepcopy(contract),
            'stage_path':base_plan['stage_path'],'state':'WORKSPACE_SOURCES_UNSEALED'}


def seal(plan, objects_fd, native_materialize):
    """Seal exactly four generated runtime-source objects, no host repinning."""
    need(plan['state']=='WORKSPACE_SOURCES_UNSEALED' and os.listdir(objects_fd)==[])
    info=os.fstat(objects_fd)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700)
    result=copy.deepcopy(plan);deps=json.loads(result['files']['DEPENDENCIES.json'])
    for target,route in result['routes'].items():
        digest=route['sha256'];data=route['data'];need(sha(data)==digest)
        fd=os.open(digest,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=objects_fd)
        try:
            native_materialize.write_all(fd,data);os.fchmod(fd,0o444);os.fsync(fd)
            os.lseek(fd,0,os.SEEK_SET);need(os.read(fd,len(data)+1)==data)
            held=os.fstat(fd);named=os.stat(digest,dir_fd=objects_fd,follow_symlinks=False)
            need(stat.S_ISREG(held.st_mode) and stat.S_IMODE(held.st_mode)==0o444
                 and held.st_size==len(data) and held.st_uid==held.st_gid==0 and held.st_nlink==1 and
                 (held.st_dev,held.st_ino,held.st_mode,held.st_uid,held.st_gid,held.st_nlink,held.st_size)==
                 (named.st_dev,named.st_ino,named.st_mode,named.st_uid,named.st_gid,named.st_nlink,named.st_size))
            source=plan['stage_path']+'/objects/'+digest
            deps['dependencies'][target].update(source=source,resolved_source=source,
                source_uid=0,source_gid=0,source_mode='0o444',source_observation='SEALED_VERIFIED',
                source_device_inode=[held.st_dev,held.st_ino])
        finally:os.close(fd)
    os.fsync(objects_fd)
    result['files']['DEPENDENCIES.json']=encoded(deps)
    manifest=json.loads(result['files']['PROPOSED_ARTIFACT_SHA256.json'])
    manifest['DEPENDENCIES.json']=sha(result['files']['DEPENDENCIES.json'])
    result['files']['PROPOSED_ARTIFACT_SHA256.json']=encoded(manifest)
    result['state']='WORKSPACE_SOURCES_SEALED'
    return result


def compile_targets(recovery_bytes, plan, bundled, native_bridge):
    need(sha(recovery_bytes)==RECOVERY_SHA and plan['state']=='WORKSPACE_SOURCES_SEALED')
    recovery=json.loads(recovery_bytes);native_bridge.zlib_amendment.amend_recovery(recovery)
    manifest=json.loads(plan['files']['PROPOSED_ARTIFACT_SHA256.json'])
    deps=json.loads(plan['files']['DEPENDENCIES.json'])
    need(set(manifest)==set(recovery['manifest'])|set(CANARIES) and len(manifest)==124)
    need(set(deps['dependencies'])==set(recovery['dependencies']) and deps['symlinks']==recovery['symlinks'])
    need(set(plan['routes'])==set(ROUTES))
    for target,route in plan['routes'].items():
        record=deps['dependencies'][target]
        source=plan['stage_path']+'/objects/'+route['sha256']
        need(manifest['rootfs'+target]==route['sha256'])
        if ROUTES[target]!='os.py':
            need(sha(plan['files'][ROUTES[target]])==route['sha256'])
        need(sha(route['data'])==route['sha256']==record['sha256'] and
             record['source']==record['resolved_source']==source and
             record['source_observation']=='SEALED_VERIFIED' and
             record['source_uid']==record['source_gid']==0 and record['source_mode']=='0o444' and
             type(record.get('source_device_inode')) is list and len(record['source_device_inode'])==2 and
             all(type(v) is int and v>0 for v in record['source_device_inode']))
    need(set(bundled)=={r['sha256'] for r in recovery['manifest'].values() if r['kind']=='bundled'})
    need(all(type(data) is bytes and sha(data)==digest for digest,data in bundled.items()))
    targets={}
    for name,digest in manifest.items():
        need(name and not name.startswith('/') and all(p not in ('','.','..') for p in name.split('/')))
        target='/'+name[7:] if name.startswith('rootfs/') else None
        mode=int(deps['dependencies'][target]['mode'],8) if target in deps['dependencies'] else 0o444
        if name in CANARIES:
            data,mode=CANARIES[name];entry={'data':data}
        elif name in SOURCE_KEYS:entry={'data':plan['files'][SOURCE_KEYS[name]]}
        else:
            route=recovery['manifest'][name]
            if route['kind']=='bundled':entry={'data':bundled[digest]}
            else:
                record=deps['dependencies'][target]
                need(route['kind']=='installed' and route['source']==record['resolved_source']
                     and route['sha256']==record['sha256']==digest)
                entry={'source':copy.deepcopy(record)}
        need(mode in (0o444,0o555) or name in CANARIES or
             (target=='/usr/bin/bwrap' and mode==0o755 and recovery['dependencies'][target]['target_mode']=='0o755'))
        if 'data' in entry:need(sha(entry['data'])==digest)
        targets[name]={**entry,'sha256':digest,'mode':mode}
    raw=encoded(manifest);targets['ARTIFACT_SHA256.json']={'data':raw,'sha256':sha(raw),'mode':0o444}
    links={'rootfs/'+p.lstrip('/'):v for p,v in deps['symlinks'].items()}
    need(len(links)==9 and not set(links)&set(targets))
    return targets,links


def write_seeded_tree(stage_fd,rootfs_fd,fixture,targets,links,native_materialize,final_check=lambda: None):
    """Existing seeded identities are preserved; all other paths are exclusive."""
    writer=native_materialize.TreeWriter(stage_fd,(0,0))
    try:
        writer.dirs['rootfs']=os.dup(rootfs_fd)
        for name in store.WS_DIRS:
            parent=stage_fd if name=='workspace' else rootfs_fd
            leaf=name.split('/')[-1]
            writer.dirs[name]=os.open(leaf,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            need(store.ws_row(os.fstat(writer.dirs[name]),True)==fixture.contract['entries'][name]['identity'])
        for name,entry in sorted(targets.items()):
            if name not in CANARIES:writer.file(name,entry)
        for name,target in sorted(links.items()):
            parent=writer.directory(name.rsplit('/',1)[0]);leaf=name.rsplit('/',1)[1]
            os.symlink(target,leaf,dir_fd=parent);need(os.readlink(leaf,dir_fd=parent)==target);os.fsync(parent)
        for name in ('evidence','daemon-tmp','rootfs/root','rootfs/diagnostic/readiness',
                     'rootfs/proc','rootfs/sys/fs/cgroup','rootfs/dev/shm','rootfs/dev/pts',
                     'rootfs/dev/mqueue','rootfs/dev/hugepages','rootfs/tmp','rootfs/var/tmp','rootfs/run','rootfs/empty-home'):
            writer.directory(name)
        for name,fd in sorted(writer.dirs.items(),key=lambda item:-item[0].count('/')):
            if name:
                mode=store.WS_DIRS.get(name,0o750 if name=='rootfs/root' else 0o700 if name=='evidence' else 0o555)
                os.fchmod(fd,mode);os.fsync(fd)
        need(fixture.observe()==fixture.contract)
        # Verify every immutable output and every fixture identity before completion.
        for name,entry in targets.items():
            parts=name.split('/');parent=writer.dirs['/'.join(parts[:-1])]
            fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            try:
                info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0
                    and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==entry['mode'])
                digest=hashlib.sha256()
                while data:=os.read(fd,65536):digest.update(data)
                need(digest.hexdigest()==entry['sha256'])
            finally:os.close(fd)
        final_check()
        marker=encoded({'state':'WORKSPACE_MATERIALIZED_NOT_EXECUTED','manifest_sha256':targets['ARTIFACT_SHA256.json']['sha256'],
                        'workspace_contract_sha256':sha(encoded(fixture.contract))})
        writer.file('MATERIALIZATION_COMPLETE.json',{'data':marker,'sha256':sha(marker),'mode':0o444})
        return sha(marker)
    finally:
        owned=list(writer.dirs.values())
        writer.dirs.clear()
        errors=[]
        for fd in owned:
            try:os.close(fd)
            except Exception:errors.append(True)
        need(not errors)


def prepare(stage,historical,objects,evidence,recovery_bytes,bundled,native_bridge,native_materialize, *, source_generator=None):
    """Root-only API; no unit start, permissions outside this fresh stage or retry."""
    need(os.geteuid()==os.getegid()==0)
    stage_fd=native_materialize.open_absolute(stage)
    rootfs=objects_fd=None;fixture=None
    try:
        objects_fd=os.open('objects',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=stage_fd)
        native_bridge.verify_stage_objects_binding(stage,objects_fd)
        need(set(os.listdir(stage_fd))=={'empty-home','empty-vartmp','objects'} and os.listdir(objects_fd)==[])
        ids=[]
        for name in ('empty-home','empty-vartmp'):
            fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=stage_fd)
            try:
                info=os.fstat(fd);need(info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o555 and os.listdir(fd)==[])
                ids.append((info.st_dev,info.st_ino))
            finally:os.close(fd)
        base=native_bridge.build(historical,objects,stage,*ids,evidence)
        # Durable reservation precedes any fixture mutation. Failures are never repaired.
        fd=os.open('MATERIALIZATION_RESERVED.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o444,dir_fd=stage_fd)
        try:native_materialize.write_all(fd,b'{"state":"WORKSPACE_RESERVED"}\n');os.fchmod(fd,0o444);os.fsync(fd)
        finally:os.close(fd)
        os.fsync(stage_fd);os.mkdir('rootfs',0o555,dir_fd=stage_fd)
        rootfs=os.open('rootfs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=stage_fd)
        os.fchmod(rootfs,0o555);os.fsync(rootfs);os.fsync(stage_fd)
        fixture=store.seed(stage_fd,rootfs)
        amended=derive(base,fixture.contract,objects[native_bridge.OS_PY],source_generator=source_generator)
        final=seal(amended,objects_fd,native_materialize)
        targets,links=compile_targets(recovery_bytes,final,bundled,native_bridge)
        binding=native_bridge.verify_stage_objects_binding(stage,objects_fd)
        def final_check():
            native_materialize.verify_written_stage_binding(binding,
                native_bridge.verify_stage_objects_binding(stage,objects_fd),stage_fd)
            need(set(os.listdir(objects_fd))=={route['sha256'] for route in final['routes'].values()})
            for name,pair in zip(('empty-home','empty-vartmp'),ids):
                fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=stage_fd)
                try:
                    info=os.fstat(fd)
                    need((info.st_dev,info.st_ino)==pair and info.st_uid==info.st_gid==0
                         and stat.S_IMODE(info.st_mode)==0o555 and os.listdir(fd)==[])
                finally:os.close(fd)
        completion=write_seeded_tree(stage_fd,rootfs,fixture,targets,links,native_materialize,final_check)
        return {'state':'WORKSPACE_MATERIALIZED_NOT_EXECUTED','completion_sha256':completion,
                'manifest_sha256':targets['ARTIFACT_SHA256.json']['sha256'],'workspace_contract':copy.deepcopy(fixture.contract)}
    finally:
        errors=[]
        if fixture is not None:
            try:fixture.close()
            except Exception as error:errors.append(type(error).__name__)
        for fd in (rootfs,objects_fd,stage_fd):
            if fd is not None:
                try:os.close(fd)
                except Exception as error:errors.append(type(error).__name__)
        need(not errors)
