"""Fixed root-supervisor to nonroot executor handoff, never a command service.

The protected supervisor owns application credentials, approval, source baseline
and SQLite. This single-attempt broker receives only source data, an explicit
restricted environment key and validated lifecycle identities. EOF/crash is an
uncertain attempt, not permission to launch a replacement.
"""
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time

from .agents_live_pilot import PreparedTarget, _key
from .agents_repository_executor import PROFILE_SHA256, RepositoryExecutor
from .agents_repository_snapshot import MAX_EXPORT, canonical, pairs
from .agents_session_controller import ExecutorBinding
from .executor_entry import connection_arguments
import json

MAX_FRAME = MAX_EXPORT + 1048576
CODE_FILES = ('__init__.py', 'task.py', 'executor_entry.py', 'agents_admission.py',
    'agents_pilot.py', 'agents_session_controller.py', 'agents_connection.py',
    'agents_live_pilot.py', 'agents_repository_snapshot.py', 'agents_repository_executor.py',
    'agents_repository_broker.py', 'agents_json_oracle.py',
    'agents_nested_acceptance.py', 'agents_credentials.py', 'agents_terminal_pilot.py')
BROKER_BOOT = "import sys; sys.path.insert(0,sys.argv[1]); from ops.bounded_worker.agents_repository_broker import serve; serve()"


@dataclass(frozen=True)
class BrokerSpec:
    uid: int
    gid: int
    code_root: str
    code_sha256: str
    python_sha256: str
    supervisor_sha256: str
    target: PreparedTarget
    profile_sha256: str = PROFILE_SHA256

    def validate(self):
        self.target.validate()
        if (any(type(v) is not int or not 1 <= v <= 2147483647 for v in (self.uid,self.gid))
                or type(self.code_root) is not str or not re.fullmatch(r'/[A-Za-z0-9_./-]+',self.code_root)
                or '..' in Path(self.code_root).parts
                or any(type(v) is not str or not re.fullmatch(r'[a-f0-9]{64}',v)
                       for v in (self.code_sha256,self.python_sha256,self.supervisor_sha256,self.profile_sha256))
                or self.profile_sha256 != PROFILE_SHA256):
            raise ValueError('broker_spec_rejected')

    @property
    def runtime_sha256(self):
        self.validate()
        return hashlib.sha256(canonical(asdict(self))).hexdigest()


def protected_directory(path):
    path = Path(path)
    for part in (*reversed(path.parents),path):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('broker_code_directory_unprotected')
    return path


def protected_file(path, maximum):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
            or before.st_mode & 0o022 or before.st_size > maximum):
        raise ValueError('broker_code_file_unprotected')
    def ident(info):
        return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
                info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as source:
        if ident(os.fstat(source.fileno())) != ident(before): raise ValueError('broker_code_changed')
        raw = source.read(maximum+1)
        if (len(raw) != before.st_size or ident(os.fstat(source.fileno())) != ident(before)
                or ident(path.lstat()) != ident(before)):
            raise ValueError('broker_code_changed')
    return hashlib.sha256(raw).hexdigest()


def verify_installation(spec):
    """Dedicated minimal root-owned installation, no writable caches/import peers."""
    spec.validate()
    root = protected_directory(spec.code_root)
    if set(os.listdir(root)) != {'ops'} or set(os.listdir(root/'ops')) != {'bounded_worker'}:
        raise ValueError('broker_code_layout')
    package = protected_directory(root/'ops'/'bounded_worker')
    if set(os.listdir(package)) != set(CODE_FILES): raise ValueError('broker_code_layout')
    records = [(name,protected_file(package/name,1048576)) for name in CODE_FILES]
    if hashlib.sha256(canonical(records)).hexdigest() != spec.code_sha256:
        raise ValueError('broker_code_pin')
    # Resolve the operator-owned system alias, then verify every resolved parent.
    interpreter = Path('/usr/bin/python3').resolve(strict=True)
    protected_directory(interpreter.parent)
    if protected_file(interpreter,33554432) != spec.python_sha256:
        raise ValueError('broker_python_pin')
    return str(interpreter)


