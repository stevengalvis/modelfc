"""Unwired Agents API connection. No key discovery, retries, or automatic launch.

Application keys are supplied by an approved caller outside the executor. This
module does not create keys, persist secrets or configure OS isolation. Never
call its live transport before authorizing the API action and data transmission.
"""
import http.client
import json
import math
import re
import urllib.error
import urllib.request


# Ordinary diagnostics never contain provider text. Unknown codes stay categorical.
PROVIDER_CODES = frozenset({
    'invalid_api_key', 'insufficient_quota', 'model_not_found', 'permission_denied',
    'insufficient_permissions', 'unsupported_parameter', 'invalid_value',
    'rate_limit_exceeded', 'server_error', 'billing_hard_limit_reached',
})
API_OPERATIONS = frozenset({'create', 'retrieve_session', 'retrieve_environment', 'input', 'cancel'})
API_OUTCOMES = frozenset({'request_started', 'response_received', 'http_error',
                          'response_rejected', 'transport_error'})
PROVIDER_TYPES = frozenset({'invalid_request_error', 'authentication_error', 'permission_error',
                           'not_found_error', 'rate_limit_error', 'server_error', 'api_error'})
PROVIDER_PARAMS = frozenset({'agent', 'agent.model', 'model', 'agent.service_tier', 'service_tier',
    'agent.instructions', 'agent.multi_agent', 'agent.multi_agent.enabled', 'environment',
    'environment.type', 'environment.workspace_directory', 'review', 'spend_control',
    'spend_control.limit', 'input', 'stream'})


def _request_id(value, key):
    if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value)
            and not value.startswith('sk-') and key not in value):
        return value
    return None


def _provider_error(raw):
    try:
        if len(raw) > 4096:
            return {}
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=pairs)
        error = value['error']
        return error if isinstance(error, dict) else {}
    except (ValueError, TypeError, KeyError, RecursionError):
        return {}


def _provider_category(error, field, allowed):
    if field not in error:
        return 'unavailable'
    value = error[field]
    if value is None and field != 'code':
        return 'not_provided'
    return value if isinstance(value, str) and value in allowed else 'other'


class _PrivateProviderError:
    """Transient, one-use terminal handoff. Never part of a result or exception."""
    __slots__ = ('_fields',)

    def __init__(self, error):
        self._fields = {name: error[name] for name in ('type', 'param', 'message')
                        if isinstance(error.get(name), str)}

    def __repr__(self):
        return '<private provider explanation withheld>'

    def take_fields(self):
        fields, self._fields = self._fields, {}
        return fields

    def clear(self):
        self._fields = {}


