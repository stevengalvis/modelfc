"""Offline prepared Responses pilot. No HTTP, credentials or code execution.

The trusted caller owns network I/O and existing sandbox use. Importing this
module is inert. A transport must not be connected without approval. The durable
gate must live outside the sandbox in one fixed, private local directory.
"""
import difflib
import hashlib
import json
import math
import re

from admission import AdmissionJournal
from diagnostics import failure_details, transport_observation

MODEL = 'gpt-6-luna'
MAX_CALLS = 3
MAX_REQUEST_BYTES = 4096
MAX_OUTPUT_TOKENS = 4096
MAX_RESPONSE_BYTES = 65536
MAX_SOURCE_BYTES = 8192
TIMEOUT_SECONDS = 60
ATTEMPT = hashlib.sha256(b'responses-usage-pilot-2026-11-08-v1').hexdigest()
TESTS = ('test_empty', 'test_groups', 'test_zero',
         'test_no_mutation_or_extra_field_dependency', 'test_invalid_collection',
         'test_invalid_model', 'test_invalid_counts', 'test_missing_counts')
PROMPT = '''Implement one small Python 3.12 utility for comparing model-reported API
token usage. Return only the complete source of usage_summary.py, no Markdown.
Implement summarize_usage(records). records must be a list of dictionaries.
Each dictionary must have model (a non-blank string, preserved exactly),
input_tokens and output_tokens (nonnegative integers, excluding bool).
Return a dictionary keyed by model; each value contains calls, input_tokens,
output_tokens and total_tokens. Sum counts per model, calculate total_tokens
from input plus output, ignore extra input fields, and never mutate inputs.
Return {} for an empty list. Raise ValueError for every invalid collection,
record, missing field, model or token count. No imports, classes, decorators,
file access, commands, network, third-party packages or top-level work.
Do not use type annotations, helper functions, decorators or underscore-prefixed
names. Use ordinary loops and dictionaries; call only ValueError, isinstance,
type, dict, list, str, int, bool, any, all, len, sum, or get/setdefault/strip/
items/values/keys/append/copy methods. Only define summarize_usage.
This summarizes reported tokens only; do not estimate money or billing.
Examples: [{"model":"luna","input_tokens":100,"output_tokens":20},
{"model":"luna","input_tokens":40,"output_tokens":5}] returns
{"luna":{"calls":2,"input_tokens":140,"output_tokens":25,"total_tokens":165}}.
'''


def request_payload(failed_tests=()):
    if (not isinstance(failed_tests, (tuple, list))
            or len(failed_tests) != len(set(failed_tests))
            or any(name not in TESTS for name in failed_tests)):
        raise ValueError('invalid_test_feedback')
    prompt = PROMPT
    if failed_tests:
        prompt += '\nA prior candidate failed these tests: ' + ', '.join(failed_tests)
        prompt += '. Produce a fresh complete implementation meeting the contract.\n'
    payload = {'model': MODEL, 'input': prompt, 'max_output_tokens': MAX_OUTPUT_TOKENS,
               'reasoning': {'effort': 'low'}, 'service_tier': 'default',
               'store': False, 'stream': False, 'tools': [], 'tool_choice': 'none',
               'truncation': 'disabled'}
    if len(json.dumps(payload, ensure_ascii=True).encode('ascii')) > MAX_REQUEST_BYTES:
        raise ValueError('request_limit')
    return payload


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite_json')
    return number


def parse_response(raw):
    if type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('response_limit')
    response = json.loads(raw, object_pairs_hook=_pairs, parse_float=_finite,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (not isinstance(response, dict) or response.get('status') != 'completed'
            or response.get('error') is not None):
        raise ValueError('response_not_completed')
    model = response.get('model')
    if (not isinstance(model, str) or not re.fullmatch(r'gpt-6-luna(?:-[0-9-]+)?', model)
            or response.get('service_tier') not in (None, 'default')):
        raise ValueError('model_or_tier_rejected')
    output = response.get('output')
    if not isinstance(output, list) or not output:
        raise ValueError('missing_output')
    parts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError('output_shape')
        if item.get('type') == 'reasoning':
            continue
        if (item.get('type') != 'message' or item.get('role') != 'assistant'
                or item.get('status') != 'completed'
                or not isinstance(item.get('content'), list)):
            raise ValueError('unexpected_output')
        for part in item['content']:
            if (not isinstance(part, dict) or part.get('type') != 'output_text'
                    or not isinstance(part.get('text'), str)):
                raise ValueError('unexpected_content_or_refusal')
            parts.append(part['text'])
    source = ''.join(parts)
    if (not source.strip() or len(source.encode('utf-8')) > MAX_SOURCE_BYTES
            or '\x00' in source or '```' in source):
        raise ValueError('source_rejected')
    usage = response.get('usage')
    fields = ('input_tokens', 'output_tokens', 'total_tokens')
    if (not isinstance(usage, dict)
            or any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields)
            or usage['input_tokens'] + usage['output_tokens'] != usage['total_tokens']
            or usage['output_tokens'] > MAX_OUTPUT_TOKENS):
        raise ValueError('usage_rejected')
    return {'source': source, 'response_model': model,
            'usage': {key: usage[key] for key in fields}}


