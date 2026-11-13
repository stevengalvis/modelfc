"""One immutable required_text request; no credential lookup or automatic retry.

Activation/approval belongs to a trusted controller. This module is not a CLI and
is never imported by the historical pilot launcher.
"""
import http.client
import json
import math
import re
import ssl

from .one_file_adapter import prepare, bind_first_reservation


class OneFileTransport:
    def __init__(self, manifest, source, tests, harness, reservation, application_key,
                 bounded_call, *, connection_factory=None):
        self.prepared = bind_first_reservation(manifest, prepare(manifest, source, tests, harness),
                                              reservation, source, tests, harness)
        if (type(application_key) is not str
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}', application_key)):
            raise ValueError('invalid_application_key')
        self.manifest = manifest
        self.key = application_key
        self.bound = bounded_call
        self.connect = connection_factory or http.client.HTTPSConnection
        self.used = False
        self.evidence = {'phase': 'prepared', 'category': 'none', 'http_status': None,
                         'delivery': 'not_sent', 'connection_closed': False}

    def close(self):
        self.key = None

    def __call__(self):
        if self.used or self.key is None:
            raise ValueError('transport_consumed_or_closed')
        self.used = True  # Even a failed connect cannot be retried on this object.

        def operation():
            connection = None
            try:
                self.evidence['phase'] = 'connect'
                connection = self.connect('api.openai.com', 443,
                                          timeout=min(10, self.manifest.provider_seconds),
                                          context=ssl.create_default_context())
                connection.connect()
                connection.auto_open = 0
                self.evidence.update(phase='send', delivery='possibly_sent')
                connection.request('POST', '/v1/responses', body=self.prepared.wire, headers={
                    'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                    'Accept-Encoding': 'identity'})
                self.evidence['phase'] = 'headers'
                response = connection.getresponse()
                if type(response.status) is int and 100 <= response.status <= 599:
                    self.evidence['http_status'] = response.status
                self.evidence['delivery'] = 'response_received'
                if type(response.status) is not int or response.status != 200:
                    raise ValueError('http_status')
                if response.getheader('Content-Encoding', 'identity') != 'identity':
                    raise ValueError('content_encoding')
                length_header = response.getheader('Content-Length', None)
                transfer = response.getheader('Transfer-Encoding', None)
                expected_length = None
                if transfer is not None:
                    if (type(transfer) is not str or transfer.strip().lower() != 'chunked'
                            or length_header is not None):
                        raise ValueError('ambiguous_http_framing')
                if length_header is not None:
                    # getheader joins duplicate fields with commas: reject them,
                    # even matching duplicates, rather than guessing a framing.
                    if (type(length_header) is not str
                            or not re.fullmatch(r'[0-9]{1,10}', length_header.strip(' \t'))):
                        raise ValueError('invalid_content_length')
                    expected_length = int(length_header.strip(' \t'))
                    if expected_length > self.manifest.max_response_bytes:
                        raise ValueError('declared_response_bound')
                self.evidence['phase'] = 'body'
                raw = response.read(self.manifest.max_response_bytes + 1)
                if type(raw) is not bytes or len(raw) > self.manifest.max_response_bytes:
                    raise ValueError('response_bound')
                if expected_length is not None and len(raw) != expected_length:
                    raise ValueError('incomplete_http_body')
                self.evidence['phase'] = 'complete'
                return raw
            except BaseException as exc:
                self.evidence['category'] = ('deadline' if isinstance(exc, TimeoutError) else
                    'response_rejected' if isinstance(exc, ValueError) else 'operation_failed')
                raise
            finally:
                if connection is not None:
                    try:
                        connection.close()
                        self.evidence['connection_closed'] = True
                    except BaseException:
                        self.evidence.update(phase='close', category='close_failed')
                        raise
        try:
            return self.bound(operation, self.manifest.provider_seconds)
        except BaseException as exc:
            if self.evidence['category'] == 'none':
                self.evidence['category'] = 'deadline' if isinstance(exc, TimeoutError) else 'operation_failed'
            raise RuntimeError('provider_failed_or_uncertain') from None



def transport_observation(transport):
    """Closed schema from the concrete transport only; never call plugin hooks."""
    value = transport.evidence if isinstance(transport, OneFileTransport) else {}
    if type(value) is not dict:
        value = {}
    phase = value.get('phase')
    category = value.get('category')
    delivery = value.get('delivery')
    status = value.get('http_status')
    return {
        'phase': phase if phase in ('prepared', 'connect', 'send', 'headers', 'body', 'complete', 'close') else 'unknown',
        'category': category if category in ('none', 'deadline', 'response_rejected', 'operation_failed', 'close_failed') else 'unknown',
        'delivery': delivery if delivery in ('not_sent', 'possibly_sent', 'response_received') else 'unknown',
        'http_status': status if type(status) is int and 100 <= status <= 599 else None,
        'connection_closed': value.get('connection_closed') is True,
    }

def parse_response(manifest, raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('nonfinite_json')
        return result

    def invalid(value):
        raise ValueError('nonfinite_json')

    if type(raw) is not bytes or len(raw) > manifest.max_response_bytes:
        raise ValueError('response_bound')
    response = json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=invalid)
    if (type(response) is not dict or response.get('status') != 'completed'
            or response.get('error') is not None
            or type(response.get('model')) is not str
            or not re.fullmatch(r'gpt-6-luna(?:-[0-9-]+)?', response['model'])
            or response.get('service_tier') not in (None, 'default')):
        raise ValueError('response_identity')
    output = response.get('output')
    if type(output) is not list or not output:
        raise ValueError('response_output')
    parts = []
    for item in output:
        if type(item) is not dict:
            raise ValueError('response_item')
        if item.get('type') == 'reasoning':
            continue
        if (item.get('type') != 'message' or item.get('role') != 'assistant'
                or item.get('status') != 'completed' or type(item.get('content')) is not list):
            raise ValueError('response_message')
        for part in item['content']:
            if (type(part) is not dict or part.get('type') != 'output_text'
                    or type(part.get('text')) is not str):
                raise ValueError('response_text')
            parts.append(part['text'])
    candidate = ''.join(parts).encode('utf-8')
    if not candidate or len(candidate) > manifest.max_source_bytes or b'\x00' in candidate or b'```' in candidate:
        raise ValueError('candidate_bound')
    usage = response.get('usage')
    if (type(usage) is not dict or any(type(usage.get(key)) is not int or usage[key] < 0
            for key in ('input_tokens', 'output_tokens', 'total_tokens'))
            or usage['input_tokens'] + usage['output_tokens'] != usage['total_tokens']
            or usage['input_tokens'] > manifest.max_input_tokens
            or usage['output_tokens'] > manifest.max_output_tokens):
        raise ValueError('response_usage')
    return candidate, {key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')}
