"""Read-only admission/cleanup predicates from authenticated supervisor source.

Never imports or executes the supervisor body and never stops a unit, takes a
production lock or certifies model-tool containment. Filesystem health remains a
trusted host assumption; external queries have separate strict byte/time bounds.
"""
import ast
import hashlib
import json
import os
import pathlib
import pwd
import selectors
import signal
import stat
import subprocess
import time

from .protected_inputs import need

FUNCTIONS = {'need','digest','uint64_property','idle','lock_metadata','production'}


def bounded_query(tool_fd, args):
    child=subprocess.Popen([f'/proc/self/fd/{tool_fd}',*args],stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},
        start_new_session=True,close_fds=True,pass_fds=(tool_fd,))
    sel=selectors.DefaultSelector();raw=bytearray()
    try:
        os.set_blocking(child.stdout.fileno(),False)
        sel.register(child.stdout,selectors.EVENT_READ)
        deadline=time.monotonic()+8
        while sel.get_map():
            need(time.monotonic()<deadline)
            for key,_ in sel.select(min(0.1,max(0,deadline-time.monotonic()))):
                try:chunk=os.read(key.fileobj.fileno(),4096)
                except BlockingIOError:continue
                if not chunk:sel.unregister(key.fileobj)
                else:
                    raw.extend(chunk);need(len(raw)<=262144)
        code=child.wait(timeout=max(0.001,deadline-time.monotonic()))
        need(code==0)
        return raw.decode('ascii')
    finally:
        try:
            if child.returncode is None:
                # Query group only, never used for a supervisor process.
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait(timeout=5)
        finally:
            sel.close();child.stdout.close()


def properties_output(raw, keys):
    need(type(raw) is str and len(raw)<=262144)
    rows={}
    for line in raw.splitlines():
        need('=' in line)
        key,value=line.split('=',1)
        need(key in keys and key not in rows)
        rows[key]=value
    need(set(rows)==set(keys))
    return rows


class Observer:
    def __init__(self, source, source_sha256, tool_fds, expected_production_sha256):
        need(type(source) is bytes and hashlib.sha256(source).hexdigest()==source_sha256)
        need(set(tool_fds)=={'systemctl','busctl'})
        tree=ast.parse(source.decode('ascii'))
        selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in FUNCTIONS]
        need(len(selected)==len(FUNCTIONS) and {node.name for node in selected}==FUNCTIONS)
        need(all(not node.decorator_list and not node.args.defaults and not node.args.kw_defaults for node in selected))
        constants={}
        for node in tree.body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                if node.targets[0].id in {'UNIT','USER','LOCKS'}:
                    need(node.targets[0].id not in constants)
                    constants[node.targets[0].id]=ast.literal_eval(node.value)
        need(constants['UNIT']=='zen53-mincompat.service' and constants['USER']=='zen53-mincompat')
        self.unit,self.user,self.locks=constants['UNIT'],constants['USER'],constants['LOCKS']
        self.expected_production=expected_production_sha256
        self.tools=tool_fds
        env=dict(os=os,stat=stat,pathlib=pathlib,time=time,json=json,hashlib=hashlib,
                 events=[],command=self.command,properties=self.properties)
        exec(compile(ast.Module(body=selected,type_ignores=[]),'<authenticated-supervisor-readonly>','exec'),env)
        self.env=env

    def command(self,args):
        need(type(args) is list and args and args[0] in self.tools
             and all(type(part) is str and '\x00' not in part for part in args))
        return bounded_query(self.tools[args[0]],args[1:])

    def properties(self,unit,keys):
        return properties_output(self.command(['systemctl','show',unit,'--no-pager',*['-p'+key for key in keys]]),keys)

    def _locks(self):
        rows={}
        for path,inode,uid,gid,mode in self.locks:
            for parent in pathlib.Path(path).parents:
                info=os.lstat(parent)
                need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,1000,1001) and not info.st_mode&0o022)
            info=self.env['lock_metadata'](path)
            # Exact original supervisor host binding, not an auto-discovered repin.
            need(info[0]==2049 and info[1:5]==[inode,uid,gid,mode])
            rows[path]=info
        raw=pathlib.Path('/proc/locks').read_text()
        need(len(raw)<=262144)
        wanted={(os.major(row[0]),os.minor(row[0]),row[1]) for row in rows.values()}
        for line in raw.splitlines():
            fields=line.split()
            if '->' in fields:fields.remove('->')
            need(len(fields)>=8)
            parts=fields[5].split(':')
            need(len(parts)==3)
            identity=(int(parts[0],16),int(parts[1],16),int(parts[2],10))
            need(identity not in wanted)
        return rows

    def _absent(self,stage):
        observed=self.properties(self.unit,('LoadState','ActiveState','MainPID','ControlPID','ControlGroup'))
        need(observed=={'LoadState':'not-found','ActiveState':'inactive','MainPID':'0','ControlPID':'0','ControlGroup':''})
        need(not os.path.lexists('/sys/fs/cgroup/system.slice/'+self.unit))
        try:pwd.getpwnam(self.user)
        except KeyError:pass
        else:need(False)
        for parent in (pathlib.Path('/tmp'),pathlib.Path('/var/tmp')):
            need(not list(parent.glob('systemd-private-*-'+self.unit+'-*')))
        raw=pathlib.Path('/proc/self/mountinfo').read_text()
        need(len(raw)<=262144)
        for line in raw.splitlines():
            need(line.count(' - ')==1)
            fields=line.split(' - ',1)[0].split();need(len(fields)>=6)
            path=fields[4]
            for code,char in (('\\040',' '),('\\011','\t'),('\\012','\n'),('\\134','\\')):
                path=path.replace(code,char)
            need(path!=stage and not path.startswith(stage+'/'))

    def admit(self,stage):
        self._absent(stage)
        memory=dict(line.split(':',1) for line in pathlib.Path('/proc/meminfo').read_text().splitlines())
        disk=os.statvfs(stage)
        need(int(memory['MemAvailable'].split()[0])>=1536*1024 and os.getloadavg()[0]<1.5
             and disk.f_bavail*disk.f_frsize>=2*1024**3)
        self.env['idle']()
        locks=self._locks()
        production=self.env['production']()
        raw=json.dumps(production,sort_keys=True,separators=(',',':')).encode('ascii')
        need(hashlib.sha256(raw).hexdigest()==self.expected_production)
        api=production['units']['modelfc-corner-api.service']
        need(api['ActiveState']=='active' and api['RuntimeMaxUSec']=='30min' and int(api['MainPID'])>0 and api['InvocationID'])
        age=time.monotonic_ns()//1000-int(api['ExecMainStartTimestampMonotonic'])
        need(0<=age<1_800_000_000-90_000_000)
        profiles=pathlib.Path('/sys/kernel/security/apparmor/profiles').read_text().splitlines()
        need('bwrap (enforce)' in profiles and 'unpriv_bwrap (enforce)' in profiles)
        return {'production':production,'locks':locks}

    def verify_cleanup(self,stage,baseline):
        # Fresh OS observations, never booleans taken from the worker result.
        self._absent(stage)
        need(self._locks()==baseline['locks'] and self.env['production']()==baseline['production'])
        self._absent(stage)
        return True
