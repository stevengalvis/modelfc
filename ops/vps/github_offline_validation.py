"""Trusted default-branch GitHub helper. Never checkout/import candidate code."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))
from offline_report import MAX_REPORT, validate_report

REPOSITORY = 'stevengalvis/modelfc'
CONTEXT = 'Trusted OFFLINE'
SHA = re.compile(r'[0-9a-f]{40}')
LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})')
DEPLOY_FINGERPRINT = 'SHA256:IpHgSvswGdBNFT6ETalX+2rGwbhdfItuXkDS4JSN1LE'


class Rejected(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token):
        self.token = token

    def call(self, path, data=None):
        request = Request('https://api.github.com/repos/' + REPOSITORY + path,
                          data=None if data is None else json.dumps(data).encode(),
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28',
                                   'User-Agent': 'Zeno-trusted-offline'})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise Rejected
            return json.loads(raw)

    def status(self, sha, state, description):
        self.call('/statuses/' + sha, {'state': state, 'context': CONTEXT,
                                     'description': description})


def event_identity(event):
    """Exact body: no whitespace stripping; usernames/PR only from event metadata."""
    try:
        comment, issue = event['comment'], event['issue']
        user = comment['user']
        if (event['action'] != 'created' or event['repository']['full_name'] != REPOSITORY
                or event['repository']['default_branch'] != 'main'
                or not issue.get('pull_request') or comment['body'] != '/validate-offline'
                or comment['author_association'] not in {'OWNER', 'MEMBER', 'COLLABORATOR'}
                or user['type'] != 'User' or not LOGIN.fullmatch(user['login'])
                or type(user['id']) is not int or user['id'] < 1
                or event['sender']['id'] != user['id']
                or event['sender']['login'] != user['login']
                or type(issue['number']) is not int or not 1 <= issue['number'] <= 9999999999):
            raise Rejected
        return issue['number'], user
    except (KeyError, TypeError, AttributeError):
        raise Rejected from None


def current_head(pr, number):
    try:
        sha = pr['head']['sha']
        if (pr['number'] != number or pr['state'] != 'open'
                or pr['base']['repo']['full_name'] != REPOSITORY
                or pr['head']['repo']['full_name'] != REPOSITORY
                or not SHA.fullmatch(sha)):
            raise Rejected
        return sha
    except (KeyError, TypeError, AttributeError):
        raise Rejected from None


def resolve(event, api):
    number, user = event_identity(event)
    permission = api.call('/collaborators/' + user['login'] + '/permission')
    # GitHub maps maintain to write and triage to read. Check immutable user ID too.
    if (permission.get('permission') not in {'admin', 'write'}
            or permission.get('user', {}).get('id') != user['id']):
        raise Rejected
    return number, current_head(api.call('/pulls/' + str(number)), number)


def transport(number, sha):
    """One bounded SSH invocation; fixed user/command, pinned host and dedicated key."""
    host = os.environ.get('VALIDATOR_HOST', '')
    pin = os.environ.get('VALIDATOR_HOST_KEY', '')
    fingerprint = os.environ.get('VALIDATOR_KEY_FINGERPRINT', '')
    key = os.environ.pop('VALIDATOR_SSH_KEY', '')
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host)
            or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint)
            or fingerprint == DEPLOY_FINGERPRINT
            or not re.fullmatch(re.escape(host) + r' ssh-ed25519 [A-Za-z0-9+/]+={0,2}', pin)
            or not key or len(key) > 16384):
        raise Rejected
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
    with tempfile.TemporaryDirectory(prefix='zeno-offline-') as directory:
        root = Path(directory)
        private = root / 'identity'
        private.touch(mode=0o600)
        private.write_text(key)
        hosts = root / 'known_hosts'
        hosts.write_text(pin + '\n')
        public = subprocess.run(['/usr/bin/ssh-keygen', '-y', '-P', '', '-f', str(private)],
                                stdin=subprocess.DEVNULL, capture_output=True, env=env,
                                timeout=10, check=True)
        digest = subprocess.run(['/usr/bin/ssh-keygen', '-lf', '-', '-E', 'sha256'],
                                input=public.stdout, capture_output=True, env=env,
                                timeout=10, check=True)
        if digest.stdout.decode('ascii').split()[1] != fingerprint:
            raise Rejected
        args = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes',
                '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(hosts), '-o', 'GlobalKnownHostsFile=/dev/null',
                '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15',
                '-o', 'ServerAliveCountMax=4', '-i', str(private),
                'modelfc-validator-automation@' + host,
                f'validate-offline {REPOSITORY} {number} {sha}']
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, timeout=420)
        if result.returncode or len(result.stdout) > MAX_REPORT:
            raise Rejected
        return result.stdout


def execute(event, expected_sha, api, remote=transport):
    # Reauthorize and reread current head after resolve, immediately before pending/SSH.
    number, sha = resolve(event, api)
    if not SHA.fullmatch(expected_sha) or sha != expected_sha:
        raise Rejected
    api.status(sha, 'pending', 'Installed OFFLINE validation running')
    state, description = 'error', 'OFFLINE transport or timeout failure'
    try:
        raw = remote(number, sha)
        try:
            report = validate_report(raw, sha)
        except ValueError:
            state, description = 'error', 'Invalid trusted OFFLINE report'
        else:
            if report['result'] == 'PASS':
                state, description = 'success', 'OFFLINE PASS; provider compatibility not tested'
            elif report['reason'] == 'BUSY':
                state, description = 'failure', 'OFFLINE validator busy; no retry performed'
            else:
                state, description = 'failure', 'Trusted OFFLINE validation failed'
            # Never transfer a result to a different head, even if it moved mid-run.
            after = current_head(api.call('/pulls/' + str(number)), number)
            if after != sha:
                description = 'OFFLINE result for old SHA; new head needs validation'
    except Exception:
        state, description = 'error', 'OFFLINE transport, API or timeout failure'
    api.status(sha, state, description)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('resolve', 'execute'))
    args = parser.parse_args()
    try:
        if (os.environ.get('GITHUB_EVENT_NAME') != 'issue_comment'
                or os.environ.get('GITHUB_REPOSITORY') != REPOSITORY
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'
                or os.environ.get('GITHUB_WORKFLOW_REF') != REPOSITORY +
                '/.github/workflows/trusted-offline.yml@refs/heads/main'):
            raise Rejected
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        api = GitHub(os.environ['GITHUB_TOKEN'])
        if args.phase == 'resolve':
            _, sha = resolve(event, api)
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('sha=' + sha + '\nready=true\n')
            print('Authorized current same-repository PR resolved')
        else:
            state = execute(event, os.environ['RESOLVED_SHA'], api)
            print('Trusted OFFLINE status: ' + state)
            return 0 if state == 'success' else 1
        return 0
    except Exception:
        print('OFFLINE request rejected or GitHub operation failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
