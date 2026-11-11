"""Explicit no-key/no-provider source-transfer acceptance fixture.

Importing or building commands starts nothing. Calling run_source_acceptance
performs real container work only with separately authorized installed inputs.
The probe replaces live registration and networking; it cannot attest either.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from .agents_live_pilot import PODMAN_PREFIX, PodmanExecutor, LiveFailure, _bounded_call
from .agents_nested_acceptance import MOUNT_CHECK
from .agents_json_oracle import OraclePipe
from .agents_repository_broker import verify_installation
from .agents_repository_executor import BOOTSTRAP, EXPORTER, PROFILE_SHA256, RepositoryExecutor
from .agents_repository_runtime import verify_supervisor
from .agents_repository_snapshot import MAX_EXPORT, canonical, decode_snapshot, encode_snapshot, tree_digest
from .agents_session_controller import ExecutorBinding

FIXTURE_FILES = {'src/source_fixture.py': b'VALUE = 0\n',
                 'assets/raw.bin': b'\x89PNG\r\n\x1a\n\x00\xff\xfe' + bytes(range(256))}
EXPECTED_FILES = FIXTURE_FILES | {'src/source_fixture.py': b'VALUE = 42'}
SOURCE_TREE_SHA256 = tree_digest(FIXTURE_FILES)
EXPECTED_TREE_SHA256 = tree_digest(EXPECTED_FILES)
TASK_SHA256 = hashlib.sha256(b'fixed-no-key-repository-source-acceptance-v1').hexdigest()
MARKER = b'fixture-marker-not-a-credential-v1\n'
IMPORTED = b'SOURCE_IMPORTED_AND_MARKER_VERIFIED\n'
OUTPUT_LIMIT = 4096
MAX_SECONDS = 90

# Reuse exactly the live prefix, fail closed if its terminal registration call
# changes. The probe's replacement is explicit and separately content-pinned.
_REGISTRATION = "os.execve('/opt/agents/bin/codex',['/opt/agents/bin/codex',*sys.argv[1:]],dict(os.environ))\n"
if not BOOTSTRAP.endswith(_REGISTRATION):
    raise ValueError('live_bootstrap_suffix_changed')
IMPORT_PREFIX = BOOTSTRAP[:-len(_REGISTRATION)]
_READY = '''import os,signal,sys,time
''' + MOUNT_CHECK + '''
status = _mount_check()
print("NESTED_READY:" + status,flush=True)
if status != "verified": raise SystemExit(74)
'''
_TAIL = '''
# The source reader must leave the following fake registration marker untouched.
if read_exact(''' + str(len(MARKER)) + ''') != ''' + repr(MARKER) + ''': raise SystemExit(75)
if os.read(0,1) != b'': raise SystemExit(75)
# Fixed source edit only. No staged Python is imported or executed.
fd = os.open('/workspace/src/source_fixture.py',os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW)
try:
    data = memoryview(b'VALUE = 42')
    while data: data = data[os.write(fd,data):]
finally: os.close(fd)
def stopped(signum,frame):
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stopped)
print('SOURCE_IMPORTED_AND_MARKER_VERIFIED',flush=True)
# Keep tmpfs alive for the EXACT live exporter. Only the parent's one stop ends
# the fixture; this is not a simulated Codex registration or successful API turn.
while True: signal.pause()
'''
PROBE_BOOTSTRAP = _READY + IMPORT_PREFIX + _TAIL
PROBE_PROFILE = dict(version=1, network='none', no_provider=True,
    live_bootstrap_sha256=hashlib.sha256(BOOTSTRAP.encode()).hexdigest(),
    import_prefix_sha256=hashlib.sha256(IMPORT_PREFIX.encode()).hexdigest(),
    probe_bootstrap_sha256=hashlib.sha256(PROBE_BOOTSTRAP.encode()).hexdigest(),
    exporter_sha256=hashlib.sha256(EXPORTER.encode()).hexdigest(),
    source_tree_sha256=SOURCE_TREE_SHA256, expected_tree_sha256=EXPECTED_TREE_SHA256,
    marker_sha256=hashlib.sha256(MARKER).hexdigest(), output_limit=OUTPUT_LIMIT,
    reused_live_profile_sha256=PROFILE_SHA256)
PROBE_PROFILE_SHA256 = hashlib.sha256(canonical(PROBE_PROFILE)).hexdigest()


def _hex(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}',value) is not None


@dataclass(frozen=True)
class SourceAcceptancePlan:
    runtime_sha256: str
    image_sha256: str
    runtime_acceptance_sha256: str
    live_profile_sha256: str
    probe_profile_sha256: str
    source_tree_sha256: str
    max_seconds: int = MAX_SECONDS
    enabled: bool = False

    def validate(self,spec):
        spec.validate()
        if (any(not _hex(value) for name,value in asdict(self).items() if name.endswith('sha256'))
                or self.runtime_sha256 != spec.runtime_sha256
                or self.image_sha256 != spec.target.image_id
                or self.runtime_acceptance_sha256 != spec.target.acceptance_id
                or self.live_profile_sha256 != PROFILE_SHA256
                or self.probe_profile_sha256 != PROBE_PROFILE_SHA256
                or self.source_tree_sha256 != SOURCE_TREE_SHA256
                or type(self.max_seconds) is not int or self.max_seconds != MAX_SECONDS
                or type(self.enabled) is not bool or self.enabled is not True):
            raise ValueError('source_acceptance_not_approved')

    @property
    def sha256(self):
        return hashlib.sha256(canonical(asdict(self))).hexdigest()


def _running_python_sha256():
    """Hash this running Linux executable, not just its replaceable pathname."""
    # This fixed procfs magic link identifies the running image. O_NOFOLLOW
    # would reject that kernel link; no caller supplies or chooses this path.
    fd = os.open('/proc/self/exe',os.O_RDONLY|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_mode & 0o022
                or before.st_nlink != 1 or not 0 < before.st_size <= 33554432):
            raise ValueError('source_acceptance_running_python')
        def identity(info):
            return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
                    info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        digest,total = hashlib.sha256(),0
        while chunk := os.read(fd,65536):
            total += len(chunk)
            if total > 33554432: raise ValueError('source_acceptance_python_limit')
            digest.update(chunk)
        if total != before.st_size or identity(os.fstat(fd)) != identity(before):
            raise ValueError('source_acceptance_python_changed')
        return digest.hexdigest()
    finally: os.close(fd)


def _private_root(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('acceptance_evidence_path')
    for entry in (*reversed(path.parents),path):
        info = entry.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.geteuid())
                or info.st_mode & 0o022):
            raise ValueError('acceptance_evidence_unprotected')
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('acceptance_evidence_unprotected')
    return path


def _write_new(path,raw):
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    fd = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _captured_evidence(executor,observed):
    """Identity of already-held objects only; never reopen or adopt a path."""
    state = observed['State']
    raw = state.get('CgroupPath')
    if (type(raw) is not str or not raw.startswith('/') or len(raw.encode('utf-8')) > 4096
            or any(c in raw for c in ('\x00','\n','\r')) or type(state.get('Pid')) is not int
            or state['Pid'] <= 0):
        raise ValueError('source_probe_scope_evidence')
    scope = raw if raw.startswith('/sys/fs/cgroup/') else '/sys/fs/cgroup'+raw
    if Path(scope).name != 'libpod-'+executor.container_id+'.scope':
        raise ValueError('source_probe_scope_binding')
    def held(fd,directory):
        if type(fd) is not int or fd < 0: raise ValueError('source_probe_held_object')
        info = os.fstat(fd)
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ValueError('source_probe_held_type')
        values = dict(device=info.st_dev,inode=info.st_ino,mode=info.st_mode,nlink=info.st_nlink)
        if any(type(v) is not int or not 0 <= v < 2**64 for v in values.values()) or values['inode'] == 0:
            raise ValueError('source_probe_held_identity')
        return values
    resources = canonical(executor.resource_diagnostics)
    if len(resources) > 4096: raise ValueError('source_probe_resource_evidence_limit')
    return dict(scope_path=scope,pid=state['Pid'],held_scope=held(executor.cgroup_fd,True),
        held_events=held(executor.cgroup_events_fd,False),mandatory_events_were_live=True,
        resources=json.loads(resources))


class SourceProbeExecutor(RepositoryExecutor):
    """Same source importer/exporter and cgroup proof, explicitly different probe."""
    def create_arguments(self,binding):
        args = super().create_arguments(binding)
        old = '--network=slirp4netns:allow_host_loopback=false'
        if args.count(old) != 1: raise LiveFailure('probe_profile_changed')
        args[args.index(old)] = '--network=none'
        image = args.index('sha256:'+self.target.image_id)
        return args[:image+1] + ['-I','-S','-B','-c',PROBE_BOOTSTRAP]

    def _created_policy(self,value):
        # All original resource/private/no-mount rules, but not a claim of live
        # network/registration equivalence. Check the exact altered probe too.
        PodmanExecutor._created_policy(self,value)
        if (value['HostConfig'].get('NetworkMode') != 'none'
                or value['Config'].get('Entrypoint') != '/usr/local/bin/python3'
                or value['Config'].get('Cmd') != ['-I','-S','-B','-c',PROBE_BOOTSTRAP]):
            raise LiveFailure('source_probe_policy_rejected')


class SourceProbePipe(OraclePipe):
    def wait_imported(self,deadline):
        while b'\n' not in self.buffer:
            if 'stdout' in self.eof: raise LiveFailure('source_probe_early_eof')
            self.pump(deadline)
        if bytes(self.buffer) != IMPORTED or self.stderr:
            raise LiveFailure('source_probe_output_rejected')

    def drain_after_stop(self,deadline):
        while self.ready.get_map(): self.pump(deadline)
        left = deadline-time.monotonic()
        if left <= 0: raise LiveFailure('source_probe_eof_timeout')
        code = self.process.wait(timeout=left)
        # PR267 stop may terminate the attach client after acknowledged stop.
        # Neither this client exit nor EOF proves container cleanup; the held
        # cgroup and separately inspected container exit state remain mandatory.
        if type(code) is not int or code not in (0,-15) or bytes(self.buffer) != IMPORTED or self.stderr:
            raise LiveFailure('source_probe_attach_rejected')
        return code


def run_source_acceptance(spec,plan,attempt_id,evidence_root,*,command=None,popen=None,capture=None):
    """EXPLICIT OS RUN after separate permission; no credentials or API calls.

    The selected user/image/runtime must already exist. This does not install,
    reconfigure, launch the broker, or authorize later live registration. Offline
    tests replace all OS/process and protected-installation boundaries.
    """
    plan.validate(spec)
    if not _hex(attempt_id): raise ValueError('source_acceptance_attempt')
    if (os.getuid(),os.geteuid(),os.getgid(),os.getegid(),os.getgroups()) != (
            spec.uid,spec.uid,spec.gid,spec.gid,[]):
        raise ValueError('source_acceptance_identity')
    interpreter = verify_installation(spec)
    if (Path(sys.executable).resolve() != Path(interpreter)
            or _running_python_sha256() != spec.python_sha256):
        raise ValueError('source_acceptance_python_identity')
    verify_supervisor(spec)
    started = time.monotonic()
    root = _private_root(evidence_root)
    evidence = root/attempt_id
    evidence.mkdir(mode=0o700)  # Exclusive reservation before any OS command.
    root_fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(root_fd)
    finally: os.close(root_fd)
    _write_new(evidence/'reservation.json',canonical(dict(version=1,attempt_id=attempt_id,
        plan_sha256=plan.sha256,runtime_sha256=spec.runtime_sha256,probe_profile_sha256=PROBE_PROFILE_SHA256)))
    source = encode_snapshot(FIXTURE_FILES,TASK_SHA256,attempt_id,SOURCE_TREE_SHA256)
    binding = ExecutorBinding(attempt_id,'source_acceptance','no_environment_key',
                              'https://api.openai.com/never-called')
    executor = SourceProbeExecutor(spec.target,'synthetic-no-key',source,TASK_SHA256,attempt_id,
        SOURCE_TREE_SHA256,command=command,popen=popen,capture=capture)
    # This constructor-only placeholder is never placed on stdin, argv or env.
    executor._environment_key = None
    report = dict(version=1,status='blocked',stage='preflight',failure=None,failed_stage=None,
        started_unix_ms=int(time.time()*1000),python_sha256=spec.python_sha256,
        attempt_id=attempt_id,container_id=None,plan_sha256=plan.sha256,
        runtime_sha256=spec.runtime_sha256,image_sha256=spec.target.image_id,
        runtime_acceptance_sha256=spec.target.acceptance_id,
        live_profile_sha256=PROFILE_SHA256,probe_profile_sha256=PROBE_PROFILE_SHA256,
        source_sha256=hashlib.sha256(source).hexdigest(),source_tree_sha256=SOURCE_TREE_SHA256,
        source_files=len(FIXTURE_FILES),source_decoded_bytes=sum(map(len,FIXTURE_FILES.values())),
        expected_tree_sha256=EXPECTED_TREE_SHA256,profile=PROBE_PROFILE.copy(),
        source_sent=False,marker_verified=False,original_cgroup_sha256=None,
        export_captured=False,export_sha256=None,
        export_bytes=0,export_matches=False,stop_acknowledged=False,cleanup_verified=False,
        stdout_eof=False,stderr_eof=False,attach_exit_code=None,container_exit_code=None,
        output_bytes=0,stdout_sha256=None,stderr_sha256=None,elapsed_ms=0,
        live_registration_tested=False,live_network_tested=False,nested_codex_tested=False,
        root_broker_handoff_tested=False,provider_operations=0)
    pipe = None
    try:
        def perform():
            nonlocal pipe
            executor.verify_prepared()
            executor.binding = binding
            code,_ = executor._command(['container','exists','agents-'+attempt_id])
            if code != 1: raise LiveFailure('source_probe_name_unavailable')
            report['stage'] = 'create'
            executor._create_attempted = True
            code,raw = executor._command(executor.create_arguments(binding))
            if code or re.fullmatch(rb'[a-f0-9]{64}\n?',raw) is None:
                raise LiveFailure('source_probe_create_uncertain')
            executor.container_id = raw.decode('ascii').strip()
            executor._created_policy(executor._inspect())
            report['stage'] = 'ready'
            executor._start_attempted = True
            executor.process = executor._popen([*PODMAN_PREFIX,'start','--attach','--interactive',executor.container_id],
                env=executor.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                bufsize=0,close_fds=True)
            pipe = SourceProbePipe(executor.process,OUTPUT_LIMIT)
            deadline = time.monotonic()+15
            pipe.wait_ready(deadline)
            observed = executor._inspect()
            if observed['State'].get('Running') is not True: raise LiveFailure('source_probe_not_running')
            executor._capture_cgroup(observed)
            if executor.cgroup_fd is None or executor.cgroup_events_fd is None:
                raise LiveFailure('source_probe_cgroup_unverified')
            current = executor._inspect()
            if (current['State'].get('Running') is not True
                    or current['State'].get('Pid') != observed['State'].get('Pid')
                    or current['State'].get('CgroupPath') != observed['State'].get('CgroupPath')):
                raise LiveFailure('source_probe_identity_changed')
            original_cgroup = _captured_evidence(executor,observed)
            _write_new(evidence/'container.json',canonical(dict(version=1,attempt_id=attempt_id,
                container_id=executor.container_id,plan_sha256=plan.sha256,original_cgroup=original_cgroup)))
            report['original_cgroup_sha256'] = hashlib.sha256(canonical(original_cgroup)).hexdigest()
            report['stage'] = 'source'
            report['source_sent'] = True  # Reservation of the one write, not acknowledgment.
            pipe.send(len(source).to_bytes(4,'big')+source+MARKER,deadline)
            pipe.wait_imported(deadline)
            report['marker_verified'] = True
        _bounded_call(perform,30)
    except BaseException:
        report['failed_stage'] = report['stage']
        report['failure'] = 'probe_work_unverified'
    finally:
        report['stage'] = 'cleanup'
        if executor._create_attempted:
            try:
                _bounded_call(lambda:executor.stop(binding),35)  # Includes exact export before stop.
                report['stop_acknowledged'] = True
            except BaseException:
                report['failure'] = report['failure'] or 'export_or_stop_unverified'
            try: report['cleanup_verified'] = _bounded_call(lambda:executor.verify_empty(binding),10) is True
            except BaseException: report['failure'] = report['failure'] or 'cleanup_unverified'
        report['container_id'] = executor.container_id
        if pipe is not None:
            try: report['attach_exit_code'] = pipe.drain_after_stop(time.monotonic()+5)
            except BaseException: report['failure'] = report['failure'] or 'output_eof_unverified'
            report.update(stdout_eof='stdout' in pipe.eof,stderr_eof='stderr' in pipe.eof,
                output_bytes=pipe.total,stdout_sha256=hashlib.sha256(bytes(pipe.buffer)).hexdigest(),
                stderr_sha256=hashlib.sha256(bytes(pipe.stderr)).hexdigest())
            try: pipe.close()
            except BaseException: report['failure'] = report['failure'] or 'pipe_close_unverified'
        try: executor.close()
        except BaseException: report['failure'] = report['failure'] or 'executor_close_unverified'
    # No inspection/parsing of snapshot bytes until the independent original
    # held-cgroup readback has proved empty/offline. No success from a probe flag.
    raw = executor.raw_candidate
    if type(raw) is bytes and len(raw) <= MAX_EXPORT:
        report.update(export_captured=True,export_sha256=hashlib.sha256(raw).hexdigest(),export_bytes=len(raw))
        _write_new(evidence/'candidate-export.json',raw)  # Opaque retention even if cleanup is unknown.
    elif raw is not None:
        report['failure'] = report['failure'] or 'export_limit_unverified'
    if report['cleanup_verified']:
        try:
            after = executor.cleanup_diagnostics['after_stop']
            if after is None or after['running'] is not False or after['exit_code'] != 0 or after['oom_killed'] is not False:
                raise ValueError('source_probe_exit_unverified')
            report['container_exit_code'] = after['exit_code']
            files = decode_snapshot(raw,TASK_SHA256,attempt_id,SOURCE_TREE_SHA256)
            if files != EXPECTED_FILES: raise ValueError('source_probe_export_mismatch')
            report['export_matches'] = True
        except BaseException:
            report['failure'] = report['failure'] or 'export_or_exit_unverified'
    report['elapsed_ms'] = max(0,int((time.monotonic()-started)*1000))
    if (report['failure'] is None and report['marker_verified'] and report['export_matches']
            and report['stop_acknowledged'] and report['cleanup_verified']
            and report['stdout_eof'] and report['stderr_eof'] and report['elapsed_ms'] <= MAX_SECONDS*1000):
        report['status'] = 'passed'
    report['stage'] = 'finished'
    _write_new(evidence/'report.json',canonical(report))
    return report
