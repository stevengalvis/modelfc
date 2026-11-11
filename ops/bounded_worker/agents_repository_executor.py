"""Concrete bounded source runtime over the unchanged PR267 Podman profile.

No invocation on import. Only an explicitly accepted rootless broker may use
this executor. No host mounts, task commands, image pulls, or fallback cleanup.
"""
import hashlib
import os
import select
import selectors
import subprocess
import time

from .agents_live_pilot import (CODEX_HOME_SETUP, LIVE_BOOTSTRAP, PODMAN_PREFIX,
                                LiveFailure, PodmanExecutor)
from .agents_repository_snapshot import MAX_EXPORT, container_codec_source, decode_snapshot, tree_digest

_IMPORT = '''
import os,stat,sys

def read_exact(size):
    result = bytearray()
    while len(result) < size:
        part = os.read(0,size-len(result))
        if not part: raise ValueError('source_closed')
        result.extend(part)
    return bytes(result)
size = int.from_bytes(read_exact(4),'big')
if not 0 < size <= MAX_EXPORT: raise ValueError('source_limit')
raw = read_exact(size)
obj = json.loads(raw,object_pairs_hook=pairs)
files = decode_snapshot(raw,obj['task_sha256'],obj['attempt_id'],obj['baseline_sha256'])
if tree_digest(files) != obj['baseline_sha256']: raise ValueError('source_baseline')
root = os.open('/workspace',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
info = os.fstat(root)
if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)) != (65534,65534,448) or os.listdir(root):
    raise ValueError('source_workspace')
os.umask(0o077)
for name,data in files.items():
    directory = os.dup(root)
    try:
        for part in name.split('/')[:-1]:
            try: os.mkdir(part,0o700,dir_fd=directory)
            except FileExistsError: pass
            child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(name.split('/')[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            view = memoryview(data)
            while view: view = view[os.write(fd,view):]
            os.fchmod(fd,0o644)
        finally: os.close(fd)
    finally: os.close(directory)
os.close(root)
# os.read consumed exactly the source frame. The next byte on stdin remains the
# first environment-key byte for the official registration-bearer-stdin reader.
del raw,files,data,obj
os.execve('/opt/agents/bin/codex',['/opt/agents/bin/codex',*sys.argv[1:]],dict(os.environ))
'''
_EXPORT = '''
import os,stat,sys

def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
root = os.open('/workspace',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
root_dev = os.fstat(root).st_dev
files = {}
total = 0
entries = 0

def walk(directory,prefix):
    global total,entries
    before = identity(os.fstat(directory))
    names = sorted(os.listdir(directory))
    for name in names:
        entries += 1
        if entries > MAX_FILES * 2: raise ValueError('source_entry_limit')
        path = prefix + name
        if not path_allowed(path): raise ValueError('source_path')
        info = os.stat(name,dir_fd=directory,follow_symlinks=False)
        if info.st_dev != root_dev: raise ValueError('source_mount')
        if stat.S_ISDIR(info.st_mode):
            fd = os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            try:
                if identity(os.fstat(fd)) != identity(info): raise ValueError('source_race')
                walk(fd,path+'/')
                if identity(os.stat(name,dir_fd=directory,follow_symlinks=False)) != identity(info): raise ValueError('source_race')
            finally: os.close(fd)
        else:
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 420 or info.st_size > MAX_FILE):
                raise ValueError('source_file')
            fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            try:
                if identity(os.fstat(fd)) != identity(info): raise ValueError('source_race')
                data = bytearray()
                while len(data) <= MAX_FILE:
                    chunk = os.read(fd,min(65536,MAX_FILE+1-len(data)))
                    if not chunk: break
                    data.extend(chunk)
                if (len(data) != info.st_size or identity(os.fstat(fd)) != identity(info)
                        or identity(os.stat(name,dir_fd=directory,follow_symlinks=False)) != identity(info)):
                    raise ValueError('source_race')
            finally: os.close(fd)
            total += len(data)
            if total > MAX_TREE or len(files) >= MAX_FILES: raise ValueError('source_limit')
            files[path] = bytes(data)
    if identity(os.fstat(directory)) != before or sorted(os.listdir(directory)) != names:
        raise ValueError('source_race')
try: walk(root,'')
finally: os.close(root)
raw = encode_snapshot(files,*sys.argv[1:])
view = memoryview(raw)
while view: view = view[os.write(1,view):]
'''

# Reading only this fixed trusted module's source; an installed broker verifies
# its root-owned code tree before loading it. Never discover source from tasks.
BOOTSTRAP = container_codec_source() + CODEX_HOME_SETUP + _IMPORT
EXPORTER = container_codec_source() + _EXPORT
PROFILE_SHA256 = hashlib.sha256((BOOTSTRAP + EXPORTER).encode('utf-8')).hexdigest()


def capture_bytes(argv, env, maximum, *, popen=subprocess.Popen, seconds=4):
    """Fixed-command capture. Opaque bytes only; never parse worker output here."""
    process = popen(argv, env=env, cwd='/', stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True, bufsize=0)
    output, deadline = bytearray(), time.monotonic() + seconds
    try:
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                if left <= 0 or not ready.select(left): raise LiveFailure('repository_export_timeout')
                try: chunk = os.read(process.stdout.fileno(), min(65536, maximum + 1 - len(output)))
                except BlockingIOError: continue
                if not chunk: break
                output.extend(chunk)
                if len(output) > maximum: raise LiveFailure('repository_export_limit')
        if process.wait(timeout=max(0.001,deadline-time.monotonic())) != 0:
            raise LiveFailure('repository_export_failed')
        return bytes(output)
    finally:
        process.stdout.close()
        if process.poll() is None: process.kill()  # Only exec client, never cleanup proof.
        process.wait(timeout=1)


