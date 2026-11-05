"""Bounded existing-login CLI process; never used for the root supervisor.

No auth-file reading/copying. Tool-event rejection happens later and is observation,
not preventive host-tool containment. Only the owned, unreaped process group is
signalled; no signals are sent after reaping permits PID/PGID reuse.
"""
from dataclasses import dataclass
import os
import selectors
import signal
import stat
import subprocess
import time


@dataclass(frozen=True)
class ModelConfig:
    cli_fd: int
    uid: int
    gid: int
    home: str
    codex_home: str
    workdir: str


@dataclass(frozen=True)
class Capture:
    stdout: bytes
    stderr: bytes
    exit_code: int
    timedout: bool
    overflow: bool


def need(ok):
    if not ok:
        raise ValueError('MODEL_PROCESS_REJECTED')


def validate(config, prompt):
    need(type(config) is ModelConfig and type(prompt) is bytes and 0<len(prompt)<=8192)
    need(type(config.cli_fd) is int and config.cli_fd>=3)
    need(all(type(value) is int and 0<value<2**32-1 for value in (config.uid,config.gid)))
    info=os.fstat(config.cli_fd)
    need(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0
         and not info.st_mode&0o022 and bool(info.st_mode&0o111))
    need(os.pread(config.cli_fd,4,0)==b'\x7fELF')
    for path in (config.home,config.codex_home,config.workdir):
        need(type(path) is str and path.startswith('/') and path not in ('/','/root')
             and all(part not in ('','.','..') for part in path.split('/')[1:]))
        value=os.lstat(path)
        need(stat.S_ISDIR(value.st_mode) and value.st_uid==config.uid
             and not value.st_mode&0o022)
    need(os.listdir(config.workdir)==[])


def run_model(config, prompt):
    need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0)
    validate(config,prompt)
    args=[f'/proc/self/fd/{config.cli_fd}','exec','--ephemeral','--ignore-user-config','--ignore-rules',
          '--skip-git-repo-check','--sandbox','read-only','--json','--cd',config.workdir,
          '-c','approval_policy="never"','-c','model_provider="openai"','-']
    env={'HOME':config.home,'CODEX_HOME':config.codex_home,'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    child=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=config.workdir,env=env,user=config.uid,group=config.gid,extra_groups=[],umask=0o077,
        start_new_session=True,close_fds=True,pass_fds=(config.cli_fd,))
    # Do not call poll(), communicate() or wait() until the final possible group
    # signal. An exited but unreaped child retains its PID, preventing PGID reuse.
    sel=None
    output={'stdout':bytearray(),'stderr':bytearray()}
    offset=0;timedout=False;overflow=False;reaped=False;signalled=False;may_reap=False
    def signal_group(sig):
        nonlocal signalled
        need(not reaped and not may_reap and child.returncode is None)
        try:os.killpg(child.pid,sig)
        except ProcessLookupError:pass
        signalled=True
    try:
        sel=selectors.DefaultSelector()
        for name,stream,event in (('stdin',child.stdin,selectors.EVENT_WRITE),
                                  ('stdout',child.stdout,selectors.EVENT_READ),('stderr',child.stderr,selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(),False)
            sel.register(stream,event,name)
        deadline=time.monotonic()+90
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                timedout=True
                break
            status=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if status is not None and not any(key.data in output for key in sel.get_map().values()):
                break
            for key,_ in sel.select(min(remaining,0.1)):
                stream=key.fileobj
                if key.data=='stdin':
                    try:count=os.write(stream.fileno(),prompt[offset:])
                    except BrokenPipeError:count=0
                    except BlockingIOError:continue
                    offset+=count
                    if count==0 or offset==len(prompt):
                        sel.unregister(stream);stream.close()
                else:
                    try:data=os.read(stream.fileno(),4096)
                    except BlockingIOError:continue
                    if not data:
                        sel.unregister(stream);stream.close()
                    else:
                        target=output[key.data]
                        room=65536-len(target)
                        target.extend(data[:room])
                        if len(data)>room:
                            overflow=True
                            break
            if overflow:break
        # Terminate any remaining members while the group identity is still pinned
        # by our unreaped child, on success as well as timeout/overflow.
        signal_group(signal.SIGTERM)
        until=time.monotonic()+5
        while time.monotonic()<until:
            time.sleep(min(0.05,max(0,until-time.monotonic())))
        signal_group(signal.SIGKILL)
        may_reap=True
        code=child.wait(timeout=5)
        reaped=True
        need(type(code) is int and os.listdir(config.workdir)==[])
        return Capture(bytes(output['stdout']),bytes(output['stderr']),code,timedout,overflow)
    finally:
        try:
            if not reaped and not may_reap and child.returncode is None:
                # No unbounded retry loop and no further signals after wait.
                if not signalled:
                    signal_group(signal.SIGTERM)
                signal_group(signal.SIGKILL)
                may_reap=True
                child.wait(timeout=5)
                reaped=True
        finally:
            if sel is not None:sel.close()
            for stream in (child.stdin,child.stdout,child.stderr):
                if not stream.closed:stream.close()
