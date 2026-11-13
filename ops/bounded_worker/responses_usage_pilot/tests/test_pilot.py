import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from pilot import (MAX_OUTPUT_TOKENS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
                   PilotJournal, candidate_patch, parse_response, propose, request_payload)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.journal = PilotJournal(self.root)
        self.response = {
            'status': 'completed', 'model': 'gpt-6-luna', 'error': None,
            'service_tier': 'default',
            'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text':
                                     'def summarize_usage(records):\n    return {}\n'}]}],
            'usage': {'input_tokens': 250, 'output_tokens': 25, 'total_tokens': 275},
        }

    def tearDown(self):
        self.journal.close()
        self.temporary.cleanup()

    def raw(self):
        return json.dumps(self.response).encode()

    def test_payload_fixed_and_bounded(self):
        payload = request_payload()
        self.assertEqual(payload['model'], 'gpt-6-luna')
        self.assertEqual(payload['max_output_tokens'], 4096)
        self.assertEqual(payload['service_tier'], 'default')
        self.assertEqual(payload['reasoning'], {'effort': 'low'})
        self.assertFalse(payload['store'])
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['tools'], [])
        self.assertLessEqual(len(json.dumps(payload).encode()), MAX_REQUEST_BYTES)

    def test_feedback_only_accepts_known_test_names(self):
        for feedback in (['private secret'], ['test_zero', 'test_zero'], 'test_zero'):
            with self.assertRaises(ValueError):
                request_payload(feedback)

    def test_candidate_is_data_only(self):
        transport = Mock(return_value=self.raw())
        result = propose(self.journal, transport)
        self.assertEqual(result['status'], 'candidate')
        self.assertIn('+++ b/usage_summary.py', result['patch'])
        transport.assert_called_once()
        self.assertEqual(list(self.root.iterdir()), [self.root / 'admission.sqlite3'])

    def test_cannot_continue_without_failed_tests(self):
        result = propose(self.journal, Mock(return_value=self.raw()))
        with self.assertRaises(ValueError):
            self.journal.reserve_call(['test_zero'])
        self.journal.transition(result['call'], 'candidate', 'passed')
        with self.assertRaises(ValueError):
            self.journal.reserve_call(['test_zero'])

    def test_maximum_three_calls_and_no_fourth(self):
        transport = Mock(return_value=self.raw())
        for number in range(1, 4):
            feedback = () if number == 1 else ('test_zero',)
            result = propose(self.journal, transport, feedback)
            self.assertEqual(result['call'], number)
            self.journal.transition(number, 'candidate', 'tests_failed')
        with self.assertRaises(ValueError):
            propose(self.journal, transport, ('test_zero',))
        self.assertEqual(transport.call_count, 3)

    def test_uncertain_outcome_survives_restart(self):
        transport = Mock(side_effect=TimeoutError('DO_NOT_LEAK'))
        result = propose(self.journal, transport)
        self.assertEqual(result['status'], 'blocked_or_uncertain')
        self.assertNotIn('DO_NOT_LEAK', json.dumps(result))
        self.journal.close()
        self.journal = PilotJournal(self.root)
        with self.assertRaises(ValueError):
            propose(self.journal, transport, ('test_zero',))
        transport.assert_called_once()

    def test_second_instance_cannot_double_reserve(self):
        other = PilotJournal(self.root)
        try:
            self.journal.reserve_call()
            with self.assertRaises(ValueError):
                other.reserve_call()
        finally:
            other.close()

    def test_malformed_or_oversized_json(self):
        for raw in (b'[]', b'{"a":1,"a":2}', b'{"a":1e999}', b'{"a":NaN}',
                    b'bad', b'\xff', b'x' * (MAX_RESPONSE_BYTES + 1)):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                parse_response(raw)

    def test_incomplete_tool_refusal_and_wrong_model_rejected(self):
        variations = [
            {'status': 'incomplete'}, {'model': 'other-model'},
            {'service_tier': 'priority'}, {'output': [{'type': 'function_call'}]},
            {'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                         'content': [{'type': 'refusal', 'refusal': 'no'}]}]},
        ]
        for update in variations:
            with self.subTest(update=update), self.assertRaises(ValueError):
                parse_response(json.dumps({**self.response, **update}).encode())

    def test_usage_rejected(self):
        for counts in ((True, 25, 26), (-1, 25, 24), (1, 2, 4),
                       (1, MAX_OUTPUT_TOKENS + 1, MAX_OUTPUT_TOKENS + 2)):
            raw = copy.deepcopy(self.response)
            raw['usage'] = dict(zip(('input_tokens', 'output_tokens', 'total_tokens'), counts))
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                parse_response(json.dumps(raw).encode())

    def test_reasoning_suppressed(self):
        self.response['output'].insert(0, {'type': 'reasoning', 'summary': ['private']})
        self.assertNotIn('private', json.dumps(parse_response(self.raw())))

    def test_patch_fixed_path_and_trailing_newline(self):
        patch = candidate_patch('def summarize_usage(records):\n    return {}')
        self.assertTrue(patch.startswith('--- /dev/null\n+++ b/usage_summary.py\n'))
        self.assertTrue(patch.endswith('\n'))


if __name__ == '__main__':
    unittest.main()
