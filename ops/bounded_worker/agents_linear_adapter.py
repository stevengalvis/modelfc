"""Unwired Task adapter for the existing Agents API session controller.

No default transport, credentials, OS runner, checkout, verifier or CLI. Trusted
callers must already hold PR268's coding lock and pass its exact SQLite connection.
Calling run with live dependencies would perform API work; fixtures supply fakes.
"""
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
import threading
import time

from .agents_admission import AdmissionJournal
from .agents_live_pilot import execute_once, _bounded_call
from .agents_pilot import Plan
from .agents_session_controller import SessionController
from .bridge_security import protected_json
from .linear_coding_intake import Request, _canonical, _task, binding, snapshot

HEX = re.compile(r'[a-f0-9]{64}')


def task_digest(task):
    task.validate()
    return hashlib.sha256(_canonical(asdict(task))).hexdigest()


@dataclass(frozen=True)
class APIApproval:
    task_sha256: str
    model: str
    spend_limit_cents: int
    runtime_sha256: str
    acceptance_sha256: str
    expires: int
    enabled: bool = False

    def validate(self):
        if (any(type(value) is not str or HEX.fullmatch(value) is None for value in
                (self.task_sha256, self.runtime_sha256, self.acceptance_sha256))
                or type(self.model) is not str
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', self.model) is None
                or type(self.spend_limit_cents) is not int or self.spend_limit_cents <= 0
                or type(self.expires) is not int or self.expires < 0
                or type(self.enabled) is not bool):
            raise ValueError('invalid_api_approval')


def load_approval(path):
    value = protected_json(path)
    fields = set(APIApproval.__dataclass_fields__) | {'version'}
    if (type(value) is not dict or set(value) != fields
            or type(value['version']) is not int or value['version'] != 1):
        raise ValueError('invalid_api_approval')
    approval = APIApproval(**{key: value[key] for key in fields - {'version'}})
    approval.validate()
    return approval


@dataclass(frozen=True)
class VerifiedResult:
    """Only an independent, isolated verifier may produce this observation."""
    task_sha256: str
    attempt_id: str
    session_id: str
    environment_id: str
    turn_id: str
    runtime_sha256: str
    changed_paths: tuple[str, ...]
    diff_sha256: str
    diff_bytes: int
    tests_passed: bool
    test_evidence_sha256: str


def initialize_adapter(db):
    """Explicit migration in the existing store. Never called by execution."""
    if db.in_transaction:
        raise ValueError('caller_transaction')
    try:
        db.execute('BEGIN IMMEDIATE')
        # Reuse the existing AdmissionJournal schema and reserve implementation.
        db.execute('CREATE TABLE operations (attempt TEXT NOT NULL, operation TEXT NOT NULL, '
                   'PRIMARY KEY (attempt, operation))')
        db.execute('CREATE TABLE linear_agents_attempts (task TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL, '
                   'task_hash TEXT NOT NULL, policy BLOB NOT NULL, phase TEXT NOT NULL, '
                   'session TEXT, environment TEXT, turn TEXT, cloud TEXT, cleanup INTEGER NOT NULL, '
                   'started INTEGER NOT NULL, submitted INTEGER, finished INTEGER, verification BLOB)')
        db.commit()
    except Exception:
        db.rollback()
        raise


def _schema(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name IN "
                                         "('operations','linear_agents_attempts')")}
    if tables != {'operations', 'linear_agents_attempts'}:
        raise ValueError('adapter_not_provisioned')


def _request(row):
    value = json.loads(row[3])
    req = Request(value['body'].encode(), value['workspace'], value['team'], value['issue'],
                  value['actor'], value['comment'], None, value.get('created_at'))
    obj, task = _task(req)
    if (snapshot(req) != row[3] or binding(req) != row[2] or obj['job_id'] != row[0]
            or req.comment != row[1] or task.task_id != row[4]):
        raise ValueError('invalid_shared_receipt')
    return req, obj, task


def _attempt(task, approval):
    return hashlib.sha256(_canonical({'task_sha256': task_digest(task), 'approval': asdict(approval)})).hexdigest()


def _verify(value, task, attempt, approval, session, environment, turn):
    if (type(value) is not VerifiedResult or value.task_sha256 != task_digest(task)
            or value.attempt_id != attempt or value.session_id != session
            or value.environment_id != environment or value.turn_id != turn
            or value.runtime_sha256 != approval.runtime_sha256
            or type(value.changed_paths) is not tuple or len(set(value.changed_paths)) != len(value.changed_paths)
            or not set(value.changed_paths) <= set(task.allowed_paths)
            or any(type(path) is not str for path in value.changed_paths)
            or any(type(digest) is not str or HEX.fullmatch(digest) is None
                   for digest in (value.diff_sha256, value.test_evidence_sha256))
            or type(value.diff_bytes) is not int or not 0 <= value.diff_bytes <= task.diff_bytes
            or type(value.tests_passed) is not bool):
        raise ValueError('independent_verification_rejected')
    return value


