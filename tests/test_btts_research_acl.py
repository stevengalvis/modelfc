"""BTTS research publication uses the existing narrow API-reader ACL boundary."""

import ctypes
import ctypes.util
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc.btts_research import (
    get_or_freeze_btts_forecast, read_btts_research, record_btts_research,
)
from modelfc.ledger_storage import LedgerStorageUnavailable
from tests.test_btts_research import fixture, forecast, observation

ROOT = Path(__file__).resolve().parents[1]


def cross_uid_available():
    try:
        return all(any(int(start) <= 65534 < int(start) + int(length)
                       for start, _, length in (line.split() for line in Path(path).read_text().splitlines()))
                   for path in ("/proc/self/uid_map", "/proc/self/gid_map"))
    except OSError:
        return False


class BttsResearchAclTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.state.mkdir(mode=0o700)
        self.directory = self.state / "btts-research"
        self.api_uid = 10001
        self.commands = []

    def acl(self, command, **kwargs):
        descriptor = kwargs["pass_fds"][0]
        self.commands.append((command, os.fstat(descriptor).st_nlink,
                              Path(os.readlink(command[-1]))))

    def publication(self):
        return (patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}),
                patch("modelfc.ledger_storage.pwd.getpwnam",
                      return_value=type("User", (), {"pw_uid": self.api_uid})()),
                patch("modelfc.ledger_storage.subprocess.run", side_effect=self.acl))

    def test_new_namespace_forecast_and_snapshot_acl_before_atomic_publication(self):
        environment, account, command = self.publication()
        with environment, account, command:
            frozen, created = get_or_freeze_btts_forecast(
                self.state, fixture(), lambda: forecast())
            self.assertTrue(created)
            result = record_btts_research(self.state, frozen, observation())

        forecast_path = next(self.directory.glob("forecast-*.json"))
        snapshot_path = self.directory / f"{result.record_id}.json"
        self.assertTrue(forecast_path.is_file())
        self.assertTrue(snapshot_path.is_file())
        self.assertTrue(stat.S_ISREG((self.directory / ".lock").stat().st_mode))
        self.assertEqual(stat.S_IMODE(forecast_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(snapshot_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.directory / ".lock").stat().st_mode), 0o600)
        entries = [call[0][2] for call in self.commands]
        self.assertEqual(entries, [f"u:{self.api_uid}:--x", f"u:{self.api_uid}:r-x",
                                   f"u:{self.api_uid}:r--", f"u:{self.api_uid}:r--"] * 2)
        self.assertFalse(any("w" in entry for entry in entries))
        # Every file ACL targets an unlinked inode and every lock ACL targets
        # the already-held BTTS lock, never a replaceable caller path.
        for invocation, links, resolved in self.commands:
            target = invocation[-1]
            self.assertTrue(target.startswith("/proc/self/fd/"))
            if invocation[2].endswith("r--"):
                self.assertIn(links, (0, 1))
                if links == 1:
                    self.assertEqual(resolved, self.directory / ".lock")

    def test_idempotent_replay_keeps_inode_bytes_and_shared_read_lock(self):
        environment, account, command = self.publication()
        with environment, account, command:
            frozen, _ = get_or_freeze_btts_forecast(self.state, fixture(), lambda: forecast())
            first = record_btts_research(self.state, frozen, observation())
            path = self.directory / f"{first.record_id}.json"
            original = path.stat().st_ino, path.read_bytes()
            call_count = len(self.commands)
            self.assertEqual(record_btts_research(self.state, frozen, observation()), first)
        self.assertEqual((path.stat().st_ino, path.read_bytes()), original)
        self.assertEqual(len(self.commands), call_count)
        self.assertEqual(len(read_btts_research(self.state)), 2)

    def test_missing_acl_opt_in_never_broadens_private_publication(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("modelfc.ledger_storage.subprocess.run") as command:
            frozen, _ = get_or_freeze_btts_forecast(self.state, fixture(), lambda: forecast())
            result = record_btts_research(self.state, frozen, observation())
        command.assert_not_called()
        self.assertEqual(stat.S_IMODE((self.directory / ".lock").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(
            (self.directory / f"{result.record_id}.json").stat().st_mode), 0o600)

    def test_record_and_lock_acl_metadata_are_synced_before_publication(self):
        synced = []
        original_fsync = os.fsync
        def fsync(descriptor):
            info = os.fstat(descriptor)
            synced.append((stat.S_IFMT(info.st_mode), info.st_nlink,
                           Path(os.readlink(f"/proc/self/fd/{descriptor}"))))
            return original_fsync(descriptor)

        environment, account, command = self.publication()
        with environment, account, command, \
                patch("modelfc.ledger_storage.os.fsync", side_effect=fsync):
            get_or_freeze_btts_forecast(self.state, fixture(), lambda: forecast())
        self.assertEqual(synced[0][1], 0)  # serialized unnamed inode
        self.assertEqual(synced[1][1:], (1, self.directory / ".lock"))
        self.assertEqual(synced[2][1], 0)  # same unnamed inode after its ACL
        self.assertTrue(stat.S_ISDIR(synced[3][0]))
        self.assertTrue(stat.S_ISDIR(synced[4][0]))

    def test_wrong_held_lock_and_acl_failure_never_publish(self):
        from modelfc.ledger_storage import write_new_record

        self.directory.mkdir(mode=0o700)
        lock = self.directory / ".lock"
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        wrong_lock = self.state / ".lock"
        wrong_descriptor = os.open(wrong_lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        target = self.directory / f"{'a' * 64}.json"
        try:
            with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                    patch("modelfc.ledger_storage.pwd.getpwnam",
                          return_value=type("User", (), {"pw_uid": self.api_uid})()), \
                    patch("modelfc.ledger_storage.subprocess.run"):
                with self.assertRaisesRegex(LedgerStorageUnavailable, "record not published"):
                    write_new_record(target, {"value": 1}, evidence_state=self.state,
                                     evidence_lock_fd=wrong_descriptor)
            self.assertFalse(target.exists())
            with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                    patch("modelfc.ledger_storage.pwd.getpwnam",
                          return_value=type("User", (), {"pw_uid": self.api_uid})()), \
                    patch("modelfc.ledger_storage.subprocess.run",
                          side_effect=OSError("private ACL detail")):
                with self.assertRaisesRegex(LedgerStorageUnavailable, "record not published") as caught:
                    write_new_record(target, {"value": 1}, evidence_state=self.state,
                                     evidence_lock_fd=descriptor)
            self.assertNotIn("private ACL detail", str(caught.exception))
            self.assertFalse(target.exists())
        finally:
            os.close(descriptor)
            os.close(wrong_descriptor)

    def test_malformed_lock_inode_blocks_acl_and_publication(self):
        self.directory.mkdir(mode=0o700)
        private = self.state / "private.json"
        private.write_text("private")
        os.link(private, self.directory / ".lock")
        environment, account, command = self.publication()
        with environment, account, command:
            with self.assertRaisesRegex(LedgerStorageUnavailable, "storage unavailable"):
                record_btts_research(self.state, forecast(), observation())
        self.assertEqual(list(self.directory.glob("*.json")), [])
        self.assertEqual(self.commands, [])

    @unittest.skipUnless(os.geteuid() == 0 and ctypes.util.find_library("acl")
                         and cross_uid_available(),
                         "cross-UID ACL acceptance requires mapped UIDs, root and libacl")
    def test_real_api_uid_can_read_and_share_lock_but_cannot_write(self):
        # TemporaryDirectory is 0700; allow traversal only so the dropped UID
        # reaches the production-like state ACL without listing the parent.
        self.state.parent.chmod(0o711)
        acl = ctypes.CDLL(ctypes.util.find_library("acl"), use_errno=True)
        acl.acl_from_text.argtypes = [ctypes.c_char_p]
        acl.acl_from_text.restype = ctypes.c_void_p
        acl.acl_set_fd.argtypes = [ctypes.c_int, ctypes.c_void_p]
        acl.acl_set_fd.restype = ctypes.c_int
        acl.acl_free.argtypes = [ctypes.c_void_p]

        def set_acl(command, **kwargs):
            permission = command[2].rsplit(":", 1)[1]
            owner = "rwx" if stat.S_ISDIR(os.fstat(kwargs["pass_fds"][0]).st_mode) else "rw-"
            value = acl.acl_from_text(
                f"u::{owner},u:65534:{permission},g::---,m::{permission},o::---".encode())
            self.assertTrue(value)
            try:
                self.assertEqual(acl.acl_set_fd(kwargs["pass_fds"][0], value), 0,
                                 os.strerror(ctypes.get_errno()))
            finally:
                acl.acl_free(value)

        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam",
                      return_value=type("User", (), {"pw_uid": 65534})()), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=set_acl):
            frozen, _ = get_or_freeze_btts_forecast(self.state, fixture(), lambda: forecast())
            record_btts_research(self.state, frozen, observation())

        code = r'''import fcntl,json,os,sys
state=sys.argv[1]; directory=state+'/btts-research'; result={}
try: result['state_list']=os.listdir(state); result['state_listed']=True
except PermissionError: result['state_listed']=False
result['records']=len([name for name in os.listdir(directory) if name.endswith('.json')])
lock=os.open(directory+'/.lock',os.O_RDONLY|os.O_NOFOLLOW)
fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB); result['shared_lock']=True
path=next(directory+'/'+name for name in os.listdir(directory) if name.endswith('.json'))
with open(path,'rb') as stream: result['read']=bool(stream.read())
try:
 with open(path,'ab') as stream: stream.write(b'x')
 result['write']=True
except PermissionError: result['write']=False
try:
 with open(directory+'/created.json','wb') as stream: stream.write(b'x')
 result['create']=True
except PermissionError: result['create']=False
print(json.dumps(result,sort_keys=True))'''
        result = subprocess.run(["/usr/bin/python3", "-I", "-B", "-c", code, str(self.state)],
                                user=65534, group=65534, extra_groups=[], check=True,
                                capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), {
            "create": False, "read": True, "records": 2, "shared_lock": True,
            "state_listed": False, "write": False,
        })

    def test_activation_contract_keeps_api_read_only_and_caddy_closed(self):
        procedure = (ROOT / "ops/vps/BTTS_RESEARCH_API.md").read_text()
        service = (ROOT / "deploy/modelfc-corner-api.service").read_text()
        caddy = (ROOT / "deploy/modelfc-api.Caddyfile").read_text()
        for required in ("modelfc-runtime", "modelfc-api", "MODELFC_EVIDENCE_ACL_USER",
                         "forecast-<64 lowercase hex>.json", "Do not use `-R`",
                         "Caddy still denies the route", "next normal scheduled run",
                         "btts_research_bootstrap.py", "--clear-groups", "O_NOFOLLOW",
                         "never invokes `setfacl`", "Do not rerun bootstrap after migration"):
            self.assertIn(required, procedure)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ReadOnlyPaths=/srv/modelfc /etc/modelfc /var/lib/modelfc/state", service)
        self.assertNotIn("ReadWritePaths=/var/lib/modelfc/state", service)
        self.assertNotIn("/api/v1/research/btts", caddy)


if __name__ == "__main__":
    unittest.main()
