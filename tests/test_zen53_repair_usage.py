"""Token accounting uses synthetic captures, no account, model or network."""
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'docs/zen53/repair-loop/usage.py'
SPEC = importlib.util.spec_from_file_location('tested_repair_usage', PATH)
usage = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(usage)


def capture(tokens):
    return ('\n'.join(json.dumps(row) for row in [
        {'type':'thread.started', 'thread_id':'fixture'}, {'type':'turn.started'},
        {'type':'item.completed', 'item':{'id':'a', 'type':'agent_message', 'text':'{}'}},
        {'type':'turn.completed', 'usage':tokens}])+'\n').encode()


class Usage(unittest.TestCase):
    def test_details_not_double_counted_and_optional_unknown(self):
        result = usage.normalize(dict(input_tokens=100, cached_input_tokens=80, output_tokens=20,
                                      cache_write_input_tokens=10, reasoning_output_tokens=15))
        self.assertEqual(result['total_tokens'], 120)
        self.assertEqual(usage.aggregate([result, result])['total_tokens'], 240)
        absent = usage.normalize(dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))
        self.assertIsNone(absent['reasoning_output_tokens'])
        self.assertEqual(absent['total_tokens'], 0)

    def test_actual_capture_extraction_and_failure_unknown(self):
        raw = capture(dict(input_tokens=100, cached_input_tokens=50, output_tokens=10))
        self.assertEqual(usage.from_capture(raw, 0, False)['total_tokens'], 110)
        for data, code, timeout in ((raw, 1, False), (raw, 0, True), (raw[:-1], 0, False),
                                    (capture(None), 0, False), (b'', 0, False)):
            self.assertEqual(usage.from_capture(data, code, timeout), usage.normalize(None))

    def test_partial_aggregate_is_not_a_complete_total(self):
        known = usage.normalize(dict(input_tokens=2, cached_input_tokens=1, output_tokens=3))
        total = usage.aggregate([known, usage.normalize(None)])
        self.assertEqual(total['state'], 'PARTIAL')
        self.assertEqual(total['known_total_tokens'], 5)
        self.assertIsNone(total['total_tokens'])
        self.assertEqual(usage.aggregate([])['state'], 'UNKNOWN')

    def test_malformed_counts_and_forged_totals_fail(self):
        for bad in (True, -1, 1.0, '1', 2**63):
            with self.assertRaises(ValueError): usage.normalize(dict(input_tokens=bad, cached_input_tokens=0, output_tokens=0))
        result = usage.normalize(dict(input_tokens=1, cached_input_tokens=0, output_tokens=2))
        result['total_tokens'] = 99
        with self.assertRaises(ValueError): usage.validate_report(result)
        for forged in (True, 1.0):
            result = usage.normalize(dict(input_tokens=1, cached_input_tokens=0, output_tokens=0))
            result['total_tokens'] = forged
            with self.assertRaises(ValueError): usage.validate_report(result)
        with self.assertRaises(ValueError): usage.aggregate([usage.normalize(None)]*3)
