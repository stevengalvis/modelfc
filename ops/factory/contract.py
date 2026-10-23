"""Model-neutral mailbox contracts. No prompts, credentials or executable inputs."""
from dataclasses import dataclass
import hashlib
import json
import re

REPOSITORY = 'stevengalvis/modelfc'
MAX_BODY = 2048
MAX_RESULT = 768


class Rejected(ValueError):
    """Deliberately contains no untrusted diagnostic content."""


def strict_json(raw):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise Rejected()
            obj[key] = value
        return obj
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Rejected()))
    except (ValueError, TypeError, RecursionError):
        raise Rejected() from None


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class Grant:
    """Trusted local, single-job approval; never constructed from an issue."""
    issue: int
    sender_id: int
    job_id: str
    revision: str
    content_digest: str
    expires_at: int

    def __post_init__(self):
        if (type(self.issue) is not int or not 1 <= self.issue <= 9999999999
                or type(self.sender_id) is not int or self.sender_id < 1
                or not isinstance(self.job_id, str)
                or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', self.job_id)
                or not isinstance(self.revision, str)
                or not re.fullmatch(r'[0-9a-f]{40}', self.revision)
                or not isinstance(self.content_digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', self.content_digest)
                or type(self.expires_at) is not int):
            raise Rejected()


def validate(body, grant, now):
    try:
        if type(body) is not str or len(body.encode('utf-8')) > MAX_BODY:
            raise Rejected()
    except UnicodeError:
        raise Rejected() from None
    job = strict_json(body)
    fields = {'version', 'job_id', 'repository', 'revision', 'expires_at',
              'mode', 'inputs', 'content_digest'}
    if type(job) is not dict or set(job) != fields:
        raise Rejected()
    payload = {k: v for k, v in job.items() if k != 'content_digest'}
    if (type(job['version']) is not int or job['version'] != 1
            or job['job_id'] != grant.job_id or job['repository'] != REPOSITORY
            or job['revision'] != grant.revision or job['mode'] != 'dry-run'
            or job['inputs'] != {'probe': 'mailbox-round-trip'}
            or type(job['expires_at']) is not int
            or not now < job['expires_at'] <= min(grant.expires_at, now + 86400)
            or job['content_digest'] != grant.content_digest
            or digest(payload) != grant.content_digest):
        raise Rejected()
    return job


def result(grant, status):
    if status not in {'acknowledged', 'cancelled', 'rejected', 'uncertain'}:
        raise Rejected()
    raw = json.dumps(dict(version=1, job_id=grant.job_id, repository=REPOSITORY,
                          revision=grant.revision, content_digest=grant.content_digest,
                          status=status, mode='dry-run', code_executed=False),
                     sort_keys=True, separators=(',', ':'))
    assert len(raw.encode()) <= MAX_RESULT
    return raw
