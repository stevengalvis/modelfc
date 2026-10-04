# Team Intelligence activation artifact (not executed by CI)

This manual artifact implements the separately authorized rollout in
[TEAM_INTELLIGENCE.md](TEAM_INTELLIGENCE.md). Publishing or merging it does not
approve host execution. No workflow, installed launcher/unit, ACL, credential or
persistent connection is changed by this PR. PR94 is unrelated.

Default invocation prints REVIEW_ONLY and exits without host inspection or writes.
Only an explicit root `--apply` invocation can activate anything. Never import or
execute the artifact from an application service or deployment hook.

## Delivery and approval

- Review the exact branch commit, tests and complete file SHA256. An existing host
  administrator can retrieve that one file through the existing repository fetch
  path; no new SSH permission or Remote registration is necessary.
- Do not merge just to deliver the artifact: every main push triggers the existing
  application deployment. This script deliberately accepts only the reviewed PR93
  release SHA and templates. A new deployed SHA requires a new compatibility review.
- Separately approve host execution: root-only backup/baseline files, timer pause
  and safe restoration, the existing deployment/refresh locks, two launcher
  replacements, five named API ACL additions with unchanged masks, API restart,
  Caddy replacement/reload and read-only acceptance requests.
- Approval must include conditional stopping of ONLY
  `modelfc-corner-refresh.service` during failure recovery, if a start job,
  process or populated cgroup remains. This may invoke systemd's existing stop
  escalation; it does not grant sudo, modify a unit, kill unrelated processes,
  manually refresh, or invoke a provider. Publishing this code is not that approval.

## Private baseline: prepare from separately reviewed read-only evidence

The root-owned mode-0600 regular file
`/etc/modelfc/team-intelligence-activation.json` must exist before `--apply`.
Its parents must be root-owned and not writable by group/other, with no symlinks.
No auto-discovery, baseline acceptance or baseline-writing shortcut is provided.
The file is bounded to 32 KiB and duplicate/unknown JSON keys are rejected.
Keep production inode values, CSV hashes, physical release nonce and config hashes
out of Git, PR bodies and test fixtures.

Exact JSON object fields:

| Field | Required value |
|---|---|
| `release` | Physical `/srv/modelfc/releases/<PR93 SHA>-<12 lowercase hex>` path |
| `installed_hashes` | Map from each exact `INSTALLED_PATHS` path in the script to its full SHA256 |
| `identities` | Map from each exact `IDENTITY_PATHS` path to `[device, inode]` integers |
| `e1_sha256` | Full SHA256 of the reviewed current `E1_2627.csv` bytes |
| `data_cutoff` | Reviewed ISO date in the 2026/27 source season |

Candidate launcher/Caddy hashes are public reviewed source constants. Runtime
accounts, the seven-league config, current season, exact CORS origin and ingress
host remain constrained to this installation; this is not a general deployment
framework. Pin the input baseline separately from the reviewed script. Drift is
an abort, not permission to regenerate evidence to make checks pass.

## Before mutation

Confirm no concurrent administrator is changing units/files/ACLs. The deployment
lock serializes normal releases, not arbitrary root activity. Review effective
refresh-unit wiring as well as its pinned base file. No unit changes are made.
The artifact checks release/file hashes, inode identities, complete numeric ACLs,
no obsolete API grants, existing denied probe targets and exact Caddy route diff.
It rejects launcher/Caddy xattrs instead of silently dropping their metadata.

Offline tests use disposable history and must finish within the bound. The real
cross-UID ACL test must RUN and pass; skips are fatal. A local non-root pass does
not substitute for this gate. A timeout prints the captured last output and
aborts before the timer pause. Do not bypass a stalled or skipped test.

## Lock, acceptance and recovery protocol

1. Hold deployment LOCK_EX. Pause the timer only with a safe future deadline.
2. Acquire the existing refresh LOCK_EX and repeat the complete baseline checks.
3. Back up files/ACLs, journal individual replacements, install launchers and five
   exact ACL entries without mask recalculation, then restart the API.
4. Convert refresh ownership to LOCK_SH and revalidate after the potentially
   non-atomic conversion. Run loopback acceptance before exposing Caddy routes.
5. Validate the exact additive Caddy diff, install/reload and verify the public
   route projections, metadata, ETag/304, errors, CORS and existing performance.
6. Restore the timer only when its schedule, last trigger, enablement and next
   deadline remain safe; retain SH through the check so a writer cannot publish.

Recovery removes new ingress first. While retaining existing flocks, it stops
and verifies the timer and conditionally stops the fixed refresh service. Zero
MainPID alone is insufficient: no pending systemd Job, zero MainPID/ControlPID
and an empty/released cgroup are required. Only then may it release SH, acquire
EX and revalidate all identities/full source hash before restoring ACLs/files.
A refresh-related recovery leaves the timer stopped for operator review.

Each stop/lock attempt is bounded. If quiescence still cannot be verified, the
process deliberately retains its locks and enters an operator recovery hold,
matching the existing deployment controller's fail-closed approach. It DOES NOT
release locks after an arbitrary timeout. Deferred TERM/HUP/INT cannot interrupt
this hold. After verified quiescence it exits failed, without further mutation.
A broken system manager may require operator intervention; SIGKILL, host crash
and power loss cannot be contained by an in-process flock. Do not force-kill the
holder while a queued refresh remains. No background daemon or persistent access
is created. Disk-full journal errors must not prevent essential recovery.

## Host acceptance and rollback evidence

- Reconfirm exact script and private baseline before a single approved apply.
- Retain the root-only backup manifest and command result; never publish them.
- Verify effective API UID/groups/capabilities, pinned process release, loopback
  listener, read-only sandbox, exact source revision/cutoff and both allowed and
  denied CORS origins. Confirm other seasons/leagues/private state remain denied.
- Use the next normal scheduled refresh to verify new E1 publication preserves
  API/validator access. Do not trigger refresh/collection as an acceptance shortcut.
- The local mocked tests do not certify host systemd, cgroups, TLS, ACL kernel
  behaviour or live data. CI separately exercises the repository cross-UID case.
- Never restore model state, provider budget, immutable evidence or CSV content.
  If protected identities changed, stop and retain the evidence for review.
