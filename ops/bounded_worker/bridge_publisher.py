"""Inactive publisher orchestrator in a separate trusted credential-bearing process.

The caller provisions a protected receipt directory and supplies trusted checkout,
launcher inspection and GitHub adapters. A pending external write is never repeated.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3

from .bridge_envelope import validate
from .bridge_patch import validate_and_apply


class Publisher:
    def __init__(self, state_dir, policy, reader, inspector, checkout, github, *, clock):
        self.state_dir = Path(state_dir)
        self.policy, self.reader, self.inspector = policy, reader, inspector
        self.checkout, self.github, self.clock = checkout, github, clock

    def publish(self, job_id):
        fd = os.open(self.state_dir / 'bridge.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy'}
            with sqlite3.connect(self.state_dir / 'receipts.sqlite') as db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('CREATE TABLE IF NOT EXISTS publications '
                           '(job TEXT PRIMARY KEY, phase TEXT NOT NULL, branch TEXT NOT NULL, '
                           'patch TEXT NOT NULL, commit_id TEXT, url TEXT)')
                db.commit()
                return self._publish(db, job_id)
        except Exception:
            return {'status': 'blocked'}
        finally:
            os.close(fd)

    def _publish(self, db, job_id):
        row = db.execute('SELECT digest,comment,node,status,body FROM jobs WHERE job=?', (job_id,)).fetchone()
        if not row or row[3] != 'completed':
            return {'status': 'blocked'}
        digest, comment_id, node, _, body = row
        fresh = [entry for entry in self.reader.comments() if entry[0] == node]
        if len(fresh) != 1 or fresh[0][1] != body or fresh[0][2]['comment_id'] != comment_id:
            return {'status': 'blocked'}
        task, check_digest = validate(body, fresh[0][2], self.policy, int(self.clock()))
        if task.task_id != job_id or digest != check_digest:
            return {'status': 'blocked'}
        inspected = self.inspector.inspect(task)
        if (type(inspected) is not dict or inspected.get('task_id') != job_id
                or inspected.get('status') != 'completed'):
            return {'status': 'blocked'}
        patch_digest = inspected.get('diff_sha256')
        branch = 'bounded/' + hashlib.sha256(job_id.encode()).hexdigest()[:32]
        publication = db.execute('SELECT phase,branch,patch,commit_id,url FROM publications WHERE job=?', (job_id,)).fetchone()
        if publication:
            phase, old_branch, old_patch, commit_id, url = publication
            if old_branch != branch or old_patch != patch_digest:
                return {'status': 'blocked'}
            # Only an already-observed, verified draft is reportable on restart.
            # In-flight or ambiguous writes require operator investigation.
            if phase == 'published' and self.github.verify_draft(branch, task.base_revision, commit_id, url):
                return {'status': 'published', 'job_id': job_id, 'patch_sha256': patch_digest, 'url': url}
            if phase == 'pr_pending' and commit_id:
                # Read-only reconciliation: independently reproduce and verify the
                # commit and a single matching draft before marking it published.
                patch = self.inspector.patch(task, patch_digest)
                work = self.checkout.prepare(task)
                contents = validate_and_apply(work, task, patch, patch_digest)
                url = self.github.find_draft(branch)
                if (url and self.github.verify_commit(branch, task.base_revision, commit_id, contents)
                        and self.github.verify_draft(branch, task.base_revision, commit_id, url)):
                    db.execute('UPDATE publications SET phase=?,url=? WHERE job=?', ('published', url, job_id))
                    db.commit()
                    return {'status': 'published', 'job_id': job_id, 'patch_sha256': patch_digest, 'url': url}
            return {'status': 'blocked'}
        if self.github.main_head() != task.base_revision:
            return {'status': 'blocked'}
        patch = self.inspector.patch(task, patch_digest)
        work = self.checkout.prepare(task)
        contents = validate_and_apply(work, task, patch, patch_digest)
        # Recheck the original command and base immediately before external writes.
        if self.github.main_head() != task.base_revision or self.reader.comments().count(fresh[0]) != 1:
            return {'status': 'blocked'}
        final_task, final_digest = validate(fresh[0][1], fresh[0][2], self.policy, int(self.clock()))
        if final_task != task or final_digest != digest:
            return {'status': 'blocked'}
        db.execute('INSERT INTO publications VALUES (?,?,?,?,?,?)',
                   (job_id, 'branch_pending', branch, patch_digest, None, None))
        db.commit()
        self.github.create_branch(branch, task.base_revision)
        db.execute('UPDATE publications SET phase=? WHERE job=?', ('commit_pending', job_id))
        db.commit()
        commit_id = self.github.create_commit(branch, task.base_revision, contents, job_id, patch_digest)
        if not self.github.verify_commit(branch, task.base_revision, commit_id, contents):
            return {'status': 'blocked'}
        latest = [entry for entry in self.reader.comments() if entry[0] == node]
        if len(latest) != 1 or latest[0] != fresh[0] or self.github.main_head() != task.base_revision:
            return {'status': 'blocked'}
        latest_task, latest_digest = validate(latest[0][1], latest[0][2], self.policy, int(self.clock()))
        if latest_task != task or latest_digest != digest:
            return {'status': 'blocked'}
        db.execute('UPDATE publications SET phase=?,commit_id=? WHERE job=?', ('pr_pending', commit_id, job_id))
        db.commit()
        url = self.github.create_draft(branch, job_id, commit_id, patch_digest)
        if not self.github.verify_draft(branch, task.base_revision, commit_id, url):
            return {'status': 'blocked'}
        db.execute('UPDATE publications SET phase=?,url=? WHERE job=?', ('published', url, job_id))
        db.commit()
        return {'status': 'published', 'job_id': job_id, 'patch_sha256': patch_digest, 'url': url}
