"""Bounded, read-only GraphQL transport for a protected PR command channel.

Construction and credential custody are the trusted operator's responsibility.
This module does not schedule polling or invoke a worker.
"""
import json
import signal
import threading
from contextlib import contextmanager
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE = 262144
QUERY = '''query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    nameWithOwner
    pullRequest(number:$number) {
      number
      comments(last:100) {
        pageInfo { hasPreviousPage }
        nodes {
          id databaseId body lastEditedAt includesCreatedEdit
          author { __typename ... on User { databaseId } ... on Bot { databaseId } }
        }
      }
    }
  }
}'''


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@contextmanager
def _deadline():
    if threading.current_thread() is not threading.main_thread() or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError('transport_deadline_unavailable')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_args):
        raise TimeoutError('transport_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate_key')
        value[key] = item
    return value


class GitHubReader:
    def __init__(self, token, thread):
        if type(token) is not str or not token or '\n' in token or '\r' in token:
            raise ValueError('invalid_token')
        if type(thread) is not int or not 1 <= thread <= 2147483647:
            raise ValueError('invalid_thread')
        self._token = token
        self._thread = thread
        self._opener = build_opener(_NoRedirect())

    def comments(self):
        payload = json.dumps({'query': QUERY, 'variables': {'owner': 'stevengalvis',
                             'repo': 'modelfc', 'number': self._thread}}).encode()
        request = Request('https://api.github.com/graphql', data=payload, headers={
            'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json',
            'Accept': 'application/vnd.github+json', 'User-Agent': 'modelfc-bridge'}, method='POST')
        with _deadline():
            with self._opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise ValueError('transport_status')
                raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('transport_oversized')
        result = json.loads(raw, object_pairs_hook=_pairs)
        try:
            if 'errors' in result:
                raise ValueError('graphql_error')
            repo = result['data']['repository']
            pr = repo['pullRequest']
            if repo['nameWithOwner'] != 'stevengalvis/modelfc' or pr['number'] != self._thread:
                raise ValueError('channel_mismatch')
            conn = pr['comments']
            if conn['pageInfo']['hasPreviousPage'] is not False:
                raise ValueError('invalid_page')
            nodes = conn['nodes']
            if type(nodes) is not list or len(nodes) > 100:
                raise ValueError('invalid_page')
            comments = []
            for node in nodes:
                author = node['author']
                if (type(node['id']) is not str or not node['id'] or len(node['id']) > 200
                        or type(node['databaseId']) is not int or node['databaseId'] <= 0
                        or type(node['body']) is not str or len(node['body'].encode()) > 16384
                        or type(node['includesCreatedEdit']) is not bool
                        or type(author) is not dict or author['__typename'] not in ('User', 'Bot')
                        or type(author['databaseId']) is not int or author['databaseId'] <= 0):
                    raise ValueError('invalid_comment')
                # Any edit marker blocks; missing fields or nullable author fail closed.
                edited = node['lastEditedAt']
                if node['includesCreatedEdit']:
                    edited = 'edited'
                comments.append((node['id'], node['body'].encode(), {
                    'actor_id': author['databaseId'], 'thread': self._thread,
                    'comment_id': node['databaseId'], 'last_edited_at': edited}))
            return comments
        except (KeyError, TypeError, AttributeError):
            raise ValueError('invalid_response') from None
