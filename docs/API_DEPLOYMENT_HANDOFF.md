# Verified API release handoff (proposed host installation)

The old controller's PASS meant that tested files and `current` were promoted,
not that the long-running API had adopted them. The API deliberately pins a physical
release. Its 30-minute rotation allowed eventual adoption without granting the
deployment account API service-control permissions. This change keeps
`RuntimeMaxSec=30min`, `Restart=always` and `RestartSec=10s` unchanged. Removing the
rotation later may avoid routine read interruptions once explicit handoff and
recovery are proven, but requires a separate reviewed operational decision.

## Controller behavior

The new installed controller reads the current remote main tip after acquisition
and again immediately before promotion, under the existing deployment lock. A
superseded request publishes nothing and does not restart the API. A new commit
arriving after the final check can still supersede an in-flight activation; the
next serialized deployment handles it. Queued GitHub concurrency alone never
prevented older already-running deployments from promoting.

After promotion, PASS requires the fixed helper to verify actual API adoption.
ALREADY_CURRENT also revalidates source and calls the helper; it cannot report
PASS merely because the symlink matches. An already healthy matching process is
not restarted. Missing helper/permission, timeout or readiness failure fails closed.

The helper accepts no command-line arguments and exactly one SHA on stdin. It
only controls `modelfc-corner-api.service`; it imports no candidate code. It checks
`current` and the protected release marker, service identity/sandbox, MainPID,
process UID, physical cwd, exact Uvicorn command, process start identity and ownership
of the listening 127.0.0.1:8000 socket. A bounded GET of `/api/v1/capabilities` must
return HTTP 200 with the v1 capabilities contract (maximum 64 KiB). This response
is bounded by configured competitions, not by the ever-growing prediction ledger.
Data-unavailable/stale warnings remain legitimate capability states, not a failed
application start; readiness does not certify forecast/result coverage. PID/start/release are checked again around
that read. This uses an existing loopback route; no ingress or public API changes.

At most one asynchronous fixed-service restart is requested per helper invocation.
After a restart request, a new systemd InvocationID is required; a briefly healthy
old invocation cannot satisfy the gate. Polling has a 120-second deadline, with 3-second systemctl and 2-second HTTP timeouts
(a final in-flight probe can extend the deadline briefly). The controller bounds
the complete helper subprocess at 150 seconds. Startup is not inferred from a
successful systemctl exit or a previous startup log. A failed handoff may leave a
systemd restart job pending; recovery explicitly reselects the prior release and
verifies the resulting API rather than assuming that the job was canceled.

On adoption failure after promotion, the controller restores the previous tested
release only if `current` still selects this candidate, reverifies its source and
checks API recovery through the same helper. It never overwrites a concurrently
changed pointer. No state/data rollback or provider call occurs. Previously
published candidates are retained even after failure because a process may have
pinned them. Rollback readiness failure is reported, not hidden. For an initial
installation without a prior release, recovery is unavailable and failure requires
operator intervention. Releases introducing irreversible data migrations are not
supported by this code-only recovery contract and require a separate migration plan.

The public deployment JSON contract and notification workflow are unchanged.
`PROMOTION_FAILED` covers failed API handoff; successful recovery still returns
FAIL, with the actual selected final SHA. A bounded private
`last-api-handoff.json` records adoption/recovery PASS/FAIL. This file is diagnostic,
not authorization. ALREADY_CURRENT is still conservatively UNVERIFIED by the
existing notification reporter; that paused work is not resumed here. Code merging
alone does not upgrade the root-installed controller or its meaning of PASS.

A controller crash or SSH termination during publication/adoption does not establish
success or recovery: inspect current, both retained releases and API readiness
before resuming. The existing deployment lock serializes running controllers, not
arbitrary operator pointer changes. Never delete possibly pinned releases.

## Exact proposed host permission and installation (not executed)

After review, CI, exact-head Trusted OFFLINE and disposable host acceptance, obtain
separate approval to install only these two reviewed root-owned mode 0555 files:

- `ops/vps/api_handoff.py` → `/opt/modelfc-deploy/api_handoff.py`
- `ops/vps/deploy_main.py` → `/opt/modelfc-deploy/deploy_main.py`

Install/verify the helper first, validate this single additional sudo rule using
`visudo`, then install the controller. Keep every existing grant unchanged:

```sudoers
modelfc-deploy ALL=(root) NOPASSWD: NOSETENV: /usr/bin/python3 -I /opt/modelfc-deploy/api_handoff.py
```

