"""Fixed Python/JSON-call oracle, independently asserted outside the candidate.

Explicit OS entry only. Import and policy parsing perform no OS/provider action.
The root supervisor must pin this helper, authorize its immutable inputs, and
persist its receipt; this module never adopts a task-selected command or test.
Only the no-network container imports candidate source. It receives arguments,
not expected values or pass/fail assertions. Its entire output is untrusted.
"""
import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import re
import select
import selectors
import subprocess
import time

from .agents_live_pilot import PODMAN_PREFIX, PodmanExecutor, LiveFailure
from .agents_nested_acceptance import MOUNT_CHECK
from .agents_session_controller import ExecutorBinding
from .agents_repository_snapshot import (container_codec_source, encode_snapshot,
                                         decode_snapshot, pairs as _pairs, tree_digest as snapshot_tree_digest)

MAX_FILE = 1048576
MAX_TREE = 16777216
MAX_FILES = 8192
MAX_FRAME = 24 * 1048576
MAX_POLICY = 65536
MAX_JSON = 16384
MAGIC = b'JCO1'
PROTOCOL = 'isolated-candidate-v1'
CLEANUP_RESERVE = 35
PROFILE = dict(version=1, network='none', memory_bytes=1073741824, swap_bytes=0,
               cpu_quota=100000, cpu_period=100000, pids=64, tmpfs_bytes=67108864,
               uid=65534, gid=65534, read_only=True, capabilities=[], host_mounts=[])


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _json(value, depth=0):
    if depth > 32:
        raise ValueError('json_depth_limit')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json(item, depth + 1)
        return
    raise ValueError('json_value_rejected')


def _parse(raw, limit):
    if type(raw) is not bytes or len(raw) > limit:
        raise ValueError('json_size_limit')
    def reject(value):
        raise ValueError('nonfinite_json')
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject)
    _json(value)
    if canonical(value) != raw:
        raise ValueError('noncanonical_json')
    return value


def _path(value):
    return (type(value) is str and re.fullmatch(r'[A-Za-z0-9_.\-/]{1,200}', value) is not None
            and not value.startswith(('/', '-')) and not any(
                part in ('', '.', '..', '.git', '.aws', '.agents', '.codex') for part in value.split('/')))


def tree_digest(files):
    if (type(files) is not dict or not 1 <= len(files) <= MAX_FILES
            or any(not _path(name) or type(raw) is not bytes or len(raw) > MAX_FILE
                   for name, raw in files.items()) or sum(map(len, files.values())) > MAX_TREE):
        raise ValueError('source_snapshot_rejected')
    # Reuse the runtime's exact snapshot validation, including prefix collisions.
    encode_snapshot(files, '0' * 64, '0' * 64, '0' * 64)
    return snapshot_tree_digest(files)


@dataclass(frozen=True)
class JSONCallPolicy:
    """Canonical immutable protected policy bytes; no mutable assertion aliases."""
    raw: bytes

    def value(self):
        value = _parse(self.raw, MAX_POLICY)
        if (type(value) is not dict or set(value) != {'version', 'task_sha256', 'image_sha256',
                'acceptance_sha256', 'profile_sha256', 'max_seconds', 'max_output_bytes', 'cases'}
                or type(value['version']) is not int or value['version'] != 1
                or any(not _hex(value[key]) for key in ('task_sha256', 'image_sha256', 'acceptance_sha256'))
                or value['profile_sha256'] != digest(canonical(PROFILE))
                or type(value['max_seconds']) is not int or not 45 <= value['max_seconds'] <= 60
                or type(value['max_output_bytes']) is not int or not 1024 <= value['max_output_bytes'] <= 65536
                or type(value['cases']) is not list or not 1 <= len(value['cases']) <= 256):
            raise ValueError('json_call_policy_rejected')
        names = set()
        for case in value['cases']:
            if (type(case) is not dict or set(case) != {'name', 'module', 'callable', 'args', 'kwargs', 'expected'}
                    or type(case['name']) is not str
                    or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,199}', case['name']) is None
                    or case['name'] in names or type(case['module']) is not str
                    or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}', case['module']) is None
                    or type(case['callable']) is not str
                    or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', case['callable']) is None
                    or type(case['args']) is not list or type(case['kwargs']) is not dict
                    or len(canonical(case)) > MAX_JSON):
                raise ValueError('json_call_case_rejected')
            names.add(case['name'])
        return value

    @property
    def sha256(self):
        self.value()
        return digest(self.raw)


