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

For this diagnostic only, a fixed post-guard, post-drop gate waits for a single
supervisor release byte before executing Codex. The supervisor binds the live
gate to the manager PID, UID, cgroup, executable, exact arguments, start time
and invocation, checks the private network and `/run`, rechecks the gate, and
durably records its observations before releasing it. Missing, malformed, timed
out or changed state blocks release and uses the existing containment cleanup.
The service's `Type=exec` active state alone is not gate readiness. This ordering
has only offline coverage; actual systemd pipe and namespace behavior is unknown.

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
production network behavior. A residual socket-risk decision must be explicit;
real credentials must be inaccessible. The gate does not inventory sockets and
no socket communication is tested. Unless separately authorized, conduct at most
one normal case; an interrupted case requires its own authorization. The profile is not
established equivalent to app-server. Before any host execution, review the exact
binary, fake auth schema, socket-relay assumptions, supervisor and artifacts, and
verify the current protected binary/helper pins.

## Credential scope and test identity

The diagnostic requires the existing `modelfc-containment-test` account, a
different UID from `zeno-worker`, and a root-protected hash-pinned scope and
metadata-only evidence. The scope lists the real worker home and every
operator-identified credential-bearing directory by device, inode, owner, mode
and change time. It must attest complete scope, resolved aliases, absence of
other same-UID processes and cleared keyrings, bound to the current boot and a
recent observation. The test account's configured home must be absent, or be a
root-owned mode-0700 directory with protected ancestors and no extended access
ACL. If present it must be included with pinned metadata in the scope, so the
dropped-identity gate checks it along with the other stores. Unsupported or
uncertain ACL metadata blocks the test. No home entries are listed or read. Hashes
bind those assertions; the code cannot
establish their truth, discover an unknown store, or prove alias absence. An
unknown, unreviewed or incomplete scope must block the diagnostic. No credential
contents are read by the validator.

Before release the fixed gate checks its real/effective/saved/filesystem UID
and GID, empty supplementary groups, zero capabilities, no-new-privileges and
pipe descriptors. Linux `faccessat2` read, write and search checks, with a
successful search control, must each return `EACCES` for every scoped directory.
The gate also attempts to open each scoped directory without following the final
symlink; it reads no directory entries or credential contents, closes any
unexpectedly opened descriptor and blocks. Unsupported calls, unexpected errors,
accessible stores or a changed scope block release. The test account
has different UID/ACL semantics from the production worker. The candidate and
operator scope have not been validated on the host.

The protected scope JSON has exactly these fields (placeholders are not valid
inputs):

```json
{"version":1,"test_username":"modelfc-containment-test","test_uid":983,"test_gid":983,"production_uid":1003,"stores":[{"path":"/home/zeno-worker","device":1,"inode":1,"uid":1003,"gid":1003,"mode":448,"ctime_ns":1}],"complete":true,"aliases_resolved":true,"same_uid_processes_absent":true,"keyrings_clear":true,"evidence_sha256":"<sha256>","boot_id":"<boot-id>","observed_boottime_ns":1}
```

UIDs, GIDs, path, inode, mode and clock values are examples only; independently
verify them. Store the scope and evidence as separate root-protected files,
pin both hashes, and pass the scope path/hash and evidence path to `run_candidate`.
The evidence must substantiate the assertions without containing credential
contents. A stale, wrong-boot or changed scope blocks the diagnostic.

## Unwired credential isolation experiment

`codex_credential_isolation_candidate.py` proposes a mode-000, bounded,
read-only tmpfs over a canonical `/home/<worker>` path, plus a metadata-only
observer for that mount in a waiting gate's namespace. It does not list the
home contents and requires the resolved mount ID to verify as a bounded tmpfs. It is
**not wired into the service or gate** and is not a credential-isolation proof.
Before integration, a trusted operator must establish the actual account home,
credential locations outside it, and accessible hard-link, bind-mount,
process-descriptor or other aliases without reading credential contents. If that
scope or worker privilege/descriptor state cannot be established, Codex must
not be released. A test-only home mask cannot establish that unknown credentials
or aliases elsewhere are inaccessible.
