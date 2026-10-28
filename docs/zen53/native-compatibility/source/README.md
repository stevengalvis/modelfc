# ZEN-53 collector and finish-control export

Export only for https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384364441.
The collector is archived as data. It was not rerun, imported or tested for this export. No sandbox or runtime operation is authorized by this package.

## Files and provenance

capture_ownership_v2.py is byte-identical to the retained original: 12408 bytes,
SHA256 a10c63987a7526331dde2eda7ca05a47ba182633d466d9167b1ad14f13c47902.
Its hardcoded source paths and expected hashes identify the two relevant Codex sources; the package contains no installed-source binary or metadata-file contents.

CONTROL_LOG_SANITIZED.json is a newly serialized allowlist derivative, not the original commands record:
- Original COMMANDS_AND_EXECUTION.json: 1924 bytes; SHA256 d1699752090dde21dde5954ee3cccf2df0a93fbbbb428f5d8d09b3deecb531a6.
- Exported CONTROL_LOG_SANITIZED.json: 2702 bytes; SHA256 a1a50cdb4dedb628bbe3a2312e79a454c0d20fb69c3f1316ccc003c241e536d4.

The original command strings and environment-variable assignments, private output paths, nested file metadata and unrelated flags were omitted. No complete capture or unrelated log is included. The original files and their permissions remain unchanged.

## What is actually retained about finish

The original commands record contains intended text "finish\n" and delivery-time marker UNKNOWN_NOT_CAPTURED. It contains no sender API, sender session ID, timestamped send request, transport delivery receipt, observed input line or positive acknowledgement. Those values are explicitly UNKNOWN in the exported log. A send request must not be inferred to have been delivered.

The held event records its event name, PID and max_hold_seconds=180, but no event timestamp. The completion event records HOST_CAPTURE_COMPLETE, the capture hash, the HOST_BRACKET / FINISH_SIGNAL_MISSING_OR_TIMEOUT blocker and completed_at_utc=2026-10-24T00:36:11.231185+00:00. This completion is not an acknowledgement of finish delivery.

The preserved code uses:
    ready = select.select([sys.stdin], [], [], 180)[0]
    if not ready or sys.stdin.readline().strip() != 'finish':
        ... FINISH_SIGNAL_MISSING_OR_TIMEOUT ...

The combined marker does not identify the branch: no readiness, EOF, or another nonmatching line can cause it. The 180 argument bounds the select wait; a subsequent readline is a separate operation. Actual wait duration, select result, received line, send time, delivery time and acknowledgement are UNKNOWN from these retained items. This export neither diagnoses the historical failure nor clears its blocker.

## Reconstruction

Use only the canonical PR191 export comment's base64 block. Decode it strictly, verify archive byte count/SHA256 and INVENTORY.json SHA256, then verify every inventoried member's size/hash and exact regular-file member set. Do not execute the collector to reconstruct this package.

No credentials, source-binary contents, unrelated logs or environment-variable values are included. Original hashes and exported hashes are distinct where sanitization changed bytes.
