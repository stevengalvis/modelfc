"""Exercise only synthetic child processes; never run the real collector."""
import importlib.util
import pathlib
import subprocess
import sys
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("zen53_offline_controller",
                                               ROOT / "controller.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def child(role, mode):
    return [sys.executable, "-I", "-S", "-B", str(ROOT / "fake_child.py"),
            role, mode]


class ControllerTests(unittest.TestCase):
    def run_case(self, host="ok", restricted="ok", hold=2, start=1):
        before = time.monotonic()
        record = controller.run(child("host", host),
                                child("restricted", restricted),
                                spawn_host=subprocess.Popen,
                                spawn_restricted=subprocess.Popen,
                                startup_seconds=start, hold_seconds=hold)
        self.assertLess(time.monotonic() - before, start + hold + 1.5)
        return record

    def test_success_and_exact_bytes_and_order(self):
        record = self.run_case()
        self.assertEqual(record["outcome"], "PROTOCOL_COMPLETE")
        self.assertEqual((record["host_exit"], record["restricted_exit"]), (0, 0))
        events = record["events"]
        self.assertEqual([e["event"] for e in events],
                         ["HOST_READY", "RESTRICTED_EXIT", "FINISH_WRITTEN",
                          "HOST_ACK", "HOST_COMPLETE"])
        self.assertEqual([e["at_ns"] for e in events],
                         sorted(e["at_ns"] for e in events))
        self.assertEqual(events[2]["sha256"], controller._digest(b"finish\n"))
        self.assertEqual(events[2]["bytes"], 7)
        self.assertIn(b"HOST_FINISH_ACK", record["streams"]["host_stdout"]["raw"])

    def test_restricted_failure_never_sends_finish(self):
        record = self.run_case(restricted="fail")
        self.assertEqual(record["reason"], "RESTRICTED_FAILED")
        self.assertNotIn("FINISH_WRITTEN", [e["event"] for e in record["events"]])
        self.assertEqual(record["outcome"], "BLOCKED")

    def test_missing_late_and_eof_ack_fail(self):
        for host in ("no_ack", "late_ack", "eof", "missing_complete",
                     "early_exit"):
            with self.subTest(host=host):
                record = self.run_case(host=host, hold=1)
                self.assertEqual(record["outcome"], "BLOCKED")
                self.assertNotEqual(record["reason"], "NONE")

    def test_missing_ready_and_blocked_child_fail_bounded(self):
        self.assertEqual(self.run_case(host="no_ready")["reason"],
                         "DEADLINE_READY")
        self.assertEqual(self.run_case(restricted="hang", hold=1)["outcome"],
                         "BLOCKED")

    def test_output_limits_fail_closed_and_children_stop(self):
        for host, restricted in (("flood", "ok"), ("ok", "flood")):
            with self.subTest(host=host, restricted=restricted):
                record = self.run_case(host=host, restricted=restricted)
                self.assertEqual(record["outcome"], "BLOCKED")
                self.assertTrue(record["reason"].startswith("OUTPUT_LIMIT_"))
                self.assertIsNotNone(record["host_exit"])
                if restricted == "flood":
                    self.assertIsNotNone(record["restricted_exit"])

    def test_finish_pipe_not_writable_and_partial_write_fail(self):
        for result, reason in (([], "FINISH_PIPE_BLOCKED"),
                               ([1], "FINISH_PARTIAL_WRITE")):
            with self.subTest(reason=reason):
                def readiness(read, write, error, timeout):
                    return ([], result, [])
                with mock.patch.object(controller.select, "select",
                                       side_effect=readiness):
                    if reason == "FINISH_PARTIAL_WRITE":
                        with mock.patch.object(controller.os, "write",
                                               return_value=3):
                            record = self.run_case()
                    else:
                        record = self.run_case()
                self.assertEqual(record["outcome"], "BLOCKED")
                self.assertEqual(record["reason"], reason)
                self.assertNotIn("HOST_ACK",
                                 [e["event"] for e in record["events"]])

    def test_late_writability_never_sends_finish(self):
        def delayed(read, write, error, timeout):
            time.sleep(1.1)
            return ([], write, [])
        with mock.patch.object(controller.select, "select", side_effect=delayed):
            record = self.run_case(hold=1)
        self.assertEqual(record["reason"], "DEADLINE_BEFORE_FINISH_WRITE")
        self.assertNotIn("FINISH_WRITTEN",
                         [e["event"] for e in record["events"]])

    def test_exited_leader_with_inherited_pipe_is_bounded(self):
        record = self.run_case(host="descendant", hold=1)
        self.assertEqual(record["outcome"], "BLOCKED")
        self.assertIsNotNone(record["host_exit"])

    def test_cleanup_uncertainty_blocks_after_other_child_cleanup(self):
        real_stop = controller._stop
        calls = []
        def uncertain(child):
            calls.append(child)
            status = real_stop(child)
            return None if len(calls) == 1 else status
        with mock.patch.object(controller, "_stop", side_effect=uncertain):
            record = self.run_case()
        self.assertEqual(len(calls), 2)
        self.assertEqual(record["outcome"], "BLOCKED")
        self.assertEqual(record["reason"], "CLEANUP_UNCERTAIN")

    def test_invalid_argv_rejected_before_spawn(self):
        for host in ([], ["sh", "-c", "x\x00y"], ["python", 1]):
            with self.subTest(host=host), self.assertRaises(ValueError):
                controller.run(host, child("restricted", "ok"),
                               spawn_host=subprocess.Popen,
                               spawn_restricted=subprocess.Popen)


if __name__ == "__main__":
    unittest.main()
