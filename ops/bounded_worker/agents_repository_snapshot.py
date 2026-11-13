"""Closed, inert source wire format shared by fixed container import/export.

This code never executes repository files. Container export is untrusted data,
not proof of every action performed in the worker workspace.
"""
import base64
import hashlib
import json
import re

MAX_FILE = 1048576
MAX_TREE = 16777216
MAX_EXPORT = 24 * 1048576
MAX_FILES = 8192


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_source_key')
        result[key] = value
    return result


def path_allowed(value):
    return (type(value) is str and re.fullmatch(r'[A-Za-z0-9_.\-/]{1,200}', value) is not None
            and not value.startswith(('/', '-'))
            and not any(p in ('', '.', '..', '.git', '.aws', '.codex', '.agents') for p in value.split('/')))


def tree_digest(files):
    return hashlib.sha256(canonical([dict(path=name, mode=420, bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest()) for name, raw in sorted(files.items())])).hexdigest()


def encode_snapshot(files, task_sha256, attempt_id, baseline_sha256):
    raw = canonical(dict(version=1, task_sha256=task_sha256, attempt_id=attempt_id,
        baseline_sha256=baseline_sha256, files=[dict(path=name, mode=420,
        content_base64=base64.b64encode(content).decode('ascii')) for name, content in sorted(files.items())]))
    decode_snapshot(raw, task_sha256, attempt_id, baseline_sha256)
    return raw


def decode_snapshot(raw, task_sha256, attempt_id, baseline_sha256):
    if (type(raw) is not bytes or len(raw) > MAX_EXPORT
            or any(type(v) is not str or not re.fullmatch(r'[a-f0-9]{64}', v)
                   for v in (task_sha256, attempt_id, baseline_sha256))):
        raise ValueError('source_limit_or_identity')
    obj = json.loads(raw, object_pairs_hook=pairs)
    if (type(obj) is not dict or set(obj) != {'version', 'task_sha256', 'attempt_id', 'baseline_sha256', 'files'}
            or type(obj['version']) is not int or obj['version'] != 1
            or obj['task_sha256'] != task_sha256 or obj['attempt_id'] != attempt_id
            or obj['baseline_sha256'] != baseline_sha256 or canonical(obj) != raw
            or type(obj['files']) is not list or not 1 <= len(obj['files']) <= MAX_FILES):
        raise ValueError('source_binding')
    files, last, total = {}, '', 0
    for item in obj['files']:
        if (type(item) is not dict or set(item) != {'path', 'mode', 'content_base64'}
                or not path_allowed(item['path']) or item['path'] <= last
                or type(item['mode']) is not int or item['mode'] != 420
                or type(item['content_base64']) is not str
                or len(item['content_base64']) > (MAX_FILE + 2) // 3 * 4):
            raise ValueError('source_file')
        data = base64.b64decode(item['content_base64'], validate=True)
        if len(data) > MAX_FILE or base64.b64encode(data).decode('ascii') != item['content_base64']:
            raise ValueError('source_file')
        total += len(data)
        if total > MAX_TREE:
            raise ValueError('source_tree_limit')
        last = item['path']
        if any('/'.join(last.split('/')[:n]) in files for n in range(1, len(last.split('/')))):
            raise ValueError('source_prefix_collision')
        files[last] = data
    return files


# Included literally in the fixed -I/-S container programs, not imported from
# worker storage. Keep the parser implementation identical across both ends.
def container_codec_source():
    from pathlib import Path
    raw = Path(__file__).read_text(encoding='utf-8')
    return raw[:raw.index('\n# Included literally')]
