"""Read-only binding of publisher input to the existing launcher evidence."""
from dataclasses import asdict
import hashlib
import os
import stat
from pathlib import Path

from . import launcher


class Evidence:
    def __init__(self, config):
        if config.prepared_baseline is not True:
            raise ValueError('prepared_baseline_required')
        self.config = config

    def inspect(self, task):
        result = launcher.inspect(task.task_id, self.config)
        state = launcher._read(Path(self.config.evidence_root) / task.task_id / 'state.json')
        task_hash = hashlib.sha256(launcher._json_bytes(asdict(task))).hexdigest()
        if (state.get('task_sha256') != task_hash or state.get('base_revision') != task.base_revision
                or state.get('repository') != task.repository):
            raise ValueError('task_binding_mismatch')
        return result

    def patch(self, task, digest):
        if self.inspect(task).get('diff_sha256') != digest:
            raise ValueError('inspect_mismatch')
        path = Path(self.config.evidence_root) / task.task_id / 'attempt' / 'diff.patch'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_size > task.diff_bytes):
                raise ValueError('invalid_patch')
            patch = source.read(task.diff_bytes + 1)
            if len(patch) != info.st_size or hashlib.sha256(patch).hexdigest() != digest:
                raise ValueError('patch_mismatch')
        return patch
