"""Discover the private Responses worker's offline suite without global imports."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class ResponsesWorkerOfflineTests(unittest.TestCase):
    def test_offline_worker_suite(self):
        root = Path(__file__).resolve().parents[1]
        helper = root / 'ops' / 'bounded_worker' / 'responses_usage_pilot'
        program = '''import ast,pathlib,socket,sys,unittest
root=pathlib.Path(sys.argv[1])
[ast.parse(p.read_text(),filename=str(p)) for p in root.rglob("*.py")]
sys.path[:0]=[str(root),sys.argv[2]]
def deny(*args,**kwargs):
    raise RuntimeError("offline_network_disabled")
socket.create_connection=deny
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.getaddrinfo=deny
suite=unittest.defaultTestLoader.discover(str(root/"tests"),pattern="test_*.py")
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun==150 else 1)
'''
        env = {'PATH': os.defpath, 'LANG': 'C.UTF-8'}
        if os.environ.get('TMPDIR'):
            env['TMPDIR'] = os.environ['TMPDIR']
        result = subprocess.run([sys.executable, '-I', '-B', '-c', program,
                                 str(helper), str(root)],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
