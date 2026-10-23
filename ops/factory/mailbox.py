"""One configured issue, one explicitly granted dry-run job per invocation.

No entrypoint, scheduler, execution adapter or installation hook. An uncertain
POST is never automatically retried, including after a crash/restart.
"""
from dataclasses import asdict
import fcntl
from pathlib import Path
import sqlite3
import time

from .contract import Rejected, REPOSITORY, strict_json, validate, result, digest
from .github_transport import MAX_RESPONSE


class Mailbox:
    def __init__(self, state_path, transport, grant, *, clock=time.time):
        # The parent directory must be operator-controlled; never use issue paths.
        self.path = Path(state_path)
        self.transport = transport
        self.grant = grant
        self.clock = clock

    def _issue(self, response):
        if response.status != 200 or len(response.body) > MAX_RESPONSE:
            raise Rejected()
        issue = strict_json(response.body)
        g = self.grant
        try:
            if (type(issue) is not dict or 'pull_request' in issue
                    or type(issue['number']) is not int or issue['number'] != g.issue
                    or issue['url'] != f'https://api.github.com/repos/{REPOSITORY}/issues/{g.issue}'
                    or type(issue['user']['id']) is not int or issue['user']['id'] != g.sender_id
                    or issue['state'] not in {'open', 'closed'}
                    or type(issue['updated_at']) is not str or len(issue['updated_at']) > 40):
                raise Rejected()
            validate(issue['body'], g, self.clock())
        except (KeyError, TypeError):
            raise Rejected() from None
        return issue

    def poll_once(self, *, cancelled=lambda: False):
        """Bounded calls, nonblocking lock, durable at-most-once acknowledgement attempt."""
        with open(str(self.path) + '.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'busy'
            with sqlite3.connect(self.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, digest TEXT, status TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS poll (issue INTEGER PRIMARY KEY, etag TEXT, next REAL, failures INTEGER, binding TEXT)')
                db.commit()
                return self._poll(db, cancelled)

    def _poll(self, db, cancelled):
        g = self.grant
        previous = db.execute('SELECT digest,status FROM jobs WHERE id=?', (g.job_id,)).fetchone()
        if previous:
            if previous[0] != g.content_digest:
                return 'rejected'
            if previous[1] in {'reserved', 'ack_pending'}:
                self._status(db, 'uncertain')
                return 'uncertain'
            return previous[1]
        if cancelled():
            self._record(db, 'cancelled')
            return 'cancelled'
        if self.clock() >= g.expires_at:
            return 'rejected'
        row = db.execute('SELECT etag,next,failures,binding FROM poll WHERE issue=?', (g.issue,)).fetchone()
        etag, next_at, failures, binding = row or (None, 0, 0, None)
        if binding != digest(asdict(g)):
            etag = None
        if self.clock() < next_at:
            return 'backoff'
        # Reserve a minimum interval before network I/O, even if the process dies.
        self._schedule(db, etag, 60, failures)
        try:
            response = self.transport.request('GET', g.issue, etag=etag)
            if response.status == 304:
                return 'unchanged'
            if response.status != 200:
                self._backoff(db, response, etag, failures)
                return 'backoff'
            self._schedule(db, self._etag(response), 60, 0)
            issue = self._issue(response)
            if issue['state'] == 'closed' or cancelled():
                self._record(db, 'cancelled')
                return 'cancelled'
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (g.job_id, g.content_digest, 'reserved'))
            db.commit()
            # Fresh unconditional read before write: no cached author/body/state.
            fresh = self._issue(self.transport.request('GET', g.issue))
            if fresh['state'] == 'closed' or cancelled():
                self._status(db, 'cancelled')
                return 'cancelled'
            if fresh['body'] != issue['body'] or fresh['updated_at'] != issue['updated_at']:
                raise Rejected()
            validate(fresh['body'], g, self.clock())
            # Commit before POST. A lost response is uncertain, never replayed.
            self._status(db, 'ack_pending')
            posted = self.transport.request('POST', g.issue, body=result(g, 'acknowledged'))
            status = 'acknowledged' if posted.status == 201 else 'uncertain'
            self._status(db, status)
            self._schedule(db, self._etag(response), 60, 0)
            return status
        except Rejected:
            # Do not publish attacker content or diagnostics.
            self._status(db, 'rejected')
            return 'rejected'
        except Exception:
            # Includes timeout: raw errors may contain credentials or issue text.
            self._status(db, 'uncertain')
            self._schedule(db, etag, min(3600, 60 * 2 ** min(failures + 1, 6)), failures + 1)
            return 'uncertain'

    def _record(self, db, status):
        db.execute('INSERT INTO jobs VALUES (?,?,?)',
                   (self.grant.job_id, self.grant.content_digest, status))
        db.commit()

    def _status(self, db, status):
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, self.grant.job_id))
        db.commit()

    @staticmethod
    def _etag(response):
        value = response.headers.get('etag')
        return value if type(value) is str and len(value) <= 256 and all(32 <= ord(c) < 127 for c in value) else None

    def _schedule(self, db, etag, delay, failures):
        db.execute('INSERT OR REPLACE INTO poll VALUES (?,?,?,?,?)',
                   (self.grant.issue, etag, self.clock() + delay, failures, digest(asdict(self.grant))))
        db.commit()

    def _backoff(self, db, response, etag, failures):
        delay = min(3600, 60 * 2 ** min(failures + 1, 6))
        try:
            retry = response.headers.get('retry-after', '0')
            reset = response.headers.get('x-ratelimit-reset', '0')
            delay = max(delay, min(86400, max(int(retry), int(reset) - self.clock())))
        except (ValueError, TypeError, OverflowError):
            delay = max(delay, 3600)
        self._schedule(db, etag, delay, failures + 1)
