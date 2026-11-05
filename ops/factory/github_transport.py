"""Outbound GitHub HTTP only. Credentials stay here; no model/agent dependency."""
from dataclasses import dataclass
import json
import signal
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contract import REPOSITORY, MAX_RESULT

MAX_RESPONSE = 16384
TIMEOUT = 10


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes = b''


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@contextmanager
def deadline():
    # Unix main-thread adapter only. Refuse to replace an existing caller timer.
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('main thread required')
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError('timer already active')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise TimeoutError('transport deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class GitHubTransport:
    """Explicit construction only; caller supplies existing, separately approved access."""
    def __init__(self, token):
        self._token = token
        self._opener = build_opener(NoRedirect())

    def request(self, method, issue, *, etag=None, body=None):
        if type(issue) is not int or not 1 <= issue <= 9999999999:
            raise ValueError('invalid issue')
        if method not in {'GET', 'POST'}:
            raise ValueError('invalid method')
        if method == 'POST' and (type(body) is not str or len(body.encode()) > MAX_RESULT):
            raise ValueError('invalid result')
        headers = {'Authorization': 'Bearer ' + self._token,
                   'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'modelfc-dry-run-mailbox'}
        if etag is not None:
            headers['If-None-Match'] = etag
        url = f'https://api.github.com/repos/{REPOSITORY}/issues/{issue}'
        data = None
        if method == 'POST':
            url += '/comments'
            data = json.dumps({'body': body}).encode()
        request = Request(url, headers=headers, data=data, method=method)
        with deadline():
            try:
                response = self._opener.open(request, timeout=TIMEOUT)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError('response limit')
                return Response(response.code, dict((k.lower(), v) for k, v in response.headers.items()), raw)
