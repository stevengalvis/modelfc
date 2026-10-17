# ZEN-48 rootless runtime regression (preparation only)

The trusted installed harness is mounted as one read-only file onto
`/trusted/validate_capture.py`. Host ownership/ancestor checks, a held descriptor
under the existing validation lock, and a command-line bootstrap checking mounted
inode/hash and compiling the same bytes prevent candidate substitution, replacement
races and baked-harness fallback. A durable transition marker blocks the new
controller before dispatch/cache reuse and again under the execution lock.

## Reviewed development candidate

The previous original-image test failed with missing `pydantic`. The earlier
site-packages-copy fixture was diagnostic only; it is not promoted and its build
option has been removed. The new candidate uses fresh, hash-verified official
wheels and an immutable official Python base. See
[build inputs and proposed upgrade](STRATEGY_VALIDATOR_IMAGE_UPGRADE.md).

Candidate ID: `sha256:6e632bc0eba8d02a188d050ccd7b59552b2c1b3e78217982026e976120f2a7af`.
Two independent offline no-cache builds produced this same ID. Base, platform,
recipe/lock/harness and wheel hashes are recorded in
`ops/vps/validator-image-inputs.json` and `ops/vps/validator-image-build.json`.

## Actual container tests and reproduction

The driver requires an existing rootless Podman identity, a local candidate OCI
archive, a new private short work path and a root-owned nonwritable development
fixture parent. Root creates/removes only trusted disposable test fixtures and
simulates a source replacement. Containers run unprivileged with a private VFS
store/runroot, `--pull=never`, `--network=none` and the unchanged runtime limits.
No production configuration, cache, credentials, directories or sockets enter
any container. No service or security change enables testing.

```text
python3 -B tests/run_rootless_harness_regression.py \
  --user <existing-rootless-identity> --archive <candidate.oci> \
  --image sha256:6e632bc0eba8d02a188d050ccd7b59552b2c1b3e78217982026e976120f2a7af \
  --work <new-short-private-path> --trusted-parent <safe-development-parent> \
  --stale-harness <reviewed-predecessor-validate_capture.py>
```

The predecessor harness must have SHA-256
`fa61840de404a8a8803b30a18b98b60acbaeba36b6445eb9cafcc1112028dfdd`.
The driver bakes it into a disposable derivative of the candidate, without changing
dependencies or promoting that derivative. The base candidate is tested separately.

Actual rootless Podman 4.9.3 on the VPS, existing unprivileged UID 1000: **3/3 PASS**.

- Real candidate worker/cleanup and receipt/cache policy run end to end against
  generated saved inputs. Only GitHub metadata/source export use fixture adapters;
  Podman, mount, harness, strategy, cleanup and envelope policy execute for real.
  Strategy/core/replay PASS, provider requests zero, cleanup COMPLETE. Matching
  cache repeats without execution; stale SHA, image, configuration and revision
  bindings reject. A pending transaction blocks new execution.
- Stale derivative's baked report lacks strategy. With the reviewed host-file
  mount, strategy/core/replay PASS and provider requests remain zero. Candidate
  `ops/` cannot substitute a harness. File hash agrees, writes to mounted harness
  and read-only root fail, no extra descriptor enters the container, effective
  capabilities are zero and networking exposes only loopback.
- A pathname replacement after command construction fails in the trusted bootstrap
  before any harness report; host descriptor verification rejects it too.

Portable tests additionally cover missing/unsafe/symlink/hardlink sources, writable
ancestors, candidate/configuration substitution, lock-inode replacement, actual
installer lock exclusion and pending/dangling transition journals. Ordinary source
CI explicitly skips the opt-in actual-container cases; the driver runs them
separately. Source CI, container integration and installed acceptance are distinct.

No installed local or pinned-SSH acceptance was performed. Active production
image, validator, configuration and cache remain unchanged. Any future activation
requires new review/approval of both heads, image and bounded upgrade/recovery plan.
Full logs stay local; remove only created disposable stores after retaining them.