@dataclass(frozen=True)
class OraclePlan:
    """Data-only projection of protected VerificationPlan for the fixed helper."""
    task_sha256: str
    runtime_sha256: str
    harness_sha256: str
    test_image_sha256: str
    test_acceptance_sha256: str
    test_names: tuple[str, ...]
    assertion_protocol: str
    max_seconds: int
    max_output_bytes: int
    enabled: bool = False

    def validate(self):
        if (any(not _hex(value) for value in (self.task_sha256, self.runtime_sha256,
                self.harness_sha256, self.test_image_sha256, self.test_acceptance_sha256))
                or type(self.test_names) is not tuple or not 1 <= len(self.test_names) <= 256
                or any(type(name) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,199}', name) is None
                       for name in self.test_names) or len(set(self.test_names)) != len(self.test_names)
                or self.assertion_protocol != PROTOCOL or type(self.max_seconds) is not int
                or not 45 <= self.max_seconds <= 60 or type(self.max_output_bytes) is not int
                or not 1024 <= self.max_output_bytes <= 65536 or type(self.enabled) is not bool):
            raise ValueError('oracle_plan_rejected')

    @property
    def sha256(self):
        self.validate()
        return digest(canonical(asdict(self)))


def decode_plan(value):
    if (type(value) is not dict or set(value) != set(OraclePlan.__dataclass_fields__)
            or type(value['test_names']) is not list):
        raise ValueError('oracle_plan_rejected')
    plan = OraclePlan(**(value | {'test_names': tuple(value['test_names'])}))
    plan.validate()
    return plan


# No expected outputs, test names, policy, host path, credentials or commands are
# interpolated here. Materialization happens before any candidate import and uses
# an exclusively created private directory. The Python stdlib is loaded first.
GATE_PROGRAM = container_codec_source() + '''
import importlib, os, select, sys, time
''' + MOUNT_CHECK + '''
status = _mount_check()
print("NESTED_READY:" + status, flush=True)
if status != "verified":
    raise SystemExit(74)
deadline = time.monotonic() + 60
def read_exact(size):
    data = bytearray()
    while len(data) < size:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([0], [], [], left)[0]:
            raise SystemExit(74)
        part = os.read(0, min(65536, size - len(data)))
        if not part:
            raise SystemExit(75)
        data.extend(part)
    return bytes(data)
header = read_exact(8)
size = int.from_bytes(header[4:], "big")
if header[:4] != b"JCO1" or not 0 < size <= 25165824:
    raise SystemExit(75)
value = json.loads(read_exact(size), object_pairs_hook=pairs)
if set(value) != {"version", "source", "calls"} or value["version"] != 1:
    raise SystemExit(75)
source = value["source"]
files = decode_snapshot(canonical(source), source["task_sha256"], source["attempt_id"], source["baseline_sha256"])
if tree_digest(files) != source["baseline_sha256"] or not 1 <= len(value["calls"]) <= 256:
    raise SystemExit(75)
os.mkdir("/workspace/source", 0o700)
root = os.open("/workspace/source", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for path, raw in files.items():
        parent = os.dup(root)
        try:
            for part in path.split("/")[:-1]:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            fd = os.open(path.split("/")[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
        finally:
            os.close(parent)
finally:
    os.close(root)
# No source code can execute before the bounded full frame has been validated.
# Any process/output manipulation after this point remains untrusted RPC output.
# A staged stdlib-name file must never accidentally test a preloaded stdlib
# implementation. Reject such entrypoints before candidate import.
if any(call["module"].split(".")[0] in sys.modules for call in value["calls"]):
    raise SystemExit(75)
sys.path.insert(0, "/workspace/source")
results = []
for index, call in enumerate(value["calls"]):
    try:
        module = importlib.import_module(call["module"])
        result = getattr(module, call["callable"])(*call["args"], **call["kwargs"])
        # Reject non-JSON/nonfinite results rather than granting a pass.
        encoded = json.dumps(result, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError()
        results.append({"index": index, "value": result})
    except BaseException:
        raise SystemExit(76)
print(json.dumps({"version": 1, "responses": results}, allow_nan=False, ensure_ascii=True,
                 sort_keys=True, separators=(",", ":")), flush=True)
'''


def harness_sha256(policy):
    return digest(canonical(dict(protocol=PROTOCOL, gate_sha256=digest(GATE_PROGRAM.encode()),
                                 profile_sha256=digest(canonical(PROFILE)), test_policy_sha256=policy.sha256)))


