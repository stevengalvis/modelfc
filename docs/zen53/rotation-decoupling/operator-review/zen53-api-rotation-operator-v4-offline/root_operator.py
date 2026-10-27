"""Explicit trusted-root provisioning/approval CLI; no autonomous issuer.

Do not run until exact package installation and owner approval are separately
authorized. This never launches the diagnostic; it only handles protected
approval data. The owner-approval reference is audit data, not proof.
"""
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import time

ROOT = '/var/lib/zen53-operator-v4'
OPERATOR_SHA = 'b1079195e43ac8afb287327de12483b2e445025cfe18f2f7b44e81b4b19e9073'
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def read_protected(path, mode, maximum, *, owner_uid=0, owner_gid=0,
                   return_identity=False):
    if (type(path) is not str or not path.startswith('/') or path == '/' or
        any(p in ('', '.', '..') for p in path.split('/')[1:])):
        raise ValueError('ISSUER_PATH')
    parent=os.open('/',DIR)
    held=[]
    try:
        parts=path.split('/')[1:]
        for part in parts[:-1]:
            nxt=os.open(part,DIR,dir_fd=parent)
            info=os.fstat(nxt)
            if (not stat.S_ISDIR(info.st_mode) or
                info.st_uid not in (0,owner_uid) or
                info.st_gid not in (0,owner_gid) or
                stat.S_IMODE(info.st_mode)&0o022):
                os.close(nxt)
                raise ValueError('ISSUER_ANCESTOR')
            held.append((parent,part,info.st_dev,info.st_ino,info.st_uid,
                         info.st_gid,info.st_mode,info.st_ctime_ns))
            parent=nxt
        fd=os.open(parts[-1],FILE,dir_fd=parent)
        try:
            before=os.fstat(fd)
            named=os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)
            key=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,
                          s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or
                (before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode))!=
                (owner_uid,owner_gid,mode) or before.st_size>maximum or
                key(before)!=key(named)):
                raise ValueError('ISSUER_FILE_META')
            raw=os.read(fd,maximum+1)
            if (len(raw)!=before.st_size or
                key(before)!=key(os.fstat(fd)) or
                key(before)!=key(os.stat(parts[-1],dir_fd=parent,
                                     follow_symlinks=False))):
                raise ValueError('ISSUER_FILE_CHANGED')
            for ancestor,name,dev,ino,uid,gid,mode,ctime in held:
                now=os.stat(name,dir_fd=ancestor,follow_symlinks=False)
                if (not stat.S_ISDIR(now.st_mode) or
                    (now.st_dev,now.st_ino,now.st_uid,now.st_gid,
                     now.st_mode,now.st_ctime_ns)!=
                    (dev,ino,uid,gid,mode,ctime)):
                    raise ValueError('ISSUER_ANCESTOR_CHANGED')
            return (raw,(before.st_dev,before.st_ino)) if return_identity else raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)
        for fd,*_ in held:
            os.close(fd)


def load_operator(*, root=ROOT, owner_uid=0, owner_gid=0):
    source=read_protected(root+'/operator_once.py',0o444,131072,
                          owner_uid=owner_uid,owner_gid=owner_gid)
    if hashlib.sha256(source).hexdigest()!=OPERATOR_SHA:
        raise ValueError('ISSUER_OPERATOR_PIN')
    spec=importlib.util.spec_from_loader('zen53_issuer_verified_operator',
                                         loader=None)
    module=importlib.util.module_from_spec(spec)
    module.__file__=root+'/operator_once.py'
    exec(compile(source,'<verified operator_once.py>','exec'),module.__dict__)
    return module


def modules(op, root, *, reader=None):
    reader = op.read_pinned if reader is None else reader
    contract=op.load_pinned('issuer_binding_contract',reader(
        root+'/approved_binding.py',op.APPROVED_BINDING_SHA))
    store_mod=op.load_pinned('issuer_binding_store',reader(
        root+'/binding_store.py',op.BINDING_STORE_SHA))
    launcher=op.load_pinned('issuer_launcher',reader(
        root+'/launch_once.py',op.LAUNCHER_SHA))
    store_mod.configure(contract)
    config=op.installed_config(reader(
        root+'/INSTALL_CONFIG.json',op.INSTALL_CONFIG_SHA))
    return contract,store_mod,launcher,config


