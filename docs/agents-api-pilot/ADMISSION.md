# Offline durable reservation gate

`agents_admission.py` is a local, unwired primitive. It records create, executor,
and input reservations before invoking injected mock callbacks. It does not
connect the API or launch an executor. Use a preinitialized journal in a trusted
private application directory outside the worker sandbox. Keep a stable trusted
attempt identifier; changing identifiers defeats deduplication.

The SQLite database uses DELETE journals and verified synchronous=EXTRA. This
includes the directory synchronization needed after rollback-journal removal.
Local storage must honor locking and synchronization. Tests cover process exit,
restart, and competing processes, not simulated physical power failure. Deleting,
replacing or rolling back the journal invalidates its guarantees. Trusted callers
and privileged administrators must not race directory replacement. Concurrent
first-time initialization may fail closed; initialize before admitting work.

A reservation means an operation may have occurred. It does not mean it completed
successfully. Failed or uncertain callbacks are never automatically replayed.
The order checks alone are not evidence of session connection, containment,
credential scope or spending authorization. A future controller must bind the
attempt to the task, image, session, environment and containment identities, then
reconcile uncertain outcomes without submitting another create/input operation.

## Isolated executor integration boundary

Do not plug enter_executor directly into a callback on the host. It requires an
already isolated child and does not establish that isolation. The production
systemd backend is unchanged: its environment allowlist excludes CODEX_API_KEY,
and its subprocess boundary does not carry the restricted-key pipe. Broadening
its allowlist would leak credentials into service command arguments.

The separate API backend must preserve cgroup resource limits and verified empty
cleanup, supply an independently pinned executor image with a clean private home,
transport only the restricted environment key through a protected channel after
isolation, and separate executor control traffic from command network access.
Reuse the existing backend's containment verification patterns, not its auth
mounts or credential-unsafe environment serialization. No host installation or
security configuration is authorized by this document.

Next offline step: a reviewed controller that combines this reservation primitive
with explicit evidence admission and a mock isolated-backend interface, including
crash reconciliation and cloud/local cleanup. Actual host/backend acceptance and
any live connection remain separate gates. No further manual VPS check is needed
merely to test this primitive.