class RepositoryExecutor(PodmanExecutor):
    """The same accepted isolation profile with a fixed source bootstrap/export."""
    def __init__(self, target, environment_key, source, task_sha256, attempt_id, baseline_sha256,
                 *, command=None, popen=None, capture=None):
        files = decode_snapshot(source, task_sha256, attempt_id, baseline_sha256)
        if tree_digest(files) != baseline_sha256: raise ValueError('source_baseline')
        super().__init__(target, environment_key, command=command, popen=popen)
        self.source = source
        self.identities = (task_sha256, attempt_id, baseline_sha256)
        self.raw_candidate = None
        self._capture = capture or capture_bytes
        self._export_attempted = False

    def _run(self, args):
        # Fixed metadata deadlines, with the full --time=5 stop grace allowed.
        # The helper retains cleanup authority even if the root's 25s watchdog
        # expires; that attempt remains uncertain, never retried or accepted.
        process = subprocess.Popen([*PODMAN_PREFIX,*args],env=self.env,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            bufsize=0,close_fds=True)
        deadline,output = time.monotonic()+(7 if args and args[0] == 'stop' else 2),bytearray()
        try:
            os.set_blocking(process.stdout.fileno(),False)
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout,selectors.EVENT_READ)
                while True:
                    left = deadline-time.monotonic()
                    if left <= 0 or not ready.select(left): raise LiveFailure('repository_metadata_timeout')
                    try: chunk = os.read(process.stdout.fileno(),65537-len(output))
                    except BlockingIOError: continue
                    if not chunk: break
                    output.extend(chunk)
                    if len(output) > 65536: raise LiveFailure('repository_metadata_limit')
            left = deadline-time.monotonic()
            if left <= 0: raise LiveFailure('repository_metadata_timeout')
            return process.wait(timeout=left),bytes(output)
        finally:
            process.stdout.close()
            if process.poll() is None: process.kill()
            process.wait(timeout=1)

    def _created_policy(self, value):
        super()._created_policy(value)
        config = value['Config']
        if value['HostConfig'].get('NetworkMode') != 'slirp4netns:allow_host_loopback=false':
            raise LiveFailure('repository_network_profile_unverified')
        # Actual Podman 4.9.3 inspect serializes Entrypoint as a string.
        if (config.get('Entrypoint') != '/usr/local/bin/python3'
                or config.get('Cmd') != self.create_arguments(self.binding)[
                    self.create_arguments(self.binding).index('sha256:'+self.target.image_id)+1:]):
            raise LiveFailure('repository_bootstrap_changed')

    def create_arguments(self, binding):
        if binding.attempt_id != self.identities[1]: raise ValueError('source_attempt_mismatch')
        args = super().create_arguments(binding)
        index = args.index(LIVE_BOOTSTRAP)
        args[index] = BOOTSTRAP
        return args

    def start(self, binding):
        # Same PR267 sequence and held-cgroup proof; only the stdin frame differs.
        if self.binding is not None: raise LiveFailure('container_start_replayed')
        self.binding = binding
        code, _ = self._command(['container','exists','agents-'+binding.attempt_id])
        if code != 1: raise LiveFailure('container_name_not_available')
        self._create_attempted = True
        code, raw = self._command(self.create_arguments(binding))
        import re
        if code or re.fullmatch(rb'[a-f0-9]{64}\n?',raw) is None:
            raise LiveFailure('container_create_uncertain')
        self.container_id = raw.decode('ascii').strip()
        self._created_policy(self._inspect())
        self._start_attempted = True
        self.process = self._popen([*PODMAN_PREFIX,'start','--attach','--interactive',self.container_id],
            env=self.env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            bufsize=0,close_fds=True)
        data = memoryview(len(self.source).to_bytes(4,'big') + self.source +
                          (self._environment_key+'\n').encode('ascii'))
        self._environment_key = self.source = None
        deadline = time.monotonic() + 15
        try:
            fd = self.process.stdin.fileno()
            os.set_blocking(fd,False)
            while data:
                left = deadline-time.monotonic()
                if left <= 0 or not select.select([],[fd],[],left)[1]:
                    raise LiveFailure('repository_input_timeout')
                try: data = data[os.write(fd,data):]
                except BlockingIOError: continue
        finally: self.process.stdin.close()
        while time.monotonic() < deadline:
            value = self._inspect()
            if value['State']['Running'] is True:
                self._capture_cgroup(value)
                return
            if self.process.poll() is not None: break
            time.sleep(0.05)
        raise LiveFailure('container_running_unverified')

    def stop(self, binding):
        if binding != self.binding or self._export_attempted:
            raise LiveFailure('repository_stop_not_admitted')
        self._export_attempted = True
        try:
            if self.cgroup_fd is not None:
                self.raw_candidate = self._capture([*PODMAN_PREFIX,'exec','--user=65534:65534',
                    '--workdir=/workspace',self.container_id,'/usr/local/bin/python3','-I','-S','-B','-c',
                    EXPORTER,*self.identities], self.env, MAX_EXPORT)
        finally:
            super().stop(binding)  # Export failure never bypasses actual container stop.
