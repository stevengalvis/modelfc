"""Offline candidate for the ZEN-53 two-child finish protocol.

The caller must supply independently reviewed host and restricted launchers.
This module does not create a namespace, choose an installed command, or run
on import. A successful protocol is only control evidence, not sandbox
acceptance or authentication of the collected source observations.
"""

import hashlib
import json
import os
import re
import select
import selectors
import signal
import subprocess
import time

FINISH = b"finish\n"
HOST_LIMIT = 262144
RESTRICTED_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 65536


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _event_line(line):
    try:
        item = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return item if type(item) is dict and type(item.get("event")) is str else None


def _stop(child):
    if child is None:
        return None
    # waitid(WNOWAIT) above keeps the session leader's PID reserved until
    # here. A descendant holding a pipe remains in this private group even
    # after its leader exits. Never use a PID supplied in child output.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        return child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return None


def _peek_exit(child):
    if child is None:
        return None
    info = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return None
    if info.si_code == os.CLD_EXITED:
        return info.si_status
    return -info.si_status


def run(host_argv, restricted_argv, *, spawn_host, spawn_restricted,
        clock=time.monotonic_ns, startup_seconds=45, hold_seconds=170):
    """Run a bounded pipe handshake with explicitly supplied launchers.

    The host collector itself has a 180 second FINISH deadline. The smaller
    parent deadline leaves a margin. All output is drained concurrently with
    hard per-stream limits; unknown output never grants progress.
    """
    if (not callable(spawn_host) or not callable(spawn_restricted) or
            type(host_argv) not in (tuple, list) or not host_argv or
            type(restricted_argv) not in (tuple, list) or not restricted_argv or
            not all(type(x) is str and x and "\x00" not in x
                    for seq in (host_argv, restricted_argv) for x in seq) or
            type(startup_seconds) is not int or not 0 < startup_seconds <= 45 or
            type(hold_seconds) is not int or not 0 < hold_seconds <= 170):
        raise ValueError("CONTROLLER_CONFIGURATION")

    record = {"version": 1, "outcome": "BLOCKED", "reason": "UNKNOWN",
              "events": [], "host_exit": None, "restricted_exit": None,
              "streams": {}, "host_argv_sha256": _digest(json.dumps(
                  host_argv, separators=(",", ":")).encode()),
              "restricted_argv_sha256": _digest(json.dumps(
                  restricted_argv, separators=(",", ":")).encode())}
    host = restricted = None
    streams = {}
    selector = selectors.DefaultSelector()
    started = clock()
    deadline = started + startup_seconds * 1_000_000_000
    phase = "READY"
    host_lines = bytearray()
    try:
        host = spawn_host(host_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        for name, pipe, limit in (("host_stdout", host.stdout, HOST_LIMIT),
                                  ("host_stderr", host.stderr, STDERR_LIMIT)):
            os.set_blocking(pipe.fileno(), False)
            streams[name] = bytearray()
            selector.register(pipe, selectors.EVENT_READ, (name, limit))
        while True:
            now = clock()
            if now >= deadline:
                record["reason"] = "DEADLINE_" + phase
                break
            if phase == "RESTRICTED" and restricted is not None and _peek_exit(restricted) is not None:
                # Require its stdout and stderr EOF too, so the captured
                # result is complete before finish is sent.
                if not any(key.data[0].startswith("restricted_") for key in selector.get_map().values()):
                    record["restricted_exit"] = _peek_exit(restricted)
                    record["events"].append({"event": "RESTRICTED_EXIT", "at_ns": clock(),
                                             "exit": record["restricted_exit"]})
                    if record["restricted_exit"] != 0:
                        record["reason"] = "RESTRICTED_FAILED"
                        break
                    raw = bytes(streams["restricted_stdout"])
                    try:
                        result = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        record["reason"] = "RESTRICTED_OUTPUT_INVALID"
                        break
                    if type(result) is not dict or result.get("context_label") != "restricted" or result.get("blockers") != []:
                        record["reason"] = "RESTRICTED_OUTPUT_FAILED"
                        break
                    fd = host.stdin.fileno()
                    os.set_blocking(fd, False)
                    try:
                        if not select.select([], [fd], [], max(0, (deadline-clock())/1e9))[1]:
                            record["reason"] = "FINISH_PIPE_BLOCKED"
                            break
                        if clock() >= deadline:
                            record["reason"] = "DEADLINE_BEFORE_FINISH_WRITE"
                            break
                        if os.write(fd, FINISH) != len(FINISH):
                            record["reason"] = "FINISH_PARTIAL_WRITE"
                            break
                    except (BrokenPipeError, BlockingIOError, OSError):
                        record["reason"] = "FINISH_WRITE_FAILED"
                        break
                    record["events"].append({"event": "FINISH_WRITTEN", "at_ns": clock(),
                                             "bytes": len(FINISH), "sha256": _digest(FINISH)})
                    host.stdin.close()
                    phase = "ACK"
            if _peek_exit(host) is not None and phase in ("READY", "RESTRICTED", "ACK"):
                # Drain any last buffered bytes before deciding. A completed
                # host without a recognized ACK can never pass.
                if not any(key.data[0].startswith("host_") for key in selector.get_map().values()):
                    record["reason"] = "HOST_EARLY_EXIT"
                    break
            for key, _ in selector.select(min(0.1, max(0, (deadline-clock())/1e9))):
                name, limit = key.data
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(streams[name]) + len(chunk) > limit:
                    record["reason"] = "OUTPUT_LIMIT_" + name.upper()
                    raise RuntimeError("OUTPUT_LIMIT")
                streams[name].extend(chunk)
                if name != "host_stdout":
                    continue
                host_lines.extend(chunk)
                while b"\n" in host_lines:
                    line, _, rest = host_lines.partition(b"\n")
                    host_lines = bytearray(rest)
                    event = _event_line(line)
                    if event is None:
                        record["reason"] = "HOST_EVENT_INVALID"
                        raise RuntimeError("HOST_EVENT_INVALID")
                    kind = event["event"]
                    if (phase == "READY" and kind == "HOST_DESCRIPTORS_HELD" and
                            type(event.get("pid")) is int and event["pid"] == host.pid and
                            type(event.get("host_ready_monotonic_ns")) is int and
                            event["host_ready_monotonic_ns"] > 0 and
                            event.get("max_hold_seconds") == 180 and
                            type(event.get("files")) is dict and set(event["files"]) ==
                            {"/opt/codex/bin/codex", "/opt/codex/codex-package.json"}):
                        record["events"].append({"event": "HOST_READY", "at_ns": clock(),
                                                 "child_monotonic_ns": event.get("host_ready_monotonic_ns")})
                        restricted = spawn_restricted(restricted_argv, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           close_fds=True, start_new_session=True)
                        for label, pipe, bound in (("restricted_stdout", restricted.stdout, RESTRICTED_LIMIT),
                                                    ("restricted_stderr", restricted.stderr, STDERR_LIMIT)):
                            os.set_blocking(pipe.fileno(), False)
                            streams[label] = bytearray()
                            selector.register(pipe, selectors.EVENT_READ, (label, bound))
                        phase = "RESTRICTED"
                        deadline = clock() + hold_seconds * 1_000_000_000
                    elif (phase == "ACK" and kind == "HOST_FINISH_ACK" and
                            type(event.get("finish_received_monotonic_ns")) is int and
                            event["finish_received_monotonic_ns"] >
                            record["events"][0]["child_monotonic_ns"]):
                        record["events"].append({"event": "HOST_ACK", "at_ns": clock(),
                                                 "child_monotonic_ns": event["finish_received_monotonic_ns"]})
                        phase = "COMPLETE"
                    elif phase == "COMPLETE" and kind == "HOST_CAPTURE_COMPLETE":
                        if (event.get("blockers") != [] or
                                type(event.get("sha256")) is not str or
                                re.fullmatch(r"[0-9a-f]{64}", event["sha256"]) is None):
                            record["reason"] = "HOST_CAPTURE_FAILED"
                            raise RuntimeError("HOST_CAPTURE_FAILED")
                        record["events"].append({"event": "HOST_COMPLETE", "at_ns": clock(),
                                                 "capture_sha256": event["sha256"]})
                        phase = "EXIT"
                    else:
                        record["reason"] = "HOST_EVENT_ORDER"
                        raise RuntimeError("HOST_EVENT_ORDER")
            if phase == "EXIT" and _peek_exit(host) is not None and not selector.get_map():
                record["host_exit"] = _peek_exit(host)
                if record["host_exit"] == 0:
                    record["outcome"] = "PROTOCOL_COMPLETE"
                    record["reason"] = "NONE"
                else:
                    record["reason"] = "HOST_FAILED"
                break
    except Exception:
        if record["reason"] == "UNKNOWN":
            record["reason"] = "CONTROLLER_ERROR"
    finally:
        cleanup_uncertain = False
        for child in (restricted, host):
            try:
                if child is not None and _stop(child) is None:
                    cleanup_uncertain = True
            except Exception:
                cleanup_uncertain = True
        for child in (host, restricted):
            if child is None:
                continue
            for pipe in (child.stdin, child.stdout, child.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        cleanup_uncertain = True
        try:
            selector.close()
        except Exception:
            cleanup_uncertain = True
        if host is not None:
            record["host_exit"] = host.returncode
        if restricted is not None:
            record["restricted_exit"] = restricted.returncode
        record["streams"] = {name: {"sha256": _digest(bytes(data)),
                                     "bytes": len(data), "raw": bytes(data)}
                             for name, data in streams.items()}
        if cleanup_uncertain:
            record["outcome"] = "BLOCKED"
            record["reason"] = "CLEANUP_UNCERTAIN"
    return record
