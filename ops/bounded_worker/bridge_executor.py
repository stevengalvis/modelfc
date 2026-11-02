"""Trusted local launcher adapter; no transport or publisher credentials."""
import os
from pathlib import Path
import pwd
import subprocess

from . import launcher
from .bridge_security import protected_dir, protected_tree


class Executor:
    def __init__(self, config, mirror):
        if config.prepared_baseline is not True:
            raise ValueError('prepared_baseline_required')
        self.config, self.mirror = config, Path(mirror)
        protected_tree(self.mirror)

    @staticmethod
    def _git(*args, cwd=None):
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
        result = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c',
                                 'core.fsmonitor=false', '-c', 'credential.helper=', *args],
                                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=60, check=False)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError('checkout_failed')
        return result.stdout

    def run(self, task):
        task.validate()
        worker = launcher.Codex(self.config.username, self.config.executable, self.config.executable_sha256)
        work_root, _ = launcher._roots(self.config, worker)
        base = self._git('--git-dir', str(self.mirror), 'rev-parse', 'refs/heads/main').strip().decode()
        if base != task.base_revision:
            raise ValueError('stale_base')
        dest = work_root / task.task_id
        # clone refuses a preexisting destination, so no attempt is ever adopted.
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
        submitted = launcher.submit(task, self.config)
        inspected = launcher.inspect(task.task_id, self.config)
        if submitted != inspected:
            raise ValueError('result_mismatch')
        return inspected
