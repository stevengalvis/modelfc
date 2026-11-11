"""AST-selected pure control tests; never execute the collector entrypoint."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'capture_ownership_v3.py'
ORIGINAL = ROOT / 'source/capture_ownership_v2.py'
EXPECTED_ORIGINAL = 'a10c63987a7526331dde2eda7ca05a47ba182633d466d9167b1ad14f13c47902'


def selected_function():
    tree = ast.parse(SOURCE.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == 'wait_finish']
    if len(nodes) != 1:
        raise AssertionError('WAIT_FINISH_AST_SITE')
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['wait_finish']


class FinishControl(unittest.TestCase):
    def setUp(self):
        self.finish = selected_function()

    def run_case(self, chunks=(), ready=None, clock=None, read_error=None,
                 select_error=None):
        chunks = iter(chunks)
        ready = iter(ready if ready is not None else [True] * 10)
        clock = iter(clock) if clock is not None else None
        clock_calls = [0]
        flags, counts, waits = [], [], []

        def fake_clock():
            if clock is not None:
                return next(clock)
            clock_calls[0] += 1000000
            return clock_calls[0]

        def fake_select(r, w, e, wait):
            waits.append(wait)
            if select_error:
                raise select_error
            return ([7] if next(ready) else [], [], [])

        def fake_read(fd, count):
            counts.append(count)
            if read_error:
                raise read_error
            return next(chunks)

        result = self.finish(fd=7, clock_ns=fake_clock, select_fn=fake_select,
                             read_fn=fake_read, get_blocking=lambda fd: True,
                             set_blocking=lambda fd, state: flags.append(state))
        self.assertEqual(flags, [False, True])
        self.assertTrue(all(0 < count <= 16 for count in counts))
        self.assertTrue(all(0 < wait <= 180 for wait in waits))
        self.assertNotIn('received_line', result)
        return result

    def test_exact_and_split_finish_ack(self):
        for chunks in ((b'finish\n',), (b'fin', b'ish', b'\n')):
            with self.subTest(chunks=chunks):
                result = self.run_case(chunks)
                self.assertEqual(result['code'], 'FINISH_ACK')
                self.assertGreater(result['finish_received_monotonic_ns'],
                                   result['wait_started_monotonic_ns'])

    def test_unknown_historical_branches_are_separate_and_fail_closed(self):
        cases = [
            ('FINISH_NO_READY', (), [False]),
            ('FINISH_EOF', (b'',), None),
            ('FINISH_PARTIAL_EOF', (b'finish', b''), None),
            ('FINISH_PARTIAL_NO_READY', (b'finish',), [True, False]),
            ('FINISH_NONMATCHING', (b'other\n',), None),
            ('FINISH_NONMATCHING', (b'finish\nextra',), None),
            ('FINISH_NONMATCHING', (b' finish\n',), None),
            ('FINISH_NONMATCHING', (b'finish\r\n',), None),
            ('FINISH_OVERLONG', (b'x' * 16,), None),
        ]
        for code, chunks, ready in cases:
            with self.subTest(code=code, chunks=chunks):
                self.assertEqual(self.run_case(chunks, ready)['code'], code)

    def test_monotonic_deadline_and_io_failures(self):
        self.assertEqual(self.run_case(clock=[0, 180000000001])['code'],
                         'FINISH_DEADLINE')
        self.assertEqual(self.run_case((b'f', b'inish\n'),
                                       clock=[0, 1, 2, 3, 180000000001])['code'],
                         'FINISH_LATE_INPUT')
        self.assertEqual(self.run_case((b'f',),
                                       clock=[0, 1, 2, 180000000001])['code'],
                         'FINISH_PARTIAL_DEADLINE')
        for receipt in (180000000000, 180000000001):
            with self.subTest(receipt=receipt):
                self.assertEqual(self.run_case((b'finish\n',),
                                               clock=[0, 1, receipt])['code'],
                                 'FINISH_LATE_INPUT')
        self.assertEqual(self.run_case((b'finish\n',),
                                       clock=[0, 1, 179999999999])['code'],
                         'FINISH_ACK')
        self.assertEqual(self.run_case(select_error=OSError(5, 'synthetic'))['code'],
                         'FINISH_SELECT_ERROR')
        self.assertEqual(self.run_case(read_error=OSError(5, 'synthetic'))['code'],
                         'FINISH_READ_ERROR')
        with self.assertRaisesRegex(ValueError, 'FINISH_WAIT_LIMIT'):
            self.finish(fd=7, max_seconds=181, clock_ns=lambda: 0,
                        select_fn=lambda *args: ([], [], []),
                        read_fn=lambda *args: b'',
                        get_blocking=lambda fd: True,
                        set_blocking=lambda fd, state: None)

    def test_original_bytes_and_unrelated_functions_unchanged(self):
        inventory = json.loads((ROOT / 'source/INVENTORY.json').read_text())
        self.assertEqual(set(inventory['files']), {
            'capture_ownership_v2.py', 'CONTROL_LOG_SANITIZED.json', 'README.md'})
        for name, pin in inventory['files'].items():
            data = (ROOT / 'source' / name).read_bytes()
            self.assertEqual(len(data), pin['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), pin['sha256'])
        raw = ORIGINAL.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED_ORIGINAL)
        old = ast.parse(raw)
        new = ast.parse(SOURCE.read_bytes())
        old_functions = {node.name: node for node in old.body
                         if isinstance(node, ast.FunctionDef)}
        new_functions = {node.name: node for node in new.body
                         if isinstance(node, ast.FunctionDef)}
        self.assertEqual(set(new_functions) - set(old_functions), {'wait_finish'})
        for name in old_functions.keys() - {'context'}:
            self.assertEqual(ast.dump(old_functions[name]),
                             ast.dump(new_functions[name]), name)
        self.assertIn("'1/ns/pid'", SOURCE.read_text())
        self.assertNotIn('sys.stdin.readline()', SOURCE.read_text())

    def test_exported_control_log_retains_historical_uncertainty(self):
        log = json.loads((ROOT / 'source/CONTROL_LOG_SANITIZED.json').read_text())
        self.assertEqual(log['recorded_counts_and_exits']['host_exit_code'], 2)
        self.assertEqual(log['collector_control_semantics']['individual_branch_taken'],
                         'UNKNOWN')
        self.assertEqual(log['finish_input']['delivery_confirmed'], 'UNKNOWN')
        self.assertEqual(log['finish_input']['positive_acknowledgement'], 'UNKNOWN')
        self.assertFalse(log['collector_control_semantics']
                         ['does_marker_prove_elapsed_180_seconds'])


if __name__ == '__main__':
    unittest.main()
