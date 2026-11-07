# Read-only VPS prerequisite check

Run as the intended unprivileged executor user, not root:

    python3 -B ops/bounded_worker/agents_preflight.py

The script uses Python's standard library. It reads only selected kernel/process
metadata, checks expected paths and locates binary names without running them.
It does not read credentials, environment values, private repository content,
service logs or production data. It does not connect to the network, install
anything, create namespaces or change system settings.

The resulting JSON deliberately always says `ready_for_live: false`. Passing
prerequisites is not OS isolation acceptance. Missing fields identify what to
inspect next, not authorization to install or enable anything.

After collecting this result on the actual VPS, choose and review a separate
executor image/namespace and its credential injection. Do not weaken the existing
ModelFC supervisor, reuse its auth mounts, or broaden its environment allowlist.
Before live execution, verify the full isolation, cleanup and budget requirements
in PLAN.md. Account-key creation and host security/network changes require their
own approval. A confirmed API-credit balance does not establish API authentication.

## Host paths and executor image paths are different

Schema `agents-api-prerequisites-v2` checks seven host prerequisites. It does not
require `/opt/agents/bin/codex` on the host: that is the proposed path inside the
future isolated API executor image, not an installed-host requirement. The
existing bridge selects `executable` and `executable_sha256` from protected
configuration; PROVISIONING_PLAN.md proposes a different host installation path
and explicitly is not an installation record.

Do not install, copy, symlink or relocate a binary to satisfy the old v1 path
check. A v1 failure of `pinned_executor_path_exists` alone does not establish a
host capability failure. It also does not prove the other checks passed unless
the original report confirms that. Preserve the original report as historical
evidence rather than relabeling it PASS.

`executor_image_verified` and `ready_for_live` remain false even when all seven
host checks pass. An independently pinned, compatible exec-server image, clean
private home, containment and all other live gates still require verification.
The entrypoint's fixed in-sandbox executable path is unchanged; there is no
fallback to an arbitrary host executable or PATH lookup.
