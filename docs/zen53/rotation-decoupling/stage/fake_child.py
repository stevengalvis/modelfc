"""Synthetic protocol child for offline tests. Never reads host sources."""
import json
import os
import subprocess
import sys
import time

role, mode = sys.argv[1:3]
if role == "restricted":
    if mode == "fail":
        sys.exit(2)
    if mode == "hang":
        time.sleep(10)
    if mode == "flood":
        sys.stdout.write("x" * 9_000_000)
        sys.stdout.flush()
        time.sleep(10)
    print(json.dumps({"context_label": "restricted", "blockers": []}), flush=True)
    sys.exit(0)

if mode == "no_ready":
    time.sleep(10)
if mode == "flood":
    sys.stdout.write("x" * 300_000)
    sys.stdout.flush()
    time.sleep(10)
print(json.dumps({"event": "HOST_DESCRIPTORS_HELD", "pid": os.getpid(),
                  "host_ready_monotonic_ns": time.monotonic_ns(),
                  "max_hold_seconds": 180,
                  "files": {"/opt/codex/bin/codex": {},
                            "/opt/codex/codex-package.json": {}}}), flush=True)
if mode == "early_exit":
    sys.exit(2)
if mode == "descendant":
    subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"],
                     stdin=subprocess.DEVNULL, stdout=sys.stdout,
                     stderr=sys.stderr, close_fds=True)
    sys.exit(2)
line = os.read(0, 16)
if mode == "no_ack":
    time.sleep(10)
if mode == "eof":
    sys.exit(2)
if line == b"finish\n":
    if mode == "late_ack":
        time.sleep(3)
    print(json.dumps({"event": "HOST_FINISH_ACK",
                      "finish_received_monotonic_ns": time.monotonic_ns()}),
          flush=True)
    if mode != "missing_complete":
        print(json.dumps({"event": "HOST_CAPTURE_COMPLETE",
                          "sha256": "a" * 64, "blockers": []}), flush=True)
    sys.exit(0)
sys.exit(2)