def prepare_payload(files, policy):
    """Only inert source bytes and call inputs cross the candidate boundary."""
    tree_digest(files)
    value = policy.value()
    calls = []
    for case in value['cases']:
        # Reject installed-module-only tests: every entrypoint must belong to the
        # staged source. Package import side effects remain inside containment.
        module = case['module'].replace('.', '/')
        if module + '.py' not in files and module + '/__init__.py' not in files:
            raise ValueError('test_entrypoint_outside_snapshot')
        calls.append({key: case[key] for key in ('module', 'callable', 'args', 'kwargs')})
    source = json.loads(encode_snapshot(files, value['task_sha256'], '0' * 64, tree_digest(files)))
    raw = canonical(dict(version=1, source=source, calls=calls))
    if len(raw) > MAX_FRAME:
        raise ValueError('source_frame_limit')
    return MAGIC + len(raw).to_bytes(4, 'big') + raw


def compare_responses(raw, policy):
    """Trusted assertion authority. Never accepts candidate pass/fail markers."""
    value = policy.value()
    if type(raw) is not bytes or not raw.endswith(b'\n'):
        raise ValueError('rpc_result_incomplete')
    observed = _parse(raw[:-1], value['max_output_bytes'])
    if (type(observed) is not dict or set(observed) != {'version', 'responses'}
            or type(observed['version']) is not int or observed['version'] != 1
            or type(observed['responses']) is not list
            or len(observed['responses']) != len(value['cases'])):
        raise ValueError('rpc_result_rejected')
    results = {}
    for index, (case, reply) in enumerate(zip(value['cases'], observed['responses'])):
        if (type(reply) is not dict or set(reply) != {'index', 'value'}
                or type(reply['index']) is not int or reply['index'] != index):
            raise ValueError('rpc_result_rejected')
        results[case['name']] = 'passed' if canonical(reply['value']) == canonical(case['expected']) else 'failed'
    return results


class OraclePipe:
    """Nonblocking bounded framing; consumes both EOFs, never just a result line."""
    def __init__(self, process, limit):
        self.process, self.limit = process, limit
        self.buffer, self.stderr = bytearray(), bytearray()
        self.total = 0
        self.eof = set()
        self.ready = selectors.DefaultSelector()
        for name in ('stdin', 'stdout', 'stderr'):
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            if name != 'stdin':
                self.ready.register(stream, selectors.EVENT_READ, name)

    def pump(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise LiveFailure('oracle_output_timeout')
        events = self.ready.select(left)
        if not events:
            raise LiveFailure('oracle_output_timeout')
        for key, _ in events:
            try:
                raw = os.read(key.fd, min(4096, self.limit + 1 - self.total))
            except BlockingIOError:
                continue
            if not raw:
                self.eof.add(key.data)
                self.ready.unregister(key.fileobj)
                continue
            self.total += len(raw)
            if self.total > self.limit:
                raise LiveFailure('oracle_output_limit')
            (self.buffer if key.data == 'stdout' else self.stderr).extend(raw)

    def wait_ready(self, deadline):
        while b'\n' not in self.buffer:
            if 'stdout' in self.eof:
                raise LiveFailure('oracle_ready_eof')
            self.pump(deadline)
        if self.buffer != b'NESTED_READY:verified\n' or self.stderr:
            raise LiveFailure('oracle_mount_check_unverified')
        self.buffer.clear()

    def send(self, framed, deadline):
        remaining = memoryview(framed)
        fd = self.process.stdin.fileno()
        while remaining:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [fd], [], left)[1]:
                raise LiveFailure('oracle_input_timeout')
            try:
                size = os.write(fd, remaining)
            except BlockingIOError:
                continue
            if size <= 0:
                raise LiveFailure('oracle_input_no_progress')
            remaining = remaining[size:]
        self.process.stdin.close()

    def finish(self, deadline):
        while self.ready.get_map():
            self.pump(deadline)
        left = deadline - time.monotonic()
        if left <= 0:
            raise LiveFailure('oracle_completion_timeout')
        code = self.process.wait(timeout=left)
        if code != 0 or self.stderr:
            raise LiveFailure('oracle_execution_failed')
        if time.monotonic() >= deadline:
            raise LiveFailure('oracle_completion_timeout')
        return code

    def close(self):
        self.ready.close()
        for name in ('stdin', 'stdout', 'stderr'):
            getattr(self.process, name).close()


