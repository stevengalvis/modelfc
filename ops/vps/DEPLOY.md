# Fresh release deployment after merge

This deployment path is separate from the trusted PR validator. A push to
`main`, after GitHub CI succeeds, requests exactly that push's SHA. The reviewed
controller installed at `/opt/modelfc-deploy/deploy_main.py` creates a new,
independent Git repository in bounded acquisition staging, exports it to
`/srv/modelfc/releases/<sha>-<nonce>/`, installs
a fresh `.venv` there, and runs offline tests. Only a passing release is pointed
to by `/srv/modelfc/current`. The incoming release never supplies the controller
or the systemd unit. This PR only provides source; it does not install anything.

## One-time VPS installation after review and merge

1. Inspect actual VPS ownership and permissions. Create `modelfc-deploy` with no
   interactive deployment shell, Docker access, production credential access or
   access to `/root/modelfc-state`. Give the account ownership of **only** the
   new `/srv/modelfc` tree and its own `/var/lib/modelfc-deploy` directory.
   Traversal of `/root` is unnecessary. Keep `/root/dev/modelfc` and its
   historical CSVs, refresh metadata and locks under their existing ownership.
   Do not recursively change ownership of `/root`, existing history, or state.

   ```sh
   useradd --create-home --shell /bin/sh modelfc-deploy
   passwd -l modelfc-deploy
   install -d -o modelfc-deploy -g modelfc-deploy -m 0755 /srv/modelfc /srv/modelfc/releases
   install -d -o modelfc-deploy -g modelfc-deploy -m 0700 /var/lib/modelfc-deploy
   install -d -o modelfc-deploy -g modelfc-deploy -m 0700 /var/lib/modelfc-deploy/reports
   ```

2. From a specifically reviewed merged SHA, install the controller **outside**
   releases and all three fixed systemd units, owned by root. Install future
   reviewed controller changes intentionally after review. The installed unit,
   not the repository copy, determines test isolation; verify its effective
   `systemctl show` properties. The test unit must have `PrivateNetwork=yes`,
   `ProtectSystem=strict`, `ReadOnlyPaths=/srv/modelfc/releases`,
   `InaccessiblePaths=/root/modelfc-state /root/dev/modelfc`, the fixed
   `User=modelfc-deploy`, `Group=modelfc-deploy`, no supplementary groups,
   `MemoryMax=2G`, `TasksMax=64`, and trusted `ExecStart`.
   The test unit mounts `/var/lib/modelfc-deploy` read-only and grants write
   access only to its `reports/` child for the sanitized result. Its trusted
   request file and `deploy.lock` stay outside that writable child; a test
   cannot unlink the active lock and allow a second deployment to acquire a
   replacement inode. The controller checks these effective installed
   properties before starting the unit. The dependency unit cannot access
   `/var/lib/modelfc-deploy` at all. It has `ProtectSystem=strict` and grants
   write access only to the instance's fresh `.venv`; package builds can use
   its private `/tmp`. The dependency unit is also limited to `MemoryMax=2G`
   and `TasksMax=64`, and the controller verifies those effective installed
   limits before starting it.
   It cannot write other releases, `current`, history or state. It writes no
   dependency report. The test unit writes only a bounded
   test report under `/var/lib/modelfc-deploy/reports`; tests receive an allowlisted
   environment with no API, GitHub, SSH or application credential.

   ```sh
   install -d -o root -g root -m 0755 /opt/modelfc-deploy
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:ops/vps/deploy_main.py" \
     | install -o root -g root -m 0555 /dev/stdin /opt/modelfc-deploy/deploy_main.py
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:ops/vps/acquisition_mount.py" \
     | install -o root -g root -m 0555 /dev/stdin /opt/modelfc-deploy/acquisition_mount.py
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-tests.service" \
     | install -o root -g root -m 0644 /dev/stdin /etc/systemd/system/modelfc-postmerge-tests.service
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-dependencies@.service" \
     | install -o root -g root -m 0644 /dev/stdin '/etc/systemd/system/modelfc-postmerge-dependencies@.service'
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-acquisition@.service" \
     | install -o root -g root -m 0644 /dev/stdin '/etc/systemd/system/modelfc-postmerge-acquisition@.service'
   systemctl daemon-reload
   systemd-analyze verify /etc/systemd/system/modelfc-postmerge-tests.service
   systemd-analyze verify '/etc/systemd/system/modelfc-postmerge-dependencies@.service'
   systemd-analyze verify '/etc/systemd/system/modelfc-postmerge-acquisition@.service'
   ```

