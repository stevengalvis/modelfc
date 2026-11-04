"""Offline candidate for a no-model sandbox probe; not an operator entrypoint.

This module deliberately has no CLI or fixture orchestration. It does not prove
that the direct sandbox command matches an app-server-generated tool command.
"""
import json
from pathlib import Path
import subprocess

from .codex import DENIED, DISABLED
from .storage_fixture import read_line


# Fixed command, not task or host input. Never opens credential files. The parent
# keeps stdin open in the interrupted case until containment cleanup runs.
PROBE = '''import errno,json,os,sys
fd = os.open("fixture.txt", os.O_WRONLY | os.O_NOFOLLOW)
os.close(fd)
for name in (".agents", ".aws", ".codex"):
    try:
        fd = os.open(name + "/fixture-denied", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise
    else:
        os.close(fd)
        raise RuntimeError("unexpected_write")
print(json.dumps({"checks":"writable_workspace_and_readonly_metadata"}), flush=True)
if sys.argv[1] == "interrupted":
    os.read(0, 1)
'''


def command(executable, work, home, mode):
    """Build fixed CLI arguments with adapter-derived pilot restrictions."""
    if mode not in ('normal', 'interrupted'):
        raise ValueError('invalid_probe_mode')
    paths = (executable, work, home)
    if any(not isinstance(p, str) or not p.startswith('/') or str(Path(p)) != p
           or '..' in Path(p).parts for p in paths):
        raise ValueError('invalid_probe_path')
    workspace = Path(work)
    for denied in (home + '/.codex', *DENIED, executable):
        denied_path = Path(denied)
        if workspace == denied_path or workspace in denied_path.parents or denied_path in workspace.parents:
            raise ValueError('overlapping_probe_paths')
    fs = {':minimal': 'read', work: 'write', **dict.fromkeys((home + '/.codex', *DENIED), 'deny'),
          executable: 'read'}
    if len(fs) != 3 + len(DENIED) + 1:
        raise ValueError('overlapping_probe_paths')
    entries = ', '.join(f'{json.dumps(k)} = {json.dumps(v)}' for k, v in fs.items())
    args = [executable, '-c', 'default_permissions="pilot"',
            '-c', f'permissions.pilot.filesystem = {{ {entries} }}',
            '-c', 'permissions.pilot.network.enabled=false', '-c', 'web_search="disabled"',
            '-c', 'shell_environment_policy.inherit="none"',
            '-c', 'shell_environment_policy.set = { PATH = "/usr/bin:/bin" }',
            '-c', 'model_reasoning_effort="medium"']
    for feature in DISABLED:
        args += ['--disable', feature]
    # Managed configuration is intentionally omitted pending a reviewed,
    # credential-free network boundary for CLI configuration bootstrap.
    return args + ['--enable', 'code_mode_host', 'sandbox', '--permission-profile', 'pilot',
                   '-C', work, '--', '/usr/bin/python3', '-I', '-S', '-B', '-c', PROBE, mode]


class CandidateWorker:
    """Worker adapter only; a separately reviewed supervisor must verify cleanup."""
    def __init__(self, pinned_binary, account, mode):
        self.pinned_binary, self.account, self.mode = pinned_binary, account, mode
        self.child = None

    def run(self, task, work, containment):
        del task
        pinned = self.pinned_binary._binary()
        executable = str(self.pinned_binary.executable)
        home = self.account.pw_dir
        args = command(executable, str(work), home, self.mode)
        if pinned != self.pinned_binary._binary():
            raise ValueError('probe_binary_changed')
        env = {'HOME': home, 'CODEX_HOME': home + '/.codex',
               'PATH': home + '/.local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1'}
        self.child = containment.launch(args, cwd=work, env=env, user=self.account.pw_uid,
                                        group=self.account.pw_gid, extra_groups=[],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        if pinned != self.pinned_binary._binary():
            raise ValueError('probe_binary_changed')
        report = read_line(self.child.stdout)
        if (type(report) is not dict or set(report) != {'checks'}
                or report['checks'] != 'writable_workspace_and_readonly_metadata'):
            raise ValueError('invalid_probe_report')
        if self.mode == 'normal':
            if self.child.wait(timeout=10) != 0:
                raise ValueError('probe_exit_failed')
            if pinned != self.pinned_binary._binary():
                raise ValueError('probe_binary_changed')
            return {'status': 'completed', 'errors': [], 'usage': None}
        if self.child.poll() is not None:
            raise ValueError('probe_not_running')
        if pinned != self.pinned_binary._binary():
            raise ValueError('probe_binary_changed')
        return {'status': 'timeout', 'errors': [], 'usage': None}