def receipts_safe(db):
    """Shared admission guard: invalid/unfinished attempts block every target."""
    try:
        return _receipts_safe(db)
    except (KeyError, TypeError, ValueError, sqlite3.DatabaseError):
        return False


def _receipts_safe(db):
    _schema(db)
    attempts = {}
    tasks = set()
    for row in db.execute('SELECT * FROM linear_agents_attempts'):
        (task_id, attempt, digest, raw, phase, session, environment, turn, cloud, cleanup,
         started, submitted, finished, verification) = row
        if type(raw) is not bytes or len(raw) > 2048 or phase not in ('completed', 'failed'):
            return False
        policy = json.loads(raw); approval = APIApproval(**policy); approval.validate()
        job = db.execute('SELECT * FROM linear_coding_jobs WHERE task=?', (task_id,)).fetchone()
        if job is None or job[5] != phase or _canonical(policy) != raw:
            return False
        _, obj, task = _request(job)
        if (obj.get('target') != 'agents-api' or digest != task_digest(task)
                or approval.task_sha256 != digest or approval.enabled is not True
                or attempt != _attempt(task, approval) or cleanup != 1
                or any(type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None
                       for value in (session, environment, turn))
                or any(type(value) is not int for value in (started, submitted, finished))
                or not 0 <= started <= submitted <= finished
                or started >= approval.expires or submitted >= approval.expires
                or cloud not in ('completed', 'failed', 'cancelled')):
            return False
        if cloud == 'completed':
            if type(verification) is not bytes or len(verification) > 8192:
                return False
            values = json.loads(verification)
            if _canonical(values) != verification:
                return False
            values['changed_paths'] = tuple(values['changed_paths'])
            checked = _verify(VerifiedResult(**values), task, attempt, approval, session, environment, turn)
            if (phase == 'completed') != checked.tests_passed:
                return False
        elif phase != 'failed' or verification is not None:
            return False
        attempts[attempt] = set()
        tasks.add(task_id)
    expected = set()
    for job in db.execute('SELECT * FROM linear_coding_jobs'):
        _, obj, task = _request(job)
        if obj.get('target') == 'agents-api' and job[5] in ('completed', 'failed'):
            expected.add(task.task_id)
    if tasks != expected:
        return False
    for attempt, operation in db.execute('SELECT attempt,operation FROM operations'):
        if attempt not in attempts or operation not in ('create', 'executor', 'input'):
            return False
        attempts[attempt].add(operation)
    return all(operations == {'create', 'executor', 'input'} for operations in attempts.values())


class SharedJournal(AdmissionJournal):
    """Same durable operations, already opened under the shared coding lock."""
    def __init__(self, db, unchanged):
        if db.in_transaction:
            raise ValueError('caller_transaction')
        _schema(db)
        if db.execute('PRAGMA journal_mode').fetchone()[0] not in ('delete', 'truncate', 'persist'):
            raise ValueError('unsafe_journal_mode')
        db.execute('PRAGMA synchronous=EXTRA')
        if db.execute('PRAGMA synchronous').fetchone() != (3,):
            raise ValueError('unsafe_sync')
        self.db, self.unchanged = db, unchanged

    def reserve(self, attempt, operation):
        self.unchanged()
        super().reserve(attempt, operation)

    def close(self):
        # The shared receipt_store context owns the connection and lock lifetime.
        pass


class RecordingController(SessionController):
    """Persist validated identity/outcome after controller transitions, not URLs."""
    def __init__(self, *args, checkpoint, **kwargs):
        super().__init__(*args, **kwargs)
        self.checkpoint = checkpoint

    def create(self):
        try:
            return super().create()
        finally:
            self.checkpoint(self)

    def submit(self, *, now):
        try:
            return super().submit(now=now)
        finally:
            self.checkpoint(self)

    def observe_event(self, event):
        try:
            return super().observe_event(event)
        finally:
            self.checkpoint(self)

    def verify_cleanup(self):
        try:
            return super().verify_cleanup()
        finally:
            self.checkpoint(self)


