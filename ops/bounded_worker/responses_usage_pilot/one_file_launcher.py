"""Fixed operator launcher. Import is inert; no implicit approval or provisioning.

Same-UID hostile changes/rollback are outside the canonical store's threat model.
Operator authorization attests exclusive host control and provenance; it is not
cryptographic proof of credit eligibility or absence of historical state.
"""
import fcntl
import json
import os
import pwd
from pathlib import Path
import sqlite3
import stat
import sys
import time

from . import canonical_admission as c
from . import one_file_adapter as a
from . import one_file_sandbox as sandbox
from .one_file_runner import run_once
from .one_file_transport import OneFileTransport
from ops.bounded_worker.agents_credentials import read_application_key
from ops.bounded_worker.agents_live_pilot import PreparedTarget, _bounded_call

ROOT = Path(__file__).resolve().parents[3]
HOME = Path('/home/zeno-worker')
AUTH = HOME / 'responses-required-text-live-001-authorization.json'
RUN = HOME / 'responses-required-text-live-001'
LOCK = HOME / 'responses-required-text-live-001.lock'
PROVISION = HOME / 'responses-required-text-live-001-provisioning.json'
STORE = Path('/home/zeno-worker/responses-task-admission')
UID = 1003
PLAN = Path('docs/agents-api-pilot/one-file-live-plan')
MANIFEST_HASH = '94c87dcc9703e346523822b6ea186980b60eb967aeb03e086e49af4f30f930b7'
IDENTITY = '63d219109f3b5bf120a07b2097827446e3e6f5a54bf44daf19114e99d28811ba'
OBSERVED = {'stage': 'entry', 'reservation': 'not_started', 'provider_delivery': 'not_started'}
WIRE = '6810b0a0cb27f7a768dda5fcc5345bb3952f3cbecd7dfa9e250fe622b1aa9abd'
TARGET = dict(image_id='8b55ec4b95fe747b3142e938a72bd182877c68a3c1da36006b766517ee4805ee',
              runtime_home='/home/agents-api-stage-6cb988668042/home',
              runtime_dir='/run/user/1003',
              acceptance_id='31bd52b2da697b7cc5ffd0d6c945285027718ff2dddf31b0238370c1611bf99d')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('json_constant')))


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _parents(path):
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022
                or info.st_uid not in (0, UID)):
            raise ValueError('unsafe_ancestor')


def _read(path, limit, private=False):
    _parents(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != UID or info.st_mode & 0o022
                or (private and stat.S_IMODE(info.st_mode) != 0o600)
                or not 0 < info.st_size <= limit):
            raise ValueError('unsafe_file')
        raw = os.read(fd, limit + 1)
        if len(raw) != info.st_size or _stamp(os.fstat(fd)) != _stamp(info):
            raise ValueError('file_changed')
        return raw
    finally:
        os.close(fd)


def bundle_digest():
    # All controller Python, not just direct imports. Operator approves this digest.
    paths = sorted((ROOT / 'ops/bounded_worker').rglob('*.py'))
    paths += [ROOT / a.SOURCE_PATH, ROOT / PLAN / 'proposed-manifest.json']
    return c.digest(c.canonical([(str(p.relative_to(ROOT)), c.digest(_read(p, 1_000_000)))
                                 for p in paths]).encode())


def load():
    raw = _read(ROOT / PLAN / 'proposed-manifest.json', 8192)
    if c.digest(raw) != MANIFEST_HASH:
        raise ValueError('manifest_bytes')
    data = _json(raw)
    data['test_names'] = tuple(data['test_names'])
    manifest = c.Manifest(**data)
    blobs = tuple(_read(ROOT / p, 65536) for p in
                  (a.SOURCE_PATH, a.FIXTURES + 'trusted_tests.py', a.FIXTURES + 'trusted_harness.py'))
    prepared = a.prepare(manifest, *blobs)
    target = PreparedTarget(**TARGET)
    if (manifest.identity != IDENTITY or prepared.wire_hash != WIRE
            or manifest.runtime_id != sandbox.runtime_identity(target)
            or manifest.profile_hash != sandbox.PROFILE_HASH or c.STORE != STORE):
        raise ValueError('fixed_identity')
    return manifest, blobs, target