class OracleExecutor(PodmanExecutor):
    """Reuse exact container identity, live cgroup capture and empty proof.

    Metadata I/O uses explicit absolute deadlines, never nested SIGALRM wrappers.
    The existing stop attach-client reap retains its full 5s+5s allowance.
    """
    def __init__(self, *args, **kwargs):
        self.command_deadline = 0.0
        super().__init__(*args, **kwargs)

    def create_arguments(self, binding):
        args = super().create_arguments(binding)
        old = '--network=slirp4netns:allow_host_loopback=false'
        if args.count(old) != 1:
            raise LiveFailure('oracle_profile_changed')
        args[args.index(old)] = '--network=none'
        index = args.index('sha256:' + self.target.image_id)
        return args[:index + 1] + ['-I', '-S', '-B', '-c', GATE_PROGRAM]

    def _created_policy(self, value):
        super()._created_policy(value)
        if value['HostConfig'].get('NetworkMode') != 'none':
            raise LiveFailure('oracle_network_not_disabled')
        config = value['Config']
        # Podman 4.9.3 serializes Entrypoint as a string, unlike Docker.
        if (type(config.get('Entrypoint')) is not str
                or config['Entrypoint'] != '/usr/local/bin/python3'
                or config.get('Cmd') != ['-I', '-S', '-B', '-c', GATE_PROGRAM]):
            raise LiveFailure('oracle_harness_changed')

    def _run(self, args):
        # Preserve Podman's full five-second grace plus response overhead.
        allowance = 7 if args and args[0] == 'stop' else 5
        deadline = min(self.command_deadline, time.monotonic() + allowance)
        if deadline <= time.monotonic():
            raise LiveFailure('oracle_metadata_timeout')
        process = subprocess.Popen([*PODMAN_PREFIX, *args], env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, close_fds=True)
        output = bytearray()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0 or not ready.select(left):
                        raise LiveFailure('oracle_metadata_timeout')
                    try:
                        raw = os.read(process.stdout.fileno(), 65537 - len(output))
                    except BlockingIOError:
                        continue
                    if not raw:
                        break
                    output.extend(raw)
                    if len(output) > 65536:
                        raise LiveFailure('oracle_metadata_limit')
            left = deadline - time.monotonic()
            if left <= 0:
                raise LiveFailure('oracle_metadata_timeout')
            return process.wait(timeout=left), bytes(output)
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)


def _bindings(target, plan, stage, files, policy, test_attempt_id):
    plan.validate()
    target.validate()
    value = policy.value()
    if (not _hex(test_attempt_id) or test_attempt_id == stage.get('attempt_id')
            or plan.enabled is not True or plan.assertion_protocol != PROTOCOL
            or plan.task_sha256 != value['task_sha256'] or stage.get('task_sha256') != plan.task_sha256
            or stage.get('runtime_sha256') != plan.runtime_sha256
            or stage.get('verification_plan_sha256') != plan.sha256
            or stage.get('candidate_tree_sha256') != tree_digest(files)
            or plan.harness_sha256 != harness_sha256(policy)
            or plan.test_image_sha256 != value['image_sha256'] or target.image_id != value['image_sha256']
            or plan.test_acceptance_sha256 != value['acceptance_sha256'] or target.acceptance_id != value['acceptance_sha256']
            or plan.max_seconds != value['max_seconds'] or plan.max_output_bytes != value['max_output_bytes']
            or tuple(case['name'] for case in value['cases']) != plan.test_names):
        raise ValueError('oracle_binding_rejected')
    return value


