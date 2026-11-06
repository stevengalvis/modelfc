"""Local executable analog, not an operator entry point or private adapter.

Uses the public controller's real capture helper and collector. Only its in-memory
bootstrap module target changes, reproducing the proposed focused entry point.
Synthetic cases use disposable packages; the focused case runs the real suite.
"""
import importlib.util
import os
from pathlib import Path
import signal
import sys
import tempfile

from ops.ci.component_campaign import BASELINE_IDS, Observation, SYNTHETIC_IDS


FIXTURE = '''import os, signal, subprocess, sys, time, unittest
from pathlib import Path
MODE = {mode!r}
class Sample(unittest.TestCase):
    def exercise(self, index):
        if index or MODE == 'success': return
        Path('marker').write_text(MODE)
        if MODE == 'assertion': self.fail('intentional assertion')
        if MODE == 'skip': self.skipTest('intentional skip')
        if MODE == 'interruption': os.kill(os.getpid(), signal.SIGTERM)
        if MODE == 'descendant':
            child = subprocess.Popen([sys.executable, '-c',
                "import time; time.sleep(2)"], start_new_session=True)
            Path('descendant').write_text(str(child.pid))
        if MODE in ('timeout', 'descendant'): time.sleep(5)
for i in range(14):
    setattr(Sample, 'test_%02d' % i, lambda self, index=i: self.exercise(index))
'''

DRIVER = '''import importlib.util, unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('actual_acceptance', {runner!r})
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)
acceptance.TEST_IDS = {ids!r}
MODE = {mode!r}
if MODE == 'missing':
    Path('marker').write_text(MODE)
    acceptance.TEST_IDS = acceptance.TEST_IDS[:-1] + ('tests.test_fixture.Absent.test_missing',)
if MODE == 'incomplete':
    Path('marker').write_text(MODE)
    class Incomplete(unittest.TestSuite):
        def run(self, result, debug=False): return result
    original = acceptance.load_suite
    acceptance.load_suite = lambda: Incomplete(original())
raise SystemExit(acceptance.main())
'''


class Tee:
    def __init__(self, collector):
        self.collector = collector
        self.raw = bytearray()

    def feed(self, chunk):
        if len(self.raw) + len(chunk) > 4 * 1024 * 1024:
            raise ValueError('event overflow')
        self.raw.extend(chunk)
        self.collector.feed(chunk)


class PublicAnalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        spec = importlib.util.spec_from_file_location('public_component_controller',
                                                     self.root / 'ops/vps/deploy_main.py')
        self.controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.controller)
        self.baseline = self.controller.TEST_BOOTSTRAP
        before = 'sys.argv = ["python -m unittest", "discover", "-v", "-s", "tests"]'
        target = 'runpy.run_module("unittest", run_name="__main__")'
        if self.baseline.count(before) != 1 or self.baseline.count(target) != 1:
            raise ValueError('public bootstrap changed')
        self.focused = self.baseline.replace(before, 'sys.argv = ["deployment_acceptance"]').replace(
            target, 'runpy.run_module("ops.vps.deployment_acceptance", run_name="__main__")')

    def run(self, case):
        from ops.vps.deployment_acceptance import TEST_IDS
        with tempfile.TemporaryDirectory(prefix='component-') as directory:
            scratch = Path(directory)
            mode = 'success' if case in ('hook_baseline', 'hook_candidate') else case
            cwd = self.root if case == 'focused' else scratch
            if case != 'focused':
                for package in ('tests', 'ops', 'ops/vps'):
                    (scratch / package).mkdir(exist_ok=True)
                    (scratch / package / '__init__.py').touch()
                (scratch / 'tests/test_fixture.py').write_text(FIXTURE.format(mode=mode))
                (scratch / 'ops/vps/deployment_acceptance.py').write_text(DRIVER.format(
                    runner=str(self.root / 'ops/vps/deployment_acceptance.py'),
                    ids=SYNTHETIC_IDS, mode=mode))
            expected = TEST_IDS if case == 'focused' else BASELINE_IDS if case == 'hook_baseline' else SYNTHETIC_IDS
            progress = self.controller.TestProgress(set(expected))
            tee = Tee(progress)
            self.controller.TEST_BOOTSTRAP = self.baseline if case == 'hook_baseline' else self.focused
            env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'PYTHONPATH': 'src',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                   'TMPDIR': tempfile.gettempdir()}
            descendant_alive = None
            cleanup = False
            try:
                try:
                    code, stderr, truncated = self.controller.capture_test_process(
                        [sys.executable], cwd=cwd, env=env, progress=tee,
                        timeout=0.5 if case in ('timeout', 'descendant') else 90)
                    classification = None
                except self.controller.TestCaptureFailure as error:
                    code, stderr, truncated = None, error.captured, error.truncated
                    classification = error.classification
                if truncated:
                    raise ValueError('truncated evidence')
            finally:
                # Local-only cleanup; production uses the isolated unit's cgroup.
                # Never signal a PID from a fixture file. The bounded descendant
                # exits itself; this observes termination, not cgroup cleanup.
                path = scratch / 'descendant'
                if path.exists():
                    try:
                        fd = os.pidfd_open(int(path.read_text()))
                        try:
                            import select
                            ready, _, _ = select.select([fd], [], [], 0)
                            descendant_alive = not bool(ready)
                            ready, _, _ = select.select([fd], [], [], 3)
                            cleanup = bool(ready)
                        finally:
                            os.close(fd)
                    except ProcessLookupError:
                        descendant_alive, cleanup = False, True
                else:
                    cleanup = True
            marker = scratch / 'marker'
            return Observation(code, classification, stderr, bytes(tee.raw), progress.snapshot(),
                               marker.read_text() if marker.exists() else None,
                               descendant_alive, cleanup)

    def report_accepts(self, report, expected_sha, cleanup_complete):
        return None  # No separable public pure validator; never call tests()/deploy().
