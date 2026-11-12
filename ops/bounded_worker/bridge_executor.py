"""Trusted local launcher adapter; no transport or publisher credentials."""
import os
from pathlib import Path
import pwd
import subprocess

from . import launcher
from .bridge_security import protected_dir, protected_tree
from .storage import admit


class Executor:
    def __init__(self, config, mirror):
        if config.prepared_baseline is not True:
            raise ValueError('prepared_baseline_required')
        self.config, self.mirror = config, Path(mirror)
        protected_tree(self.mirror)

    @staticmethod
    def _git(*args, cwd=None, pass_fds=(), umask=-1):
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
        result = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c',
                                 'core.fsmonitor=false', '-c', 'credential.helper=', *args],
                                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=60, check=False, pass_fds=pass_fds, umask=umask)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError('checkout_failed')
        return result.stdout

    def run(self, task):
        task.validate()
        worker = launcher.Codex(self.config.username, self.config.executable, self.config.executable_sha256)
        storage_identity = None
        if self.config.storage is not None:
            if self.config.workspace_root != self.config.storage.workspace:
                raise ValueError('storage_workspace_mismatch')
            storage_identity = admit(self.config.storage, worker_uid=worker.account.pw_uid,
                                     worker_gid=worker.account.pw_gid)
        work_root, evidence_root = launcher._roots(self.config, worker)
        base = self._git('--git-dir', str(self.mirror), 'rev-parse', 'refs/heads/main').strip().decode()
        if base != task.base_revision:
            raise ValueError('stale_base')
        if storage_identity is not None:
            launcher.record_storage_admission(task, self.config, evidence_root, storage_identity)
        dest = work_root / task.task_id
        if storage_identity is None:
            self._git('clone', '--no-local', '--no-checkout', '--', str(self.mirror), str(dest))
            self._git('checkout', '--detach', task.base_revision, cwd=dest)
            if self._git('rev-parse', 'HEAD', cwd=dest).strip().decode() != task.base_revision:
                raise ValueError('checkout_base_mismatch')
            account = pwd.getpwnam(self.config.username)
            for directory, dirs, files in os.walk(dest, topdown=False, followlinks=False):
                for name in dirs + files:
                    os.chown(Path(directory) / name, account.pw_uid, account.pw_gid, follow_symlinks=False)
                os.chown(directory, account.pw_uid, account.pw_gid, follow_symlinks=False)
            os.chmod(dest, 0o700)
        else:
            # Keep all checkout writes on the opened filesystem even if its
            # mountpoint is subsequently detached or replaced.
            fd = os.open(work_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                if f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' != storage_identity['device']:
                    raise ValueError('storage_identity_changed')
                fd_dest = Path('/proc/self/fd') / str(fd) / task.task_id
                self._git('clone', '--no-local', '--no-checkout', '--', str(self.mirror), str(fd_dest),
                          pass_fds=(fd,))
                self._git('checkout', '--detach', task.base_revision, cwd=fd_dest, pass_fds=(fd,))
                if self._git('rev-parse', 'HEAD', cwd=fd_dest, pass_fds=(fd,)).strip().decode() != task.base_revision:
                    raise ValueError('checkout_base_mismatch')
                if admit(self.config.storage, worker_uid=worker.account.pw_uid,
                         worker_gid=worker.account.pw_gid) != storage_identity:
                    raise ValueError('storage_identity_changed')
                account = pwd.getpwnam(self.config.username)
                for directory, dirs, files in os.walk(fd_dest, topdown=False, followlinks=False):
                    for name in dirs + files:
                        os.chown(Path(directory) / name, account.pw_uid, account.pw_gid, follow_symlinks=False)
                    os.chown(directory, account.pw_uid, account.pw_gid, follow_symlinks=False)
                os.chmod(fd_dest, 0o700)
            finally:
                os.close(fd)
        if storage_identity is None:
            submitted = launcher.submit(task, self.config)
        else:
            submitted = launcher.submit(task, self.config, _storage_identity=storage_identity)
        inspected = launcher.inspect(task.task_id, self.config)
        if submitted != inspected:
            raise ValueError('result_mismatch')
        return inspected