def run_oracle(target, plan, stage, files, policy, test_attempt_id, *, command=None, popen=None):
    """Return (receipt, evidence); receipt is None for every uncertain outcome.

    Requires an independently approved rootless helper. No production caller is
    enabled here. Host supervisor must reserve test_attempt_id durably first and
    leave it held on crash, timeout, malformed relay, or absent cleanup proof.
    command/popen injection is solely an offline testing seam, not task input.
    Caller must give at least plan.max_seconds+10s wall budget, including cleanup.
    """
    started = time.monotonic()
    value = _bindings(target, plan, stage, files, policy, test_attempt_id)
    framed = prepare_payload(files, policy)
    stage_sha256 = digest(canonical(stage))
    binding = ExecutorBinding(test_attempt_id, 'json_oracle', 'no_environment_key',
                              'https://api.openai.com/never-called')
    executor = OracleExecutor(target, 'synthetic-no-key', command=command, popen=popen)
    deadline = started + value['max_seconds'] - CLEANUP_RESERVE
    executor.command_deadline = deadline
    pipe, results = None, None
    evidence = dict(version=1, stage_sha256=stage_sha256, test_policy_sha256=policy.sha256,
                    stdout_sha256=None, stderr_sha256=None, failure=None, cleanup_verified=False,
                    stop_acknowledged=False, output_bytes=0, stdout_eof=False, stderr_eof=False,
                    candidate_attach_exit_code=None, candidate_container_exit_code=None)
    try:
        if time.monotonic() >= deadline:
            raise LiveFailure('oracle_work_budget_exhausted')
        executor.verify_prepared()
        executor.binding = binding
        code, _ = executor._command(['container', 'exists', 'agents-' + test_attempt_id])
        if code != 1:
            raise LiveFailure('oracle_name_unavailable')
        executor._create_attempted = True
        code, raw = executor._command(executor.create_arguments(binding))
        if code or re.fullmatch(rb'[a-f0-9]{64}\n?', raw) is None:
            raise LiveFailure('oracle_create_uncertain')
        executor.container_id = raw.decode('ascii').strip()
        executor._created_policy(executor._inspect())
        executor._start_attempted = True
        executor.process = executor._popen([*PODMAN_PREFIX, 'start', '--attach', '--interactive', executor.container_id],
            env=executor.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, close_fds=True)
        pipe = OraclePipe(executor.process, value['max_output_bytes'])
        pipe.wait_ready(deadline)
        observed = executor._inspect()
        if observed['State'].get('Running') is not True:
            raise LiveFailure('oracle_not_running')
        executor._capture_cgroup(observed)
        if executor.cgroup_fd is None or executor.cgroup_events_fd is None:
            raise LiveFailure('oracle_cgroup_not_captured')
        current = executor._inspect()
        if (current['State'].get('Running') is not True
                or current['State'].get('Pid') != observed['State'].get('Pid')
                or current['State'].get('CgroupPath') != observed['State'].get('CgroupPath')):
            raise LiveFailure('oracle_identity_changed')
        pipe.send(framed, deadline)
        evidence['candidate_attach_exit_code'] = pipe.finish(deadline)
        ended = executor._inspect()['State']
        if (ended.get('Running') is not False or type(ended.get('ExitCode')) is not int
                or ended['ExitCode'] != 0 or ended.get('OOMKilled') is not False):
            raise LiveFailure('oracle_container_exit_unverified')
        evidence['candidate_container_exit_code'] = ended['ExitCode']
        results = compare_responses(bytes(pipe.buffer), policy)
    except BaseException:
        evidence['failure'] = 'execution_unverified'
    finally:
        # Work expiry never suppresses stop/readback. Start independent reserves;
        # overrunning the total plan budget blocks acceptance, not cleanup.
        if executor._create_attempted:
            executor.command_deadline = time.monotonic() + 13
            try:
                executor.stop(binding)
                evidence['stop_acknowledged'] = True
            except BaseException:
                evidence['failure'] = 'stop_unverified'
            executor.command_deadline = time.monotonic() + 10
            try:
                evidence['cleanup_verified'] = executor.verify_empty(binding) is True
            except BaseException:
                evidence['failure'] = 'cleanup_unverified'
        if pipe is not None:
            evidence.update(stdout_sha256=digest(bytes(pipe.buffer)), stderr_sha256=digest(bytes(pipe.stderr)),
                output_bytes=pipe.total, stdout_eof='stdout' in pipe.eof, stderr_eof='stderr' in pipe.eof)
            try:
                pipe.close()
            except BaseException:
                evidence['failure'] = 'pipe_close_unverified'
        try:
            executor.close()
        except BaseException:
            evidence['failure'] = 'executor_close_unverified'
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    evidence.update(elapsed_ms=elapsed_ms, container_id=executor.container_id)
    if (evidence['failure'] is not None or results is None or not evidence['cleanup_verified']
            or not evidence['stdout_eof'] or not evidence['stderr_eof']
            or not _hex(executor.container_id) or elapsed_ms > value['max_seconds'] * 1000):
        return None, evidence
    passed = all(result == 'passed' for result in results.values())
    receipt = dict(version=1, stage_sha256=stage_sha256, verification_plan_sha256=plan.sha256,
        test_attempt_id=test_attempt_id, test_image_sha256=target.image_id,
        test_acceptance_sha256=target.acceptance_id, harness_sha256=harness_sha256(policy),
        results=results, container_id=executor.container_id, assertion_protocol=PROTOCOL, network='none',
        exit_code=0 if passed else 1, stdout_eof=True, stderr_eof=True, cleanup_verified=True,
        elapsed_ms=elapsed_ms, output_bytes=evidence['output_bytes'], evidence_sha256=digest(canonical(evidence)))
    return receipt, evidence