3. Create a dedicated SSH keypair. Bind its public key to the controller in
   `modelfc-deploy`'s `authorized_keys`:

   ```text
   restrict,command="/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py" ssh-ed25519 AAAA... modelfc-postmerge
   ```

   `restrict` disables PTY, forwarding, X11 and user rc. The account only
   needs narrow passwordless sudo permission for `/usr/bin/systemctl start
   --wait modelfc-postmerge-tests.service`, `systemctl stop` for that exact unit,
   and start/stop for the fixed
   `modelfc-postmerge-dependencies@<validated-release-id>.service` and
   `modelfc-postmerge-acquisition@<validated-release-id>.service` instances,
   without SETENV. The trusted controller validates the release ID, and the
   installed dependency unit validates it again. The account never gets an
   unrestricted root shell. The fixed service is not enabled by a timer.
   Verify a login attempt without a valid `deploy stevengalvis/modelfc <sha>`
   request produces `INVALID_REQUEST`.

4. Independently verify and pin the VPS SSH host key. Set GitHub environment
   `production` to accept `main`; configure `MODELFC_DEPLOY_SSH_KEY` (secret),
   `MODELFC_DEPLOY_HOST` and `MODELFC_DEPLOY_HOST_KEY` (variables containing
   host and known_hosts line). Never put OddsPapi keys, validator tokens, state
   contents or other production secrets in GitHub Actions.

## Runtime and historical-data wiring

Deployments do not move or copy historical CSVs. Their current location remains
`/root/dev/modelfc`; refreshing those CSVs and acquiring `data/corner-refresh/
refresh.lock` remain separate operations. The release's committed
`corner_data.json` still resolves `data_directory: "."` relative to that file.
Production commands must instead use a separately maintained runtime config
outside releases, containing the same `leagues` and `max_age_days` as the
reviewed configuration and an **absolute** `data_directory` of
`/root/dev/modelfc`. Confirm actual history file and refresh lock locations on
the host before creating that config. Do not put the config in Git; keep it
root/admin managed. The configuration parser accepts an absolute directory.

Before switching any runtime command to `/srv/modelfc/current`, verify its
service account can traverse and read the historical data and write the
existing refresh lock, while the `modelfc-deploy` account still cannot modify
history or state. Existing state configuration must continue to point to
`/root/modelfc-state`; the deploy controller does not inspect its contents.
Model FC's `git_commit_sha()` resolves the code path through the `current`
symlink and reads the independent `.git` retained in each release. Use an
absolute runtime `--data-config` pointing to the external history config.

Audit installed services and manual commands before enabling automatic
deployment. Repository examples hardcode `/root/dev/modelfc`, notably
`deploy/modelfc-corner-refresh.service` and `DATA_REFRESH.md`; the refresh
service currently runs code from the old checkout and uses its existing config.
Plan its separate migration explicitly: execute refreshed code through
`/srv/modelfc/current` while retaining `/root/dev/modelfc` as the data
directory, and pass the external config. Audit OddsPapi and prospective runner
commands and any locally installed services or scripts for their working
directory, `PYTHONPATH`, virtualenv Python and config paths. This PR does not
change or restart those installed services, modify their configuration, or
migrate any data. Until that separate wiring is completed, promoting `current`
does not itself switch the old running commands to the new code.

## Deployment and acceptance

