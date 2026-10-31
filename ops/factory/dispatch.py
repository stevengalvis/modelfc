"""Inactive one-job calc receiver; all execution/provenance stays behind a trusted adapter.

Reuses the unchanged PR203 transport, delivery journal and manual-call reservation.
The repair coordinator alone owns worker attempts. No scheduler, token reader,
model client, native launcher, checkout or arbitrary instruction execution is added.
"""
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

from .contract import Grant, MAX_BODY, MAX_RESULT, REPOSITORY, Rejected, digest, strict_json
from .github_transport import MAX_RESPONSE
from .mailbox import Mailbox
from .once import reserve_poll, state_directory

MODE = 'synthetic-calc-repair'
WORKFLOW = 'calc-two-case-v1'
STATUSES = {'acknowledged', 'cancelled', 'rejected', 'uncertain', 'busy', 'already_invoked'}


def need(ok):
    if not ok:
        raise Rejected()


def module(name):
    path = Path(__file__).resolve().parents[2] / 'docs/zen53/repair-loop' / (name + '.py')
    spec = importlib.util.spec_from_file_location('dispatch_repair_' + name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@dataclass(frozen=True)
class DispatchGrant:
    """Trusted local authority; constructing this object does not authenticate approval."""
    job: Grant
    loop_id: str
    operator_id: int
    approved_at: int
    queue_verified_at: int

    def check(self, now, runtime_revision):
        need(type(self.job) is Grant)
        need(module('contract').hex64(self.loop_id))
        need(type(self.operator_id) is int and self.operator_id > 0)
        need(type(runtime_revision) is str and runtime_revision == self.job.revision)
        need(type(now) in (int, float) and now == now)
        need(all(type(value) is int and now - 3600 <= value <= now
                 for value in (self.approved_at, self.queue_verified_at)))
        need(self.queue_verified_at <= self.approved_at
             and now < self.job.expires_at <= self.approved_at + 3600)


def validate_job(body, approval, now, runtime_revision):
    approval.check(now, runtime_revision)
    need(type(body) is str)
    try:
        need(len(body.encode('utf-8')) <= MAX_BODY)
    except UnicodeError:
        raise Rejected() from None
    job = strict_json(body)
    need(type(job) is dict and set(job) == {'version', 'job_id', 'repository', 'revision',
         'expires_at', 'mode', 'inputs', 'content_digest'})
    grant = approval.job
    need(type(job['version']) is int and job['version'] == 1
         and job['job_id'] == grant.job_id and job['repository'] == REPOSITORY
         and job['revision'] == grant.revision and job['mode'] == MODE
         and type(job['expires_at']) is int and now < job['expires_at'] <= grant.expires_at)
    expected = {'workflow': WORKFLOW, 'loop_id': approval.loop_id, 'max_attempts': 2, 'path': 'calc.py'}
    need(type(job['inputs']) is dict and json.dumps(job['inputs'], sort_keys=True) == json.dumps(expected, sort_keys=True))
    need(job['content_digest'] == grant.content_digest
         and digest({key: value for key, value in job.items() if key != 'content_digest'}) == grant.content_digest)
    return job


@dataclass(frozen=True)
class ExecutionEvidence:
    """Trusted adapter return, NOT authentication of a dict/model assertion.

    The installed adapter must independently authenticate the capture, exact fresh
    stage/preparation and protected supervisor result, and inspect terminal cleanup.
    No such installed adapter is supplied here. Tests provide synthetic evidence.
    """
    capture_stdout: bytes
    exit_code: int
    timedout: bool
    preparation: dict
    protected_result: dict
    terminal_cleanup_verified: bool


class CalcMailbox(Mailbox):
    def __init__(self, state_path, transport, approval, loop, executor, runtime_revision,
                 *, clock=time.time):
        super().__init__(state_path, transport, approval.job, clock=clock)
        self.approval, self.loop, self.executor = approval, loop, executor
        self.runtime_revision = runtime_revision

    def _issue(self, response):
        need(response.status == 200 and type(response.body) is bytes and len(response.body) <= MAX_RESPONSE)
        issue = strict_json(response.body)
        grant = self.grant
        try:
            need(type(issue) is dict and 'pull_request' not in issue
                 and type(issue['number']) is int and issue['number'] == grant.issue
                 and issue['url'] == f'https://api.github.com/repos/{REPOSITORY}/issues/{grant.issue}'
                 and type(issue['user']) is dict and type(issue['user']['id']) is int
                 and issue['user']['id'] == grant.sender_id and issue['state'] in ('open', 'closed')
                 and type(issue['updated_at']) is str and 0 < len(issue['updated_at']) <= 40)
            validate_job(issue['body'], self.approval, self.clock(), self.runtime_revision)
        except (KeyError, TypeError):
            raise Rejected() from None
        return issue

    def _fresh(self, original, cancelled):
        self.approval.check(self.clock(), self.runtime_revision)
        need(cancelled() is False)
        fresh = self._issue(self.transport.request('GET', self.grant.issue))
        need(fresh['state'] == 'open' and fresh['body'] == original['body']
             and fresh['updated_at'] == original['updated_at'] and cancelled() is False)
        return fresh

    def _execute(self, reserved):
        # Only fixed structured bindings/feedback reach the trusted executor.
        # It never receives issue text, transport/token, grant or a user path.
        evidence = self.executor(json.loads(json.dumps(reserved)))
        need(type(evidence) is ExecutionEvidence and evidence.terminal_cleanup_verified is True)
        pipeline = module('pipeline')
        capture = pipeline.module('capture')
        raw = capture.extract(evidence.capture_stdout, evidence.exit_code, evidence.timedout)
        capture_sha = hashlib.sha256(evidence.capture_stdout).hexdigest()
        usage = module('usage').from_capture(evidence.capture_stdout, evidence.exit_code, evidence.timedout)
        receipt = pipeline.receipt_from_result(raw, capture_sha, reserved['reservation'],
            evidence.preparation, evidence.protected_result, usage)
        return self.loop.record_result(raw, capture_sha, receipt)

    def _poll(self, db, cancelled):
        grant = self.grant
        previous = db.execute('SELECT digest,status FROM jobs WHERE id=?', (grant.job_id,)).fetchone()
        if previous:
            if previous[0] != grant.content_digest:
                return 'rejected'
            if previous[1] in {'reserved', 'ack_pending'}:
                self._status(db, 'uncertain')
                return 'uncertain'
            return previous[1] if previous[1] in STATUSES else 'uncertain'
        execution_started = False
        try:
            self.approval.check(self.clock(), self.runtime_revision)
            if cancelled() is not False:
                self._record(db, 'cancelled')
                return 'cancelled'
            original = self._issue(self.transport.request('GET', grant.issue))
            if original['state'] == 'closed':
                self._record(db, 'cancelled')
                return 'cancelled'
            # Delivery reservation precedes any executor, independent of the repair
            # journal's per-model reservation. Existing delivery states are reused.
            self._record(db, 'reserved')
            record = None
            for attempt in (1, 2):
                self._fresh(original, cancelled)
                # Domain-separated fresh bindings are deterministic per granted loop.
                request_id = digest({'loop': self.approval.loop_id, 'attempt': attempt, 'kind': 'request'})
                stage_id = digest({'loop': self.approval.loop_id, 'attempt': attempt, 'kind': 'stage'})
                reserved = self.loop.reserve(request_id, stage_id)
                execution_started = True
                self.approval.check(self.clock(), self.runtime_revision)
                need(cancelled() is False)
                record = self._execute(reserved)
                if record['receipt']['task'] == 'PASS':
                    break
            # Fresh approval/body check after execution and immediately before POST.
            # Lost/edited/expired authority never starts another attempt or posts.
            self._fresh(original, cancelled)
            body = result_body(self.approval, record)
            self._status(db, 'ack_pending')
            posted = self.transport.request('POST', grant.issue, body=body)
            status = 'acknowledged' if posted.status == 201 else 'uncertain'
            self._status(db, status)
            return status
        except Rejected:
            status = 'uncertain' if execution_started else 'rejected'
            self._status(db, status)
            return status
        except Exception:
            self._status(db, 'uncertain')
            return 'uncertain'


def result_body(approval, record):
    """Generated bounded public summary only: no patch, feedback, paths or raw errors."""
    receipt = record['receipt']
    need(receipt['task'] in ('PASS', 'FAIL') and type(receipt['attempt']) is int and receipt['attempt'] in (1, 2))
    value = dict(version=1, job_id=approval.job.job_id, repository=REPOSITORY,
        revision=approval.job.revision, content_digest=approval.job.content_digest,
        mode=MODE, workflow=WORKFLOW, loop_id=approval.loop_id,
        task=receipt['task'], attempts=receipt['attempt'], result_sha256=module('contract').digest(record),
        status='FIXTURE_RESULT_NOT_GENERAL_AGENT_ACCEPTANCE')
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'))
    need(len(raw.encode('ascii')) <= MAX_RESULT)
    return raw


def _receive(approval, state_dir, loop_fd, transport, executor, runtime_revision, clock, cancelled, fixture):
    attempted = False
    loop = None
    try:
        approval.check(clock(), runtime_revision)
        need(callable(executor) and callable(cancelled))
        directory = state_directory(state_dir)
        # Same existing PR203 once-receipt survives all read/execution/publication
        # uncertainty. This is delivery dedupe, not another worker-attempt ledger.
        if not reserve_poll(directory, approval.job):
            return 'already_invoked'
        attempted = True
        repair = module('coordinator')
        loop = (repair.open_loop_fixture if fixture else repair.open_loop)(loop_fd, approval.loop_id)
        reservations, records = loop._state()
        need(not reservations and not records)  # No adoption/resumption of prior work.
        return CalcMailbox(directory / 'mailbox.sqlite', transport, approval, loop, executor,
                           runtime_revision, clock=clock).poll_once(cancelled=cancelled)
    except (Exception, KeyboardInterrupt):
        return 'uncertain' if attempted else 'rejected'
    finally:
        if loop is not None:
            try:
                loop.close()
            except Exception:
                return 'uncertain'


def receive_once(approval, state_dir, loop_fd, transport, executor, runtime_revision,
                 *, clock=time.time, cancelled=lambda: False):
    """Root-only inactive library; caller supplies independently authenticated release ID."""
    if os.geteuid() != 0 or os.getuid() != 0:
        return 'rejected'
    return _receive(approval, state_dir, loop_fd, transport, executor, runtime_revision, clock, cancelled, False)


def receive_once_fixture(approval, state_dir, loop_fd, transport, executor, runtime_revision,
                         *, clock=time.time, cancelled=lambda: False):
    """Local synthetic tests only; never substitutes for production admission."""
    if os.geteuid() == 0 or os.getuid() != os.geteuid():
        return 'rejected'
    return _receive(approval, state_dir, loop_fd, transport, executor, runtime_revision, clock, cancelled, True)
