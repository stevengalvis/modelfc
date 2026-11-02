"""Publish one fixed, bounded status; uncertain posts are never repeated."""
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3


def post_once(state_dir, thread, github):
    state_dir = Path(state_dir)
    fd = os.open(state_dir / 'bridge.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        with sqlite3.connect(state_dir / 'receipts.sqlite') as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS status_posts (job TEXT, outcome TEXT, '
                       'phase TEXT NOT NULL, PRIMARY KEY(job,outcome))')
            db.commit()
            has_pub = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
            for job, digest, outcome, body in db.execute('SELECT job,digest,status,body FROM jobs ORDER BY rowid'):
                publication = (db.execute('SELECT phase,patch,url FROM publications WHERE job=?', (job,)).fetchone()
                               if has_pub else None)
                patch, url = None, None
                if publication and publication[0] == 'published':
                    outcome, patch, url = 'published', publication[1], publication[2]
                elif publication and publication[0] != 'published':
                    outcome = 'blocked'
                elif outcome == 'completed':
                    outcome = 'awaiting_publication'
                elif outcome in ('reserved', 'launching'):
                    outcome = 'blocked'
                if outcome not in ('published', 'awaiting_publication', 'failed', 'timeout', 'rejected', 'blocked'):
                    continue
                if db.execute('SELECT 1 FROM status_posts WHERE job=? AND outcome=?', (job, outcome)).fetchone():
                    continue
                obj = json.loads(body)
                if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', job)
                        or not re.fullmatch(r'[a-f0-9]{64}', digest)
                        or not re.fullmatch(r'[a-f0-9]{40}', obj['base'])
                        or (patch is not None and not re.fullmatch(r'[a-f0-9]{64}', patch))
                        or (url is not None and not re.fullmatch(r'https://github\.com/stevengalvis/modelfc/pull/[1-9][0-9]*', url))):
                    return {'status': 'blocked'}
                status = {'job_id': job, 'outcome': outcome, 'base': obj['base'], 'request_sha256': digest}
                if patch:
                    status['patch_sha256'] = patch
                    status['draft_url'] = url
                text = 'Bounded task status\n\n```json\n' + json.dumps(status, sort_keys=True, separators=(',', ':')) + '\n```'
                db.execute('INSERT INTO status_posts VALUES (?,?,?)', (job, outcome, 'pending'))
                db.commit()
                github.post_status(thread, text)
                db.execute('UPDATE status_posts SET phase=? WHERE job=? AND outcome=?', ('posted', job, outcome))
                db.commit()
                return {'status': 'posted', 'job_id': job, 'outcome': outcome}
            return {'status': 'idle'}
    except Exception:
        return {'status': 'blocked'}
    finally:
        os.close(fd)
