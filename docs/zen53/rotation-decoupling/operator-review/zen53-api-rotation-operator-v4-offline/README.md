# ZEN-53 static Python diagnostic runner — installation candidate, OFFLINE

This package is not installed or authorized for VPS use. Its executable
operator and launcher bytes are static across prepared stages. The exact
root-owned 0444 `INSTALL_CONFIG.json` fixes the operator path
`/var/lib/zen53-operator-v4`, root-protected approval-store path
`/var/lib/zen53-run-approval-v1`, and Python diagnostic profile. The operator
pins the config and all helper sources by SHA256. Run code never accepts a
caller-selected approval file, nonce or manifest hash as authority.

The separately trusted root operator confirms explicit owner approval of the
exact stage nonce, manifest SHA256 and diagnostic profile. The reviewed
`root_operator.py approve` command reads the protected root-owned 0600
prepared manifest with no-follow, compares its full hash to that approved
value, measures current tool/stage/manifest identities, checks the pinned
launcher/source/archive/systemd-run contract, and issues one root-owned 0600
record. Its `owner_approval_ref` is audit text, not authentication.

`provision` exclusively creates the one new root-owned 0700 approval store and
0700 `claims`/`history` children. `prepare-output` exclusively creates the
matching empty root-owned 0700 launch directory. The operator rereads the
fixed approval and expiry, checks protected stage/source identities, waits for
schedule clearance, audits checks, fsyncs an exclusive nonce claim, and
rechecks approval immediately before a bounded scope handoff. A partial
claim or interrupted history retirement is a protective hold. `retire` is a
separate trusted-root action only after independently verified terminal state
and empty cleanup. Old approvals, claims and terminal records remain retained.
The direct launcher CLI cannot spawn.

Private audit records link `job_id` (prepared stage nonce) and separate
`attempt_id` with UTC timestamps, elapsed milliseconds, fixed stage/check
codes, intent, result and explicitly `UNVERIFIED` cleanup until independently
checked. No raw streams, arbitrary exception text, environment or credentials
enter these records. Native Codex/Claude Code JSONL tool-event tracing is a
future, separately scoped task; this candidate is Python diagnostic only.

The original systemd resource limits, origin 8:0 finite I/O caps, 20-minute
schedule observation, 453-second clearance, locks, source checks, no network
or credentials in the payload, 360+3-second scope and 375-second observer are
unchanged. The existing cooperative-root/deploy/validator assumption remains;
this does not defend against hostile root. A passing synthetic suite is not
live isolation, measured throttling, native Codex containment or cleanup.

Use a clean environment, trusted private TMPDIR and Python `-I -S -B`
unittest discovery on the exact extracted package. The separate launcher
fixture has its own tests. The package tests never run systemd or the payload.
See the top-level INSTALL_PROCEDURE.md in the review archive for exact
future commands, prerequisites, ownership, stop rules and approval boundary.
No host package install, service, credential, global security policy or
production file change occurred while preparing this candidate.

Offline v4 proposal: retain the API active/state check but decouple the independent Python diagnostic from its scheduled 30-minute service rotation. Real timer, lock, source, backing, headroom and cleanup gates remain unchanged. No host installation or execution authorized by this package.
