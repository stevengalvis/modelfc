"""Bounded GitHub publication adapter, explicitly constructed in publisher only."""
import base64
import hashlib
import json
import re
from urllib.parse import quote
from urllib.request import Request, build_opener

from .bridge_transport import _NoRedirect, _deadline, _pairs

OWNER = 'stevengalvis'
REPO = 'modelfc'
API = 'https://api.github.com'
MAX_RESPONSE = 262144


class PublisherGitHub:
    def __init__(self, token):
        if type(token) is not str or not token or '\r' in token or '\n' in token:
            raise ValueError('invalid_token')
        self._token = token
        self._opener = build_opener(_NoRedirect())

    def _request(self, method, path, data=None):
        if not path.startswith('/repos/stevengalvis/modelfc/') and path != '/graphql':
            raise ValueError('invalid_api_path')
        payload = None if data is None else json.dumps(data, separators=(',', ':')).encode()
        if payload is not None and len(payload) > 1048576:
            raise ValueError('request_oversized')
        request = Request(API + path, data=payload, method=method, headers={
            'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json',
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'modelfc-bridge-publisher'})
        with _deadline():
            with self._opener.open(request, timeout=10) as response:
                if response.status not in (200, 201):
                    raise ValueError('github_status')
                raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('github_response_oversized')
        obj = json.loads(raw, object_pairs_hook=_pairs)
        if type(obj) is dict and 'errors' in obj:
            raise ValueError('github_error')
        return obj

    def _rest(self, method, suffix, data=None):
        return self._request(method, '/repos/stevengalvis/modelfc/' + suffix, data)

    def post_status(self, thread, body):
        if type(thread) is not int or not 1 <= thread <= 2147483647 or type(body) is not str or len(body.encode()) > 2048:
            raise ValueError('invalid_status')
        self._rest('POST', f'issues/{thread}/comments', {'body': body})

    def main_head(self):
        obj = self._rest('GET', 'git/ref/heads/main')
        value = obj['object']['sha']
        if not re.fullmatch('[a-f0-9]{40}', value):
            raise ValueError('invalid_head')
        return value

    def create_branch(self, branch, base):
        self._rest('POST', 'git/refs', {'ref': 'refs/heads/' + branch, 'sha': base})

    def create_commit(self, branch, base, contents, job, patch_digest):
        mutation = '''mutation($input:CreateCommitOnBranchInput!) {
          createCommitOnBranch(input:$input) { commit { oid } }
        }'''
        additions = [{'path': name, 'contents': base64.b64encode(content).decode()}
                     for name, content in sorted(contents.items())]
        result = self._request('POST', '/graphql', {'query': mutation, 'variables': {'input': {
            'branch': {'repositoryNameWithOwner': OWNER + '/' + REPO, 'branchName': branch},
            'expectedHeadOid': base, 'message': {'headline': 'Bounded task ' + job,
                                                 'body': 'Patch SHA256: ' + patch_digest},
            'fileChanges': {'additions': additions}}}})
        oid = result['data']['createCommitOnBranch']['commit']['oid']
        if not re.fullmatch('[a-f0-9]{40}', oid):
            raise ValueError('invalid_commit')
        return oid

    def verify_commit(self, branch, base, commit_id, contents):
        if not re.fullmatch('[a-f0-9]{40}', commit_id):
            return False
        ref = self._rest('GET', 'git/ref/heads/' + quote(branch, safe='/'))
        if ref['object']['sha'] != commit_id:
            return False
        commit = self._rest('GET', 'git/commits/' + commit_id)
        if [parent.get('sha') for parent in commit.get('parents', [])] != [base]:
            return False
        tree = commit.get('tree', {}).get('sha')
        if type(tree) is not str or not re.fullmatch('[a-f0-9]{40}', tree):
            return False
        for path in contents:
            current = tree
            for index, part in enumerate(path.split('/')):
                listing = self._rest('GET', 'git/trees/' + current)
                if listing.get('truncated') is not False or type(listing.get('tree')) is not list:
                    return False
                matches = [entry for entry in listing['tree'] if entry.get('path') == part]
                if len(matches) != 1:
                    return False
                entry = matches[0]
                if index == len(path.split('/')) - 1:
                    if entry.get('mode') != '100644' or entry.get('type') != 'blob':
                        return False
                elif entry.get('mode') != '040000' or entry.get('type') != 'tree':
                    return False
                current = entry.get('sha')
                if type(current) is not str or not re.fullmatch('[a-f0-9]{40}', current):
                    return False
        compare = self._rest('GET', 'compare/' + base + '...' + commit_id + '?per_page=100')
        if compare.get('total_commits') != 1 or compare.get('status') != 'ahead':
            return False
        files = compare.get('files')
        if type(files) is not list or len(files) != len(contents) or len(files) >= 100:
            return False
        seen = set()
        for entry in files:
            path = entry.get('filename')
            if path not in contents or path in seen or entry.get('status') != 'modified':
                return False
            raw = contents[path]
            git_digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if entry.get('sha') != git_digest:
                return False
            seen.add(path)
        return seen == set(contents)

    def create_draft(self, branch, job, commit_id, patch_digest):
        obj = self._rest('POST', 'pulls', {'title': 'Bounded task ' + job,
            'head': branch, 'base': 'main', 'draft': True,
            'body': 'Bounded task `' + job + '`.\n\nPatch SHA256: `' + patch_digest + '`.'})
        return obj['html_url']

    def find_draft(self, branch):
        pulls = self._rest('GET', 'pulls?state=open&head=stevengalvis:' + quote(branch, safe='') + '&base=main&per_page=100')
        if type(pulls) is not list or len(pulls) != 1:
            return None
        return pulls[0].get('html_url')

    def verify_draft(self, branch, base, commit_id, url):
        if type(url) is not str or not re.fullmatch(r'https://github\.com/stevengalvis/modelfc/pull/[1-9][0-9]*', url):
            return False
        pulls = self._rest('GET', 'pulls?state=open&head=stevengalvis:' + quote(branch, safe='') + '&base=main&per_page=100')
        if type(pulls) is not list or len(pulls) != 1:
            return False
        pr = pulls[0]
        return (pr.get('html_url') == url and pr.get('draft') is True
                and pr.get('head', {}).get('sha') == commit_id
                and pr.get('head', {}).get('ref') == branch
                and pr.get('base', {}).get('ref') == 'main'
                and pr.get('base', {}).get('sha') == base)