def _transfer(fd, size, deadline, *, data=None):
    result = bytearray()
    while size:
        left = deadline-time.monotonic()
        if left <= 0: raise ValueError('broker_pipe_timeout')
        ready = select.select([fd] if data is None else [],[] if data is None else [fd],[],left)
        if not ready[0 if data is None else 1]: raise ValueError('broker_pipe_timeout')
        try:
            if data is None:
                part = os.read(fd,min(size,65536))
                if not part: raise ValueError('broker_pipe_closed')
                result.extend(part)
                size -= len(part)
            else:
                count = os.write(fd,data[:min(size,65536)])
                if not count: raise ValueError('broker_pipe_closed')
                data = data[count:]
                size -= count
        except BlockingIOError:
            continue
    return bytes(result)


def write_frame(fd, raw, deadline):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME: raise ValueError('broker_frame_limit')
    data = len(raw).to_bytes(4,'big') + raw
    _transfer(fd,len(data),deadline,data=memoryview(data))


def read_frame(fd, deadline):
    size = int.from_bytes(_transfer(fd,4,deadline),'big')
    if not 0 < size <= MAX_FRAME: raise ValueError('broker_frame_limit')
    return _transfer(fd,size,deadline)


def _object(raw):
    value = json.loads(raw,object_pairs_hook=pairs)
    if type(value) is not dict or canonical(value) != raw: raise ValueError('broker_frame_shape')
    return value


class BrokerOperationFailure(RuntimeError):
    """Known negative acknowledgment, with validated aligned protocol identity."""