def authorization(mode):
    # This is first, before touching store, key or runtime. No artifact generator.
    if os.getuid() != UID or os.geteuid() != UID:
        raise ValueError('worker_uid')
    account = pwd.getpwnam('zeno-worker')
    if account.pw_uid != UID or account.pw_dir != str(HOME):
        raise ValueError('canonical_account')
    _parents(HOME)
    if HOME.stat().st_uid != UID:
        raise ValueError('worker_home')
    value = _json(_read(AUTH, 4096, private=True))
    common = {'manifest', 'bundle', 'expires_utc_epoch', 'approved_modes',
              'exclusive_operator_control', 'approval_reference', 'new_store_provenance',
              'credits_only_verified', 'credits_checked_utc_epoch'}
    if type(value) is not dict or set(value) != common:
        raise ValueError('authorization_schema')
    now = time.time()
    if (value['manifest'] != IDENTITY or value['bundle'] != bundle_digest()
            or type(value['expires_utc_epoch']) is not int
            or not now < value['expires_utc_epoch'] <= now + 3600
            or type(value['approved_modes']) is not list
            or any(m not in ('inspect', 'provision', 'run') for m in value['approved_modes'])
            or mode not in value['approved_modes'] or value['exclusive_operator_control'] is not True
            or type(value['approval_reference']) is not str
            or not 1 <= len(value['approval_reference']) <= 200
            or type(value['new_store_provenance']) is not bool
            or type(value['credits_only_verified']) is not bool
            or type(value['credits_checked_utc_epoch']) is not int):
        raise ValueError('authorization_invalid')
    if mode == 'provision' and value['new_store_provenance'] is not True:
        raise ValueError('new_store_unproven')
    if mode == 'run' and (value['credits_only_verified'] is not True
                          or not 0 <= now - value['credits_checked_utc_epoch'] <= 300):
        raise ValueError('credits_only_unverified')
    return value


def classify(manifest):
    """Read-only fixed store classifier, no create/recovery/immutable workaround.

    Caller must establish exclusive operator control. Other old controllers do
    not honor our lock; no filesystem observation can establish this by itself.
    """
    _parents(STORE.parent)
    try:
        info = STORE.lstat()
    except FileNotFoundError:
        return 'absent'
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != UID
            or stat.S_IMODE(info.st_mode) != 0o700):
        return 'blocked'
    dbpath = STORE / 'canonical.sqlite3'
    if set(p.name for p in STORE.iterdir()) != {'canonical.sqlite3'}:
        return 'blocked'
    dbinfo = dbpath.lstat()
    if (not stat.S_ISREG(dbinfo.st_mode) or dbinfo.st_uid != UID
            or stat.S_IMODE(dbinfo.st_mode) != 0o600 or dbinfo.st_nlink != 1):
        return 'blocked'
    # SQLite mode=ro can create WAL/SHM even without pre-existing sidecars.
    # Reject WAL-format headers BEFORE opening SQLite; never use immutable=1.
    fd = os.open(dbpath, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        header = os.read(fd, 100)
        if (_stamp(os.fstat(fd)) != _stamp(dbinfo) or len(header) != 100
                or header[:16] != b'SQLite format 3\0' or header[18:20] != b'\x01\x01'):
            return 'blocked'
    finally:
        os.close(fd)
    db = None
    try:
        db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True, timeout=0,
                             isolation_level=None)
        db.execute('BEGIN')
        schema = {row[0] for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        if (schema != set(c.SCHEMA) or db.execute('PRAGMA user_version').fetchone() != (c.VERSION,)
                or db.execute('SELECT * FROM metadata').fetchall() != [(c.VERSION, str(STORE))]
                or db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                or db.execute('PRAGMA foreign_key_check').fetchall()):
            return 'blocked'
        rows = db.execute('SELECT task_id, identity, scope, manifest FROM tasks '
                          'WHERE task_id=? OR identity=? OR scope=?',
                          (manifest.task_id, manifest.identity, manifest.scope)).fetchall()
        if rows and rows != [(manifest.task_id, manifest.identity, manifest.scope, manifest.serialized)]:
            return 'blocked'
        if db.execute('SELECT 1 FROM calls WHERE task_id=?', (manifest.task_id,)).fetchone():
            return 'spent'
        return ('compatible' if _stamp(STORE.lstat()) == _stamp(info) and _stamp(dbpath.lstat()) == _stamp(dbinfo)
                and set(p.name for p in STORE.iterdir()) == {'canonical.sqlite3'} else 'blocked')
    except (sqlite3.Error, OSError, ValueError):
        return 'blocked'
    finally:
        if db is not None:
            db.close()


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)


def _write(name, raw):
    _exclusive(RUN / name, raw)


def _transport(manifest, source, tests, harness, reservation):
    # run_once invokes this only after durable reservation. Key never reaches child.
    OBSERVED.update(stage='credential', reservation='reserved')
    authorization('run')
    key = read_application_key()
    OBSERVED.update(stage='provider_or_runtime', provider_delivery='unknown')
    return OneFileTransport(manifest, source, tests, harness, reservation, key, _bounded_call)


