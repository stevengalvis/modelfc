import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

from ops.bounded_worker.responses_prototype import Configuration, ResponsesPrototype
from ops.bounded_worker.task import Task


class ResponsesPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.task = Task('offline', 'stevengalvis/modelfc', 'a' * 40,
                         'Explain this synthetic task.', ('docs/example.md',))
        self.response = {
            'status': 'completed', 'model': 'test-model-snapshot', 'error': None,
            'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'Synthetic answer.'}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
        }

    def run_response(self, response=None, *, raw=None, clock=lambda: 0):
        transport = Mock(return_value=raw if raw is not None else json.dumps(
            self.response if response is None else response).encode())
        adapter = ResponsesPrototype(Configuration('test-model'), transport, clock=clock)
        return adapter.generate(self.task), transport

    def test_success_and_request_boundaries(self):
        result, transport = self.run_response()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['text'], 'Synthetic answer.')
        self.assertEqual(result['usage']['total_tokens'], 15)
        self.assertEqual(result['response_model'], 'test-model-snapshot')
        transport.assert_called_once_with(
            endpoint='https://api.openai.com/v1/responses',
            request={'model': 'test-model', 'input': self.task.instructions,
                     'max_output_tokens': 1024, 'store': False, 'tools': [],
                     'tool_choice': 'none', 'stream': False},
            timeout_seconds=180, max_response_bytes=262144)

    def test_invalid_configuration(self):
        for model, tokens in [('', 1), ('x\n', 1), ('x', True), ('x', 0), ('x', 4097)]:
            with self.subTest(model=model, tokens=tokens), self.assertRaises(ValueError):
                ResponsesPrototype(Configuration(model, tokens), Mock())

    def test_invalid_task_never_calls_transport(self):
        transport = Mock()
        adapter = ResponsesPrototype(Configuration('test-model'), transport)
        for task in [None, replace(self.task, repository='other/repo'),
                     replace(self.task, instructions='x' * 8193),
                     replace(self.task, allowed_paths=('../secret',))]:
            with self.subTest(task=task), self.assertRaises(ValueError):
                adapter.generate(task)
        transport.assert_not_called()

    def test_bad_json_and_oversized_or_wrong_type(self):
        for raw in [b'bad', b'[]', b'{"a":1,"a":2}', b'{"a":NaN}',
                    b'\xff', b'x' * 262145, 'string']:
            with self.subTest(raw_type=type(raw)):
                result, transport = self.run_response(raw=raw)
                self.assertEqual(result['status'], 'failed')
                transport.assert_called_once()

    def test_nonterminal_and_provider_errors_fail_closed(self):
        for updates in [{'status': 'incomplete'}, {'status': 'queued'},
                        {'error': {'message': 'sensitive'}}, {'model': None},
                        {'output': []}, {'usage': None}]:
            with self.subTest(updates=updates):
                response = {**self.response, **updates}
                result, _ = self.run_response(response)
                self.assertEqual(result['status'], 'failed')
                self.assertIsNone(result['text'])
                self.assertNotIn('sensitive', json.dumps(result))

    def test_tool_calls_rejected_never_executed(self):
        for kind in ['function_call', 'shell_call', 'computer_call', 'mcp_call']:
            with self.subTest(kind=kind):
                response = {**self.response, 'output': [{'type': kind}]}
                self.assertEqual(self.run_response(response)[0]['status'], 'failed')

    def test_refusal(self):
        response = copy.deepcopy(self.response)
        response['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'private'}]
        result, _ = self.run_response(response)
        self.assertEqual(result['status'], 'refused')
        self.assertIsNone(result['text'])

    def test_reasoning_is_not_exposed(self):
        response = copy.deepcopy(self.response)
        response['output'].insert(0, {'type': 'reasoning', 'summary': ['private']})
        result, _ = self.run_response(response)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('private', json.dumps(result))

    def test_usage_values_rejected(self):
        for usage in [{'input_tokens': True, 'output_tokens': 1, 'total_tokens': 2},
                      {'input_tokens': -1, 'output_tokens': 1, 'total_tokens': 0},
                      {'input_tokens': 10, 'output_tokens': 1, 'total_tokens': 50}]:
            result, _ = self.run_response({**self.response, 'usage': usage})
            self.assertEqual(result['status'], 'failed')
            self.assertIsNone(result['text'])

    def test_deadline_after_transport_returns(self):
        times = iter([0, 180])
        result, transport = self.run_response(clock=lambda: next(times))
        self.assertEqual(result['status'], 'timeout')
        transport.assert_called_once()

    def test_transport_failure_never_retries_or_leaks(self):
        for error, status in [(TimeoutError('private'), 'timeout'),
                              (OSError('secret credential'), 'failed')]:
            transport = Mock(side_effect=error)
            result = ResponsesPrototype(Configuration('test-model'), transport).generate(self.task)
            self.assertEqual(result['status'], status)
            self.assertNotIn('private', json.dumps(result))
            self.assertNotIn('secret', json.dumps(result))
            transport.assert_called_once()


if __name__ == '__main__':
    unittest.main()
