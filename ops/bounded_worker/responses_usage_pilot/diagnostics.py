"""Closed-schema diagnostic values only; never serialize raw errors or bodies."""
import errno
import http.client
import json
import re
import socket
import ssl

PHASES = frozenset(('validation', 'tls_context', 'connection_setup', 'connect',
                    'send', 'response_headers', 'response_body', 'complete'))
CATEGORIES = frozenset(('none', 'validation_error', 'dns_error', 'tls_verification_error',
    'tls_error', 'timeout', 'permission_denied', 'connection_refused', 'connection_reset',
    'broken_pipe', 'remote_disconnected', 'incomplete_response', 'http_protocol_error',
    'http_status_rejected', 'content_encoding_rejected', 'response_limit',
    'invalid_http_status', 'invalid_json', 'invalid_encoding', 'os_error',
    'interrupted_or_deadline', 'operation_failed'))
DELIVERY = frozenset(('not_sent', 'possibly_sent', 'response_received', 'unknown'))
ERRNOS = frozenset(getattr(errno, name) for name in (
    'EACCES', 'EPERM', 'ENOENT', 'EIO', 'ENETUNREACH', 'EHOSTUNREACH',
    'ECONNREFUSED', 'ECONNRESET', 'ECONNABORTED', 'ETIMEDOUT', 'EPIPE',
    'EADDRNOTAVAIL', 'ENOTCONN', 'EMFILE', 'ENFILE'))


class DiagnosticRejection(ValueError):
    def __init__(self, category):
        self.category = category if category in CATEGORIES else 'validation_error'
        super().__init__(self.category)


def failure_details(exc):
    """Classify known types, never str/repr/args or arbitrary class names."""
    kinds = ((DiagnosticRejection, None), (socket.gaierror, 'dns_error'),
             (ssl.SSLCertVerificationError, 'tls_verification_error'),
             (ssl.SSLError, 'tls_error'), (TimeoutError, 'timeout'),
             (PermissionError, 'permission_denied'),
             (ConnectionRefusedError, 'connection_refused'),
             (http.client.RemoteDisconnected, 'remote_disconnected'),
             (ConnectionResetError, 'connection_reset'), (BrokenPipeError, 'broken_pipe'),
             (http.client.IncompleteRead, 'incomplete_response'),
             (http.client.HTTPException, 'http_protocol_error'),
             (json.JSONDecodeError, 'invalid_json'), (UnicodeError, 'invalid_encoding'),
             (OSError, 'os_error'), (ValueError, 'validation_error'))
    category = 'operation_failed' if isinstance(exc, Exception) else 'interrupted_or_deadline'
    for kind, value in kinds:
        if isinstance(exc, kind):
            category = exc.category if kind is DiagnosticRejection else value
            break
    if category not in CATEGORIES:
        category = 'operation_failed'
    number = exc.errno if isinstance(exc, OSError) and not isinstance(exc, ssl.SSLError) else None
    return {'category': category,
            'os_errno': number if type(number) is int and number in ERRNOS else None}


def request_id(value):
    # Deliberately narrow. Duplicate headers joined with commas and unfamiliar
    # formats are discarded, not printed or partially retained.
    return value if type(value) is str and re.fullmatch(r'req_[a-f0-9]{32}', value) else None


def transport_snapshot(value=None):
    """Copy only known primitive fields, with conservative missing-data defaults."""
    value = value if type(value) is dict else {}
    def choice(name, allowed, default):
        item = value.get(name)
        return item if type(item) is str and item in allowed else default
    status, number = value.get('http_status'), value.get('os_errno')
    return {'schema_version': 1,
            'phase': choice('phase', PHASES, 'validation'),
            'category': choice('category', CATEGORIES, 'operation_failed'),
            'delivery': choice('delivery', DELIVERY, 'unknown'),
            'http_status': status if type(status) is int and 100 <= status <= 599 else None,
            'request_id': request_id(value.get('request_id')),
            'os_errno': number if type(number) is int and number in ERRNOS else None,
            'connection_close': choice('connection_close',
                ('not_started', 'closed', 'failed'), 'not_started')}


def transport_observation(transport):
    try:
        return transport_snapshot(transport.diagnostic_snapshot())
    except BaseException:
        return transport_snapshot()