class AgentsTaskExecutor:
    """No default dependencies. The separate protected factory must supply them.

    runtime supplies fixed identity digests, connection/events, start/stop/empty
    callbacks, and an independent bounded verify(task, attempt, binding, turn).
    prepare must configure transport/events and evidence to the exact Task limits.
    It must prepare a fresh isolated checkout before create; no pilot workspace
    is silently reused. These injected objects are trusted, not self-attesting.
    """
    target = 'agents-api'

    def __init__(self, db, approval_path, runtime, *, clock=time.time):
        self.db, self.approval_path = db, approval_path
        self.runtime, self.clock = runtime, clock

    def run(self, task):
        db = self.db
        if db.in_transaction:
            return {'status': 'blocked', 'task_id': task.task_id}
        attempt = None
        try:
            _schema(db)
            task.validate()
            if threading.current_thread() is not threading.main_thread():
                raise ValueError('dedicated_main_thread_required')
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise ValueError('dedicated_sync_process_required')
            approval = load_approval(self.approval_path)
            approval.validate()
            def identity_unchanged():
                if (load_approval(self.approval_path) != approval or approval.enabled is not True
                        or self.runtime.runtime_sha256 != approval.runtime_sha256
                        or self.runtime.acceptance_sha256 != approval.acceptance_sha256):
                    raise ValueError('api_approval_changed')
            def unchanged():
                identity_unchanged()
                if not 0 <= int(self.clock()) < approval.expires:
                    raise ValueError('api_approval_expired')
            unchanged()
            if (approval.task_sha256 != task_digest(task) or task.output_bytes > 65536
                    or self.runtime.runtime_sha256 != approval.runtime_sha256
                    or self.runtime.acceptance_sha256 != approval.acceptance_sha256):
                raise ValueError('api_task_or_runtime_not_approved')
            row = db.execute('SELECT * FROM linear_coding_jobs WHERE task=?', (task.task_id,)).fetchone()
            if row is None or row[5] != 'launching':
                raise ValueError('shared_dispatch_required')
            req, obj, retained = _request(row)
            state = db.execute('SELECT target,acknowledged,running FROM linear_coding_pickups WHERE job=?', (row[0],)).fetchone()
            ack = db.execute('SELECT phase FROM linear_coding_acknowledgments WHERE job=?', (row[0],)).fetchone()
            if (retained != task or obj.get('target') != self.target or state is None
                    or state[0] != self.target or type(state[1]) is not int or type(state[2]) is not int
                    or state[2] < state[1] or ack != ('posted',)):
                raise ValueError('confirmed_pickup_required')
            attempt = _attempt(task, approval)
            journal = SharedJournal(db, unchanged)
            # A task can have only one API attempt even if policy/model is changed.
            db.execute('INSERT INTO linear_agents_attempts VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,0,?,NULL,NULL,NULL)',
                        (task.task_id, attempt, task_digest(task), _canonical(asdict(approval)),
                         'reserved', int(self.clock())))
            db.commit()
            def checkpoint(controller):
                now = int(self.clock())
                state = controller.state
                db.execute('UPDATE linear_agents_attempts SET session=?,environment=?,turn=?,cloud=?,cleanup=?, '
                           'submitted=COALESCE(submitted,?) WHERE task=?',
                           (state.session_id, state.environment_id, controller.turn_id, state.cloud_outcome,
                            int(state.local_empty), now if state.input_confirmed else None, task.task_id))
                db.commit()
            # Trusted bounded checkout preparation, not a worker/model launch.
            _bounded_call(lambda: self.runtime.prepare(task, attempt), 30)
            unchanged()
            controller = RecordingController(Plan(task, approval.model, approval.spend_limit_cents),
                attempt, journal, self.runtime.connection, start_executor=self.runtime.start,
                stop_executor=self.runtime.stop, verify_empty=self.runtime.verify_empty, checkpoint=checkpoint)
            asyncio.run(execute_once(controller, self.runtime.events, overall_seconds=task.timeout_seconds))
            checkpoint(controller)
            if (controller.state.blocked or not controller.state.input_confirmed
                    or controller.state.cloud_outcome not in ('completed', 'failed', 'cancelled')
                    or not controller.state.local_empty or controller.turn_id is None):
                raise ValueError('api_outcome_uncertain')
            identity_unchanged()
            verification = None
            status = 'failed'
            if controller.state.cloud_outcome == 'completed':
                observed = _bounded_call(lambda: self.runtime.verify(task, attempt, controller.binding,
                                                                    controller.turn_id), 90)
                observed = _verify(observed, task, attempt, approval, controller.binding.session_id,
                                   controller.binding.environment_id, controller.turn_id)
                result = controller.verify_result(changed_paths=observed.changed_paths,
                                                  tests_passed=observed.tests_passed)
                if result not in ('completed', 'not_completed'):
                    raise ValueError('api_result_unverified')
                status = 'completed' if result == 'completed' else 'failed'
                verification = _canonical(asdict(observed))
            identity_unchanged()
            finished = int(self.clock())
            started, submitted = db.execute('SELECT started,submitted FROM linear_agents_attempts WHERE task=?',
                                            (task.task_id,)).fetchone()
            if (type(submitted) is not int or not 0 <= started <= submitted <= finished
                    or submitted >= approval.expires):
                raise ValueError('api_timing_unverified')
            db.execute('UPDATE linear_agents_attempts SET phase=?,finished=?,verification=? WHERE task=?',
                       (status, finished, verification, task.task_id))
            db.commit()
            return {'status': status, 'task_id': task.task_id}
        except BaseException:
            db.rollback()
            # Existing launching job and any reserved API operation remain held.
            # Do not expose exception/provider text or erase/renew an attempt.
            return {'status': 'blocked', 'task_id': task.task_id}
        finally:
            close = getattr(self.runtime, 'close', None)
            if close is not None:
                try:
                    # Keep the cleanup authority alive through its complete
                    # bounded shutdown; process exit is not containment proof.
                    if not callable(close) or _bounded_call(close, 50) is not True:
                        return {'status': 'blocked', 'task_id': task.task_id}
                except BaseException:
                    return {'status': 'blocked', 'task_id': task.task_id}