The controller accepts only `deploy stevengalvis/modelfc <40 lowercase hex>`.
It locks the host deployment path, verifies any current release's independent
Git HEAD, mounts the fixed private acquisition tmpfs described below, initializes
a new repository there, fetches fixed `origin/main` with
isolated Git config, proves the requested SHA is on that fetched branch, and
rejects moving a verified active release backward. It checks out the exact
SHA, reaps acquisition processes, validates and exports the independent repository
to a fresh permanent candidate, reverifies HEAD and tracked source, then
installs `requirements-deploy.lock` into a fresh `.venv` at the candidate's permanent
path through the narrowly writable dependency service, verifies every tracked
source blob against the reviewed Git commit, and runs the complete offline
tests in the installed systemd sandbox. It verifies tracked source again before
promotion. Package installation has no production state or provider credential.
The host fingerprints the fresh virtualenv's Python interpreter links/files and
`pyvenv.cfg` before package installation and rejects the candidate if those
bootstrap artifacts change. This narrow check does not make arbitrary installed
Python packages safe: approved dependency artifacts are part of the trusted
runtime, while their build and installation execution remains sandboxed.
Successful tests must produce a positive final unittest count on stderr.
Promotion creates a temporary absolute symlink and atomically replaces
`/srv/modelfc/current`. The old successful release remains on disk. A failed
candidate never becomes current and is removed only if it was created by that
run; abandoned candidates after a crash may be inspected and pruned manually.
There is no automatic rollback: failures before promotion leave `current`
pointing to the previous successful release. A repeated request for the
already current SHA returns `ALREADY_CURRENT` without rebuilding; a superseded
push reports `SUPERSEDED` without moving `current` backward.

Before enabling the GitHub secret, exercise a reviewed main SHA through the
forced SSH command on the actual VPS, verify the reported exact SHA and
nonzero tests, verify the current symlink's direct target and retained `.git`,
then retry that SHA. Test invalid SHA, an older event, dependency failure and
test failure in a controlled acceptance environment. Confirm historical CSVs,
refresh locks and `/root/modelfc-state` remain unchanged; confirm the installed
systemd test process cannot read them or reach the network. Inspect local VPS
journal if tests fail; GitHub receives only the fixed JSON report. The normal
host execution context is used, independent of coding-agent mount restrictions.

Release retention and the one-time runtime path migration are administrator
tasks. Never delete a release still referenced by `current`; keep previous
successful releases until a separate retention policy is reviewed. This system
does not restart Model FC services, call providers, refresh history, settle
outcomes, or replace the trusted PR validator.

## Locked dependency and storage policy

Deployment accepts only the regular, non-symlink `requirements-deploy.lock`
tracked at the requested reviewed SHA. Its bytes must match that Git blob before
installation. Source verification runs before installation, afterward, and before
promotion. There is no requirements.txt fallback and no incidental pip upgrade.
The candidate Python invokes pip with `--require-hashes --only-binary=:all:
--no-cache-dir`; pip check and bootstrap integrity verification remain required.

Reviewed source, exact hash-approved wheels, and host Python/pip bootstrap tooling
are trusted. Wheels avoid source distributions and build backends. These controls
are not a claim of hard containment against intentionally malicious approved
wheel code. Existing service isolation still protects retained releases, current,
history, evidence and deployment-control files.

Before candidate creation and immediately before dependency installation, the
release filesystem must have at least 1 GiB available. Private dependency /tmp
and /var/tmp each use tmpfs limited to 256 MiB and 16,384 inodes. The controller
requires those exact effective systemd settings. Existing PrivateTmp, resource,
credential, bind and write-boundary checks remain in force. During host acceptance,
verify inside the installed service namespace that these tmpfs mounts and limits
are actually active; static unit verification alone does not establish this.

After installation, .venv must occupy no more than 128 MiB in either logical or
allocated bytes and contain at most 10,000 entries. Measurement does not follow
symlinks. This is operational protection for a finite reviewed artifact set,
not a filesystem quota. Reconsider limits whenever the lock changes. Concurrent
unrelated host writes can still exhaust free space. Failure returns the fixed
STORAGE_LIMIT_FAILED reason, blocks testing/promotion and removes only the failed
candidate using existing cleanup. No retained-release pruning is automatic.

