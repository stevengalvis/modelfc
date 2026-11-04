# Unwired no-model sandbox candidate

This offline candidate has no operator entrypoint and is **not ready for a host
run**. It neither implements fixture preparation nor supplies acceptance evidence.
It proposes a fixed direct `codex sandbox` command and a test worker for the
existing launcher. It does not start an app-server, request a model, or prove
app-server/code-mode behavior. Its probe output is worker-shared and untrusted.

The retained prior prepared-baseline report is PR 234 comment 6566424521; it
used Codex 0.166.0, disposable inputs and the direct sandbox CLI, on an older
revision without this storage boundary. It reported normal and interrupted
metadata checks and cleanup, while the outer supervisor exited 1 and the
interrupted terminal signal was not retained. PR 233 comment 6565547327
explains CLI and app-server helper-grant differences. No historical cleanup or
new-system acceptance follows from those reports.

The candidate adds a fresh-fixture preparer with valid TOML and JSON literals,
using the existing lock, admission, creation receipt and source-identity checks.
Its input-version constant is not recorded in the durable receipt; literal
content is checked against the reviewed source. Syntax does not prove Codex
accepts the fake auth schema. Earlier fixtures are never rewritten.

The candidate's supervisor helpers bind an untrusted namespace-local PID to a
unique host process in the exact cgroup using UID, command, executable, PID
namespace and start time. They provide independent cleanup verification, but
do not yet orchestrate a complete run. Before any host proposal, a reviewed
orchestrator must invoke these helpers through the storage launcher and prepared
baseline, retain bounded output, and verify cleanup and snapshots after normal
and interrupted cases. Checking the `systemd-run` client alone is not proof
that the probe is alive. Preserve evidence and keep the bridge paused.

The candidate omits managed configuration: the pinned CLI can perform cloud
configuration bootstrap when it is included, and the current outer unit does
not establish network isolation for that bootstrap. Thus its effective profile
is not verified equivalent to app-server and configured child network denial is
not proof of actual network behavior. Review the exact binary's source and
resolve the no-network and configuration gates before any host execution. The
operator must independently verify the current protected binary and helper pins.
