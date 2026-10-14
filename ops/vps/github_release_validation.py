"""Trusted main-push release gate; never run candidate application code here."""
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_offline_validation import GitHub, REPOSITORY, Rejected, transport
from release_evidence import binding, validate_envelope


def candidate(event, environment):
    sha = environment.get('GITHUB_SHA', '')
    if (environment.get('GITHUB_EVENT_NAME') != 'push'
            or environment.get('GITHUB_REF') != 'refs/heads/main'
            or environment.get('GITHUB_REPOSITORY') != REPOSITORY
            or environment.get('GITHUB_WORKFLOW_REF') != REPOSITORY +
            '/.github/workflows/tests.yml@refs/heads/main'
            or not re.fullmatch(r'[0-9a-f]{40}', sha)
            or event.get('after') != sha or event.get('ref') != 'refs/heads/main'
            or event.get('deleted') is not False
            or event.get('repository', {}).get('full_name') != REPOSITORY
            or event.get('repository', {}).get('default_branch') != 'main'):
        raise Rejected
    return sha


def execute(sha, api, remote=transport):
    # Fail stale requests before using SSH; host rechecks canonical main itself.
    tip = api.call('/git/ref/heads/main')
    if (tip.get('ref') != 'refs/heads/main' or tip.get('object', {}).get('type') != 'commit'
            or tip.get('object', {}).get('sha') != sha):
        raise Rejected
    value = validate_envelope(remote(None, sha, release=True), sha)
    expected = binding({'repository': REPOSITORY, 'image': value['image_digest']},
                       Path(__file__).resolve().parent, lambda path: None)
    if any(value[key] != expected[key] for key in expected):
        raise Rejected
    return value


def main():
    output = {'status': 'UNVERIFIED', 'candidate_sha': None, 'reason': 'INVALID_RELEASE_EVIDENCE'}
    code = 1
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        sha = candidate(event, os.environ)
        output['candidate_sha'] = sha
        value = execute(sha, GitHub(os.environ['GITHUB_TOKEN']))
        output = {'status': value['report']['result'], 'evidence': value}
        code = 0 if output['status'] == 'PASS' else 1
    except Exception:
        pass  # No raw transport/API/candidate output or credentials in evidence.
    finally:
        os.environ.pop('VALIDATOR_SSH_KEY_BASE64', None)
    raw = json.dumps(output, sort_keys=True)
    print(raw)
    # Artifact publication is best effort; it cannot turn failure into success.
    try:
        Path(os.environ['RELEASE_OFFLINE_OUTPUT']).write_text(raw + '\n')
    except (OSError, KeyError):
        pass
    return code


if __name__ == '__main__':
    sys.exit(main())