SSH uses ConnectTimeout=15, ServerAliveInterval=15 and ServerAliveCountMax=4;
the deployment step has a 75-minute timeout. Quiet responsive sessions continue;
unresponsive connections fail. GitHub timeout is not a replacement for host
locking, bounded service execution or cleanup.

## Activation gate

PR #59 supplies the reviewed wheels-only lock. This integration does not authorize
VPS installation or activation. Installing the reviewed controller and all three units,
configuring credentials, and host acceptance remain separate manually approved
steps. Preserve all existing acceptance checks, including state/history isolation,
mount-limit verification and failure cleanup, before enabling automatic deployment.

Reviewed Model FC source and its tests are trusted code, as are the exact
hash-approved dependency wheels. The test sandbox does not provide hard disk
containment against intentionally malicious reviewed tests. Memory, task,
timeout and free-space controls are operational safeguards against accidental
failures, not a hard filesystem quota. Test-count validation detects accidental
zero-test execution and ordinary failures; it is not adversarial attestation
against trusted tests deliberately fabricating their own result. The controller
requires effective test-service KillMode=control-group so an installed override
cannot weaken process cleanup.


## Bounded Git acquisition

The separately reviewed root-owned `acquisition_mount.py` helper has only three
operations: `mount`, `verify`, and `unmount`, each taking one validated
`<40-hex-sha>-<12-hex-nonce>` release ID. It accepts no path, mount options or
remote from the caller. Install it alongside the trusted controller from the
same reviewed revision. Add only these narrowly scoped sudoers entries, using
`visudo` and validating the resulting file before enabling deployment:

```sudoers
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/modelfc-deploy/acquisition_mount.py mount *
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/modelfc-deploy/acquisition_mount.py verify *
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/modelfc-deploy/acquisition_mount.py unmount *
```

The wildcard is constrained by the installed helper's exact argument count and
release-ID grammar. No SETENV permission, arbitrary Python execution, general
mount command or new daemon is authorized. Existing systemctl permissions remain
separate. The helper creates only `/run/modelfc-acquisition`, mounts **256 MiB /
32,768 inodes** of tmpfs there, and requires mode **0700**, dedicated deployment
UID/GID, `nodev,nosuid,noexec`, exact capacity and an attempt-specific mount source.
Its root-owned `/run/modelfc-acquisition-control.lock` serializes mount operations;
the existing host `deploy.lock` still serializes the entire deployment.

Repository initialization, full main fetch, objects, checkout and Git temporary
files live on this filesystem. Git receives an isolated configuration and staging
HOME/TMPDIR/TMP/TEMP/cache paths; automatic Git maintenance is disabled. No candidate
code runs during acquisition. The controller runs the installed acquisition worker
in `modelfc-postmerge-acquisition@<validated-release-id>.service`, with
`MemoryMax=512M`, `TasksMax=32`, `Delegate=no` and `KillMode=control-group`.
The controller attests the effective resource, lifecycle, command and filesystem
boundary before starting the unit. Its only writable deployment path is staging,
and it cannot access history, state or deployment controls. The trusted request
and bounded result use fixed files within staging. It verifies service/cgroup
termination before export or unmount. The worker has a 600-second deadline; individual
Git operations retain their bounded timeouts. Full history preserves queued-event,
forward-ancestry and supersession checks; shallow fetch is not the security boundary.

Before any persistent candidate is created, a no-follow traversal bounds the
repository plus checkout to **128 MiB logical and allocated bytes** and **16,384
entries**. Special files, Git-metadata symlinks and external object alternates are
rejected. Source symlinks are preserved, never dereferenced. Export makes fresh
files, not shared object links, and verifies the same bounds again. The permanent
candidate's exact HEAD and tracked source bytes are independently verified before
its `.venv` is created. Every retained release still has its own usable `.git`.

