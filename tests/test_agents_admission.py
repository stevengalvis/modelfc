import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from ops.bounded_worker.agents_admission import AdmissionJournal


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.journal = AdmissionJournal(self.path)
        self.addCleanup(self.journal.close)
        self.attempt = 'a' * 64

    def test_durable_commit_is_visible_before_callback(self):
        self.assertEqual(self.journal.db.execute('PRAGMA synchronous').fetchone(), (3,))
        self.assertEqual(self.journal.db.execute('PRAGMA journal_mode').fetchone(), ('delete',))
        def callback():
            second = AdmissionJournal(self.path)
            try:
                with self.assertRaises(ValueError):
                    second.reserve(self.attempt, 'create')
            finally:
                second.close()
        self.journal.run_once(self.attempt, 'create', callback)

    def test_ordered_fake_integration(self):
        calls = []
        for operation in ('create', 'executor', 'input'):
            self.journal.run_once(self.attempt, operation, lambda: calls.append(operation))
        self.assertEqual(calls, ['create', 'executor', 'input'])
        with self.assertRaises(ValueError):
            self.journal.reserve(self.attempt, 'input')

    def test_restart_after_uncertain_call_never_replays(self):
        callback = Mock(side_effect=TimeoutError('synthetic'))
        with self.assertRaises(TimeoutError):
            self.journal.run_once(self.attempt, 'create', callback)
        second = AdmissionJournal(self.path)
        try:
            with self.assertRaises(ValueError):
                second.run_once(self.attempt, 'create', callback)
        finally:
            second.close()
        self.assertEqual(callback.call_count, 1)

    def test_process_exit_after_reservation_never_replays(self):
        code = """
import os, sys
from ops.bounded_worker.agents_admission import AdmissionJournal
journal = AdmissionJournal(sys.argv[1])
journal.run_once('b' * 64, 'create', lambda: os._exit(23))
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.path)],
                                timeout=5, check=False, capture_output=True)
        self.assertEqual(result.returncode, 23)
        with self.assertRaises(ValueError):
            self.journal.reserve('b' * 64, 'create')

    def test_two_processes_only_one_callback(self):
        code = """
import sys
from ops.bounded_worker.agents_admission import AdmissionJournal
journal = AdmissionJournal(sys.argv[1])
try:
    journal.run_once('c' * 64, 'create', lambda: print('CALLED', flush=True))
except ValueError:
    pass
finally:
    journal.close()
"""
        children = [subprocess.Popen([sys.executable, '-B', '-c', code, str(self.path)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(2)]
        outputs = [child.communicate(timeout=5) for child in children]
        self.assertEqual([child.returncode for child in children], [0, 0])
        self.assertEqual(sum(out.count(b'CALLED') for out, _ in outputs), 1)

    def test_out_of_order_has_no_callback(self):
        callback = Mock()
        with self.assertRaises(ValueError):
            self.journal.run_once(self.attempt, 'executor', callback)
        callback.assert_not_called()

    def test_invalid_identifier_rejected(self):
        for attempt in ('', '../escape', 'x' * 64):
            with self.assertRaises(ValueError):
                self.journal.reserve(attempt, 'create')

    def test_private_directory_required(self):
        os.chmod(self.path, 0o755)
        with self.assertRaises(ValueError):
            AdmissionJournal(self.path)
        os.chmod(self.path, 0o700)

    def test_symlink_directory_rejected(self):
        link = self.path / 'linked'
        link.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(ValueError):
            AdmissionJournal(link)

    def test_existing_journal_is_private(self):
        os.chmod(self.path / 'admission.sqlite3', 0o644)
        with self.assertRaises(ValueError):
            AdmissionJournal(self.path)


if __name__ == '__main__':
    unittest.main()
