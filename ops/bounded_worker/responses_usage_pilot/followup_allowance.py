"""One explicitly approved additive allowance, never a reset of the spent task.

Schema upgrade is an explicit operator action. No provider, credentials or runtime.
Same-UID hostile rollback is outside the canonical store's existing threat model.
"""
from dataclasses import asdict
from . import canonical_admission as c

IDENTITY = '63d219109f3b5bf120a07b2097827446e3e6f5a54bf44daf19114e99d28811ba'
ALLOWANCE = 'required-text-followup-001'
TABLE = ('CREATE TABLE followup (original TEXT PRIMARY KEY REFERENCES tasks(identity), '
         'record_hash TEXT NOT NULL, approval TEXT NOT NULL, '
         'request TEXT UNIQUE, sandbox TEXT UNIQUE, receipt TEXT, container_id TEXT)')
SCHEMA = c.SCHEMA + (TABLE,)


def sandbox_name(manifest):
    if manifest.identity != IDENTITY or manifest.max_calls != 1:
        raise ValueError('followup_original_mismatch')
    return 'responses-task-' + IDENTITY + '-2'


def reservation_plan(manifest, approval_hash):
    name = sandbox_name(manifest)
    c._hex(approval_hash)
    return dict(number=2, allowance=ALLOWANCE, approval_hash=approval_hash,
                request_hash=c.digest(c.canonical([IDENTITY, ALLOWANCE, 2, approval_hash]).encode()),
                sandbox_name=name)


def original_record(db, manifest):
    """Exact sole original immutable row and spent call, including absent evidence."""
    sandbox_name(manifest)
    tasks = db.execute('SELECT * FROM tasks').fetchall()
    calls = db.execute('SELECT * FROM calls').fetchall()
    expected_call = (manifest.task_id, 1,
                     c.digest(c.canonical([IDENTITY, 1, []]).encode()),
                     'responses-task-' + IDENTITY + '-1', None, None)
    if (tasks != [(manifest.task_id, IDENTITY, manifest.scope, manifest.serialized)]
            or calls != [expected_call]):
        raise ValueError('original_evidence_changed')
    return c.digest(c.canonical([tasks, calls]).encode())


def upgrade(manifest, approval):
    """Explicit v2→v3 only; caller must verify authorization and filesystem first.

    Does not initialize absent stores. A crash commits everything or nothing;
    an uncertain outcome is inspected, never replayed or replenished.
    """
    if not (c.STORE / 'canonical.sqlite3').is_file():
        raise ValueError('existing_store_required')
    gate = c.CanonicalAdmission()
    try:
        gate.db.execute('BEGIN IMMEDIATE')
        record = original_record(gate.db, manifest)
        if approval['original_record_hash'] != record:
            raise ValueError('approval_record_mismatch')
        gate.db.execute(TABLE)
        gate.db.execute('INSERT INTO followup VALUES (?, ?, ?, NULL, NULL, NULL, NULL)',
                        (IDENTITY, record, c.canonical(approval)))
        gate.db.execute('UPDATE metadata SET version=3')
        gate.db.execute('PRAGMA user_version=3')
        gate.db.execute('COMMIT')
    finally:
        gate.close()


class FollowupAdmission(c.CanonicalAdmission):
    schema = SCHEMA
    version = 3

    def __init__(self, approval):
        # Never initialize a new schema3 store or accept journal recovery implicitly.
        if set(p.name for p in c.STORE.iterdir()) != {'canonical.sqlite3'}:
            raise ValueError('existing_store_required')
        self.approval = c.canonical(approval)
        super().__init__()

    def _check(self, manifest):
        record = original_record(self.db, manifest)
        rows = self.db.execute('SELECT original, record_hash, approval FROM followup').fetchall()
        if rows != [(IDENTITY, record, self.approval)]:
            raise ValueError('allowance_changed')

    def register(self, manifest, source, tests, harness):
        manifest.validate(source, tests, harness)
        self._check(manifest)
        return manifest.identity

    def reserve(self, manifest, source, tests, harness):
        manifest.validate(source, tests, harness)
        plan = reservation_plan(manifest, c.digest(self.approval.encode()))
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self._check(manifest)
            row = self.db.execute('SELECT request, sandbox, receipt, container_id FROM followup').fetchone()
            if row != (None, None, None, None):
                raise ValueError('followup_spent_or_uncertain')
            self.db.execute('UPDATE followup SET request=?, sandbox=? WHERE original=?',
                            (plan['request_hash'], plan['sandbox_name'], IDENTITY))
            self.db.execute('COMMIT')
            return plan
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _reserved(self, manifest, number):
        self._check(manifest)
        plan = reservation_plan(manifest, c.digest(self.approval.encode()))
        if type(number) is not int or number != 2:
            raise ValueError('invalid_followup_call')
        row = self.db.execute('SELECT request, sandbox, receipt, container_id FROM followup').fetchone()
        if row is None or row[:3] != (plan['request_hash'], plan['sandbox_name'], None):
            raise ValueError('followup_not_reserved_or_complete')
        return row

    def bind_container(self, manifest, number, sandbox_name, container_id):
        c._hex(container_id)
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._reserved(manifest, number)
            if row[1] != sandbox_name or row[3] is not None:
                raise ValueError('container_binding_rejected')
            self.db.execute('UPDATE followup SET container_id=?', (container_id,))
            self.db.execute('COMMIT')
            return True
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def record(self, manifest, number, receipt):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._reserved(manifest, number)
            receipt.validate(manifest, row[1])
            if row[3] is None or row[3] != receipt.container_id:
                raise ValueError('created_container_mismatch')
            self.db.execute('UPDATE followup SET receipt=?', (c.canonical(asdict(receipt)),))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
