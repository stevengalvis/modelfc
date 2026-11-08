"""Optional local artifact preparation only; no transport, CLI or automatic send."""
from dataclasses import asdict
import hashlib
import re
from pathlib import Path

from . import launcher
from .bridge_security import protected_bytes, protected_json
from .linear_coding_intake import _canonical, _task, binding, _uuid


def patch_paths(raw, allowed):
    """Accept only the bounded unified-text format produced by task.execute."""
    if type(raw) is not bytes or len(raw) > 4096:
        raise ValueError('patch_size')
    text = raw.decode('utf-8')
    if any(ord(c) < 32 and c not in '\n\t' or ord(c) == 127 for c in text):
        raise ValueError('patch_control')
    lines, i, paths = text.splitlines(keepends=True), 0, []
    while i < len(lines):
        if not lines[i].startswith('--- a/') or not lines[i].endswith('\n'):
            raise ValueError('patch_header')
        name = lines[i][6:-1]
        if name not in allowed or name in paths:
            raise ValueError('patch_scope')
        if i + 1 >= len(lines) or lines[i + 1] != '+++ b/' + name + '\n':
            raise ValueError('patch_header')
        paths.append(name)
        i += 2
        hunks = 0
        while i < len(lines) and lines[i].startswith('@@ '):
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n', lines[i])
            if not match:
                raise ValueError('patch_hunk')
            old, new = int(match[2] or 1), int(match[4] or 1)
            if old + new > 8192:
                raise ValueError('patch_hunk')
            i += 1
            changed = False
            while old or new:
                if i >= len(lines) or not lines[i].endswith('\n'):
                    raise ValueError('patch_hunk')
                prefix = lines[i][:1]
                if prefix not in (' ', '-', '+'):
                    raise ValueError('patch_hunk')
                old -= prefix in (' ', '-')
                new -= prefix in (' ', '+')
                changed |= prefix in ('-', '+')
                if old < 0 or new < 0:
                    raise ValueError('patch_hunk')
                i += 1
            if not changed:
                raise ValueError('patch_hunk')
            hunks += 1
        if not hunks:
            raise ValueError('patch_hunk')
    return paths


def prepare_export(request, config, policy_path, *, _policy_loader=None):
    """Caller holds coding.lock. Policy is separate protected disclosure opt-in.

    This never executes a worker or sends a message. It reads only a completed
    retained artifact; approval to run code alone does not approve disclosure.
    """
    load = _policy_loader or (lambda: protected_json(policy_path))
    policy = load()
    if (type(policy) is not dict or set(policy) !=
            {'version', 'enabled', 'request_sha256', 'paths', 'viewer'}
            or type(policy['version']) is not int or policy['version'] != 1
            or policy['enabled'] is not True or not _uuid(policy['viewer'])
            or policy['request_sha256'] != binding(request)
            or type(policy['paths']) is not list or not policy['paths']
            or any(type(p) is not str for p in policy['paths'])
            or len(set(policy['paths'])) != len(policy['paths'])):
        raise ValueError('export_not_authorized')
    obj, task = _task(request)
    if not set(policy['paths']).issubset(task.allowed_paths):
        raise ValueError('export_scope')
    inspected = launcher.inspect(task.task_id, config)
    if inspected.get('task_id') != task.task_id or inspected.get('status') != 'completed':
        raise ValueError('artifact_not_completed')
    root = Path(config.evidence_root) / task.task_id
    state = protected_json(root / 'state.json')
    if state.get('task_sha256') != hashlib.sha256(launcher._json_bytes(asdict(task))).hexdigest():
        raise ValueError('artifact_task_mismatch')
    raw = protected_bytes(root / 'attempt' / 'diff.patch', maximum=min(4096, task.diff_bytes))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != inspected.get('diff_sha256'):
        raise ValueError('artifact_digest_mismatch')
    paths = patch_paths(raw, policy['paths'])
    # Recheck protected consent and durable evidence after reading the artifact.
    if load() != policy or launcher.inspect(task.task_id, config) != inspected:
        raise ValueError('artifact_changed')
    return _canonical(dict(version=1, operation='coding-patch', job_id=obj['job_id'],
        task_id=task.task_id, request_comment_id=request.comment,
        request_sha256=binding(request), diff_sha256=digest,
        paths=paths, empty=not raw, tests='not recorded', patch=raw.decode('utf-8')))
