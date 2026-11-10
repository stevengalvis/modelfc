"""Unwired, text-only Responses API boundary; no HTTP, credentials or tools.

The injected transport is trusted and must enforce timeout/body limits while
receiving bytes, disable automatic retries, and return the raw JSON body.
This is deliberately NOT the supervisor's run(task, work, containment) adapter.
"""
from dataclasses import dataclass
import json
import re
import time

from ops.bounded_worker.task import Task


@dataclass(frozen=True)
class Configuration:
    model: str
    max_output_tokens: int = 1024

    def validate(self):
        if (not isinstance(self.model, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', self.model)
                or type(self.max_output_tokens) is not int
                or not 1 <= self.max_output_tokens <= 4096):
            raise ValueError('invalid_responses_configuration')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _parse(raw, limit):
    if type(raw) is not bytes or len(raw) > limit:
        raise ValueError('response_size_or_type')
    value = json.loads(raw, object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
    if not isinstance(value, dict):
        raise ValueError('response_shape')
    return value


class ResponsesPrototype:
    """One request, no retries/fallbacks, no file access or command execution.

    Only caller-supplied task instructions are sent, not repository content.
    A completed result means text generation, never a completed coding task.
    """
    endpoint = 'https://api.openai.com/v1/responses'

    def __init__(self, configuration, transport, *, clock=time.monotonic):
        configuration.validate()
        if not callable(transport):
            raise ValueError('transport_required')
        self.configuration = configuration
        self.transport = transport
        self.clock = clock

    def generate(self, task):
        if not isinstance(task, Task):
            raise ValueError('invalid_task')
        task.validate()  # Reuse the existing repository/task/size constraints.
        self.configuration.validate()
        request = {
            'model': self.configuration.model,
            'input': task.instructions,
            'max_output_tokens': self.configuration.max_output_tokens,
            'store': False,
            'tools': [],
            'tool_choice': 'none',
            'stream': False,
        }
        result = {'status': 'failed', 'text': None, 'usage': None,
                  'error': None, 'scope': 'text_only_prototype'}
        started = self.clock()
        try:
            raw = self.transport(
                endpoint=self.endpoint, request=request,
                timeout_seconds=task.timeout_seconds,
                max_response_bytes=task.output_bytes,
            )
            if self.clock() - started >= task.timeout_seconds:
                raise TimeoutError
            response = _parse(raw, task.output_bytes)
            # Version aliases can resolve to a snapshot; do not falsely claim
            # that the requested and billed model were independently verified.
            actual_model = response.get('model')
            if not isinstance(actual_model, str) or not actual_model:
                raise ValueError('missing_model')
            result['response_model'] = actual_model
            if response.get('status') != 'completed' or response.get('error') is not None:
                raise ValueError('response_not_completed')
            output = response.get('output')
            if not isinstance(output, list) or not output:
                raise ValueError('missing_output')
            text = []
            for item in output:
                if not isinstance(item, dict):
                    raise ValueError('output_shape')
                if item.get('type') == 'reasoning':
                    continue  # Never expose hidden reasoning/opaque state.
                if (item.get('type') != 'message' or item.get('role') != 'assistant'
                        or item.get('status') != 'completed'
                        or not isinstance(item.get('content'), list)):
                    raise ValueError('unexpected_output')
                for part in item['content']:
                    if not isinstance(part, dict):
                        raise ValueError('content_shape')
                    if part.get('type') == 'refusal':
                        result['status'] = 'refused'
                        result['error'] = 'model_refused'
                        return result
                    if part.get('type') != 'output_text' or not isinstance(part.get('text'), str):
                        raise ValueError('unexpected_content')
                    text.append(part['text'])
            if not text or not ''.join(text).strip():
                raise ValueError('missing_text')
            usage = response.get('usage')
            if not isinstance(usage, dict):
                raise ValueError('missing_usage')
            fields = ('input_tokens', 'output_tokens', 'total_tokens')
            if any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields):
                raise ValueError('invalid_usage')
            if usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']:
                raise ValueError('invalid_usage')
            result.update(status='completed', text=''.join(text),
                          usage={k: usage[k] for k in fields})
        except TimeoutError:
            result.update(status='timeout', error='deadline_exceeded')
        except Exception:
            # Transport errors may embed credentials or response bodies. Do not
            # persist their strings. Never retry an uncertain paid request.
            result.update(status='failed', error='transport_or_response_failure')
        return result
