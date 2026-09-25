# Fresh release deployment after merge

This deployment path is separate from the trusted PR validator. A push to
`main`, after GitHub CI succeeds, requests exactly that push's SHA. The reviewed
controller installed at `/opt/modelfc-deploy/deploy_main.py` creates a new,
independent Git repository under `/srv/modelfc/releases/<sha>-<nonce>/`, installs
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
   releases and both fixed systemd units, owned by root. Install future
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
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-tests.service" \
     | install -o root -g root -m 0644 /dev/stdin /etc/systemd/system/modelfc-postmerge-tests.service
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-dependencies@.service" \
     | install -o root -g root -m 0644 /dev/stdin '/etc/systemd/system/modelfc-postmerge-dependencies@.service'
   systemctl daemon-reload
   systemd-analyze verify /etc/systemd/system/modelfc-postmerge-tests.service
   systemd-analyze verify '/etc/systemd/system/modelfc-postmerge-dependencies@.service'
   ```

3. Create a dedicated SSH keypair. Bind its public key to the controller in
   `modelfc-deploy`'s `authorized_keys`:

   ```text
   restrict,command="/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py" ssh-ed25519 AAAA... modelfc-postmerge
   ```

   `restrict` disables PTY, forwarding, X11 and user rc. The account only
   needs narrow passwordless sudo permission for `/usr/bin/systemctl start
   --wait modelfc-postmerge-tests.service` and the fixed
   `modelfc-postmerge-dependencies@<validated-release-id>.service` instances,
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
Git HEAD, initializes a new repository, fetches fixed `origin/main` with
isolated Git config, proves the requested SHA is on that fetched branch, and
rejects moving a verified active release backward. It checks out the exact
SHA, installs `requirements-deploy.lock` into a fresh `.venv` at the candidate's permanent
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
VPS installation or activation. Installing the reviewed controller and both units,
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
