# Unwired no-model sandbox candidate

This offline candidate has no command-line entrypoint and is **not ready for a
host run**. It proposes a fixed direct `codex sandbox` command and a test worker for the
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
Before launch it retains a separate exclusive root-owned record binding its
input-version constant, the expected content hashes and the original creation
receipt digest. Syntax does not prove Codex
accepts the fake auth schema. Earlier fixtures are never rewritten.

The candidate's supervisor helpers bind an untrusted namespace-local PID to a
unique host process in the exact cgroup using UID, command, executable, PID
namespace and start time. `run_candidate` invokes the existing storage launcher
with the prepared baseline and requires independent cleanup, reinspection and
unchanged snapshots for one case. The code and its offline mocks do not establish
real CLI, systemd or kernel behavior. Checking the `systemd-run` client alone is
not proof that the probe is alive. Preserve evidence and keep the bridge paused.

The candidate omits managed configuration: the pinned CLI can perform cloud
configuration bootstrap when it is included. It adds `PrivateNetwork=yes` only
to the test unit, records that property separately from the production storage
properties hash, and checks the live unit's manager and network namespace views.
The diagnostic also substitutes an empty read-only `/run` tmpfs for the host
`/run` bind and separately records and checks its resolved mount ID and flags.
Repeated tmpfs settings must preserve the existing `/dev/shm`; the unchanged guard
still validates it. Neither change has been tested on the host, and an empty
`/run` may break Codex or a required helper. Do not bind host sockets back in to
make the test pass.

These changes do not block every filesystem-path UNIX socket relay or prove
production network behavior. The user condition that accessible relays be
resolved before the fixture remains in force, including paths and aliases outside
`/run`; no network or socket communication is tested. The profile is not
established equivalent to app-server. Before any host execution, review the exact
binary, fake auth schema, socket-relay assumptions, supervisor and artifacts, and
verify the current protected binary/helper pins.
