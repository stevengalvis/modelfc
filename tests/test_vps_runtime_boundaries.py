"""Regressions for the two real systemd 255 Phase 5 failures; no network calls."""

from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_vps_deploy import deploy


EVIDENCE = json.loads((Path(__file__).parent / "fixtures/deployment-systemd255-runtime.json").read_text())


class TestNetworkProof(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.request, self.output = root / "request", root / "output"
        self.sha = "a" * 40
        self.release = root / (self.sha + "-" + "b" * 12)
        self.release.mkdir()
        self.value = {"release_id": self.release.name, "sha": self.sha, "controller_netns": [4, 100]}
        self.fields = {"PrivateNetwork": "yes", "NetworkNamespacePath": "",
                       "JoinsNamespaceOf": "", "MainPID": str(os.getpid())}

    def run_worker(self, *, namespace=200, fields=None, interfaces=None, stat_error=None, bus_error=None, interface_error=None):
        self.request.write_text(json.dumps(self.value))
        self.reads = []
        real_stat = os.stat
        def proc_stat(path, *args, **kwargs):
            if str(path).startswith("/proc/"):
                self.reads.append(str(path))
                if str(path) == "/proc/1/ns/net":
                    raise PermissionError(errno.EACCES, "Permission denied")
                if str(path) == "/proc/self/ns/net":
                    if stat_error:
                        raise stat_error
                    return SimpleNamespace(st_dev=4, st_ino=namespace)
                raise AssertionError(path)
            return real_stat(path, *args, **kwargs)
        text = "\n".join(k + "=" + v for k, v in (self.fields if fields is None else fields).items())
        with ExitStack() as stack:
            for name, value in (("REQUEST", self.request), ("TEST_OUTPUT", self.output),
                                ("RELEASES", self.release.parent)):
                stack.enter_context(patch.object(deploy, name, value))
            stack.enter_context(patch.object(deploy.os, "stat", side_effect=proc_stat))
            stack.enter_context(patch.object(deploy.os, "access", return_value=False))
            stack.enter_context(patch.object(deploy.socket, "if_nameindex",
                                            return_value=[(1, "lo")] if interfaces is None else interfaces,
                                            side_effect=interface_error))
            bus = stack.enter_context(patch.object(deploy, "command", return_value=text, side_effect=bus_error))
            head = stack.enter_context(patch.object(deploy, "head", return_value=self.sha))
            candidate = stack.enter_context(patch.object(deploy, "capture_test_process", return_value=
                (0, b"Ran 1 test in 0.01s\n\nOK\n", False)))
            result = deploy.run_tests()
        return result, candidate, head, bus

    def test_intended_private_namespace_passes_without_pid1_access(self):
        self.assertEqual(EVIDENCE["namespace_access"][1]["errno"], errno.EACCES)
        self.assertNotEqual(EVIDENCE["test_network_namespace"], EVIDENCE["host_network_namespace"])
        result, candidate, head, bus = self.run_worker()
        self.assertTrue(result)
        candidate.assert_called_once()
        self.assertEqual(self.reads, ["/proc/self/ns/net"])
        self.assertIn("PrivateNetwork", bus.call_args.args[0])
        self.assertEqual(bus.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C"})
        self.assertTrue(json.loads(self.output.read_text())["state_boundary_enforced"])

    def test_same_namespace_or_unreadable_self_prevents_all_candidate_execution(self):
        for kwargs in ({"namespace": 100}, {"stat_error": PermissionError(errno.EACCES, "denied")},
                       {"stat_error": FileNotFoundError()}, {"bus_error": deploy.Failure("INTERNAL_ERROR")},
                       {"interface_error": OSError()}):
            with self.subTest(kwargs=kwargs):
                result, candidate, head, _ = self.run_worker(**kwargs)
                self.assertFalse(result)
                candidate.assert_not_called()
                head.assert_not_called()

    def test_disabled_mismatched_or_reused_unit_namespace_fails(self):
        for name, values in {"PrivateNetwork": [None, "no", ""], "MainPID": [None, "0", "999999"],
                             "NetworkNamespacePath": [None, "/run/netns/external"],
                             "JoinsNamespaceOf": [None, "other.service"]}.items():
            for value in values:
                fields = self.fields.copy()
                if value is None:
                    del fields[name]
                else:
                    fields[name] = value
                with self.subTest(name=name, value=value):
                    result, candidate, head, _ = self.run_worker(fields=fields)
                    self.assertFalse(result)
                    candidate.assert_not_called()
                    head.assert_not_called()

    def test_non_loopback_interfaces_and_unverifiable_inventory_fail(self):
        for interfaces in ([], [(1, "lo"), (2, "eth0")], [(2, "eth0")]):
            with self.subTest(interfaces=interfaces):
                result, candidate, _, _ = self.run_worker(interfaces=interfaces)
                self.assertFalse(result)
                candidate.assert_not_called()

    def test_missing_malformed_controller_reference_fails(self):
        for value in (None, [], [4], [4, 100, 1], [False, 100], [4, "100"], [4, 0], [-1, 100]):
            self.value["controller_netns"] = value
            with self.subTest(value=value):
                result, candidate, _, _ = self.run_worker()
                self.assertFalse(result)
                candidate.assert_not_called()
        del self.value["controller_netns"]
        result, candidate, _, _ = self.run_worker()
        self.assertFalse(result)
        candidate.assert_not_called()

    def test_controller_records_own_live_namespace_before_start(self):
        def service(unit):
            self.assertEqual(json.loads(self.request.read_text())["controller_netns"], [4, 123])
            self.output.write_text(json.dumps({"sha": self.sha, "release_id": self.release.name,
                "tests_status": "PASS", "tests_run": 1, "state_boundary_enforced": True}))
        with patch.object(deploy, "network_namespace", return_value=[4, 123]), patch.object(
                deploy, "run_service", side_effect=service):
            self.assertEqual(deploy.tests(self.release, self.sha, request=self.request, output=self.output), 1)
        with patch.object(deploy.os, "stat", side_effect=PermissionError()), patch.object(
                deploy, "run_service") as start:
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.tests(self.release, self.sha, request=self.request, output=self.output)
            start.assert_not_called()


class DependencyRuntimeTmpfs(unittest.TestCase):
    mounts = ("101 1 0:47 / /tmp rw,nosuid,nodev - tmpfs tmpfs rw,size=262144k,nr_inodes=16384\n"
              "102 1 0:48 / /var/tmp rw,nosuid,nodev - tmpfs tmpfs rw,size=262144k,nr_inodes=16384\n")

    def verify(self, *, text=None, blocks=65536, inodes=16384, flags=0, error=None, devices=(47, 48), mode=0o41777, uid=0):
        with patch.object(Path, "read_text", return_value=self.mounts if text is None else text), patch.object(
                deploy.os, "open", side_effect=error or [10, 11]) as opened, patch.object(
                deploy.os, "fstat", side_effect=[SimpleNamespace(st_dev=os.makedev(0, n), st_mode=mode, st_uid=uid) for n in devices]), patch.object(
                deploy.os, "fstatvfs", return_value=SimpleNamespace(f_blocks=blocks, f_frsize=4096,
                    f_files=inodes, f_flag=flags)), patch.object(deploy.os, "close") as closed:
            deploy.verify_dependency_tmpfs()
            self.assertEqual(opened.call_count, 2)
            for call in opened.call_args_list:
                self.assertTrue(call.args[1] & os.O_NOFOLLOW)
            self.assertEqual(closed.call_count, 2)

    def test_two_actual_bounded_tmpfs_mounts_pass(self):
        self.verify()

    def test_real_vps_private_directory_mounts_are_rejected(self):
        with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
            self.verify(text=EVIDENCE["dependency_mountinfo"])

    def test_missing_incorrect_unlimited_or_readonly_kernel_limits_fail(self):
        for kwargs in ({"blocks": 0}, {"blocks": 65535}, {"blocks": 131072}, {"inodes": 0},
                       {"inodes": 16383}, {"inodes": 32768}, {"flags": os.ST_RDONLY},
                       {"error": PermissionError()}, {"devices": (49, 48)}, {"mode": 0o40755}, {"uid": 1001}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                self.verify(**kwargs)

    def test_each_mount_type_missing_duplicate_bind_and_nested_mounts_fail(self):
        rows = self.mounts.splitlines(keepends=True)
        bad = ["", "malformed", rows[0], rows[1], self.mounts + rows[0],
               self.mounts.replace("0:48", "0:47"),
               self.mounts + "103 101 8:1 / /tmp/unbounded rw - ext4 /dev/sda1 rw\n"]
        for row in rows:
            bad.extend(self.mounts.replace(row, changed) for changed in (
                row.replace("tmpfs tmpfs", "ext4 /dev/sda1"),
                row.replace(" / /", " /subdir /"), row.replace("rw,nosuid", "ro,nosuid")))
        for text in bad:
            with self.subTest(text=text), self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                self.verify(text=text)

    def test_failed_runtime_verification_precedes_candidate_python_and_pip(self):
        with patch.object(deploy, "verify_dependency_tmpfs", side_effect=deploy.Failure("STATE_BOUNDARY_FAILED")), patch.object(
                deploy, "release_path") as release, patch.object(deploy, "command") as execute:
            self.assertFalse(deploy.run_dependency_install("a" * 40 + "-" + "b" * 12))
            release.assert_not_called()
            execute.assert_not_called()

    def test_unit_removes_conflicting_private_tmp_mounts_without_widening_paths(self):
        unit = (Path(__file__).parents[1] / "deploy/modelfc-postmerge-dependencies@.service").read_text()
        self.assertIn("\nPrivateTmp=no\n", unit)
        self.assertNotIn("\nPrivateTmp=yes\n", unit)
        self.assertEqual([line for line in unit.splitlines() if line.startswith("ReadWritePaths=")],
                         ["ReadWritePaths=/srv/modelfc/releases/%i/.venv"])
        for entry in deploy.DEPENDENCY_TMPFS:
            self.assertIn("TemporaryFileSystem=" + entry, unit)
