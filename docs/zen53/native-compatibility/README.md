# ZEN-53 owner/finish v3: offline correction

This is an **unpublished**, unprepared candidate. It builds on the reviewed owner-anchor v2 archive SHA256 `ae3870713df5c7ec5e186ef4dd0b77f5bb0b5d8ec0caff6f7017b86fa470846c` and the verified collector export at [PR191 comment 6384774798](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384774798). Neither archive, prior VPS evidence, historical dependency records nor a runtime stage is changed here.

`OWNER_FINISH_V3_REVIEW.md` explains the exact original control ambiguity and proposed change. The original collector could block in text `readline()` after a bounded select, combined no-readiness/EOF/wrong input under one marker, and emitted no positive FINISH acknowledgement. The retained log proves only that `finish\n` was intended; delivery and the branch taken remain UNKNOWN. The host capture exited 2 and stays failed. Its marker alone does not prove 180 seconds elapsed.

`capture_ownership_v3.py` is a proposed successor, never executed as an operational entrypoint in these tests. It reads only an exact `finish\n` from nonblocking descriptor 0 under one monotonic deadline and a 16-byte total ceiling, returns fixed bounded status codes, acknowledges only the exact line, and retains all old source/mount checks. It also records the local `/proc/1/ns/pid` link. `owner_context.py` v3 pins the proposed collector hash and requires a completed bracket plus an independently reviewed sender-ordering record. The pure validator does not authenticate either record. A synthetic plan cannot constitute accepted host evidence.

All reporter, supervisor, launcher, unit, resource, isolation and cleanup source bytes remain identical to owner-anchor v2. The existing `bridge.py` remains unchanged. The Ubuntu source tarballs are provenance-replay inputs, not prerequisites for these synthetic tests. No full external verifier PASS is claimed.

Allowed local checks:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B verify_compact.py
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -q
```

The tests AST-select only the new pure control function; they do **not** run the collector entrypoint, a host capture, Bubblewrap, systemd or native Codex. Actual sender code/transcript is still missing. A separately reviewed sender sequence and new user approval are needed before at most one bounded read-only capture. Stage preparation and live sandbox acceptance remain separate decisions.

The compact verifier inventories the nested `source/INVENTORY.json` as an
ordinary member; a clean extraction must pass both integrity and test checks.