def execute(mode):
    OBSERVED.update(stage='authorization', reservation='not_started', provider_delivery='not_started')
    auth = authorization(mode)
    OBSERVED['stage'] = 'source'
    manifest, blobs, target = load()
    OBSERVED['stage'] = 'classification'
    if mode == 'inspect':
        return {'status': classify(manifest)}  # no lock creation or runtime access
    # Advisory serialization for this helper. Never unlink the lock to retry.
    OBSERVED['stage'] = 'lock'
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != UID
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('unsafe_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        OBSERVED['stage'] = 'classification'
        state = classify(manifest)
        if mode == 'provision':
            OBSERVED['stage'] = 'provisioning'
            if state != 'absent' or not auth['new_store_provenance']:
                raise ValueError('provisioning_blocked')
            _exclusive(PROVISION, c.canonical({'manifest': IDENTITY, 'bundle': auth['bundle']}).encode())
            STORE.mkdir(mode=0o700)  # exclusive, never adopt partial directory
            _sync(STORE.parent)
            gate = c.CanonicalAdmission()
            try:
                if (gate.db.execute('SELECT count(*) FROM tasks').fetchone() != (0,)
                        or gate.db.execute('SELECT count(*) FROM calls').fetchone() != (0,)):
                    raise ValueError('new_store_not_empty')
            finally:
                gate.close()
            if classify(manifest) != 'compatible':
                raise ValueError('initialization_unverified')
            return {'status': 'provisioned'}
        if mode != 'run' or state != 'compatible':
            raise ValueError('admission_blocked')
        OBSERVED['stage'] = 'run_marker'
        RUN.mkdir(mode=0o700)  # collision is terminal, even before reservation
        _sync(RUN.parent)
        _write('started.json', c.canonical({'manifest': IDENTITY, 'bundle': auth['bundle']}).encode())
        OBSERVED['stage'] = 'readiness'
        if sandbox.verify_readiness(manifest, target) is not True:
            raise ValueError('readiness_unverified')
        # Recheck fresh credit approval and state immediately before mutating opener.
        authorization('run')
        if classify(manifest) != 'compatible':
            raise ValueError('store_changed')
        OBSERVED['stage'] = 'admission'
        gate = c.CanonicalAdmission()
        try:
            OBSERVED['reservation'] = 'unknown'
            result = run_once(manifest, *blobs, gate=gate, target=target,
                              transport_factory=_transport)
        finally:
            gate.close()
        OBSERVED.update(stage='evidence',
                        reservation='reserved' if result.get('reservation') else 'unknown',
                        provider_delivery=result.get('transport', {}).get('delivery', 'not_started'))
        evidence = {name: result[name] for name in (
            'reservation', 'wire_hash', 'manifest_identity', 'response_hash',
            'candidate_hash', 'runtime', 'usage', 'transport', 'status', 'phase') if name in result}
        evidence_raw = c.canonical(evidence).encode()
        if len(evidence_raw) > 65536:
            raise ValueError('evidence_bound')
        _write('evidence.json', evidence_raw)
        patch = result.get('patch')
        report = {'status': result['status'], 'phase': result['phase'], 'manifest': IDENTITY,
                  'usage': result.get('usage'), 'transport': result.get('transport'),
                  'evidence_sha256': c.digest(evidence_raw),
                  'patch_sha256': c.digest(patch) if patch is not None else None}
        # Runner owns receipt/trusted result validation; never persist raw provider data.
        if patch is not None and result['status'] == 'pass' and result['phase'] == 'complete':
            _write('proposed.patch', patch)
        elif patch is not None:
            raise ValueError('patch_without_pass')
        _write('report.json', c.canonical(report).encode())
        return report
    finally:
        os.close(fd)


def main():
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode
            or len(sys.argv) != 2 or sys.argv[1] not in ('--inspect', '--provision', '--run')):
        raise ValueError('explicit_isolated_mode_required')
    result = execute(sys.argv[1][2:])
    # Fixed statuses only, no exception text, key, paths or raw response.
    print(c.canonical({'status': result['status']}))
    return 0 if result['status'] in ('absent', 'compatible', 'provisioned', 'pass') else 1


def entry():
    try:
        code = main()
    except BaseException as exc:
        allowed = {'worker_uid', 'canonical_account', 'worker_home', 'unsafe_ancestor',
                   'unsafe_file', 'file_changed', 'authorization_schema', 'authorization_invalid',
                   'new_store_unproven', 'credits_only_unverified', 'manifest_bytes', 'fixed_identity',
                   'unsafe_lock', 'provisioning_blocked', 'new_store_not_empty',
                   'initialization_unverified', 'admission_blocked', 'readiness_unverified',
                   'store_changed', 'evidence_bound', 'patch_without_pass',
                   'explicit_isolated_mode_required'}
        reason = (exc.args[0] if type(exc) is ValueError and len(exc.args) == 1
                  and type(exc.args[0]) is str and exc.args[0] in allowed else
                  'missing_file' if isinstance(exc, FileNotFoundError) else
                  'existing_artifact' if isinstance(exc, FileExistsError) else
                  'lock_busy' if isinstance(exc, BlockingIOError) else 'operation_failed')
        print(c.canonical(dict(OBSERVED, status='blocked_or_unknown_preserve_state', reason=reason)))
        code = 1
    return code


if __name__ == '__main__':
    raise SystemExit(entry())
