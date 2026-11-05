"""Inactive bounded Linear adapters. Construction/import never performs I/O.

The trusted caller supplies protected policy and credentials; no CLI or polling.
"""
import json
import re
from urllib.request import Request as HTTPRequest, build_opener

from .bridge_transport import _NoRedirect, _deadline, _pairs, MAX_RESPONSE
from .linear_status import Policy, Request, REPORTS

DESTINATION = '''
 organization { id }
 viewer { id active organization { id } }
 issue(id:$issue) {
   id archivedAt inheritsSharedAccess
   sharedAccess { isShared sharedWithCount viewerHasOnlySharedAccess }
   team {
     id archivedAt visibility parent { id } organization { id }
     members(first:100) { pageInfo { hasNextPage } nodes { id active } }
   }
 }
'''
COMMENT = '''id body archivedAt editedAt updatedAt createdAt
 user { id active organization { id } }
 botActor { __typename } externalUser { id } onBehalfOf { id }
 externalThread { __typename } syncedWith { __typename } parent { id }
 issue { id }
'''
READ = 'query($issue:String!,$comment:String!){' + DESTINATION + 'comment(id:$comment){' + COMMENT + '}}'
CHECK = 'query($issue:String!){' + DESTINATION + '}'
POST = '''mutation($issue:String!,$body:String!){
 commentCreate(input:{issueId:$issue,body:$body}) {
   success comment { id body user { id } issue { id } }
 }
}'''


def _id(value):
    if type(value) is not str or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value):
        raise ValueError('invalid_linear_id')
    return value


class LinearAPI:
    def __init__(self, token, *, oauth=False):
        if (type(token) is not str or not token or len(token) > 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in token)
                or type(oauth) is not bool):
            raise ValueError('invalid_credential')
        self._authorization = ('Bearer ' if oauth else '') + token
        self._opener = build_opener(_NoRedirect())

    def call(self, query, variables):
        # Only trusted fixed query constants reach this internal boundary.
        if query not in (READ, CHECK, POST):
            raise ValueError('unsupported_query')
        request = HTTPRequest('https://api.linear.app/graphql',
                              data=json.dumps({'query': query, 'variables': variables}).encode(),
                              headers={'Authorization': self._authorization,
                                       'Content-Type': 'application/json',
                                       'Accept': 'application/json'}, method='POST')
        try:
            with _deadline():
                with self._opener.open(request, timeout=10) as response:
                    if response.status != 200:
                        raise ValueError('http_failure')
                    raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError('oversized_response')
            result = json.loads(raw, object_pairs_hook=_pairs)
            if type(result) is not dict or 'errors' in result or type(result.get('data')) is not dict:
                raise ValueError('incomplete_response')
            return result['data']
        except Exception:
            # Never surface response/error text, tokens, issue contents or raw logs.
            raise ValueError('linear_transport_failed') from None


class Destination:
    def __init__(self, policy, viewer_id, member_ids):
        if type(policy) is not Policy:
            raise ValueError('invalid_policy')
        for value in (policy.workspace, policy.team, policy.issue, policy.actor, viewer_id):
            _id(value)
        if (type(member_ids) is not tuple or not 1 <= len(member_ids) <= 100
                or len(set(member_ids)) != len(member_ids)
                or viewer_id not in member_ids or policy.actor not in member_ids):
            raise ValueError('invalid_members')
        for value in member_ids:
            _id(value)
        self.policy, self.viewer_id, self.member_ids = policy, viewer_id, frozenset(member_ids)

    def check(self, data):
        try:
            policy = self.policy
            viewer, issue = data['viewer'], data['issue']
            team, shared = issue['team'], issue['sharedAccess']
            members = team['members']
            nodes = members['nodes']
            if (data['organization']['id'] != policy.workspace
                    or viewer['id'] != self.viewer_id or viewer['active'] is not True
                    or viewer['organization']['id'] != policy.workspace
                    or issue['id'] != policy.issue or issue['archivedAt'] is not None
                    or issue['inheritsSharedAccess'] is not False
                    or shared['isShared'] is not False
                    or type(shared['sharedWithCount']) is not int or shared['sharedWithCount'] != 0
                    or shared['viewerHasOnlySharedAccess'] is not False
                    or team['id'] != policy.team or team['archivedAt'] is not None
                    or team['organization']['id'] != policy.workspace or team['visibility'] != 'private'
                    or team['parent'] is not None
                    or members['pageInfo']['hasNextPage'] is not False
                    or type(nodes) is not list or len(nodes) != len(self.member_ids)
                    or any(node['active'] is not True for node in nodes)
                    or {node['id'] for node in nodes} != self.member_ids):
                raise ValueError('destination_mismatch')
        except (KeyError, TypeError, AttributeError):
            raise ValueError('invalid_destination') from None


class LinearReader:
    def __init__(self, api, destination):
        self.api, self.destination = api, destination

    def fetch(self, comment):
        _id(comment)
        policy = self.destination.policy
        data = self.api.call(READ, {'issue': policy.issue, 'comment': comment})
        self.destination.check(data)
        try:
            value = data['comment']
            user = value['user']
            if (value['id'] != comment or value['issue']['id'] != policy.issue
                    or value['archivedAt'] is not None
                    or user['id'] != policy.actor or user['active'] is not True
                    or user['organization']['id'] != policy.workspace
                    or any(value[k] is not None for k in ('botActor', 'externalUser', 'onBehalfOf', 'externalThread', 'parent'))
                    or value['syncedWith'] != []
                    or type(value['body']) is not str or len(value['body'].encode()) > 1024
                    or type(value['createdAt']) is not str or not value['createdAt']
                    or type(value['updatedAt']) is not str or not value['updatedAt']
                    or (value['editedAt'] is not None and type(value['editedAt']) is not str)):
                raise ValueError('invalid_requester')
            # Never turn a null editedAt into a claim of immutable edit history.
            return Request(value['body'].encode(), policy.workspace, policy.team, policy.issue,
                           user['id'], comment, None)
        except (KeyError, TypeError, AttributeError):
            raise ValueError('invalid_linear_comment') from None


class LinearSink:
    def __init__(self, api, destination):
        self.api, self.destination = api, destination
        self.last_comment_id = None

    def post(self, issue, report):
        self.last_comment_id = None
        if issue != self.destination.policy.issue or type(report) is not str or report not in REPORTS:
            raise ValueError('invalid_report')
        self.destination.check(self.api.call(CHECK, {'issue': issue}))
        data = self.api.call(POST, {'issue': issue, 'body': report})
        try:
            result = data['commentCreate']
            comment = result['comment']
            if (result['success'] is not True or comment['issue']['id'] != issue
                    or comment['body'] != report or comment['user']['id'] != self.destination.viewer_id):
                raise ValueError('unconfirmed_delivery')
            self.last_comment_id = _id(comment['id'])
        except (KeyError, TypeError, AttributeError):
            raise ValueError('unconfirmed_delivery') from None
        return True
