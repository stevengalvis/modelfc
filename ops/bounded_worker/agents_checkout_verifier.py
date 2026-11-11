"""Independent data-only API candidate verification; no candidate code execution.

A protected local mirror supplies the clean checkout. The separately accepted isolated runtime
must export bounded inert bytes and supply independent tests through a separately
trusted protected receipt. This module neither starts that runtime nor supplies a
fake test result. The shared coding lock/store must remain held throughout.
"""
import base64
from dataclasses import asdict, dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .agents_linear_adapter import APIApproval, VerifiedResult, _attempt, task_digest
from .bridge_envelope import _pairs
from .bridge_patch import validate_and_apply
from .bridge_security import protected_dir, protected_json
from .linear_coding_intake import _canonical
from .task import _snapshot, _git

MAX_FILE = 1048576
MAX_TREE = 16777216
MAX_EXPORT = 24 * 1048576
MAX_FILES = 8192
HEX = re.compile(r'[a-f0-9]{64}')


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _path(value):
    return (type(value) is str and re.fullmatch(r'[A-Za-z0-9_.\-/]{1,200}', value) is not None
            and not value.startswith(('/', '-'))
            and not any(p in ('', '.', '..', '.git', '.aws', '.codex', '.agents') for p in value.split('/')))


def _write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _regular(work, name):
    current = work
    for part in name.split('/')[:-1]:
        current = current / part
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise ValueError('unsafe_candidate_parent')
    path = work / name
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o644 or before.st_size > MAX_FILE):
        raise ValueError('source_file_rejected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        initial = os.fstat(stream.fileno()); raw = stream.read(MAX_FILE + 1); final = os.fstat(stream.fileno())
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)
    if (identity(before) != identity(initial) or identity(initial) != identity(final)
            or identity(final) != identity(path.lstat()) or len(raw) != before.st_size):
        raise ValueError('source_changed_during_read')
    return raw


def _tree_digest(files):
    return _digest(_canonical([{ 'path': name, 'mode': 0o644, 'bytes': len(raw), 'sha256': _digest(raw)}
                               for name, raw in sorted(files.items())]))


@dataclass(frozen=True)
class VerificationPlan:
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
                       for name in self.test_names)
                or len(set(self.test_names)) != len(self.test_names)
                or self.assertion_protocol != 'isolated-candidate-v1'
                or type(self.max_seconds) is not int or not 1 <= self.max_seconds <= 60
                or type(self.max_output_bytes) is not int or not 1024 <= self.max_output_bytes <= 65536
                or type(self.enabled) is not bool):
            raise ValueError('invalid_verification_plan')

    @property
    def sha256(self):
        self.validate()
        return _digest(_canonical(asdict(self)))


def load_plan(path):
    obj = protected_json(path)
    if (type(obj) is not dict or set(obj) != set(VerificationPlan.__dataclass_fields__) | {'version'}
            or type(obj['version']) is not int or obj['version'] != 1 or type(obj['test_names']) is not list):
        raise ValueError('invalid_verification_plan')
    values = {name: obj[name] for name in VerificationPlan.__dataclass_fields__}
    values['test_names'] = tuple(values['test_names'])
    plan = VerificationPlan(**values); plan.validate()
    return plan


