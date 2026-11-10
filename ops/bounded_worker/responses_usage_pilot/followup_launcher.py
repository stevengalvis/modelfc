"""Inactive explicit upgrade/run entry point for ONE separately approved call.

No staging, key lookup, runtime or store access on import. Operator stages reviewed
source first; this entry point does not publish, deploy or obtain authorization.
"""
import fcntl
import os
import stat
import sys
import time
from . import canonical_admission as c
from . import one_file_launcher as old
from . import followup_allowance as f
from .one_file_runner import run_once

AUTH = old.HOME / 'responses-required-text-followup-001-authorization.json'
RUN = old.HOME / 'responses-required-text-followup-001'
OBSERVED = {'stage': 'entry', 'reservation': 'not_started', 'provider_delivery': 'not_started'}


def authorization(mode):
    if os.getuid() != old.UID or os.geteuid() != old.UID:
        raise ValueError('worker_uid')
    account = old.pwd.getpwnam('zeno-worker')
    if account.pw_uid != old.UID or account.pw_dir != str(old.HOME):
        raise ValueError('canonical_account')
    old._parents(old.HOME)
    if old.HOME.stat().st_uid != old.UID:
        raise ValueError('worker_home')
    value = old._json(old._read(AUTH, 4096, private=True))
    fields = {'manifest', 'bundle', 'original_record_hash', 'allowance', 'additional_calls',
              'expires_utc_epoch', 'approved_modes', 'exclusive_operator_control',
              'approval_reference', 'credits_only_verified', 'credits_checked_utc_epoch'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('authorization_schema')
    now = time.time()
    if (value['manifest'] != f.IDENTITY or value['bundle'] != old.bundle_digest()
            or value['allowance'] != f.ALLOWANCE or type(value['additional_calls']) is not int
            or value['additional_calls'] != 1
            or type(value['expires_utc_epoch']) is not int
            or not now < value['expires_utc_epoch'] <= now + 3600
            or value['approved_modes'] != ['upgrade', 'run'] or mode not in value['approved_modes']
            or value['exclusive_operator_control'] is not True
            or type(value['approval_reference']) is not str
            or not 1 <= len(value['approval_reference']) <= 200
            or value['credits_only_verified'] is not True
            or type(value['credits_checked_utc_epoch']) is not int
            or not 0 <= now - value['credits_checked_utc_epoch'] <= old.MAX_CREDIT_AGE_SECONDS):
        raise ValueError('authorization_invalid')
    c._hex(value['original_record_hash'])
    return value


def _store_header():
    """Reject absent/partial/sidecar/WAL state before any mutating SQLite opener."""
    old._parents(old.STORE)
    if (c.STORE != old.STORE or old.STORE.stat().st_uid != old.UID
            or stat.S_IMODE(old.STORE.stat().st_mode) != 0o700
            or set(p.name for p in old.STORE.iterdir()) != {'canonical.sqlite3'}):
        raise ValueError('store_blocked')
    path = old.STORE / 'canonical.sqlite3'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        header = os.read(fd, 100)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != old.UID or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or len(header) != 100
                or header[:16] != b'SQLite format 3\0' or header[18:20] != b'\x01\x01'
                or old._stamp(info) != old._stamp(os.fstat(fd))):
            raise ValueError('store_blocked')
    finally:
        os.close(fd)


