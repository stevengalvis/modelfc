"""Explicit one-shot entry points. No timer or service is installed or started."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from .bridge_checkout import Checkout
from .bridge_dispatch import Dispatcher
from .bridge_evidence import Evidence
from .bridge_executor import Executor
from .bridge_github import PublisherGitHub
from .bridge_policy import load as load_policy
from .bridge_publisher import Publisher
from .bridge_status import post_once
from .bridge_security import disjoint, protected_bytes, protected_dir, protected_json
from .bridge_transport import GitHubReader
from .launcher import Configuration


def _settings(path):
    value = protected_json(path)
    keys = {'policy', 'state_root', 'mirror', 'workspace_root', 'evidence_root', 'publisher_root',
            'username', 'executable', 'executable_sha256', 'reader_token_file', 'publisher_token_file'}
    if type(value) is not dict or set(value) != keys or any(type(v) is not str or not v for v in value.values()):
        raise ValueError('invalid_settings')
    for key in ('state_root', 'mirror', 'workspace_root', 'evidence_root', 'publisher_root'):
        protected_dir(value[key], 0o700 if key in ('state_root', 'evidence_root', 'publisher_root') else None)
    roots = [Path(value[k]) for k in ('state_root', 'mirror', 'workspace_root', 'evidence_root', 'publisher_root')]
    for index, root in enumerate(roots):
        for other in roots[index + 1:]:
            if root == other or root in other.parents or other in root.parents:
                raise ValueError('overlapping_roots')
    for key in ('reader_token_file', 'publisher_token_file'):
        disjoint(value[key], *roots)
    return value


def _token(path):
    token = protected_bytes(path, maximum=4096).decode('utf-8').strip()
    if not token or '\n' in token or '\r' in token:
        raise ValueError('invalid_token')
    return token


def _configuration(settings, policy):
    return Configuration(workspace_root=settings['workspace_root'], evidence_root=settings['evidence_root'],
                         username=settings['username'], executable=settings['executable'],
                         executable_sha256=settings['executable_sha256'],
                         timeout_seconds=policy.max_timeout, output_bytes=policy.max_output,
                         diff_bytes=policy.max_diff, prepared_baseline=True)


def dispatch_once(path):
    settings = _settings(path)
    policy = load_policy(settings['policy'])
    config = _configuration(settings, policy)
    reader = GitHubReader(_token(settings['reader_token_file']), policy.thread)
    return Dispatcher(settings['state_root'], reader, policy,
                      Executor(config, settings['mirror'])).once()


def publish_once(path):
    settings = _settings(path)
    policy = load_policy(settings['policy'])
    config = _configuration(settings, policy)
    reader = GitHubReader(_token(settings['reader_token_file']), policy.thread)
    github = PublisherGitHub(_token(settings['publisher_token_file']))
    dbpath = Path(settings['state_root']) / 'receipts.sqlite'
    if not dbpath.is_file() or dbpath.is_symlink():
        return {'status': 'idle'}
    with sqlite3.connect(f'file:{dbpath}?mode=ro', uri=True) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
        query = "SELECT job FROM jobs WHERE status='completed'"
        if exists:
            query += " AND (NOT EXISTS (SELECT 1 FROM publications WHERE publications.job=jobs.job) OR EXISTS (SELECT 1 FROM publications WHERE publications.job=jobs.job AND phase='pr_pending'))"
        rows = db.execute(query + ' ORDER BY rowid LIMIT 100').fetchall()
    if not rows:
        return {'status': 'idle'}
    publisher = Publisher(settings['state_root'], policy, reader, Evidence(config),
                          Checkout(settings['mirror'], settings['publisher_root']), github, clock=time.time)
    for row in rows:
        result = publisher.publish(row[0])
        if result.get('status') in ('published', 'busy'):
            return result
    return {'status': 'blocked'}


def status_once(path):
    settings = _settings(path)
    policy = load_policy(settings['policy'])
    github = PublisherGitHub(_token(settings['publisher_token_file']))
    return post_once(settings['state_root'], policy.thread, github)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('dispatch', 'publish', 'status'))
    parser.add_argument('settings')
    args = parser.parse_args()
    try:
        result = {'dispatch': dispatch_once, 'publish': publish_once, 'status': status_once}[args.mode](args.settings)
    except Exception:
        result = {'status': 'blocked'}
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