def parse_export(raw, task, attempt, baseline_sha256):
    """Canonical inert full-source snapshot, not a tar/zip and never extractall."""
    if type(raw) is not bytes or len(raw) > MAX_EXPORT:
        raise ValueError('candidate_export_limit')
    obj = json.loads(raw, object_pairs_hook=_pairs)
    if (type(obj) is not dict or set(obj) != {'version', 'task_sha256', 'attempt_id', 'baseline_sha256', 'files'}
            or type(obj['version']) is not int or obj['version'] != 1
            or obj['task_sha256'] != task_digest(task) or obj['attempt_id'] != attempt
            or obj['baseline_sha256'] != baseline_sha256 or _canonical(obj) != raw
            or type(obj['files']) is not list or not 1 <= len(obj['files']) <= MAX_FILES):
        raise ValueError('candidate_export_binding')
    files, total, last = {}, 0, ''
    for item in obj['files']:
        if (type(item) is not dict or set(item) != {'path', 'mode', 'content_base64'}
                or not _path(item['path']) or item['path'] <= last
                or type(item['mode']) is not int or item['mode'] != 0o644
                or type(item['content_base64']) is not str or len(item['content_base64']) > (MAX_FILE + 2) // 3 * 4):
            raise ValueError('candidate_file_rejected')
        content = base64.b64decode(item['content_base64'], validate=True)
        if base64.b64encode(content).decode('ascii') != item['content_base64'] or len(content) > MAX_FILE:
            raise ValueError('candidate_file_rejected')
        total += len(content)
        if total > MAX_TREE:
            raise ValueError('candidate_tree_limit')
        last = item['path']; files[last] = content
    return files


class CheckoutVerifier:
    """Stage a candidate and admit only a bound independent protected test receipt.

    checkout is the existing trusted bridge_checkout.Checkout, not a worker path.
    Execution is deliberately absent. A no-retry evidence directory keeps all
    staged/failed candidates private; no patch is published or applied to main.
    """
    def __init__(self, db, checkout, evidence_root, plan_path):
        self.db, self.checkout, self.plan_path = db, checkout, plan_path
        self.root = protected_dir(evidence_root, 0o700)
        self.task = self.attempt = self.work = self.evidence = self.plan = None
        self.original = self.baseline = self.checkout_snapshot = None
        self.stage = self.patch = self.derived = None
        self.failed = False

    def prepare(self, task, attempt):
        if self.task is not None or self.failed or not _hex(attempt) or self.db.in_transaction:
            raise ValueError('verifier_not_admitted')
        self.task, self.attempt = task, attempt
        try:
            task.validate(); self.plan = load_plan(self.plan_path)
            if (self.plan.enabled is not True or self.plan.task_sha256 != task_digest(task)
                    or self.plan.max_output_bytes > task.output_bytes):
                raise ValueError('task_tests_not_approved')
            self.evidence = self.root / attempt
            self.evidence.mkdir(mode=0o700)
            _write_new(self.evidence / 'reservation.json', _canonical(dict(
                task_sha256=task_digest(task), attempt_id=attempt, verification_plan_sha256=self.plan.sha256)))
            self.work = protected_dir(self.checkout.prepare(task))
            if not (self.work / '.git').is_dir() or (self.work / '.git').is_symlink():
                raise ValueError('standalone_checkout_required')
            if self.work == self.root or self.work in self.root.parents or self.root in self.work.parents:
                raise ValueError('overlapping_verification_paths')
            if (_git(self.work, 'rev-parse', 'HEAD').strip().decode('ascii') != task.base_revision
                    or _git(self.work, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching')):
                raise ValueError('clean_verifier_checkout_required')
            names = _git(self.work, 'ls-files', '-z').split(b'\0')
            names = [name.decode('utf-8') for name in names if name]
            if (not 1 <= len(names) <= MAX_FILES or len(set(names)) != len(names)
                    or any(not _path(name) for name in names)):
                raise ValueError('source_tree_rejected')
            self.original = {name: _regular(self.work, name) for name in sorted(names)}
            if sum(map(len, self.original.values())) > MAX_TREE or not set(task.allowed_paths) <= self.original.keys():
                raise ValueError('source_tree_limit')
            self.baseline = _tree_digest(self.original)
            self.checkout_snapshot = _snapshot(self.work)
            _write_new(self.evidence / 'baseline.json', _canonical(dict(
                task_sha256=task_digest(task), tree_sha256=self.baseline, paths=sorted(self.original))))
            return dict(self.original)  # Data for a future credential-free disposable source copy.
        except BaseException:
            self.failed = True
            raise

    def _unchanged(self):
        if self.failed or self.task is None or load_plan(self.plan_path) != self.plan:
            raise ValueError('verification_plan_changed')

    def _cloud(self, binding, turn):
        row = self.db.execute('SELECT task_hash,policy,phase,session,environment,turn,cloud,cleanup '
                              'FROM linear_agents_attempts WHERE task=? AND attempt=?',
                              (self.task.task_id, self.attempt)).fetchone()
        if row is None or type(row[1]) is not bytes:
            raise ValueError('completed_cloud_cleanup_required')
        values = json.loads(row[1], object_pairs_hook=_pairs)
        approval = APIApproval(**values); approval.validate()
        if (row[0] != task_digest(self.task) or row[2:] !=
                ('reserved', binding.session_id, binding.environment_id, turn, 'completed', 1)
                or any(type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None
                       for value in (binding.session_id, binding.environment_id, turn))
                or binding.attempt_id != self.attempt or _canonical(values) != row[1]
                or approval.enabled is not True or approval.task_sha256 != task_digest(self.task)
                or approval.runtime_sha256 != self.plan.runtime_sha256 or _attempt(self.task, approval) != self.attempt
                or self.db.execute('SELECT phase FROM linear_coding_jobs WHERE task=?',
                                   (self.task.task_id,)).fetchone() != ('launching',)):
            raise ValueError('completed_cloud_cleanup_required')

    def stage_candidate(self, raw, binding, turn):
        if self.stage is not None:
            raise ValueError('candidate_already_staged')
        try:
            self._unchanged(); self._cloud(binding, turn)
            if _snapshot(self.work) != self.checkout_snapshot:
                raise ValueError('verifier_checkout_changed')
            if type(raw) is not bytes or len(raw) > MAX_EXPORT:
                raise ValueError('candidate_export_limit')
            _write_new(self.evidence / 'candidate-export.json', raw)
            files = parse_export(raw, self.task, self.attempt, self.baseline)
            if set(files) != set(self.original):
                raise ValueError('candidate_tree_changed')
            changed = sorted(name for name in files if files[name] != self.original[name])
            if not changed or not set(changed) <= set(self.task.allowed_paths):
                raise ValueError('candidate_scope_rejected')
            derived, lineage, chunks = {}, {}, []
            for name in changed:
                left, right = self.original[name].decode('utf-8'), files[name].decode('utf-8')
                if '\x00' in left or '\x00' in right or (left and not left.endswith('\n')):
                    raise ValueError('non_text_baseline_or_candidate')
                normalize = bool(right and not right.endswith('\n'))
                if normalize: right += '\n'
                value = right.encode('utf-8')
                if len(value) > MAX_FILE:
                    raise ValueError('normalized_candidate_limit')
                derived[name] = value
                lineage[name] = dict(raw_sha256=_digest(files[name]), raw_bytes=len(files[name]),
                    derived_sha256=_digest(value), derived_bytes=len(value), appended_final_lf=normalize)
                chunks.extend(difflib.unified_diff(left.splitlines(keepends=True), right.splitlines(keepends=True),
                                                   fromfile='a/' + name, tofile='b/' + name))
            derived = {name: value for name, value in derived.items() if value != self.original[name]}
            changed = sorted(derived)
            patch = ''.join(chunks).encode('utf-8')
            if not patch or len(patch) > self.task.diff_bytes:
                raise ValueError('candidate_diff_limit')
            _write_new(self.evidence / 'candidate.patch', patch)
            if validate_and_apply(self.work, self.task, patch, _digest(patch)) != derived:
                raise ValueError('independent_patch_mismatch')
            self.derived, self.patch = derived, patch
            self.stage = dict(version=1, task_sha256=task_digest(self.task), attempt_id=self.attempt,
                session_id=binding.session_id, environment_id=binding.environment_id, turn_id=turn,
                runtime_sha256=self.plan.runtime_sha256, verification_plan_sha256=self.plan.sha256,
                baseline_sha256=self.baseline, export_sha256=_digest(raw), diff_sha256=_digest(patch),
                diff_bytes=len(patch), changed_paths=changed, lineage=lineage,
                candidate_tree_sha256=_tree_digest(self.original | derived))
            _write_new(self.evidence / 'stage.json', _canonical(self.stage))
            self.staged_snapshot = _snapshot(self.work)
            return json.loads(_canonical(self.stage))
        except BaseException:
            self.failed = True
            raise

    def finish(self, test_receipt_path, binding, turn):
        """No receipt means no success. Tests must be observed outside candidate authority."""
        try:
            self._unchanged(); self._cloud(binding, turn)
            if self.stage is None or _snapshot(self.work) != self.staged_snapshot:
                raise ValueError('staged_candidate_changed')
            receipt = protected_json(test_receipt_path)
            fields = {'version', 'stage_sha256', 'verification_plan_sha256', 'test_attempt_id',
                      'test_image_sha256', 'test_acceptance_sha256', 'harness_sha256', 'results',
                      'container_id', 'assertion_protocol', 'network',
                      'exit_code', 'stdout_eof', 'stderr_eof', 'cleanup_verified', 'elapsed_ms',
                      'output_bytes', 'evidence_sha256'}
            if (type(receipt) is not dict or set(receipt) != fields
                    or type(receipt['version']) is not int or receipt['version'] != 1
                    or receipt['stage_sha256'] != _digest(_canonical(self.stage))
                    or receipt['verification_plan_sha256'] != self.plan.sha256
                    or not _hex(receipt['test_attempt_id']) or not _hex(receipt['evidence_sha256'])
                    or not _hex(receipt['container_id']) or receipt['network'] != 'none'
                    or receipt['assertion_protocol'] != self.plan.assertion_protocol
                    or receipt['test_image_sha256'] != self.plan.test_image_sha256
                    or receipt['test_acceptance_sha256'] != self.plan.test_acceptance_sha256
                    or receipt['harness_sha256'] != self.plan.harness_sha256
                    or type(receipt['exit_code']) is not int or receipt['exit_code'] not in (0, 1)
                    or any(receipt[name] is not True for name in ('stdout_eof', 'stderr_eof', 'cleanup_verified'))
                    or type(receipt['elapsed_ms']) is not int or not 0 <= receipt['elapsed_ms'] <= self.plan.max_seconds * 1000
                    or type(receipt['output_bytes']) is not int or not 0 <= receipt['output_bytes'] <= self.plan.max_output_bytes
                    or type(receipt['results']) is not dict or set(receipt['results']) != set(self.plan.test_names)
                    or any(value not in ('passed', 'failed') for value in receipt['results'].values())):
                raise ValueError('independent_tests_unverified')
            passed = all(value == 'passed' for value in receipt['results'].values())
            if (receipt['exit_code'] == 0) != passed:
                raise ValueError('test_exit_mismatch')
            self._unchanged(); self._cloud(binding, turn)
            if _snapshot(self.work) != self.staged_snapshot:
                raise ValueError('staged_candidate_changed')
            _write_new(self.evidence / 'test-receipt.json', _canonical(receipt))
            result = VerifiedResult(task_digest(self.task), self.attempt, binding.session_id,
                binding.environment_id, turn, self.plan.runtime_sha256, tuple(self.stage['changed_paths']),
                _digest(self.patch), len(self.patch), passed, _digest(_canonical(receipt)))
            _write_new(self.evidence / 'verified-result.json', _canonical(asdict(result)))
            return result
        except BaseException:
            self.failed = True
            raise