def candidate_patch(source):
    """Return a proposed add-file diff. Never apply, import or execute output."""
    if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise ValueError('source_rejected')
    if not source.endswith('\n'):
        source += '\n'
    return ''.join(difflib.unified_diff([], source.splitlines(keepends=True),
        fromfile='/dev/null', tofile='b/usage_summary.py'))


class PilotJournal(AdmissionJournal):
    """One fixed task, at most three admitted calls; uncertainty stops everything."""
    def __init__(self, directory):
        super().__init__(directory)
        self.db.execute('CREATE TABLE IF NOT EXISTS responses_usage_pilot '
                        '(n INTEGER PRIMARY KEY, state TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS responses_usage_invocation '
                        '(identity TEXT PRIMARY KEY)')

    def reserve_invocation(self):
        """One live invocation; no resuming an interrupted broker automatically."""
        self.db.execute('INSERT INTO responses_usage_invocation VALUES (?)', (ATTEMPT,))

    def reserve_call(self, failed_tests=()):
        request_payload(failed_tests)
        try:
            self.db.execute('BEGIN IMMEDIATE')
            rows = self.db.execute('SELECT n,state FROM responses_usage_pilot ORDER BY n').fetchall()
            if (len(rows) >= MAX_CALLS or any(n != i + 1 for i, (n, _) in enumerate(rows))
                    or rows and rows[-1][1] != 'tests_failed'
                    or bool(rows) != bool(failed_tests)):
                raise ValueError('call_not_admitted')
            number = len(rows) + 1
            self.db.execute('INSERT INTO responses_usage_pilot VALUES (?,?)', (number, 'reserved'))
            self.db.execute('COMMIT')
            return number
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def transition(self, number, expected, state):
        allowed = {('reserved', 'candidate'), ('candidate', 'tests_failed'),
                   ('candidate', 'passed')}
        if (expected, state) not in allowed:
            raise ValueError('invalid_transition')
        result = self.db.execute('UPDATE responses_usage_pilot SET state=? WHERE n=? AND state=?',
                                 (state, number, expected))
        if result.rowcount != 1:
            raise ValueError('invalid_transition')


def propose(journal, transport, failed_tests=()):
    """Transport injection only. No automatic requests, repairs or retry loop.

    Trusted transport receives no filesystem access or tool instructions. It must
    independently enforce total I/O deadline/body cap, no redirects/proxies/retries,
    and use only api.openai.com/v1/responses with the approved application key.
    Any exception, interruption, refusal, incomplete result or malformed response
    leaves a reserved row, permanently stopping automatic continuation.
    """
    payload = request_payload(failed_tests)
    number = journal.reserve_call(failed_tests)
    phase, candidate = 'transport', None
    try:
        raw = transport(payload=payload, timeout_seconds=TIMEOUT_SECONDS,
                        max_response_bytes=MAX_RESPONSE_BYTES)
        phase = 'response_parse'
        candidate = parse_response(raw)
        # Source remains untrusted data, even with a valid response envelope.
        phase = 'journal_transition'
        journal.transition(number, 'reserved', 'candidate')
        phase = 'patch_generation'
        patch = candidate_patch(candidate['source'])
        return {'status': 'candidate', 'call': number, **candidate,
                'patch': patch, 'diagnostic': {
                    'schema_version': 1, 'call_number': number, 'phase': 'complete',
                    'category': 'none', 'os_errno': None,
                    'usage_state': 'validated', 'transport': transport_observation(transport)}}
    except BaseException as exc:
        result = {'status': 'blocked_or_uncertain', 'call': number, 'diagnostic': {
            'schema_version': 1, 'call_number': number, 'phase': phase,
            **failure_details(exc), 'usage_state': 'unknown',
            'transport': transport_observation(transport)}}
        if candidate is not None:
            # These fields have already passed parse_response's strict checks.
            result.update(usage=candidate['usage'], response_model=candidate['response_model'])
            result['diagnostic']['usage_state'] = 'validated'
        return result
