"""Root-protected v1 approval record and durable nonce claim.

A trusted root operator may issue only after explicit owner approval. This
module does not authenticate a reference string or grant automatic approval.
The operational root is deliberately not configured in this offline package.
"""
import hashlib
import json
import os
import stat
import time
canonical = decode = need = validate = None

def configure(contract):
    global canonical, decode, need, validate
    canonical, decode, need, validate = (contract.canonical, contract.decode,
                                     contract.need, contract.validate)

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOATIME
NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC

class RootStore:
    def __init__(self, root, *, owner_uid=0, owner_gid=0,
                 clock_ns=time.time_ns):
        self.root = root
        self.uid = owner_uid
        self.gid = owner_gid
        self.clock_ns = clock_ns

    def _now(self):
        value = self.clock_ns()
        need(type(value) is int and value >= 0, 'BINDING_CLOCK')
        return value // 1_000_000_000

    def _open_dir(self, path):
        need(type(path) is str and path.startswith('/') and
             all(p not in ('', '.', '..') for p in path.split('/')[1:]),
             'BINDING_PATH')
        fd = os.open('/', DIR)
        try:
            for part in path.split('/')[1:]:
                nxt = os.open(part, DIR, dir_fd=fd)
                os.close(fd)
                fd = nxt
                st = os.fstat(fd)
                need(stat.S_ISDIR(st.st_mode) and
                     st.st_uid in (0,self.uid) and
                     st.st_gid in (0,self.gid) and
                     not stat.S_IMODE(st.st_mode) & 0o022,
                     'BINDING_ANCESTOR')
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _root(self):
        fd = self._open_dir(self.root)
        st = os.fstat(fd)
        try:
            named = os.stat(self.root, follow_symlinks=False)
            need((st.st_dev,st.st_ino,st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)) ==
                 (named.st_dev,named.st_ino,named.st_uid,named.st_gid,
                  stat.S_IMODE(named.st_mode)) and
                 (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)) ==
                 (self.uid,self.gid,0o700), 'BINDING_ROOT_META')
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _protected_child(self, rootfd, name):
        fd = os.open(name, DIR, dir_fd=rootfd)
        try:
            st = os.fstat(fd)
            named = os.stat(name, dir_fd=rootfd, follow_symlinks=False)
            key = lambda x:(x.st_dev,x.st_ino,x.st_uid,x.st_gid,
                            stat.S_IMODE(x.st_mode))
            need(stat.S_ISDIR(st.st_mode) and key(st)==key(named) and
                 (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)) ==
                 (self.uid,self.gid,0o700), 'BINDING_CHILD_META')
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _claims(self, rootfd):
        return self._protected_child(rootfd,'claims')

    def _claims_history(self, rootfd):
        claims=self._claims(rootfd)
        try:
            history=self._protected_child(rootfd,'history')
        except BaseException:
            os.close(claims)
            raise
        return claims,history

    def provision(self):
        """Exclusively create only new protected objects; never repair old."""
        need((os.geteuid(),os.getegid())==(self.uid,self.gid),
             'BINDING_ISSUER_UID')
        parent_path,name=os.path.split(self.root)
        need(parent_path and name not in ('','.','..'), 'BINDING_PATH')
        parent=self._open_dir(parent_path)
        try:
            os.mkdir(name,0o700,dir_fd=parent)
            rootfd=os.open(name,DIR,dir_fd=parent)
            try:
                os.fchmod(rootfd,0o700)
                for child in ('claims','history'):
                    os.mkdir(child,0o700,dir_fd=rootfd)
                    fd=os.open(child,DIR,dir_fd=rootfd)
                    try:
                        os.fchmod(fd,0o700)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                os.fsync(rootfd)
            finally:
                os.close(rootfd)
            os.fsync(parent)
        finally:
            os.close(parent)
        rootfd=self._root()
        try:
            for child in ('claims','history'):
                fd=self._protected_child(rootfd,child)
                try:
                    need(not os.listdir(fd),'BINDING_CHILD_NOT_EMPTY')
                finally:
                    os.close(fd)
        finally:
            os.close(rootfd)
        return True

    def read_current(self):
        rootfd=self._root()
        try:
            fd=os.open('current.json',READ,dir_fd=rootfd)
            try:
                before=os.fstat(fd)
                named=os.stat('current.json',dir_fd=rootfd,follow_symlinks=False)
                key=lambda x:(x.st_dev,x.st_ino,x.st_uid,x.st_gid,x.st_mode,
                              x.st_size,x.st_nlink,x.st_mtime_ns,x.st_ctime_ns)
                need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and
                     (before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode))==
                     (self.uid,self.gid,0o600) and before.st_size<=4096 and
                     key(before)==key(named),'BINDING_FILE_META')
                raw=os.read(fd,4097)
                after=os.fstat(fd)
                named=os.stat('current.json',dir_fd=rootfd,follow_symlinks=False)
                need(len(raw)==before.st_size and key(before)==key(after)==key(named),
                     'BINDING_FILE_CHANGED')
                return raw
            finally:
                os.close(fd)
        finally:
            os.close(rootfd)

    def issue(self, record, measured, owner_approval_ref):
        """Trusted root operator only; approval reference is audit data."""
        need((os.geteuid(),os.getegid())==(self.uid,self.gid),'BINDING_ISSUER_UID')
        raw=canonical(record)
        item=validate(raw,measured,self._now())
        need(type(owner_approval_ref) is str and
             item['owner_approval_ref']==owner_approval_ref,
             'BINDING_REFERENCE')
        rootfd=self._root()
        try:
            claimfd=self._claims(rootfd)
            try:
                try:
                    os.stat(item['nonce'],dir_fd=claimfd,follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    need(False,'BINDING_REPLAY')
            finally:
                os.close(claimfd)
            history=self._protected_child(rootfd,'history')
            os.close(history)
            fd=os.open('current.json',NEW,0o600,dir_fd=rootfd)
            try:
                need(os.write(fd,raw)==len(raw),'BINDING_WRITE')
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(rootfd)
            need(self.read_current() == raw, 'BINDING_FILE_CHANGED')
            return hashlib.sha256(raw).hexdigest()
        finally:
            os.close(rootfd)

    def claim_once(self, nonce, attempt, expected_sha):
        """Preserve a partial claim on failure; only fsynced success returns True."""
        current=self.read_current()
        item=decode(current,self._now())
        need(item['nonce']==nonce and item['attempt_id']==attempt and
             hashlib.sha256(current).hexdigest()==expected_sha,
             'BINDING_CLAIM_CHANGED')
        rootfd=self._root()
        try:
            claimfd=self._claims(rootfd)
            try:
                payload=canonical({'nonce':nonce,'attempt_id':attempt,
                                   'current_sha256':hashlib.sha256(current).hexdigest()})
                fd=os.open(nonce,NEW,0o600,dir_fd=claimfd)
                try:
                    need(os.write(fd,payload)==len(payload),'BINDING_CLAIM_WRITE')
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(claimfd)
                return True
            finally:
                os.close(claimfd)
        finally:
            os.close(rootfd)

    def retire_current(self, nonce, attempt, expected_sha, terminal_ref,
                       cleanup_state):
        """Trusted root operator only, after terminal and empty-cleanup review.

        Link the current approval into protected history exclusively before
        unlinking it. A crash between operations leaves current nlink != 1
        or history collision: both block another issue until manual review.
        """
        need((os.geteuid(),os.getegid())==(self.uid,self.gid),
             'BINDING_ISSUER_UID')
        need(cleanup_state == 'VERIFIED_EMPTY' and
             type(terminal_ref) is str and 1 <= len(terminal_ref) <= 256 and
             all(0x21 <= ord(ch) <= 0x7e for ch in terminal_ref),
             'BINDING_TERMINAL')
        raw = self.read_current()
        need(hashlib.sha256(raw).hexdigest() == expected_sha,
             'BINDING_RETIRE_CHANGED')
        item = json.loads(raw)
        need(item['nonce'] == nonce and item['attempt_id'] == attempt,
             'BINDING_RETIRE_CHANGED')
        rootfd=self._root()
        try:
            claims,history=self._claims_history(rootfd)
            try:
                claimfd=os.open(nonce, READ, dir_fd=claims)
                try:
                    st=os.fstat(claimfd)
                    named=os.stat(nonce,dir_fd=claims,follow_symlinks=False)
                    need(stat.S_ISREG(st.st_mode) and st.st_nlink==1 and
                         (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))==
                         (self.uid,self.gid,0o600) and
                         (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,
                          st.st_ctime_ns)==
                         (named.st_dev,named.st_ino,named.st_size,
                          named.st_mtime_ns,named.st_ctime_ns) and
                         st.st_size<=1024,'BINDING_CLAIM_META')
                    expected=canonical({'nonce':nonce,'attempt_id':attempt,
                                        'current_sha256':expected_sha})
                    need(os.read(claimfd,1025)==expected,
                         'BINDING_CLAIM_CHANGED')
                finally:
                    os.close(claimfd)
                saved=nonce+'.approval.json'
                os.link('current.json',saved,src_dir_fd=rootfd,
                        dst_dir_fd=history,follow_symlinks=False)
                os.fsync(history)
                terminal=canonical({'nonce':nonce,'attempt_id':attempt,
                    'approval_sha256':expected_sha,'terminal_ref':terminal_ref,
                    'cleanup_state':cleanup_state})
                fd=os.open(nonce+'.terminal.json',NEW,0o600,dir_fd=history)
                try:
                    need(os.write(fd,terminal)==len(terminal),
                         'BINDING_TERMINAL_WRITE')
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(history)
                os.unlink('current.json',dir_fd=rootfd)
                os.fsync(rootfd)
                return True
            finally:
                os.close(claims)
                os.close(history)
        finally:
            os.close(rootfd)

    def inspect_unclaimed(self, nonce, attempt, expected_sha, observer):
        """Read-only pre-retirement inspection; never reserves or retires."""
        need((os.geteuid(),os.getegid())==(self.uid,self.gid),
             'BINDING_ISSUER_UID')
        need(callable(observer), 'BINDING_NO_LAUNCH_UNVERIFIED')
        raw=self.read_current()
        need(hashlib.sha256(raw).hexdigest()==expected_sha,
             'BINDING_RETIRE_CHANGED')
        try:
            provisional=json.loads(raw)
        except (ValueError,UnicodeError):
            need(False,'BINDING_JSON')
        need(type(provisional) is dict and
             type(provisional.get('issued_at')) is int,
             'BINDING_SCHEMA')
        item=decode(raw,provisional['issued_at'])
        need(item['issued_at']<=self._now() and item['nonce']==nonce and
             item['attempt_id']==attempt, 'BINDING_RETIRE_CHANGED')
        def no_prior_claim_or_history():
            rootfd=self._root()
            try:
                claims,history=self._claims_history(rootfd)
                try:
                    for fd,name in ((claims,nonce),
                                    (history,nonce+'.approval.json'),
                                    (history,nonce+'.terminal.json')):
                        try:
                            os.stat(name,dir_fd=fd,follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        need(False,'BINDING_REPLAY')
                finally:
                    os.close(claims)
                    os.close(history)
            finally:
                os.close(rootfd)
        no_prior_claim_or_history()
        need(observer(item) is True,'BINDING_NO_LAUNCH_UNVERIFIED')
        need(self.read_current()==raw,'BINDING_RETIRE_CHANGED')
        no_prior_claim_or_history()
        return True

    def retire_unclaimed(self, nonce, attempt, expected_sha, terminal_ref,
                         cleanup_state, observer):
        """Retire a no-claim stop only after independent trusted observations.

        The exclusive, fsynced claim-name tombstone is the linearization point:
        it races safely against claim_once and permanently blocks nonce reuse.
        Any later failure leaves the current approval or partial history held.
        """
        need((os.geteuid(),os.getegid())==(self.uid,self.gid),
             'BINDING_ISSUER_UID')
        need(cleanup_state == 'VERIFIED_EMPTY' and
             type(terminal_ref) is str and 1 <= len(terminal_ref) <= 256 and
             all(0x21 <= ord(ch) <= 0x7e for ch in terminal_ref) and
             callable(observer), 'BINDING_TERMINAL')
        raw=self.read_current()
        need(hashlib.sha256(raw).hexdigest()==expected_sha,
             'BINDING_RETIRE_CHANGED')
        try:
            provisional=json.loads(raw)
        except (ValueError,UnicodeError):
            need(False,'BINDING_JSON')
        need(type(provisional) is dict and
             type(provisional.get('issued_at')) is int,
             'BINDING_SCHEMA')
        # Retirement may occur after expiry, but the original approval must
        # still be canonical and intrinsically valid at its issuance instant.
        item=decode(raw,provisional['issued_at'])
        need(item['issued_at']<=self._now(), 'BINDING_CLOCK')
        need(item['nonce']==nonce and item['attempt_id']==attempt,
             'BINDING_RETIRE_CHANGED')
        need(observer(item) is True, 'BINDING_NO_LAUNCH_UNVERIFIED')
        rootfd=self._root()
        try:
            claims,history=self._claims_history(rootfd)
            try:
                payload=canonical({'nonce':nonce,'attempt_id':attempt,
                                   'current_sha256':expected_sha,
                                   'status':'UNCLAIMED_TERMINAL'})
                fd=os.open(nonce,NEW,0o600,dir_fd=claims)
                try:
                    need(os.write(fd,payload)==len(payload),
                         'BINDING_TOMBSTONE_WRITE')
                    os.fsync(fd)
                    st=os.fstat(fd)
                    named=os.stat(nonce,dir_fd=claims,follow_symlinks=False)
                    need(stat.S_ISREG(st.st_mode) and st.st_nlink==1 and
                         (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))==
                         (self.uid,self.gid,0o600) and
                         (st.st_dev,st.st_ino)==(named.st_dev,named.st_ino),
                         'BINDING_TOMBSTONE_META')
                finally:
                    os.close(fd)
                # The name must survive power loss before current may go away.
                os.fsync(claims)
                need(observer(item) is True,
                     'BINDING_NO_LAUNCH_UNVERIFIED')
                need(self.read_current()==raw,
                     'BINDING_RETIRE_CHANGED')
                os.link('current.json',nonce+'.approval.json',
                        src_dir_fd=rootfd,dst_dir_fd=history,
                        follow_symlinks=False)
                os.fsync(history)
                terminal=canonical({'nonce':nonce,'attempt_id':attempt,
                    'approval_sha256':expected_sha,'terminal_ref':terminal_ref,
                    'cleanup_state':cleanup_state,
                    'retirement_kind':'UNCLAIMED'})
                fd=os.open(nonce+'.terminal.json',NEW,0o600,dir_fd=history)
                try:
                    need(os.write(fd,terminal)==len(terminal),
                         'BINDING_TERMINAL_WRITE')
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(history)
                os.unlink('current.json',dir_fd=rootfd)
                os.fsync(rootfd)
                return True
            finally:
                os.close(claims)
                os.close(history)
        finally:
            os.close(rootfd)
