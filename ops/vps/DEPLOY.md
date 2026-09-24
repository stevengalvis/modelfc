# Reviewed main to VPS checkout sync

This is separate from the rootless PR validator. It synchronizes an already
merged `main` revision into `/root/dev/modelfc` and runs offline tests. It does
not restart services, access OddsPapi, refresh history, or write captures or
outcomes. Merging this PR **does not install or activate** the VPS side.

## One-time VPS installation, after review and merge

1. On the real VPS host, inspect ownership and access for `/root`, `/root/dev`,
   `/root/dev/modelfc`, `.git`, the ignored historical CSVs, `.venv`, and
   `/root/modelfc-state`. Create `modelfc-deploy` with no sudo shell or other
   groups. Give it traversal of the parent directories and write access to the
   canonical checkout and the entire existing virtualenv (including permission
   to remove its old contents after replacement), **without granting access to
   `/root/modelfc-state` or the validator credential files**. Historical CSVs
   live in the checkout root; inspect their ownership and protect them when
   assigning checkout permissions. Stop installation if these boundaries cannot
   be established. Do not recursively chmod/chown `/root` or the state directory.
   A writable directory permits unlinking a read-only file. If historical CSVs
   stay in the checkout root, simply chowning the whole checkout to the deploy
   user is **not** safe for that data. One possible targeted layout is a
   root-owned, sticky checkout root with a write ACL for `modelfc-deploy`,
   deploy-owned tracked files/directories and `.git`, and root-owned historical
   CSVs. Verify with a harmless disposable file that the deploy user cannot
   replace a root-owned CSV before allowing automatic fetches. The refresh
   service can continue writing those CSVs as root.
   The account creation itself is one-time administrator work:

   ```sh
   useradd --create-home --shell /bin/sh modelfc-deploy
   passwd -l modelfc-deploy
   ```

   Apply checkout ownership/ACL changes only after inspecting the real host.
   The controller deliberately refuses a checkout whose `origin` URL is not
   exactly `https://github.com/stevengalvis/modelfc.git`.
2. As administrator, install `deploy_main.py` from a specifically reviewed main
   SHA under `/opt/modelfc-deploy/deploy_main.py`, owned by root, mode 0555;
   install this service under `/etc/systemd/system/`, root-owned mode 0644;
   `systemctl daemon-reload`. Never run the controller from the incoming
   checkout. Later reviewed controller changes require another explicit install.
   Example with `REVIEWED_SHA` set to the exact approved main revision:

   ```sh
   install -d -o root -g root -m 0755 /opt/modelfc-deploy
   install -d -o modelfc-deploy -g modelfc-deploy -m 0700 /var/lib/modelfc-deploy
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:ops/vps/deploy_main.py" \
     | install -o root -g root -m 0555 /dev/stdin /opt/modelfc-deploy/deploy_main.py
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-tests.service" \
     | install -o root -g root -m 0644 /dev/stdin /etc/systemd/system/modelfc-postmerge-tests.service
   systemctl daemon-reload
   ```
3. Create `/var/lib/modelfc-deploy` owned `modelfc-deploy`, mode 0700. Create a
   dedicated SSH keypair. In the deploy user's `authorized_keys`, bind its
   public key to the installed controller with a forced command:

   ```text
   restrict,command="/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py" ssh-ed25519 AAAA... modelfc-postmerge
   ```

   `restrict` disables PTY, port and agent forwarding, X11 and user rc.
   A normal interactive SSH login is not needed. Restrict this account's sudo
   permission to **only** `/usr/bin/systemctl start --wait
   modelfc-postmerge-tests.service`, with no SETENV. The unit executes as
   `modelfc-deploy`; the controller itself is not run as root.
4. Independently verify and pin the VPS's SSH host public key. Do not obtain it
   with `ssh-keyscan` inside the deployment workflow as a trust decision. Set
   GitHub environment `production` to allow only `main`, with no approval gate
   if deployments should be automatic. Configure repository/environment:
   `MODELFC_DEPLOY_SSH_KEY` (private key secret), `MODELFC_DEPLOY_HOST` (host
   variable), and `MODELFC_DEPLOY_HOST_KEY` (complete known_hosts line variable).
   The provider key, validator GitHub token and app secrets never go to Actions.

The controller accepts only `deploy stevengalvis/modelfc <40 lowercase hex>`
as `SSH_ORIGINAL_COMMAND`. It checks a clean canonical `main` checkout, fetches
only from the fixed public repository, checks both the requested SHA and
current checkout SHA are reachable from the fetched main tip, and merges only
the requested SHA with `--ff-only`. A clean local-only commit fails with
`LOCAL_SHA_NOT_ON_MAIN`, even when it descends from the requested SHA. Git marks
only `/root/dev/modelfc` as a safe directory for this controller's isolated
invocations; no global or wildcard trust setting is installed. It never
resets, stashes or cleans. Ignored `.venv`, managed historical CSVs, refresh
files and Python caches are accounted for, not removed. Unknown ignored files
and normal untracked/modified files block deployment. When requirements change,
the controller builds a clean virtualenv under `/var/lib/modelfc-deploy` (which
must share the checkout's filesystem), installs and checks dependencies, then
atomically swaps it into `.venv`. The successful requirements hash travels with
the validated replacement. A failed build preserves the previous `.venv` and
its stamp; no packages are installed into the old environment.

The installed systemd unit runs trusted `--run-tests` code outside the Git
checkout. The controller checks the **installed effective** `User`,
`PrivateNetwork`, `InaccessiblePaths`, `ProtectSystem=strict`, and canonical
`ReadOnlyPaths` properties before starting tests. It rejects writable path or
bind overrides; only its fixed report directory may be writable. The unit has no provider,
GitHub or SSH credentials. Its child test process gets a fresh allowlisted
environment. The test unit is **not enabled**, scheduled, or started except by
the trusted controller. A failure leaves the already fast-forwarded checkout
at the merged SHA; no automatic rollback or service restart occurs.

## Acceptance checks before enabling the GitHub secret

- Verify the deploy user cannot list/read/write `/root/modelfc-state` and
  cannot read `/etc/modelfc-validator`. Confirm the unit's actual
  `PrivateNetwork=yes` and `InaccessiblePaths` properties on the host.
- Confirm the host SSH invocation can fetch in the **real host context**. A
  restricted coding-agent mount of `.git` does not establish host behavior.
- Run a reviewed SHA using the forced-command key, confirm its JSON contains
  the exact requested/final SHAs and a nonzero test count, and check the state
  directory remains untouched. Then retry the same SHA.
- Check that an invalid SHA, wrong branch, dirty tracked file, unexpected
  untracked/ignored file, older already deployed SHA, failed pip install,
  failed tests and concurrent call produce fixed outcomes without removing work.
- Verify the systemd test process cannot reach an external network or read
  `/root/modelfc-state`; inspect the host journal locally if a test fails.

GitHub sees only a sanitized JSON report. Raw test output, filesystem contents,
credentials and command stderr never cross the SSH reporting channel. Neither
the post-merge workflow nor the installed controller replaces the separate PR
validator or constitutes approval to merge unreviewed code.