Capacity/inode exhaustion or fetch failure reports `FETCH_FAILED` (or
`STORAGE_LIMIT_FAILED` for local filesystem/export limits); verification failures
retain `SHA_NOT_ON_MAIN`, `ACTIVE_SHA_NOT_ON_MAIN` or `SOURCE_INVALID`. Mount or
cleanup boundary failure reports `STATE_BOUNDARY_FAILED`. Raw Git output is never
returned. Failures prevent dependencies, tests and promotion. Cleanup removes only
the current attempt's newly created incomplete candidate and unmounts its staging;
current, retained releases, historical data and evidence remain untouched.

Handled SIGINT/SIGTERM/SIGHUP interruptions follow the same cleanup. SIGKILL, host
crash or a busy/failed unmount can leave staging behind. A subsequent attempt fails
closed rather than adopting or deleting it. An administrator must confirm no
acquisition process remains, inspect the exact mount source/limits, and invoke the
reviewed helper's `unmount <original-release-id>` operation. No lazy/forced unmount
is used. Foreign mounts, nested mounts, symlinks or an unexpected plain directory
require manual investigation; the controller never recursively deletes that state.
A reboot discards the tmpfs. Retained releases still require manual lifecycle
maintenance; this change adds no pruning policy.

Real-VPS acceptance is required before enabling deployment: verify installed sudo
restrictions and actual mount UID/GID/mode, capacity, inode count and flags; exhaust
bytes and inodes in disposable staging and confirm ENOSPC without host disk growth;
exercise success, fetch failure, export failure and SSH interruption; check staging
unmount/process reaping and unchanged current/retained/history/state paths. Test
stale-state refusal and deliberate administrative recovery. Verify queued older
main requests, divergent requests, final `git rev-parse HEAD`, capture provenance,
and locked dependency installation/tests at the permanent path. Offline tests mock
mounting and cannot attest the VPS kernel, sudo policy or actual mount lifecycle.


## Complete service termination

All three installed units explicitly set `TimeoutStopSec=30`,
`SendSIGKILL=yes` and `KillMode=control-group`. The controller checks those
**effective** properties plus `TimeoutStartUSec` before execution. Acquisition
also requires exactly **512 MiB MemoryMax / 32 TasksMax**, with no cgroup
delegation. These give Git headroom above its 256 MiB tmpfs while remaining
conservative for the 4 GB VPS. Setsid/fork descendants stay in the systemd cgroup.

| Service | Start budget | Stop budget | Controller start/wait budget |
| --- | ---: | ---: | ---: |
| Tests | 330 s | 30 s | 390 s |
| Dependencies | 510 s | 30 s | 570 s |
| Acquisition | 600 s | 30 s | 660 s |

Each controller wait includes 30 seconds of overhead beyond start plus stop.
A timed-out/interrupted systemctl call does not imply service termination. The
controller explicitly stops a possibly running unit, allowing 60 seconds for
that stop command, then verifies inactive/failed state, no queued job, zero main/control PIDs,
and an empty or removed service cgroup. It never treats an inactive main PID
alone as proof that descendants are gone. Only after confirmation may it read
results, unmount staging, clean a candidate, or release the deployment lock.

If systemd/kernel failure makes termination unverifiable, the controller stays
in a fail-closed wait with the host lock and candidate retained. It ignores
SSH-disconnect/termination signals during this termination gate. There is no
unsafe fallback timeout that deletes files under a possibly live process. An
administrator may need to restore the manager or terminate a stuck unit; even
if shutdown is later confirmed, this attempt fails with `STATE_BOUNDARY_FAILED`.
The fixed `service-pending.json` marker in the protected control directory is
written before service start and removed only after confirmed termination. It
survives a forced controller death: the next attempt acquires the host lock,
validates the recorded allowlisted unit, stops/confirms it, and refuses to resume
the crashed deployment. Incomplete candidates/staging then follow the existing
manual recovery policy. Never delete this marker as a substitute for checking
service/cgroup termination.

One-time sudo policy must authorize only the following fixed command forms
(in addition to the existing fixed mount helper), with validated release IDs:

```sudoers
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start --wait modelfc-postmerge-tests.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl stop modelfc-postmerge-tests.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start --wait modelfc-postmerge-dependencies@*.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl stop modelfc-postmerge-dependencies@*.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start --wait modelfc-postmerge-acquisition@*.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl stop modelfc-postmerge-acquisition@*.service
```

