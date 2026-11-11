"""Offline envelope validation only; this is not an intake or execution service.

A trusted future transport must supply independently authenticated comment metadata.
No caller should use this module to authorize execution without that boundary.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from .task import Task


@dataclass(frozen=True)
class Policy:
    actor_id: int
    thread: int
    paths: tuple[str, ...]
    publication_authorized: bool = False
    max_timeout: int = 90
    max_output: int = 262144
    max_diff: int = 4096


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def validate(body, metadata, policy, now):
    """Validate canonical bytes and trusted metadata; no I/O or authorization grant."""
    if (type(body) is not bytes or len(body) > 16384 or type(now) is not int
            or type(policy.actor_id) is not int or policy.actor_id <= 0
            or type(policy.thread) is not int or policy.thread <= 0
            or policy.publication_authorized is not True):
        raise ValueError('invalid_input')
    try:
        obj = json.loads(body.decode('utf-8'), object_pairs_hook=_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('invalid_json') from None
    if (type(obj) is not dict or set(obj) != {'version', 'job_id', 'base', 'paths',
            'prompt', 'expires', 'timeout', 'output', 'diff', 'publish'}
            or json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() != body
            or type(obj['version']) is not int or obj['version'] != 1
            or type(obj['expires']) is not int or not now < obj['expires'] <= now + 86400
            or obj['publish'] is not True):
        raise ValueError('invalid_envelope')
    if (type(metadata) is not dict or set(metadata) != {'actor_id', 'thread', 'comment_id', 'last_edited_at'}
            or type(metadata['actor_id']) is not int or metadata['actor_id'] != policy.actor_id
            or type(metadata['thread']) is not int or metadata['thread'] != policy.thread
            or type(metadata['comment_id']) is not int or metadata['comment_id'] <= 0
            or metadata['last_edited_at'] is not None):
        raise ValueError('invalid_provenance')
    if (type(obj['job_id']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', obj['job_id'])
            or type(obj['paths']) is not list or any(type(p) is not str for p in obj['paths'])
            or not all(p in policy.paths for p in obj['paths'])
            or type(obj['timeout']) is not int or obj['timeout'] > policy.max_timeout
            or type(obj['output']) is not int or obj['output'] > policy.max_output
            or type(obj['diff']) is not int or obj['diff'] > policy.max_diff):
        raise ValueError('outside_policy')
    task = Task(obj['job_id'], 'stevengalvis/modelfc', obj['base'], obj['prompt'],
                tuple(obj['paths']), obj['timeout'], obj['output'], obj['diff'])
    task.validate()
    return task, hashlib.sha256(body).hexdigest()
