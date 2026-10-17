"""Unprivileged full-suite capture. Installed code, never imported from candidate."""
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

ROOT = Path('/usr/lib/modelfc-suite')
DEADLINE = 300
# Instrument only the stdlib runner; execute unchanged discovery and test bodies.
BOOTSTRAP = r'''
import json, os, time, unittest
fd = int(os.environ.pop('MODELFC_PROGRESS_FD'))
def emit(value):
    raw = (json.dumps(value, separators=(',', ':')) + '\n').encode()
    if len(raw) <= 512:
        try: os.write(fd, raw)
        except OSError: pass
start = unittest.TextTestResult.startTest
stop = unittest.TextTestResult.stopTest
run = unittest.TextTestRunner.run
def started(self, test):
    self._suite_start = time.monotonic()
    return start(self, test)
def stopped(self, test):
    emit(dict(event='stop', test=test.id()[:160], seconds=time.monotonic()-self._suite_start))
    return stop(self, test)
def completed(self, suite):
    result = run(self, suite)
    emit(dict(event='terminal', tests=result.testsRun, failures=len(result.failures),
              errors=len(result.errors), skipped=len(result.skipped),
              expected_failures=len(result.expectedFailures),
              unexpected_successes=len(result.unexpectedSuccesses)))
    return result
unittest.TextTestResult.startTest = started
unittest.TextTestResult.stopTest = stopped
unittest.TextTestRunner.run = completed
unittest.main(module=None, argv=['unittest', 'discover', '-v', '-s', 'tests'])
'''


def capture(command, cwd, env, seconds=DEADLINE):
    """Only tests use a shorter deadline; installed entrypoint always uses 300s."""
    origin = time.monotonic()
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    os.set_blocking(write_fd, False)
    env = {**env, 'MODELFC_PROGRESS_FD': str(write_fd)}
    child = None
    observed, last, terminal, tail, pending = 0, None, None, b'', b''
    slowest = []
    truncated = False
    timed_out = False
    try:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 pass_fds=(write_fd,), start_new_session=True)
        os.close(write_fd); write_fd = -1
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ, 'progress')
            selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = origin + seconds - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'stderr':
                        truncated |= len(tail) + len(chunk) > 4096
                        tail = (tail + chunk)[-4096:]
                        continue
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        if len(line) > 512:
                            continue
                        try:
                            item = json.loads(line)
                            if item.get('event') == 'stop':
                                name, duration = item['test'], item['seconds']
                                if not re.fullmatch(r'[A-Za-z0-9_.]{1,160}', name):
                                    continue
                                if type(duration) not in (int, float) or not 0 <= duration <= seconds:
                                    continue
                                observed += 1
                                last = dict(test=name, elapsed_seconds=round(time.monotonic()-origin, 6))
                                slowest.append(dict(test=name, seconds=round(duration, 6)))
                                slowest = sorted(slowest, key=lambda x: x['seconds'], reverse=True)[:8]
                            elif item.get('event') == 'terminal' and set(item) == {
                                'event','tests','failures','errors','skipped','expected_failures','unexpected_successes'
                            } and all(type(v) is int and 0 <= v <= 1000000 for k,v in item.items() if k != 'event'):
                                terminal = {k:v for k,v in item.items() if k != 'event'}
                        except (ValueError, TypeError, KeyError):
                            pass
                    if len(pending) > 512:
                        pending = b''
                if child.poll() is not None and not selector.get_map():
                    break
        if timed_out:
            os.killpg(child.pid, signal.SIGKILL)
        code = child.wait(timeout=2)
        summary = re.search(rb'(?m)^Ran ([0-9]+) tests? in [0-9.]+s\r?\n\r?\n(OK(?: \([^\r\n]*\))?|FAILED(?: \([^\r\n]*\))?)\r?\n*\Z', tail)
        complete = not timed_out and terminal is not None and summary is not None and int(summary[1]) == terminal['tests']
        if timed_out:
            classification = 'TIMEOUT'
        elif not complete:
            classification = 'INCOMPLETE'
        elif code == 0 and summary[2].startswith(b'OK') and terminal['tests'] > 0 and not any(terminal[k] for k in ('failures','errors','unexpected_successes')):
            classification = 'PASS'
        else:
            classification = 'TEST_FAILURE'
        return dict(classification=classification, complete=complete,
                    terminal=terminal if complete else None, observed_completed=observed,
                    last_progress=last, slowest=slowest, stderr_truncated=truncated,
                    elapsed_seconds=round(time.monotonic()-origin, 6))
    finally:
        if child is not None:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            try: child.wait(timeout=2)
            except subprocess.TimeoutExpired: pass  # systemd is the independent final cgroup boundary.
            child.stderr.close()
        os.close(read_fd)
        if write_fd >= 0: os.close(write_fd)


def main():
    env = dict(PATH='/usr/bin:/bin', HOME='/nonexistent', PYTHONPATH='src',
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
               GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory',
               GIT_CONFIG_VALUE_0=str(ROOT/'source'))
    result = capture([str(ROOT/'runtime/bin/python3'), '-B', '-c', BOOTSTRAP], ROOT/'source', env)
    result['run_id'] = os.environ['MODELFC_SUITE_RUN_ID']
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['classification'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