The trusted controller supplies one exact allowlisted unit argument. No candidate
supplies service names, privileged options or resource settings; instance IDs
are validated before acquisition code executes. Install the reviewed unit and
controller together; no automatic installation occurs in this PR.

Real-VPS acceptance must verify cgroup v2 accounting and effective limits, provoke
memory/task exhaustion in disposable acquisition staging, exercise start timeout
and SIGTERM-resistant descendants for all three units, and confirm cgroups are
empty before export/cleanup. Also exercise controller timeout, SSH disconnect,
and pending-marker recovery while checking lock exclusion and unchanged current/
retained releases. Ordinary offline tests simulate manager states and cannot
prove actual kernel/systemd enforcement or installed sudo policy.


## Effective environments, trusted helper, and runtime permissions

All three service boundaries now query `Environment`, `EnvironmentFiles`,
`PassEnvironment`, and `UnsetEnvironment` with `systemctl show --all`. Missing or
unverifiable output fails before execution. Test/acquisition units may configure
only `PYTHONDONTWRITEBYTECODE=1`; the dependency unit may configure no Environment
assignments. EnvironmentFiles and PassEnvironment must be empty. The complete
UnsetEnvironment list must contain exactly `ODDSPAPI_API_KEY`, `GITHUB_TOKEN`,
`SSH_AUTH_SOCK`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT`, `PYTHONPATH`, and
`PYTHONHOME`. Unexpected assignments are rejected, not merely scanned for known
bad names. The explicit unsets also remove these loader/Python overrides from
inherited manager defaults. Install the reviewed updated units with the controller.

Before **every** sudo mount-helper invocation, the controller opens `/`, `opt`,
`modelfc-deploy`, and `acquisition_mount.py` through directory-relative no-follow
lookups. Every directory must be a real root-owned directory without group/world
write permissions; the helper must be a regular root-owned non-symlink file with
no group/world write permissions. Failure prevents sudo. Root administrators
remain trusted to install reviewed code; this checks path ownership and integrity,
not a new controller/helper signing system.

Exported source and Git directories receive explicit mode **0755**, independent
of umask. Regular files become readable by the production account, retain their
executable/non-executable distinction, preserve existing owner-write permission,
and lose all group/world write permissions. No write permission is added to a
read-only file. Source symlinks stay symlinks and are never followed or chmodded.
The fresh `.venv` is normalized with the same policy both before bootstrap
fingerprinting and after successful installation/bootstrap verification. Interpreter
and library symlinks are preserved; external targets are never normalized.

The one-time `/srv/modelfc` and `releases` ancestors must remain traversable by the
intended production account. After verified export, the trusted controller creates
`.git/modelfc-deployed-sha` exclusively inside each permanent release: exactly the
verified 40-character SHA, deployment-account ownership and mode **0444**. It
rechecks the file before promotion. Tests/dependency services cannot write it;
the distinct runtime account cannot modify it or its protected parent directories.
Tracked-source and deployment Git verification remain unchanged.

Runtime `git_commit_sha()` accepts this metadata only beneath
`/srv/modelfc/releases/<sha>-<nonce>`, checks protected directory ownership/modes,
opens directories and metadata without following symlinks, and requires the
metadata SHA to match the release name. Resolving `/srv/modelfc/current` selects
the new release automatically. This needs no runtime `safe.directory` entries.
Missing/invalid metadata supplies no SHA; the existing Git fallback remains for
development checkouts and older releases. Host administrators and the deployment
account remain trusted. Real distinct-account readability still requires VPS
acceptance, since restricted test containers may forbid UID/GID changes.

All three service boundaries attest both `ExecStart` and `ExecStartEx`: exactly
one expected executable and complete argument vector, with **empty execution
flags** in `ExecStartEx`. Privileged (`+` / `privileged`), other flags, missing or
unprintable properties fail before service execution. Confirm installed systemd
255 effective output during VPS acceptance; the committed units need no changes.
