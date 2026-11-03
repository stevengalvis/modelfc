"""Opt-in real containment check; never run without isolated-host authorization.

Set MODELFC_SYSTEMD_ACCEPTANCE=1 only on the authorized host, along with
MODELFC_TEST_UID and MODELFC_TEST_GID naming a dedicated non-root account.
A private evidence directory is deliberately retained after every run.
"""
import datetime
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import unittest

from ops.bounded_worker.systemd_backend import SystemdContainment

ACCEPTANCE_COMMAND = ['/usr/bin/python3', '-c',
                      'import os,time; os.fork() or (os.setsid(), '
                      'print(os.getpid(), flush=True)); time.sleep(300)']


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ObservedContainment(SystemdContainment):
    """Record only the bounded readbacks consumed by the existing verifier."""
    def __init__(self, *args):
        super().__init__(*args)
        self.observations = []

    def record(self, kind, value):
        # Startup polling is bounded; do not retain arbitrary command output.
        self.observations.append({'time': timestamp(), 'kind': kind, 'value': value})
        if len(self.observations) > 256:
            self.observations.pop(0)

    def _show(self):
        try:
            fields = super()._show()
        except Exception as exc:
            self.record('systemd_show_error', type(exc).__name__)
            raise
        keys = ('LoadState', 'ActiveState', 'MainPID', 'ControlPID', 'ControlGroup', 'Job', 'Transient')
        self.record('systemd_show', {k: fields[k][:256] for k in keys if k in fields})
        return fields

    def _read_cgroup_events(self, directory):
        try:
            data = super()._read_cgroup_events(directory)
        except OSError as exc:
            self.record('cgroup_events_error', type(exc).__name__)
            raise
        self.record('cgroup_events', data[:1024])
        return data

    def _cgroup_exists(self, directory):
        try:
            result = super()._cgroup_exists(directory)
        except OSError as exc:
            self.record('cgroup_exists_error', type(exc).__name__)
            raise
        self.record('cgroup_exists', result)
        return result


def proc_identity(pid):
    try:
        stat = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
        return {'state': stat[0], 'ppid': int(stat[1]), 'session': int(stat[3]),
                'starttime_ticks': int(stat[19])}
    except FileNotFoundError:
        return {'state': 'absent'}
    except (OSError, ValueError, IndexError):
        return {'state': 'unknown'}


def run_acceptance(root, uid, gid, backend_class=ObservedContainment):
    attempt = root / 'attempt'
    attempt.mkdir(mode=0o700)
    (attempt / 'reservation.json').write_text('{}\n')
    backend = backend_class(attempt, uid, gid)
    backend.bind_attempt(attempt)
    evidence = {'started_at': timestamp(), 'attempt': str(attempt), 'unit': backend.unit,
                'uid': uid, 'gid': gid, 'revision': None, 'readiness_pid': None,
                'readiness_identity': None, 'post_stop_identity': None,
                'launch': 'not_attempted', 'stop': 'not_attempted', 'empty': 'not_attempted',
                'result': 'failed'}
    child = None
    try:
        revision = subprocess.run(['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=True).stdout.strip()
        if len(revision) not in (40, 64) or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('invalid_revision')
        evidence['revision'] = revision
        evidence['launch'] = 'attempted'
        child = backend.launch(ACCEPTANCE_COMMAND, cwd='/tmp', env={'PATH': '/usr/bin:/bin'},
                               user=uid, group=gid, extra_groups=[], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, bufsize=0)
        evidence['launch'] = 'confirmed'
        if not select.select([child.stdout], [], [], 10)[0]:
            raise AssertionError('no child readiness')
        pid = int(child.stdout.readline())
        if pid <= 1:
            raise AssertionError('invalid readiness pid')
        evidence['readiness_pid'] = pid
        evidence['readiness_at'] = timestamp()
        evidence['readiness_identity'] = proc_identity(pid)
    except Exception as exc:
        evidence['error'] = type(exc).__name__
        raise
    finally:
        try:
            evidence['stop_started_at'] = timestamp()
            evidence['stop'] = backend.terminate()
            evidence['stop_finished_at'] = timestamp()
            evidence['empty'] = backend.verify_empty()
            if evidence['readiness_pid'] is not None:
                evidence['post_stop_identity'] = proc_identity(evidence['readiness_pid'])
            if (evidence['launch'] == 'confirmed' and evidence['stop'] is True
                    and evidence['empty'] is True and evidence['readiness_identity'] is not None
                    and evidence['readiness_identity']['state'] not in ('absent', 'unknown')
                    and evidence['post_stop_identity'] is not None
                    and (evidence['post_stop_identity']['state'] == 'absent'
                         or (evidence['post_stop_identity']['state'] == 'Z'
                             and evidence['post_stop_identity']['starttime_ticks']
                             == evidence['readiness_identity']['starttime_ticks']))):
                evidence['result'] = 'passed'
        finally:
            evidence['finished_at'] = timestamp()
            evidence['observations'] = backend.observations
            path = attempt / 'acceptance.json'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as out:
                json.dump(evidence, out, sort_keys=True)
                out.write('\n')
                out.flush()
                os.fsync(out.fileno())
            if child:
                for pipe in (child.stdin, child.stdout, child.stderr):
                    pipe.close()
    if evidence['result'] != 'passed':
        raise AssertionError('acceptance unproven; retain ' + str(root))


@unittest.skipUnless(os.environ.get('MODELFC_SYSTEMD_ACCEPTANCE') == '1',
                     'real systemd acceptance is opt-in and requires isolated-host approval')
class RealSystemdAcceptance(unittest.TestCase):
    def test_detached_session_is_killed(self):
        uid = int(os.environ['MODELFC_TEST_UID'])
        gid = int(os.environ['MODELFC_TEST_GID'])
        root = Path(tempfile.mkdtemp(prefix='modelfc-containment-acceptance-'))
        run_acceptance(root, uid, gid)