def approve_stage(op, contract, store, launcher, nonce,
                  approved_manifest_sha, owner_approval_ref, archive_path,
                  package_root, valid_seconds, *, now_ns=time.time_ns,
                  owner_uid=0, owner_gid=0):
    """Trusted root operator witnesses explicit approval of exact manifest SHA.

    Data from the protected stage is not permission by itself. The human/root
    operator must establish that the owner approved this exact nonce, profile
    and full SHA before calling this command. No model or scope is started.
    """
    if (type(nonce) is not str or contract.NONCE.fullmatch(nonce) is None or
        type(approved_manifest_sha) is not str or
        contract.HASH.fullmatch(approved_manifest_sha) is None or
        type(valid_seconds) is not int or
        not 1 <= valid_seconds <= 3600):
        raise ValueError('ISSUER_ARGUMENTS')
    tool=launcher.BASE+'/zen53-owner-tool-'+nonce
    stage=launcher.BASE+'/zen53-owner-capture-'+nonce
    raw, manifest_id=read_protected(tool+'/LAUNCH_MANIFEST.json',0o600,
                                    262144,owner_uid=owner_uid,
                                    owner_gid=owner_gid,return_identity=True)
    if hashlib.sha256(raw).hexdigest()!=approved_manifest_sha:
        raise ValueError('ISSUER_MANIFEST_APPROVAL_MISMATCH')
    envelope=json.loads(raw,object_pairs_hook=launcher.unique_pairs)
    if (type(envelope) is not dict or set(envelope)!=
        {'manifest','manifest_sha256'} or
        type(envelope['manifest_sha256']) is not str or
        contract.HASH.fullmatch(envelope['manifest_sha256']) is None):
        raise ValueError('ISSUER_MANIFEST_SCHEMA')
    tool_id=launcher._directory(tool)
    stage_id=launcher._directory(stage)
    timestamp=now_ns()
    if type(timestamp) is not int or timestamp<0:
        raise ValueError('ISSUER_CLOCK')
    issued=timestamp//1_000_000_000
    # The attempt ID is an identifier, not a credential or authentication.
    import secrets
    attempt=secrets.token_hex(16)
    if attempt==nonce:
        raise ValueError('ISSUER_ATTEMPT_COLLISION')
    approved={'schema':'zen53-run-approval-v1',
        'profile':'zen53-python-diagnostic-v1',
        'nonce':nonce,'attempt_id':attempt,
        'manifest_sha256':approved_manifest_sha,
        'manifest_seal_sha256':envelope['manifest_sha256'],
        'tool_identity':list(tool_id),'stage_identity':list(stage_id),
        'manifest_identity':list(manifest_id),
        'issued_at':issued,'expires_at':issued+valid_seconds,
        'owner_approval_ref':owner_approval_ref}
    contract.decode(contract.canonical(approved),issued)
    launcher.bind(approved)
    item=launcher.read_prepared(op.SYSTEMD_SHA,archive_path,package_root)
    op.need(item['nonce']==nonce and
            item['tool_identity']==approved['tool_identity'] and
            item['stage_identity']==approved['stage_identity'],
            'ISSUER_STAGE_MISMATCH')
    measured={key:approved[key] for key in contract.MEASURED}
    approval_sha=store.issue(approved,measured,owner_approval_ref)
    return approved,approval_sha


def prepare_output(store, contract, launcher, nonce, *, base=None):
    """Exclusively create the one empty protected output root after approval."""
    base=launcher.BASE if base is None else base
    if type(nonce) is not str or contract.NONCE.fullmatch(nonce) is None:
        raise ValueError('ISSUER_ARGUMENTS')
    approved=contract.decode(store.read_current(),store._now())
    if approved['nonce']!=nonce:
        raise ValueError('ISSUER_STAGE_MISMATCH')
    parent=store._open_dir(base)
    name='zen53-owner-launch-'+nonce
    try:
        os.mkdir(name,0o700,dir_fd=parent)
        fd=os.open(name,DIR,dir_fd=parent)
        try:
            os.fchmod(fd,0o700)
            st=os.fstat(fd)
            named=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if (not stat.S_ISDIR(st.st_mode) or
                (st.st_dev,st.st_ino,st.st_uid,st.st_gid,
                 stat.S_IMODE(st.st_mode))!=
                (named.st_dev,named.st_ino,named.st_uid,named.st_gid,
                 stat.S_IMODE(named.st_mode)) or
                (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))!=
                (store.uid,store.gid,0o700) or os.listdir(fd)):
                raise ValueError('ISSUER_OUTPUT_META')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)
    return True

