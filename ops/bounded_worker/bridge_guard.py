"""Fail closed on unresolved or malformed durable bridge receipts.

Call only while holding the global bridge lock, before any network or worker I/O.
Missing optional tables are normal before their respective mode has run.
"""
import hashlib
import json
import math
import re

from .bridge_envelope import _pairs
from .task import Task


def _exists(db, table):
    row = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
    if row and row[0] != 'table':
        raise ValueError('invalid_receipt_table')
    return row is not None


def _text(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def safe(db):
    """Return False for incomplete, unknown, inconsistent or corrupt receipts."""
    try:
        if not _exists(db, 'jobs'):
            return False
        jobs, comments, nodes = {}, set(), set()
        for job, digest, comment, node, status, body in db.execute(
                'SELECT job,digest,comment,node,status,body FROM jobs'):
            if (not _text(job, r'[a-z0-9][a-z0-9_-]{0,63}')
                    or not _text(digest, r'[0-9a-f]{64}')
                    or type(comment) is not int or not 1 <= comment <= 9223372036854775807
                    or type(node) is not str or not 1 <= len(node) <= 200
                    or job in jobs or comment in comments or node in nodes
                    or status not in ('completed', 'failed', 'timeout', 'rejected')
                    or type(body) is not bytes or len(body) > 16384
                    or hashlib.sha256(body).hexdigest() != digest):
                return False
            obj = json.loads(body.decode('utf-8'), object_pairs_hook=_pairs)
            if (type(obj) is not dict or set(obj) != {'version', 'job_id', 'base', 'paths', 'prompt',
                    'expires', 'timeout', 'output', 'diff', 'publish'}
                    or json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() != body
                    or type(obj['version']) is not int or obj['version'] != 1
                    or obj['job_id'] != job or type(obj['expires']) is not int
                    or obj['publish'] is not True or type(obj['paths']) is not list):
                return False
            task = Task(job, 'stevengalvis/modelfc', obj['base'], obj['prompt'], tuple(obj['paths']),
                        obj['timeout'], obj['output'], obj['diff'])
            task.validate()
            jobs[job] = status
            comments.add(comment)
            nodes.add(node)
        if _exists(db, 'poll'):
            rows = db.execute('SELECT id,next,failures FROM poll').fetchall()
            if len(rows) > 1:
                return False
            for ident, next_at, failures in rows:
                if (ident != 1 or type(next_at) not in (int, float) or not math.isfinite(next_at)
                        or type(failures) is not int or not 0 <= failures <= 6):
                    return False
        published = set()
        if _exists(db, 'publications'):
            for job, phase, branch, patch, commit, url in db.execute(
                    'SELECT job,phase,branch,patch,commit_id,url FROM publications'):
                if (jobs.get(job) != 'completed' or phase != 'published'
                        or job in published
                        or branch != 'bounded/' + hashlib.sha256(job.encode()).hexdigest()[:32]
                        or not _text(patch, r'[0-9a-f]{64}') or not _text(commit, r'[0-9a-f]{40}')
                        or not _text(url, r'https://github\.com/stevengalvis/modelfc/pull/[1-9][0-9]*')):
                    return False
                published.add(job)
        if _exists(db, 'status_posts'):
            seen_statuses = set()
            for job, outcome, phase in db.execute('SELECT job,outcome,phase FROM status_posts'):
                if (job not in jobs or phase != 'posted'
                        or (job, outcome) in seen_statuses
                        or outcome not in ('awaiting_publication', 'published', 'failed', 'timeout', 'rejected', 'blocked')
                        or (outcome == 'published' and job not in published)
                        or (outcome == 'awaiting_publication' and jobs[job] != 'completed')
                        or (outcome in ('failed', 'timeout', 'rejected') and outcome != jobs[job])):
                    return False
                seen_statuses.add((job, outcome))
        return True
    except Exception:
        return False
