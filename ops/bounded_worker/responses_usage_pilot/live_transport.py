"""Bounded Responses transport with safe failure receipts; no automatic retry."""
import http.client
import json
import re
import ssl

from diagnostics import DiagnosticRejection, failure_details, request_id, transport_snapshot
from pilot import MAX_REQUEST_BYTES, PROMPT, request_payload

ENDPOINT = 'api.openai.com'


class TransportFailure(RuntimeError):
    pass


class ResponsesTransport:
    def __init__(self, application_key, bounded_call, *, connection_factory=None):
        if (not isinstance(application_key, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}', application_key)):
            raise ValueError('invalid_application_key')
        self.key = application_key
        self.bounded_call = bounded_call
        self.connection_factory = connection_factory or http.client.HTTPSConnection
        self._reset_diagnostic()

    def _reset_diagnostic(self):
        self.http_status = None
        self._diagnostic = transport_snapshot({'phase': 'validation', 'category': 'none',
                                              'delivery': 'not_sent'})

    def diagnostic_snapshot(self):
        return transport_snapshot(self._diagnostic)

    def close(self):
        self.key = None

    def __call__(self, *, payload, timeout_seconds, max_response_bytes):
        self._reset_diagnostic()
        try:
            if (not isinstance(payload, dict) or set(payload) != set(request_payload())
                    or any(payload[k] != v for k, v in request_payload().items() if k != 'input')
                    or not isinstance(payload.get('input'), str)):
                raise ValueError('request_configuration_rejected')
            if payload != request_payload():
                prefix = PROMPT + '\nA prior candidate failed these tests: '
                suffix = '. Produce a fresh complete implementation meeting the contract.\n'
                content = payload['input']
                if not content.startswith(prefix) or not content.endswith(suffix):
                    raise ValueError('unapproved_task_content')
                failed = content[len(prefix):-len(suffix)].split(', ')
                if request_payload(failed) != payload:
                    raise ValueError('unapproved_task_content')
            raw = json.dumps(payload, ensure_ascii=True).encode('ascii')
            if len(raw) > MAX_REQUEST_BYTES or timeout_seconds != 60 or max_response_bytes != 65536:
                raise ValueError('transport_limits_rejected')
            if self.key is None:
                raise ValueError('transport_closed')
        except BaseException as exc:
            self._diagnostic.update(failure_details(exc))
            raise

        def operation():
            connection = None
            try:
                self._diagnostic['phase'] = 'tls_context'
                context = ssl.create_default_context()
                self._diagnostic['phase'] = 'connection_setup'
                connection = self.connection_factory(ENDPOINT, 443, timeout=10, context=context)
                self._diagnostic['phase'] = 'connect'
                connection.connect()
                # Connect only once, inside the overall deadline; never implicitly
                # reconnect if the socket disappears before or during request().
                connection.auto_open = 0
                self._diagnostic.update(phase='send', delivery='possibly_sent')
                connection.request('POST', '/v1/responses', body=raw, headers={
                    'Authorization': 'Bearer ' + self.key,
                    'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
                self._diagnostic['phase'] = 'response_headers'
                response = connection.getresponse()
                self._diagnostic['delivery'] = 'response_received'
                if type(response.status) is not int or not 100 <= response.status <= 599:
                    raise DiagnosticRejection('invalid_http_status')
                self.http_status = response.status
                self._diagnostic['http_status'] = response.status
                correlation = request_id(response.getheader('x-request-id'))
                self._diagnostic['request_id'] = (
                    correlation if correlation is not None and self.key not in correlation else None)
                if response.status != 200:
                    raise DiagnosticRejection('http_status_rejected')
                if response.getheader('Content-Encoding', 'identity') != 'identity':
                    raise DiagnosticRejection('content_encoding_rejected')
                self._diagnostic['phase'] = 'response_body'
                body = response.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise DiagnosticRejection('response_limit')
                self._diagnostic['phase'] = 'complete'
                return body
            except BaseException as exc:
                self._diagnostic.update(failure_details(exc))
                raise
            finally:
                if connection is not None:
                    try:
                        connection.close()
                        self._diagnostic['connection_close'] = 'closed'
                    except BaseException as exc:
                        self._diagnostic['connection_close'] = 'failed'
                        if self._diagnostic['category'] == 'none':
                            self._diagnostic.update(failure_details(exc))
                            raise
        try:
            return self.bounded_call(operation, timeout_seconds)
        except BaseException as exc:
            if self._diagnostic['category'] == 'none':
                self._diagnostic.update(failure_details(exc))
            raise TransportFailure('request_failed_or_uncertain') from None