class RootlessBroker:
    """Explicit concrete launch. Construction alone does not invoke OS commands."""
    def __init__(self,spec, *, popen=None, installation_check=None):
        spec.validate()
        self.spec = spec
        self._popen = popen or subprocess.Popen
        self._installation_check = installation_check or verify_installation
        self.process = self.binding = self.raw_candidate = None
        self.phase = 'new'
        self.closed = False
        self.close_result = False
        self.sequence = 0
        self.uncertain = False

    def _call(self, message, seconds):
        if self.uncertain: raise ValueError('broker_protocol_uncertain')
        self.sequence += 1
        deadline = time.monotonic()+seconds
        try:
            write_frame(self.process.stdin.fileno(),canonical(message | {'sequence':self.sequence}),deadline)
            response = _object(read_frame(self.process.stdout.fileno(),deadline))
            identity = {'op':message['op'],'sequence':self.sequence}
            if type(response.get('sequence')) is not int or type(response.get('ok')) is not bool:
                raise ValueError('broker_response_types')
            if response == identity | {'ok':False}:
                raise BrokerOperationFailure('broker_operation_failed')  # Known response, framing is aligned.
            if response != identity | {'ok':True}:
                raise ValueError('broker_response_binding')
        except BrokerOperationFailure:
            raise
        except BaseException:
            self.uncertain = True
            raise

    def _payload(self,kind,seconds):
        try:
            raw = read_frame(self.process.stdout.fileno(),time.monotonic()+seconds)
            prefix = kind + self.sequence.to_bytes(8,'big')
            if not raw.startswith(prefix): raise ValueError('broker_payload_binding')
            return raw[len(prefix):]
        except BaseException:
            self.uncertain = True
            raise

    def prepare(self,source,environment_key,identities):
        if self.phase != 'new' or os.getuid() != 0 or os.geteuid() != 0:
            raise ValueError('root_broker_supervisor_required')
        self.phase = 'preparing'  # Reserve before Popen; a failed launch is never retried.
        _key(environment_key)
        executable = self._installation_check(self.spec)
        self.process = self._popen([executable,'-I','-S','-B','-c',BROKER_BOOT,self.spec.code_root],
            user=self.spec.uid,group=self.spec.gid,extra_groups=(),cwd='/',
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        os.set_blocking(self.process.stdin.fileno(),False)
        os.set_blocking(self.process.stdout.fileno(),False)
        self._call(dict(op='prepare',spec=asdict(self.spec),source=source.decode('ascii'),
                        environment_key=environment_key,identities=list(identities)),25)
        self.phase = 'prepared'

    def start(self,binding):
        if self.phase != 'prepared': raise ValueError('broker_start_replayed')
        self.phase,self.binding = 'starting',binding
        self._call(dict(op='start',binding=asdict(binding)),25)
        self.phase = 'started'

    def stop(self,binding):
        if self.phase not in ('starting','started') or binding != self.binding:
            raise ValueError('broker_stop_replayed')
        self.phase = 'stopping'
        if self.uncertain:
            # A timed-out start may still be in flight. Queue the one fixed stop,
            # without consuming misaligned responses or ever claiming cleanup.
            self.sequence += 1
            write_frame(self.process.stdin.fileno(),canonical(dict(op='stop',sequence=self.sequence)),
                        time.monotonic()+1)
            raise ValueError('broker_cleanup_outcome_uncertain')
        self._call(dict(op='stop'),22)
        self.phase = 'stopped'

    def verify_empty(self,binding):
        if self.phase not in ('stopping','stopped') or binding != self.binding:
            return False
        self._call(dict(op='empty'),8)
        # No source parsing here. The broker sends raw bytes only after its
        # original held cgroup proves stopped and empty/offline.
        raw = self._payload(b'S',5)
        self.raw_candidate = None if raw == b'\x00' else raw
        self.phase = 'empty'
        return True

    def run_tests(self, target, plan, stage, files, policy, test_attempt_id):
        if self.phase != 'empty': raise ValueError('broker_tests_not_admitted')
        from .agents_repository_snapshot import encode_snapshot
        self.phase = 'testing'  # Never retry a reserved oracle operation.
        source = encode_snapshot(files,stage['task_sha256'],stage['attempt_id'],stage['candidate_tree_sha256'])
        self._call(dict(op='test',target=asdict(target),plan=asdict(plan),stage=stage,
            source=source.decode('ascii'),policy=policy.raw.decode('ascii'),test_attempt_id=test_attempt_id),
            plan.max_seconds+15)
        response = _object(self._payload(b'T',5))
        if set(response) != {'receipt','evidence'}: raise ValueError('broker_test_response')
        self.phase = 'tested'
        return response['receipt'],response['evidence']

    def close(self):
        if self.closed: return self.close_result
        self.closed = True
        if self.process is None:
            self.close_result = True
            return True
        # EOF asks the trusted broker to finish its one bounded cleanup. Never
        # kill the sole cleanup authority after an RPC timeout. Exit is resource
        # closure only; it does not set phase=empty or prove container cleanup.
        try:
            self.process.stdin.close()
            self.process.stdout.close()
            self.close_result = self.process.wait(timeout=35) == 0
        except BaseException:
            self.close_result = False
        return self.close_result


def serve():
    """Dedicated child entry. No generic argv, filesystem or subprocess RPC."""
    executor,phase,binding = None,'new',None
    sequence = 0
    try:
        if (os.getuid() == 0 or os.geteuid() != os.getuid() or os.getgid() != os.getegid()
                or os.getgroups() or dict(os.environ) != {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}):
            raise ValueError('broker_identity')
        for name in os.listdir('/proc/self/fd'):
            if int(name) > 2:
                try: os.fstat(int(name))
                except OSError: continue
                raise ValueError('broker_descriptor')
        for fd in (0,1):
            if not stat.S_ISFIFO(os.fstat(fd).st_mode): raise ValueError('broker_pipe')
            os.set_blocking(fd,False)
        while True:
            request = _object(read_frame(0,time.monotonic()+210))
            sequence += 1
            if type(request.get('sequence')) is not int or request.pop('sequence') != sequence:
                raise ValueError('broker_request_binding')
            op = request.get('op')
            reply = {'op':op,'sequence':sequence}
            try:
                if op == 'prepare' and phase == 'new' and set(request) == {
                        'op','spec','source','environment_key','identities'}:
                    phase = 'preparing'
                    spec_value = request['spec']
                    spec = BrokerSpec(**(spec_value | {'target':PreparedTarget(**spec_value['target'])}))
                    spec.validate()
                    if (os.getuid(),os.getgid()) != (spec.uid,spec.gid): raise ValueError('broker_identity')
                    verify_installation(spec)
                    identities = request['identities']
                    if type(identities) is not list or len(identities) != 3: raise ValueError('broker_source_binding')
                    executor = RepositoryExecutor(spec.target,request['environment_key'],
                        request['source'].encode('ascii'),*identities)
                    executor.verify_prepared()
                    phase = 'prepared'
                elif op == 'start' and phase == 'prepared' and set(request) == {'op','binding'}:
                    phase = 'starting'
                    binding = ExecutorBinding(**request['binding'])
                    connection_arguments('/opt/agents/bin/codex',binding.environment_id,binding.remote_url)
                    if (binding.attempt_id != executor.identities[1]
                            or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',binding.session_id)):
                        raise ValueError('broker_binding')
                    executor.start(binding)
                    phase = 'started'
                elif op == 'stop' and phase in ('starting','started') and set(request) == {'op'}:
                    phase = 'stopping'
                    try: executor.stop(binding)
                    finally: phase = 'stopped'
                elif op == 'empty' and phase == 'stopped' and set(request) == {'op'}:
                    if executor.verify_empty(binding) is not True: raise ValueError('broker_cleanup_unverified')
                    write_frame(1,canonical(reply | {'ok':True}),time.monotonic()+5)
                    write_frame(1,b'S'+sequence.to_bytes(8,'big')+(executor.raw_candidate if executor.raw_candidate is not None else b'\x00'),
                                time.monotonic()+5)
                    phase = 'empty'
                    continue
                elif op == 'test' and phase == 'empty' and set(request) == {
                        'op','target','plan','stage','source','policy','test_attempt_id'}:
                    phase = 'testing'
                    from .agents_json_oracle import JSONCallPolicy, decode_plan, run_oracle
                    from .agents_repository_snapshot import decode_snapshot
                    target = PreparedTarget(**request['target'])
                    # Same approved account/runtime, independently approved test image/profile.
                    if (target.runtime_home,target.runtime_dir) != (spec.target.runtime_home,spec.target.runtime_dir):
                        raise ValueError('oracle_runtime_changed')
                    plan = decode_plan(request['plan'])
                    stage = request['stage']
                    files = decode_snapshot(request['source'].encode('ascii'),stage['task_sha256'],
                                            stage['attempt_id'],stage['candidate_tree_sha256'])
                    receipt,evidence = run_oracle(target,plan,stage,files,
                        JSONCallPolicy(request['policy'].encode('ascii')),request['test_attempt_id'])
                    response = canonical(dict(receipt=receipt,evidence=evidence))
                    if len(response) > 65536: raise ValueError('oracle_evidence_limit')
                    write_frame(1,canonical(reply | {'ok':True}),time.monotonic()+5)
                    write_frame(1,b'T'+sequence.to_bytes(8,'big')+response,time.monotonic()+5)
                    phase = 'tested'
                    continue
                else:
                    raise ValueError('broker_operation_rejected')
                request = None
                write_frame(1,canonical(reply | {'ok':True}),time.monotonic()+5)
            except BaseException:
                request = None
                write_frame(1,canonical(reply | {'ok':False}),time.monotonic()+1)
                # Retain original executor/cgroup objects after partial start or
                # failed stop so the one permitted cleanup can still run.
                if phase not in ('starting','stopped'):
                    return
    except BaseException:
        pass  # Closed pipe, invalid framing or crash is uncertain, never success.
    finally:
        if executor is not None:
            # A parent failure/EOF cannot abandon a running container. This is
            # the same one-shot stop, not a replay of an uncertain prior stop.
            if executor._create_attempted:
                if not executor._stop_attempted:
                    try: executor.stop(executor.binding)
                    except BaseException: pass
                try: executor.verify_empty(executor.binding)
                except BaseException: pass
            executor.close()