def _read_locks(*, open_file=os.open, read=os.read, close=os.close):
    """Read the bounded proc lock table through EOF, never a single read."""
    fd=open_file('/proc/locks',os.O_RDONLY | os.O_NOFOLLOW |
                 os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        chunks=[];total=0
        while True:
            part=read(fd,min(8192,131073-total))
            if not part:
                break
            if type(part) is not bytes:
                raise ValueError('ISSUER_LOCK_OBSERVATION')
            total+=len(part)
            if total>131072:
                raise ValueError('ISSUER_LOCK_OBSERVATION')
            chunks.append(part)
        try:
            return b''.join(chunks).decode('ascii')
        except UnicodeDecodeError:
            raise ValueError('ISSUER_LOCK_OBSERVATION') from None
    finally:
        close(fd)

def _locks_clear(raw, locks):
    """Inspect a complete kernel lock table without acquiring host locks."""
    if type(raw) is not str or len(raw) > 131072:
        raise ValueError('ISSUER_LOCK_OBSERVATION')
    targets={(os.major(device),os.minor(device),inode)
             for _,device,inode,*_ in locks}
    for line in raw.splitlines():
        match=re.fullmatch(
            r'[0-9]+: (?:-> )?(?:POSIX|FLOCK|OFDLCK) ADVISORY '
            r'(?:READ|WRITE) -?[0-9]+ ([0-9a-fA-F]+):([0-9a-fA-F]+):'
            r'([0-9]+) [0-9]+ (?:[0-9]+|EOF)',line)
        if match is None:
            raise ValueError('ISSUER_LOCK_OBSERVATION')
        major,minor,inode=match.groups()
        if (int(major,16),int(minor,16),int(inode)) in targets:
            raise ValueError('ISSUER_LOCK_HELD')
    return True

def observe_unclaimed(op, approved, launcher, *, reader=None,
                      admission=None):
    """Independent bounded no-launch snapshot for the trusted root retirer.

    The claim tombstone in RootStore is the linearization point. These reads
    supplement, rather than replace, the proof that reviewed code claims
    before spawning. Any unavailable or contradictory observation blocks.
    """
    launcher.bind(approved)
    if admission is None:
        source=op.read_pinned(launcher.TOOL+'/admission.py',op.ADMISSION_SHA)
        admission=op.load_pinned('zen53_retire_verified_admission',source)
    reader=admission.HostReader() if reader is None else reader
    if (launcher._directory(launcher.TOOL,
                            tuple(approved['tool_identity'])) !=
            tuple(approved['tool_identity']) or
        launcher._directory(launcher.STAGE,
                            tuple(approved['stage_identity'])) !=
            tuple(approved['stage_identity'])):
        raise ValueError('ISSUER_STAGE_CHANGED')
    launcher.verified_file(launcher.MANIFEST,approved['manifest_sha256'],
                           262144,0o600,
                           tuple(approved['manifest_identity']))
    launcher._directory(launcher.LAUNCH_ROOT,empty=True)
    for name in ('host-evidence','controller-evidence'):
        launcher._directory(launcher.STAGE+'/'+name,empty=True)
    if not reader.missing('/var/lib/zen53-operator-v4-audit-'+approved['nonce']):
        raise ValueError('ISSUER_AUDIT_PRESENT')
    if not reader.missing('/sys/fs/cgroup/system.slice/'+launcher.UNIT):
        raise ValueError('ISSUER_SCOPE_CGROUP_PRESENT')
    props=admission._property(reader,launcher.UNIT,('LoadState','ActiveState'))
    if props!={'LoadState':'not-found','ActiveState':'inactive'}:
        raise ValueError('ISSUER_SCOPE_PRESENT')
    op.lock_metadata(admission)
    _locks_clear(_read_locks(),admission.LOCKS)
    return True

def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    try:
        if ((os.geteuid(),os.getegid())!=(0,0) or
            os.path.abspath(__file__)!=ROOT+'/root_operator.py'):
            raise ValueError('ISSUER_ROOT')
        op=load_operator()
        contract,store_mod,launcher,config=modules(op,ROOT)
        store=store_mod.RootStore(config['approval_root'])
        if args==['provision']:
            store.provision()
            print('ZEN53_APPROVAL_STORE_PROVISIONED')
        elif len(args)==13 and args[0]=='approve' and args[1::2]==[
                '--nonce','--manifest-sha256','--approval-ref',
                '--archive','--package','--valid-seconds']:
            approved,sha=approve_stage(op,contract,store,launcher,
                args[2],args[4],args[6],args[8],args[10],int(args[12]))
            print('ZEN53_APPROVAL_ISSUED nonce='+approved['nonce']+
                  ' attempt_id='+approved['attempt_id']+
                  ' sha256='+sha)
        elif (len(args)==3 and args[0]=='prepare-output' and
              args[1]=='--nonce'):
            if prepare_output(store,contract,launcher,args[2]) is not True:
                raise ValueError('ISSUER_OUTPUT_META')
            print('ZEN53_OUTPUT_PREPARED')
        elif len(args)==11 and args[0]=='retire' and args[1::2]==[
                '--nonce','--attempt-id','--approval-sha256',
                '--terminal-ref','--cleanup-state']:
            if store.retire_current(args[2],args[4],args[6],args[8],args[10]) is not True:
                raise ValueError('ISSUER_RETIRE_UNCONFIRMED')
            print('ZEN53_APPROVAL_RETIRED')
        elif len(args)==11 and args[0]=='retire-unclaimed' and args[1::2]==[
                '--nonce','--attempt-id','--approval-sha256',
                '--terminal-ref','--cleanup-state']:
            def observe(item):
                return observe_unclaimed(op,item,launcher)
            if store.retire_unclaimed(args[2],args[4],args[6],args[8],
                                      args[10],observe) is not True:
                raise ValueError('ISSUER_RETIRE_UNCONFIRMED')
            print('ZEN53_UNCLAIMED_APPROVAL_RETIRED')
        else:
            raise ValueError('ISSUER_ARGUMENTS')
        return 0
    except BaseException:
        print('ZEN53_ISSUER_BLOCKED_NO_RETRY',file=sys.stderr)
        return 1

if __name__=='__main__':
    sys.exit(main())
