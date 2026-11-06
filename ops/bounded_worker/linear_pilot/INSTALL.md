# Linear pilot: one-session installation and acceptance plan

This is an inactive, reviewable package, not an installer already run. It needs no
model, coding sandbox, pip dependencies, timer or paid Linear plan. Existing
`linear_poll.run()` and its protected storage/deduplication checks are reused.
The launcher adds isolated imports, a protected dedicated source tree, fixed
configuration paths, a clean environment and sanitized terminal outcomes. The
service template adds an outer deadline and no automatic retry. These controls
have offline tests; live systemd compatibility and credential isolation remain
unverified.

## Prepare before the single VPS session

1. Review the exact candidate commit, focused tests and diff. Export only the
   files below from that commit to a **new staging directory**, never a moving
   deployment checkout. Preserve their SHA-256 manifest privately. Do not label
   uncommitted files as matching a Git commit. Transfer through an already trusted
   administrative channel; verify the manifest before using any script as root.
2. Prepare the non-secret settings and grant using the field contracts in
   [LINEAR_STATUS.md](../LINEAR_STATUS.md). Use the privately verified dedicated
   issue, requester, credential viewer and full approved workspace/team roster.
   Do not put real IDs, machine IDs or access inventories in the public package.
   Host identity, scope digest and final expiry are filled from live verification.
3. Obtain one consolidated authorization covering protected-file installation,
   secure local key provisioning, a read-only Linear credential/audience check,
   one status request/result and one duplicate poll. This does not authorize a
   timer, coding runner, restart of the target API, merge or deployment retry.
   Create the Zeno-only Read + Create-comments key in Linear's security settings
   only when authorized. Do not paste it into chat, shell arguments or an agent's
   tool call. General Write, Admin and Create-issues permissions are unnecessary.

The runtime release directory contains exactly:

```text
launch.py                         <- linear_pilot/launch.py
bounded_worker/__init__.py        <- ops/bounded_worker/__init__.py
bounded_worker/linear_poll.py
bounded_worker/linear_status.py
bounded_worker/linear_transport.py
bounded_worker/service_status.py
bounded_worker/bridge_security.py
bounded_worker/bridge_envelope.py
bounded_worker/bridge_transport.py
bounded_worker/task.py
```

The last four modules supply existing validation/transport dependencies; no coding
executor, GitHub publisher, model adapter or third-party package is included.
Keep the manifest, service template and this runbook **outside** the runtime
directory: the launcher rejects extra files, including bytecode caches.

## Consolidated host session, after authorization

Use an existing trusted root administration session, not the coding worker. Do
not give that worker sudo, a host Docker socket, host PID/mount access or new
permissions. Stop on any unknown security boundary; do not weaken the guards.

1. **Read-only gate:** verify `modelfc-corner-api.service` really is the intended
   installed API unit. Loaded inactive/failed is a valid observation; missing or
   masked blocks initial acceptance. Read the machine ID privately and verify
   the protected systemctl/machine-id files. Inspect the proposed paths from the
   main runbook, ancestors, ACLs and mounts. Verify the coding worker is a separate
   unprivileged identity and cannot read/traverse these paths or obtain equivalent
   root access. Review privileged supervisors/backups separately. A paused worker
   is not proof of credential isolation. Report only sanitized pass/block reasons.
2. **Install only after that gate:** for previously absent locations create
   root:root 0700 `/etc/modelfc-linear-status`, its `credentials` subdirectory,
   `/var/lib/modelfc-linear-status`, `/opt/modelfc-linear-status`, `releases`,
   `<reviewed-commit-sha>` and its `bounded_worker` subdirectory. Install the listed
   runtime files root:root 0400 from the verified staging bundle. Never overwrite
   an existing release or replace/delete state. Existing conflicting files block.
   Preserve a private before/after inventory. The status process requires root
   under the current library contract; zero capabilities and filesystem controls
   reduce its privileges but do not make it an unprivileged process.
3. **Provision the key directly:** use the hidden-input snippet below in a trusted
   local terminal with input/session recording disabled. It never accepts the key
   through argv/environment or prints it. If secure local input is unavailable,
   stop; do not substitute chat or an agent terminal. A new file is required.
