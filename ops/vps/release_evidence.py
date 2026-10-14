"""Versioned installed OFFLINE release evidence. No candidate imports or credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from offline_report import MAX_REPORT, unique_object, reject_constant, validate_report

HEX = re.compile(r'[0-9a-f]{64}')
FILES = ('validate_pr.py', 'validate_capture.py', 'validate_offline_ssh.py',
         'offline_report.py', 'release_evidence.py')
BINDING_FIELDS = {'validator_revision', 'configuration_digest', 'image_digest'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def revision(directory, check=lambda path: None):
    hashes = {}
    for name in FILES:
        path = Path(directory) / name
        check(path)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(hashes)


def binding(config, directory, check):
    # OFFLINE uses no live history, max_age_days, provider key or token contents.
    # Scenario/history and sandbox policy are covered by controller/harness hashes.
    image = config['image']
    if (config['repository'] != 'stevengalvis/modelfc'
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', image)):
        raise ValueError('INVALID_CONFIGURATION')
    return {'validator_revision': revision(directory, check), 'image_digest': image,
            'configuration_digest': digest({'repository': config['repository'],
                                           'image': image, 'mode': 'OFFLINE',
                                           'source': 'refs/heads/main', 'schema': 1})}


def envelope(report, identity):
    return {'schema': 1, 'candidate_sha': report['commit_sha'], **identity, 'report': report}


def validate_envelope(raw, sha, expected=None):
    try:
        if type(raw) is not bytes or len(raw) > MAX_REPORT:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if (type(value) is not dict or set(value) != BINDING_FIELDS | {'schema', 'candidate_sha', 'report'}
                or type(value['schema']) is not int or value['schema'] != 1
                or not re.fullmatch(r'[0-9a-f]{40}', sha) or value['candidate_sha'] != sha
                or any(type(value[k]) is not str or not HEX.fullmatch(value[k])
                       for k in ('validator_revision', 'configuration_digest'))
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_digest'])):
            raise ValueError
        if expected is not None and any(value[k] != expected[k] for k in BINDING_FIELDS):
            raise ValueError
        validate_report(json.dumps(value['report']).encode(), sha)
        return value
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise ValueError('INVALID_RELEASE_EVIDENCE') from None


def read_cache(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > MAX_REPORT):
            raise ValueError('INVALID_RELEASE_EVIDENCE')
        raw = stream.read(MAX_REPORT + 1)
    # Validate even mismatched cache entries before deciding they are not reusable.
    parsed = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    return validate_envelope(raw, parsed['candidate_sha'])


def cached_release(config, run, sha, identity, *, runs, verify, execute, current_binding):
    """Called under the controller's existing process-lifetime validation lock.

    One bounded last-success file. The container cannot mount/access this path.
    No candidate, Actions artifact or commit status can populate the cache.
    """
    token = Path(config['github_token_file']).read_text().strip()
    if not token:
        raise ValueError('INVALID_CONFIGURATION')
    verify(sha, token)  # Recheck main eligibility even when reusing successful evidence.
    path = runs / 'release-evidence.json'
    saved = read_cache(path)
    if (saved is not None and saved['candidate_sha'] == sha
            and all(saved[k] == identity[k] for k in BINDING_FIELDS)
            and saved['report']['result'] == 'PASS'):
        return saved['report']
    report = execute()
    if current_binding() != identity:
        raise ValueError('INVALID_CONFIGURATION')
    result = envelope(report, identity)
    raw = json.dumps(result, sort_keys=True, separators=(',', ':')).encode()
    validate_envelope(raw, sha, identity)
    return report


def save_success(report, identity, runs):
    """Parent only: service exited successfully AND final cleanup was verified."""
    if report['result'] != 'PASS':
        return
    raw = json.dumps(envelope(report, identity), sort_keys=True, separators=(',', ':')).encode()
    validate_envelope(raw, report['commit_sha'], identity)
    temporary = runs / 'release-evidence.tmp'
    created = False
    try:
        with temporary.open('xb') as stream:
            created = True
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, runs / 'release-evidence.json')
    finally:
        if created:
            temporary.unlink(missing_ok=True)
