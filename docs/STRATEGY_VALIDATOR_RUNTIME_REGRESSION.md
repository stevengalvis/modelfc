# ZEN-48 executed-harness regression (preparation only)

The controller's fixed single-file read-only mount replaces the stale image copy
at `/trusted/validate_capture.py`. The launch bootstrap verifies mounted inode and
bytes against the host's held descriptor and executes that checked buffer. The
existing validation lock excludes the installer throughout launch/execution;
host verification also rejects a changed source before a receipt can be accepted.
No candidate path or configuration field selects the source. The five-file
revision includes this controller in both PR171 and stacked PR172.

## Running the disposable regression

`tests/test_validator_harness_mount.py` contains portable safety tests and two
opt-in actual-container cases. Ordinary discovery explicitly marks the latter
NOT RUN (skipped). The driver requires an existing rootless Podman identity, a
local OCI archive of a deliberately stale harness image, a new private work path,
and a root-owned nonwritable fixture parent outside production. It creates only
disposable test resources; no validator installation or production config/cache
is invoked. Root creates/removes the trusted fixture and simulates a pathname
replacement outside the cooperative lock. All container work runs unprivileged
with a separate VFS image store/runroot and `--pull=never`, `--network=none`.

```text
python3 -B tests/run_rootless_harness_regression.py \
  --user <existing-rootless-identity> --archive <local-stale-image.oci> \
  --image <immutable-stale-image-ID> --work <new-private-short-path> \
  --trusted-parent <root-owned-nonwritable-development-parent>
```

Optional `--dependency-site <development-site-packages>` builds an offline,
disposable fixture layer containing only Pydantic and its direct dependencies.
It retains the stale baked harness. This option does **not** change the configured
activation image, and its success must never be reported as installed acceptance.
It uses only local files, no package/registry/provider requests. Runroot must be
at most 50 characters, a Podman requirement. Full logs remain in the private work
directory; only the driver's newly created trusted fixture is removed afterward.

## Evidence and scope

Actual rootless Podman on the validator VPS, existing unprivileged UID 1000,
isolated store and generated candidate/history inputs:

- Original immutable image `sha256:3ea9d3919aa47eef62861ec31e2464c3dcbf9353a1ccb327a37b72f96980d1b6`:
  launch-gap replacement rejection PASS; mounted new harness executes, but full
  strategy regression FAIL: `ModuleNotFoundError: No module named 'pydantic'`.
  Core/replay PASS and provider requests zero. This is an activation blocker.
- Offline dependency fixture `sha256:3eadf24cd34ccb5ab635012f83525221922dd8c5eea0f9c1354bbdb7531087f2`
  (Pydantic 2.13.5 and direct dependencies from the existing deployment lock
  environment): 2/2 actual-container tests PASS. The baked report lacks strategy;
  the mounted report has strategy/core/replay PASS and zero provider requests.
  File hash matches, mounted-file and root-filesystem writes fail, no extra fd
  enters the container, network exposes only loopback, effective capabilities are
  zero, and a launch-gap replacement fails before harness output.
- Portable cases cover missing/unsafe/symlink/hardlink sources, writable
  ancestors, candidate/config substitution, lock-inode replacement and actual
  installer lock exclusion. Existing installer/release tests cover revision/cache
  binding, partial failure and rollback of the five-file/cache set.

The configured image, installed validator, cache, configuration, services,
credentials and workflow states were not changed. Source tests and this fixture
regression are not local/pinned-SSH installed acceptance. Both PRs must remain
draft pending review of this correction and a separately authorized resolution
of the image dependency blocker and future activation sequence.