The command has fixed arguments and no trailing wildcard. No general systemctl,
shell, arbitrary Python script, SETENV, credential access, group membership or
production-state permission is granted. The helper's input is a validated SHA,
not a unit name, path or shell command. Verify that extra argv and other commands
are denied. Use the normal restricted deployment SSH interface, not a new remote
execution endpoint. No API launcher/unit, timer, ingress or credential changes and
no daemon-reload are needed for this controller/helper installation itself.

Before enabling the new controller, establish that the existing running API is
healthy on the current protected release, and retain that compatible rollback
baseline. This approval includes allowing the fixed helper to restart only the
API during deployment/recovery; it does not authorize other service changes.

Disposable acceptance must use copied releases/state and a separately named fake
API service through an inspected test-only harness, never overwrite production
constants in the installed helper. Prove real MainPID/cwd/socket ownership,
readiness, delayed startup, wrong PID/release/listener, successful recovery,
failed recovery, superseded jobs and unchanged data. Exercise at least one actual
isolated systemd restart and report kernel/UID limitations honestly. Unit tests
with mocked systemctl and proc fixtures do not prove that host boundary.

After approved installation, one ordinary authorized deployment must show the
requested SHA actually serving healthy reads before PASS. Verify the 30-minute
settings remain unchanged. Restore the prior controller and remove only the new
permission/helper if installation acceptance fails; coordinate the API's selected
release separately and never undo immutable data.


## Late-review boundary correction — new installation acceptance required

The original PR137 helper checked only four effective unit properties. Do not
install that superseded helper under its earlier acceptance report. This patch
checks the fixed effective installed unit contract: account and supplementary
groups, filesystem hiding/read-only paths with no writable/bind/image exceptions,
private devices/tmp/home, capability restrictions, SUID/SGID and address-family
restrictions, localhost-only IP rules, environment/credential/startup-hook absence,
exact launcher commands, no drop-ins or pending daemon reload, resource limits
and the unchanged 30-minute rotation. Missing/unknown serialization fails closed;
this does not edit a unit or repair drift automatically. The checked-in unit is
the contract; verify its effective serialization on the actual host before install.

Both NSS account groups and actual MainPID status are checked: real/effective/
saved/filesystem UID/GID, supplementary groups, no-new-privileges, seccomp mode
and zero capability masks. Checks occur before restarting an existing process and
before readiness can pass, including another boundary check after a failed
readiness probe. Drift raises failure without a retry restart. Existing systemctl,
HTTP and overall handoff time bounds remain. Remote tip command failures now
report FETCH_FAILED, preserving the prior release without promotion/handoff.

Require fresh exact-head CI/review/OFFLINE and isolated real systemd acceptance of
the revised helper. Test each effective-property drift, account/group drift and
actual process supplementary-group mismatch, plus drift between readiness and
restart and after restart. Record real effective-property serialization; do not
silently loosen mismatches or change production security settings. Repeat the
healthy/adoption/recovery checks and original-data hashes. Unit tests do not prove
kernel enforcement or installed compatibility. After those pass, obtain explicit
updated approval for the revised file hashes before installation; the previous
approval covered different bytes. Keep the same narrow proposed sudo command,
30-minute rotation, no provider requests and no other host changes.


Systemd 255 omits empty EnvironmentFiles/Exec-hook arrays and may print credential
arrays as [unprintable]. The helper verifies those using typed empty arrays from
read-only system-bus busctl queries, with the same fixed-unit encoding and strict
signature checks as the controller. No empty-text assumption, provider call or
new permission is used. Each bus/systemctl probe retains its three-second bound;
the 120-second handoff deadline now begins before initial boundary/readiness
checks, and an expired deadline prevents issuing a restart. The controller's
150-second outer timeout remains unchanged. Include actual systemd serialization
and nonempty/wrong-type/malformed bus replies in disposable acceptance.

The API UID and GID must be positive and distinct from the deployment, runtime
and validator accounts' UIDs, primary GIDs and named groups. Missing required
service-account lookups fail closed. Disposable acceptance must include GID 0,
shared numeric IDs and named-group drift without modifying production NSS.

The invocation baseline is refreshed immediately before the helper's restart
request, after initial readiness probes that may cross an automatic rotation.
Readiness also requires no pending systemd Job before and after HTTP; an empty
text Job is verified as the typed (uo) [0, /] tuple. An already queued job blocks
issuing another restart. Include automatic rotation during the first readiness
probe and a queued restart with an otherwise healthy old invocation in acceptance.