def execute(mode):
    OBSERVED.update(stage='authorization', reservation='not_started', provider_delivery='not_started')
    auth = authorization(mode)  # before store/runtime/credentials
    OBSERVED['stage'] = 'source'
    manifest, blobs, target = old.load()
    OBSERVED['stage'] = 'lock'
    fd = os.open(old.LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != old.UID
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('unsafe_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        OBSERVED['stage'] = 'classification'
        _store_header()
        if mode == 'upgrade':
            if old.classify(manifest) != 'spent':
                raise ValueError('original_not_spent')
            OBSERVED['stage'] = 'upgrade'
            f.upgrade(manifest, auth)
            return {'status': 'upgraded'}
        if mode != 'run':
            raise ValueError('explicit_mode_required')
        OBSERVED['stage'] = 'admission'
        gate = f.FollowupAdmission(auth)
        try:
            gate.register(manifest, *blobs)
            OBSERVED['stage'] = 'run_marker'
            RUN.mkdir(mode=0o700)  # never reuse, even if crash precedes reservation
            old._sync(RUN.parent)
            def write(name, raw):
                old._exclusive(RUN / name, raw)
            write('started.json', c.canonical(auth).encode())
            def ready(m, t):
                OBSERVED['stage'] = 'readiness'
                return old.sandbox.verify_readiness(m, t, followup=True)
            def retain(candidate):
                if type(candidate) is not bytes or not 0 < len(candidate) <= 8192:
                    raise ValueError('candidate_retention_failed')
                write('candidate-source.bin', candidate)
                return old._read(RUN / 'candidate-source.bin', 8192, private=True) == candidate
            pre_key_failure = None
            def transport(m, source, tests, harness, reservation):
                nonlocal pre_key_failure
                # Recheck exact approval, eligible credit freshness and durable state
                # BEFORE the unchanged saved-key reader. Never resume a reservation.
                OBSERVED.update(stage='credential', reservation='reserved')
                try:
                    if authorization('run') != auth:
                        raise ValueError('authorization_changed')
                except BaseException as exc:
                    # run_once catches factory failures; retain only a closed code
                    # locally so a consumed allowance is not an opaque provider error.
                    allowed = {'authorization_changed', 'authorization_invalid', 'authorization_schema'}
                    pre_key_failure = (exc.args[0] if type(exc) is ValueError and len(exc.args) == 1
                        and type(exc.args[0]) is str and exc.args[0] in allowed
                        else 'authorization_recheck_failed')
                    raise
                row = gate._reserved(m, 2)
                if row[3] is not None:
                    raise ValueError('unexpected_container')
                key = old.read_application_key()
                OBSERVED.update(stage='provider_or_runtime', provider_delivery='unknown')
                return old.OneFileTransport(m, source, tests, harness, reservation, key, old._bounded_call)
            OBSERVED.update(stage='admission', reservation='unknown')
            result = run_once(manifest, *blobs, gate=gate, target=target,
                              readiness=ready, transport_factory=transport, candidate_recorder=retain)
            if pre_key_failure is not None:
                result.update(phase='pre_key_authorization', failure_reason=pre_key_failure)
            OBSERVED.update(stage='evidence',
                reservation='reserved' if result.get('reservation') else 'unknown',
                provider_delivery=result.get('transport', {}).get('delivery', 'not_started'))
            evidence = {k: v for k, v in result.items() if k != 'patch'}
            raw = c.canonical(evidence).encode()
            if len(raw) > 65536:
                raise ValueError('evidence_bound')
            write('evidence.json', raw)
            if result.get('patch') is not None:
                if result['status'] != 'pass' or result['phase'] != 'complete':
                    raise ValueError('patch_without_pass')
                write('proposed.patch', result['patch'])
            report = dict(status=result['status'], phase=result['phase'],
                          manifest=f.IDENTITY, allowance=f.ALLOWANCE,
                          approval_hash=c.digest(c.canonical(auth).encode()),
                          evidence_sha256=c.digest(raw), usage=result.get('usage'),
                          transport=result.get('transport'),
                          patch_sha256=c.digest(result['patch']) if result.get('patch') is not None else None)
            report.update({name: result[name] for name in (
                'candidate_hash', 'candidate_bytes', 'candidate_retained', 'failure_reason') if name in result})
            write('report.json', c.canonical(report).encode())
            return report
        finally:
            gate.close()
    finally:
        os.close(fd)


def entry():
    try:
        if (not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode
                or len(sys.argv) != 2 or sys.argv[1] not in ('--upgrade', '--run')):
            raise ValueError('explicit_isolated_mode_required')
        result = execute(sys.argv[1][2:])
        print(c.canonical(result))
        return 0 if result['status'] in ('upgraded', 'pass') else 1
    except BaseException as exc:
        # No provider text, keys, source, exception text or invented observations.
        allowed = {'worker_uid', 'canonical_account', 'worker_home', 'unsafe_ancestor',
                   'unsafe_file', 'file_changed', 'authorization_schema', 'authorization_invalid',
                   'authorization_changed', 'invalid_hash', 'manifest_bytes', 'fixed_identity',
                   'unsafe_lock', 'store_blocked', 'original_not_spent', 'original_evidence_changed',
                   'approval_record_mismatch', 'existing_store_required', 'schema_or_store_mismatch',
                   'allowance_changed', 'followup_spent_or_uncertain', 'evidence_bound',
                   'patch_without_pass', 'explicit_mode_required', 'explicit_isolated_mode_required'}
        reason = (exc.args[0] if type(exc) is ValueError and len(exc.args) == 1
                  and type(exc.args[0]) is str and exc.args[0] in allowed else
                  'missing_file' if isinstance(exc, FileNotFoundError) else
                  'existing_artifact' if isinstance(exc, FileExistsError) else
                  'lock_busy' if isinstance(exc, BlockingIOError) else 'operation_failed')
        print(c.canonical(dict(OBSERVED, status='blocked_or_unknown_preserve_state', reason=reason)))
        return 1


if __name__ == '__main__':
    raise SystemExit(entry())