4. **Credential/audience gate:** use an authenticated read-only probe with the key
   loaded from that file in the trusted administration process. Verify viewer,
   organization, exact team/issue, all sharing fields and complete workspace roster
   against the admin members view. Reuse `LinearAPI`/`Destination.check()` from the
   verified runtime, with the final candidate policy; do not invoke `run()` yet.
   Incomplete/filtered access blocks. Never silently broaden key permissions.
   This step needs the real key and live Linear; it is not covered by offline CI.
5. **Finish protected configuration:** install root:root single-link 0600
   `settings.json`, `host.json` and `grant.json` at the fixed paths. Use the exact
   unit and locally read machine ID, and derive the grant's `scope_sha256` from
   `ServiceStatus(host_path).approval_scope`. Set both token-file paths to
   `/etc/modelfc-linear-status/credentials/linear.key`, both OAuth flags false,
   explicit public audience, and a finite grant expiry (one hour for initial
   acceptance). Set enabled=true only within the authorized session. Never infer
   authorization from a request comment. The existing settings validator and
   destination gate still apply; the launcher does not generate policy/rosters.
6. **Install the inactive unit:** substitute only the accepted 40-hex SHA for
   `@REVIEWED_SHA@` in `modelfc-linear-status.service.in`. Inspect the resulting
   file, run `systemd-analyze verify` locally, and install root:root 0644 as
   `/etc/systemd/system/modelfc-linear-status.service` only if absent. Reload the
   manager when authorized; do not enable a service or create a timer. Verify the
   effective unit has the reviewed ExecStart, no unexpected drop-ins, no Restart,
   the deadline, filesystem restrictions and capability bounds. Verify trusted
   OS Python supports the package and no ambient credential environment is used.
7. **One status acceptance:** Steve posts one canonical top-level request in the
   private handoff's dedicated issue, with a unique job ID and expiry within one
   hour and grant expiry. Use actual Unix time; do not copy an example expiry.
   Start this one-shot unit once. Expect exactly one sanitized two-state report
   and launcher status `reported`. `failed_reported` is failure, even though a
   failure comment was delivered. `blocked`, timeout or uncertain send means stop
   and retain receipts; never retry by deleting state. `idle` means no request was
   accepted and is not acceptance success.
8. **Verify and finish:** privately inspect the SQLite request and delivery rows
   read-only: phase=posted, exact job/binding, destination, posting actor, returned
   comment UUID and report hash. Match that comment in Linear. After the stored
   next-poll deadline, one explicitly authorized second poll with the same request
   must leave the rows unchanged and add no comment. The request must still be
   valid and the issue must contain no other eligible requests. Record sanitized
   success and stop; leave the unit unscheduled and revoke/expire the pilot grant
   when the session ends. A permanent grant or schedule needs separate scope.

Free-plan visibility remains workspace-wide; future members may read old reports.
The roster check blocks future reports when membership changes, but is not an
access-control mechanism. Reports must stay limited to the two enumerated states.
This bundle does not automate credential issuance, roster discovery, host policy
creation or root/coding isolation acceptance. Those are trusted operator actions
performed together once, not facts the installer can safely guess.

## Hidden local key input (only after the preceding authorization and path gate)

Run this directly in the trusted interactive root terminal, not through chat or
an agent tool. The directory must already exist with the reviewed protections.
The code checks ownership/links/modes, disables getpass fallback, creates the file
exclusively and fsyncs it. A failed/partial write requires inspection; no automatic
overwrite or deletion is provided. Do not print the resulting file.

```bash
/usr/bin/python3 -I -B -S <<'PY'
import getpass, os, pathlib, stat, warnings
assert os.geteuid() == 0
directory = pathlib.Path('/etc/modelfc-linear-status/credentials')
assert directory.resolve() == directory
for item in (directory, *directory.parents):
    info = item.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
assert stat.S_IMODE(directory.stat().st_mode) == 0o700
warnings.simplefilter('error', getpass.GetPassWarning)
key = getpass.getpass('Linear key (hidden): ')
assert 1 <= len(key) <= 8192 and key.isascii() and all(32 < ord(c) < 127 for c in key)
os.umask(0o077)
fd = os.open(directory / 'linear.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as target:
    target.write(key.encode('ascii'))
    target.flush()
    os.fsync(target.fileno())
del key
fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.fsync(fd)
finally:
    os.close(fd)
print('Protected key file created; value not displayed.')
PY
```

No root-readable file protection excludes another root-equivalent process. If the
coding worker or its controllable supervisor can gain those privileges, **do not
provision the key on that host** until the boundary is resolved.