class ConnectionFailure(RuntimeError):
    """Outcome may be uncertain. Reconcile; never blindly repeat a write."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionFailure('redirect_rejected')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('invalid_session_id')
    return value


class AgentsConnection:
    """One explicit request per method; only fixed official HTTPS endpoints.

    Inject opener for offline tests. The default opener performs real networking
    when a method is called, not on construction. Proxy environment variables
    are intentionally ignored to avoid sending application credentials through
    an unreviewed proxy. All HTTP/network failures have uncertain write outcomes.
    """
    def __init__(self, application_key, *, timeout=20, max_bytes=262144, opener=None,
                 private_error_sink=None):
        if (not isinstance(application_key, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}', application_key)
                or type(timeout) is not int or not 1 <= timeout <= 60
                or type(max_bytes) is not int or not 1024 <= max_bytes <= 1048576
                or private_error_sink is not None and not callable(private_error_sink)):
            raise ValueError('invalid_connection_configuration')
        self._key = application_key
        self._last_observation = self._first_failure = None
        self._private_error_sink = private_error_sink
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    @property
    def diagnostic(self):
        """First failed REST call, or last response if policy rejects it later."""
        value = self._first_failure or self._last_observation
        return dict(value) if value is not None else None

    def _request(self, method, suffix, payload=None, *, empty_ack=False, resource='sessions', operation):
        raw = None if payload is None else json.dumps(payload, allow_nan=False).encode('utf-8')
        if raw is not None and len(raw) > self.max_bytes:
            raise ValueError('request_too_large')
        if resource not in ('sessions', 'environments') or operation not in API_OPERATIONS:
            raise ValueError('invalid_resource')
        request = urllib.request.Request('https://api.openai.com/v1/agents/' + resource + suffix,
            method=method, data=raw, headers={
                'Authorization': 'Bearer ' + self._key,
                'OpenAI-Beta': 'agents=v1', 'Content-Type': 'application/json',
            })
        observation = {'operation': operation, 'outcome': 'request_started',
                       'http_status': None, 'request_id': None, 'provider_code': None}
        self._last_observation = observation

        def response_metadata(response):
            if response.geturl() != request.full_url:
                raise ConnectionFailure('response_target_or_status_rejected')
            status = response.status
            if type(status) is int and 100 <= status <= 599:
                observation['http_status'] = status
            headers = getattr(response, 'headers', None)
            if headers is not None:
                observation['request_id'] = _request_id(headers.get('x-request-id'), self._key)

        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                response_metadata(response)
                if response.geturl() != request.full_url or not 200 <= response.status < 300:
                    raise ConnectionFailure('response_target_or_status_rejected')
                if empty_ack and response.status != 202:
                    raise ConnectionFailure('event_ack_status_rejected')
                body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise ConnectionFailure('response_limit')
            if empty_ack:
                if body != b'':
                    raise ConnectionFailure('event_ack_body_rejected')
                # Accepted is not durable input completion or turn termination.
                observation['outcome'] = 'response_received'
                return None
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError('duplicate_key')
                    result[key] = value
                return result
            def finite_float(raw):
                number = float(raw)
                if not math.isfinite(number):
                    raise ValueError('nonfinite_json')
                return number
            value = json.loads(body, object_pairs_hook=pairs, parse_float=finite_float,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
            if not isinstance(value, dict):
                raise ValueError('response_shape')
            observation['outcome'] = 'response_received'
            return value
        except urllib.error.HTTPError as error:
            observation['outcome'] = 'http_error'
            if self._first_failure is None:
                self._first_failure = observation
            try:
                with error:
                    response_metadata(error)
                    # No truncated/unparseable-body fallback, including the private sink.
                    fields = _provider_error(error.read(4097))
                    observation['provider_code'] = _provider_category(fields, 'code', PROVIDER_CODES)
                    if operation == 'create' and observation['http_status'] == 400:
                        observation['provider_type'] = _provider_category(fields, 'type', PROVIDER_TYPES)
                        observation['provider_param'] = _provider_category(fields, 'param', PROVIDER_PARAMS)
                        observation['private_explanation'] = 'not_requested'
                        if self._private_error_sink is not None:
                            observation['private_explanation'] = 'unavailable'
                            private = _PrivateProviderError(fields)
                            try:
                                if self._private_error_sink(private) is True:
                                    observation['private_explanation'] = 'displayed'
                            except Exception:
                                pass  # Never echo sink exceptions or retry the request.
                            finally:
                                private.clear()
                    fields.clear()
            except Exception:
                observation['provider_code'] = 'unavailable'
            raise ConnectionFailure('request_failed_outcome_uncertain') from None
        except Exception as error:
            transport = isinstance(error, (OSError, http.client.HTTPException, urllib.error.URLError))
            observation['outcome'] = ('transport_error' if transport else
                'response_rejected' if isinstance(error, (ConnectionFailure, ValueError))
                or observation['http_status'] is not None else 'transport_error')
            if self._first_failure is None:
                self._first_failure = observation
            # No provider error bodies, URLs containing secrets or exception text.
            raise ConnectionFailure('request_failed_outcome_uncertain') from None

    def create_session(self, plan):
        return self._request('POST', '', plan.create_payload(), operation='create')

    def retrieve_session(self, session_id):
        return self._request('GET', '/' + _id(session_id), operation='retrieve_session')

    def retrieve_environment(self, environment_id):
        return self._request('GET', '/' + _id(environment_id), resource='environments',
                             operation='retrieve_environment')

    def submit_task(self, session_id, plan):
        return self._request('POST', '/' + _id(session_id) + '/events', plan.input_payload(),
                             empty_ack=True, operation='input')

    def cancel_turn(self, session_id):
        return self._request('POST', '/' + _id(session_id) + '/events',
                             {'events': [{'type': 'agent.session.input.cancel'}]}, empty_ack=True,
                             operation='cancel')
