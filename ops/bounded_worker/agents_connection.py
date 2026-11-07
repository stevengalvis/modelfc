"""Unwired Agents API connection. No key discovery, retries, or automatic launch.

Application keys are supplied by an approved caller outside the executor. This
module does not create keys, persist secrets or configure OS isolation. Never
call its live transport before authorizing the API action and data transmission.
"""
import json
import math
import re
import urllib.error
import urllib.request


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
    def __init__(self, application_key, *, timeout=20, max_bytes=262144, opener=None):
        if (not isinstance(application_key, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}', application_key)
                or type(timeout) is not int or not 1 <= timeout <= 60
                or type(max_bytes) is not int or not 1024 <= max_bytes <= 1048576):
            raise ValueError('invalid_connection_configuration')
        self._key = application_key
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, method, suffix, payload=None, *, empty_ack=False, resource='sessions'):
        raw = None if payload is None else json.dumps(payload, allow_nan=False).encode('utf-8')
        if raw is not None and len(raw) > self.max_bytes:
            raise ValueError('request_too_large')
        if resource not in ('sessions', 'environments'):
            raise ValueError('invalid_resource')
        request = urllib.request.Request('https://api.openai.com/v1/agents/' + resource + suffix,
            method=method, data=raw, headers={
                'Authorization': 'Bearer ' + self._key,
                'OpenAI-Beta': 'agents=v1', 'Content-Type': 'application/json',
            })
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
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
            return value
        except Exception:
            # No provider error bodies, URLs containing secrets or exception text.
            raise ConnectionFailure('request_failed_outcome_uncertain') from None

    def create_session(self, plan):
        return self._request('POST', '', plan.create_payload())

    def retrieve_session(self, session_id):
        return self._request('GET', '/' + _id(session_id))

    def retrieve_environment(self, environment_id):
        return self._request('GET', '/' + _id(environment_id), resource='environments')

    def submit_task(self, session_id, plan):
        return self._request('POST', '/' + _id(session_id) + '/events', plan.input_payload(),
                             empty_ack=True)

    def cancel_turn(self, session_id):
        return self._request('POST', '/' + _id(session_id) + '/events',
                             {'events': [{'type': 'agent.session.input.cancel'}]}, empty_ack=True)
