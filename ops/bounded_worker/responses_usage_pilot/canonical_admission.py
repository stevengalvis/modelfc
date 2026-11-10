"""Offline planning only; trusted-controller evidence, not an executor or approval.

No provider, credential reader, runtime or historical journal access. A separately
reviewed caller must authorize the manifest, supply reviewed immutable bytes and
execute exactly those bytes. Receipt fields are claims from that trusted caller,
not cryptographic attestation. Same-UID tampering/rollback is outside this gate.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat

STORE = Path('/home/zeno-worker/responses-task-admission')
VERSION = 2
CANDIDATE_DERIVATION_POLICY = 'append-missing-final-lf-v1'
SCHEMA = (
    'CREATE TABLE metadata (version INTEGER NOT NULL, directory TEXT NOT NULL)',
    'CREATE TABLE tasks (task_id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, '
    'scope TEXT UNIQUE NOT NULL, manifest TEXT NOT NULL)',
    'CREATE TABLE calls (task_id TEXT NOT NULL REFERENCES tasks(task_id), '
    'number INTEGER NOT NULL, request TEXT UNIQUE NOT NULL, sandbox TEXT UNIQUE NOT NULL, '
    'receipt TEXT, container_id TEXT, PRIMARY KEY(task_id, number))',
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def _hex(value, length=64):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{%d}' % length, value):
        raise ValueError('invalid_hash')


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}', value):
        raise ValueError('invalid_label')


def _path(value):
    _label(value)
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in {'.git', '.agents', '.aws', '.codex', '..'}
                                  for part in path.parts)
            or str(path) != value or not value.endswith('.py')):
        raise ValueError('invalid_source_path')


@dataclass(frozen=True)
class Manifest:
    task_id: str
    repository: str
    base_sha: str
    source_path: str
    function: str
    source_hash: str
    test_path: str
    test_hash: str
    harness_path: str
    harness_hash: str
    test_names: tuple[str, ...]
    instructions: str
    image_id: str
    profile_hash: str
    runtime_id: str
    credential_reference: str
    profile: str = 'rootless-no-network-readonly-no-capabilities'
    model: str = 'gpt-6-luna'
    reasoning: str = 'low'
    service_tier: str = 'default'
    max_calls: int = 3
    max_input_tokens: int = 8192
    max_output_tokens: int = 4096
    max_request_bytes: int = 4096
    max_response_bytes: int = 65536
    max_source_bytes: int = 8192
    provider_seconds: int = 60
    sandbox_seconds: int = 45

    def validate(self, source, tests, harness):
        for value in (self.task_id, self.repository, self.runtime_id, self.credential_reference):
            _label(value)
        if self.credential_reference != 'credential-ref:responses-pilot-saved-key':
            raise ValueError('non_secret_reference_required')
        if self.repository != 'stevengalvis/modelfc':
            raise ValueError('canonical_repository_slug_required')
        if self.profile != 'rootless-no-network-readonly-no-capabilities':
            raise ValueError('unsupported_profile')
        _hex(self.base_sha, 40)
        for value in (self.source_hash, self.test_hash, self.harness_hash,
                      self.image_id, self.profile_hash):
            _hex(value)
        for value in (self.source_path, self.test_path, self.harness_path):
            _path(value)
        if len({self.source_path, self.test_path, self.harness_path}) != 3:
            raise ValueError('separate_trusted_tests_required')
        if not isinstance(self.function, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.function):
            raise ValueError('invalid_function')
        if (type(self.test_names) is not tuple or not 1 <= len(self.test_names) <= 32
                or len(set(self.test_names)) != len(self.test_names)):
            raise ValueError('invalid_tests')
        for name in self.test_names:
            _label(name)
        if (not isinstance(self.instructions, str) or not self.instructions.strip()
                or len(self.instructions.encode()) > 2048):
            raise ValueError('invalid_instructions')
        if (self.model, self.reasoning, self.service_tier) != ('gpt-6-luna', 'low', 'default'):
            raise ValueError('unsupported_settings')
        limits = {'max_calls': 3, 'max_input_tokens': 8192, 'max_output_tokens': 4096,
                  'max_request_bytes': 4096, 'max_response_bytes': 65536,
                  'max_source_bytes': 8192, 'provider_seconds': 60, 'sandbox_seconds': 45}
        for key, ceiling in limits.items():
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('invalid_limit')
        for value, expected, bound in ((source, self.source_hash, self.max_source_bytes),
                                        (tests, self.test_hash, 65536),
                                        (harness, self.harness_hash, 65536)):
            if type(value) is not bytes or not 0 < len(value) <= bound or digest(value) != expected:
                raise ValueError('immutable_bytes_mismatch')

    @property
    def serialized(self):
        return canonical(asdict(self))

    @property
    def identity(self):
        return digest(self.serialized.encode())

    @property
    def scope(self):
        # Deliberately conservative: aliases or changed prose/settings/tests do not
        # authorize another budget for this same source scope. No repeat workflow.
        return digest(canonical([self.repository, self.base_sha,
                                 self.source_path, self.function]).encode())


@dataclass(frozen=True)
class Receipt:
    outcome: str  # pass, test_failure, unknown, blocked
    response_hash: str
    candidate_hash: str  # Exact tested bytes, possibly derived by the controller.
    container_id: str
    sandbox_name: str
    image_id: str
    test_results: tuple[tuple[str, bool], ...]
    input_tokens: int
    output_tokens: int
    response_bytes: int
    candidate_bytes: int
    execution_receipt_hash: str
    cleanup_receipt_hash: str
    cleanup_verified: bool
    candidate_derivation: dict | None = None  # Absent in historical receipts.

    def validate(self, manifest, sandbox):
        if self.candidate_derivation is not None:
            lineage = self.candidate_derivation
            if (type(lineage) is not dict or set(lineage) != {
                    'policy', 'applied', 'raw_hash', 'raw_bytes', 'derived_hash', 'derived_bytes'}
                    or lineage['policy'] != CANDIDATE_DERIVATION_POLICY
                    or type(lineage['applied']) is not bool):
                raise ValueError('invalid_candidate_derivation')
            for name in ('raw_hash', 'derived_hash'):
                _hex(lineage[name])
            for name in ('raw_bytes', 'derived_bytes'):
                if type(lineage[name]) is not int or not 0 < lineage[name] <= manifest.max_source_bytes:
                    raise ValueError('invalid_candidate_derivation')
            if (lineage['derived_hash'] != self.candidate_hash
                    or lineage['derived_bytes'] != self.candidate_bytes
                    or lineage['derived_bytes'] != lineage['raw_bytes'] + int(lineage['applied'])
                    or (lineage['raw_hash'] != lineage['derived_hash']) != lineage['applied']):
                raise ValueError('invalid_candidate_derivation')
        if self.outcome not in ('pass', 'test_failure', 'unknown', 'blocked'):
            raise ValueError('invalid_outcome')
        for value in (self.response_hash, self.candidate_hash, self.container_id,
                      self.image_id, self.execution_receipt_hash, self.cleanup_receipt_hash):
            _hex(value)
        if self.image_id != manifest.image_id or self.sandbox_name != sandbox:
            raise ValueError('sandbox_identity_mismatch')
        if type(self.cleanup_verified) is not bool:
            raise ValueError('invalid_cleanup')
        if self.outcome == 'pass' and not self.cleanup_verified:
            raise ValueError('success_requires_cleanup')
        if self.execution_receipt_hash == self.cleanup_receipt_hash:
            raise ValueError('independent_cleanup_required')
        if (type(self.test_results) is not tuple
                or any(type(row) is not tuple or len(row) != 2 or type(row[1]) is not bool
                       for row in self.test_results)
                or tuple(row[0] for row in self.test_results) != manifest.test_names):
            raise ValueError('complete_named_tests_required')
        passed = all(row[1] for row in self.test_results)
        if ((self.outcome == 'pass' and not passed)
                or (self.outcome == 'test_failure' and passed)):
            raise ValueError('outcome_mismatch')
        for value, bound in ((self.input_tokens, manifest.max_input_tokens),
                             (self.output_tokens, manifest.max_output_tokens),
                             (self.response_bytes, manifest.max_response_bytes),
                             (self.candidate_bytes, manifest.max_source_bytes)):
            if type(value) is not int or not 0 <= value <= bound:
                raise ValueError('usage_out_of_bounds')


class CanonicalAdmission:
    """Fixed store, no directory argument and no callbacks/side effects beyond SQLite.

    Directory provisioning is a separate operator action. The fixed store must
    be outside worker mounts and retained forever; deletion/rollback is unsafe.
    """
    schema = SCHEMA
    version = VERSION

    def __init__(self):
        path = STORE
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('invalid_store')
        for ancestor in (*reversed(path.parents), path):
            info = ancestor.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('unsafe_directory')
        info = path.lstat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('private_directory_required')
        target = path / 'canonical.sqlite3'
        created = False
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            created = True
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('private_database_required')
        self.db = sqlite3.connect(target, timeout=5, isolation_level=None)
        try:
            # Reject old/unknown stores before changing journal settings. No
            # automatic migration, reset, or retry of historical uncertainty.
            if not created:
                actual = {row[0] for row in self.db.execute(
                    "SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
                if (actual != set(self.schema)
                        or self.db.execute('PRAGMA user_version').fetchone() != (self.version,)):
                    raise ValueError('schema_or_store_mismatch')
            if self.db.execute('PRAGMA journal_mode=DELETE').fetchone() != ('delete',):
                raise ValueError('journal_mode_rejected')
            self.db.execute('PRAGMA synchronous=EXTRA')
            self.db.execute('PRAGMA foreign_keys=ON')
            if self.db.execute('PRAGMA synchronous').fetchone() != (3,):
                raise ValueError('synchronization_rejected')
            self.db.execute('BEGIN IMMEDIATE')
            if created:
                for sql in self.schema:
                    self.db.execute(sql)
                self.db.execute('INSERT INTO metadata VALUES (?, ?)', (self.version, str(path)))
                self.db.execute(f'PRAGMA user_version={self.version}')
            actual = {row[0] for row in self.db.execute(
                "SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
            if (actual != set(self.schema)
                    or self.db.execute('PRAGMA user_version').fetchone() != (self.version,)
                    or self.db.execute('SELECT * FROM metadata').fetchall() != [(self.version, str(path))]
                    or self.db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]):
                raise ValueError('schema_or_store_mismatch')
            self.db.execute('COMMIT')
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def _registered(self, manifest):
        row = self.db.execute('SELECT manifest FROM tasks WHERE task_id=?',
                              (manifest.task_id,)).fetchone()
        if row != (manifest.serialized,):
            raise ValueError('task_not_registered_or_changed')

    def register(self, manifest, source, tests, harness):
        manifest.validate(source, tests, harness)
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT manifest FROM tasks WHERE task_id=?',
                                  (manifest.task_id,)).fetchone()
            if row is None:
                self.db.execute('INSERT INTO tasks VALUES (?, ?, ?, ?)',
                                (manifest.task_id, manifest.identity, manifest.scope, manifest.serialized))
            elif row != (manifest.serialized,):
                raise ValueError('immutable_task_changed')
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
        return manifest.identity

    def reserve(self, manifest, source, tests, harness):
        """Commit before any future provider action; returned request is a plan hash.

        A lost return/acknowledgement consumes the slot. Never resend a reserved
        call. Only a trusted, complete failure receipt can unlock the next slot.
        """
        manifest.validate(source, tests, harness)
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self._registered(manifest)
            rows = self.db.execute('SELECT number, receipt FROM calls WHERE task_id=? ORDER BY number',
                                   (manifest.task_id,)).fetchall()
            if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
                raise ValueError('noncontiguous_call_history')
            if len(rows) >= manifest.max_calls:
                raise ValueError('call_cap_exhausted')
            feedback = []
            if rows:
                prior = json.loads(rows[-1][1]) if rows[-1][1] else None
                if not prior or prior['outcome'] != 'test_failure' or prior['cleanup_verified'] is not True:
                    raise ValueError('prior_call_terminal_or_uncertain')
                feedback = [name for name, passed in prior['test_results'] if not passed]
            number = len(rows) + 1
            request = digest(canonical([manifest.identity, number, feedback]).encode())
            sandbox = 'responses-task-' + manifest.identity + '-' + str(number)
            self.db.execute('INSERT INTO calls VALUES (?, ?, ?, ?, NULL, NULL)',
                            (manifest.task_id, number, request, sandbox))
            self.db.execute('COMMIT')
            return {'number': number, 'request_hash': request, 'sandbox_name': sandbox}
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def bind_container(self, manifest, number, sandbox_name, container_id):
        """Write once before candidate transmission to the newly created sandbox.

        This is a trusted controller observation, not independent attestation.
        A lost acknowledgement blocks execution; no idempotent replay is offered.
        """
        _hex(container_id)
        if type(number) is not int or not 1 <= number <= manifest.max_calls:
            raise ValueError('invalid_call')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self._registered(manifest)
            row = self.db.execute('SELECT sandbox, receipt, container_id FROM calls '
                                  'WHERE task_id=? AND number=?', (manifest.task_id, number)).fetchone()
            if row != (sandbox_name, None, None):
                raise ValueError('container_binding_rejected')
            self.db.execute('UPDATE calls SET container_id=? WHERE task_id=? AND number=?',
                            (container_id, manifest.task_id, number))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
        return True

    def record(self, manifest, number, receipt):
        """Append a single trusted-controller receipt, never replace/replay one.

        Unknown/missing evidence should remain reserved, or record unknown if all
        receipt fields are actually known. Do not invent placeholder observations.
        """
        if type(number) is not int or not 1 <= number <= manifest.max_calls:
            raise ValueError('invalid_call')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self._registered(manifest)
            row = self.db.execute('SELECT sandbox, receipt, container_id FROM calls WHERE task_id=? AND number=?',
                                  (manifest.task_id, number)).fetchone()
            if row is None or row[1] is not None:
                raise ValueError('missing_or_completed_reservation')
            receipt.validate(manifest, row[0])
            if row[2] is not None and receipt.container_id != row[2]:
                raise ValueError('created_container_mismatch')
            self.db.execute('UPDATE calls SET receipt=? WHERE task_id=? AND number=?',
                            (canonical(asdict(receipt)), manifest.task_id, number))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
