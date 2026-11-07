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
