# Mount visibility model: offline, not an admission check

`mount_visibility_prototype.py` is an isolated pure model; the bridge and
`storage_guard.py` do not import it. The standalone guard now has a separate,
self-contained candidate implementation of this approach, pending real-systemd
acceptance. The model demonstrates selecting an entry only when
a trusted mountpoint observation reports that entry's mount ID, while retaining
separately visible aliases. It rejects missing/unknown/inconsistent observations,
changed snapshots, noncanonical paths, and ambiguous basic inputs. It does not
verify mount permissions, source identities, credentials, cleanup or isolation.

`mount_observer_prototype.py` experimentally holds namespace descriptors, checks
the `/proc` filesystem type and current-thread identity, uses `openat2` with
in-root, no-symlink and no-magic-link resolution, reads the mount ID from thread
fdinfo and compares mountinfo and namespace identities. Unsupported
platforms or resolution errors block. It returns raw mount records and is not a
sanitized report, trusted observer, production check or guard integration.
The prototype caps mountpoints and checks a monotonic deadline between calls;
an individual blocking kernel call is not interrupted by that deadline.

`mount_guard_candidate.py` composes this view with the existing guard's mount,
source identity and working-directory checks in a standalone experiment. It
does not launch a child or participate in admission. Ignoring a covered entry
changes the guard's current behavior and is not yet proved equivalent or safe.

These checks reject an ordinary substituted filesystem; they do not authenticate
procfs against a privileged administrator, prevent replacement and restoration
between checks, or anchor all reads to the pinned descriptor. Before use, an
observer must establish trusted procfs and root provenance, keep
exclusive ownership of its descriptors (including against other threads), and
pin the service's mount, user and PID namespaces across observation and use.
It must bound elapsed time and work, account for path aliases and mount IDs,
and apply the **unchanged writable-mount and identity
policy** to every effective entry. Mountinfo parent IDs and propagation fields,
mount stacking, filesystem identity, inherited standard descriptors and access
through `/proc` or other processes need separate verification. A path-based ID
alone does not prove that a covered mount is unreachable by every other handle.

Two identical snapshots do not rule out a mount change followed by a revert,
mount-ID reuse, or later host-to-service propagation. The trust model must keep
privileged mount changes and competing worker launches controlled throughout the
entire task; unprivileged namespace creation and capabilities must be addressed
without breaking required nested tools. Root and other authorized privileged
administrators remain trusted. If these assumptions cannot be established, block.
`PrivateMounts=yes` can still receive host mount events. A private propagation
setting is a candidate for a separately reviewed test, not proof by itself, and
may keep host filesystems busy. Any worker-accessible mount, socket, file
descriptor or namespace escape capable of writing outside the bounded storage
is relevant; defending against a malicious host root is not the goal.

The candidate implements the observation within the standalone `python3 -I`
guard, avoiding a new import path. The launcher's existing guard identity binds
file metadata, not a cryptographic digest; exact installation bytes still require
independent verification. Observation descriptors close before guard evaluation;
ordinary host mount propagation can invalidate the view before or after that
evaluation. A production candidate must make the mount topology stable for the
worker lifetime and verify its propagation mode, not infer safety from two reads.

Before any deployment, independently review the actual kernel observer and
guard changes, exercise stacked and covered mounts and adversarial aliases in a
disposable real namespace, and test the exact complete systemd service on the
target host under separate authorization. A passing model or snapshot is not
host acceptance. Prior fixture state, unit and cleanup uncertainty are unchanged.

## Offline property candidate for PR 245 diagnostic

`storage_property_candidate.py` builds an **unwired diagnostic candidate**, not a
change to the bridge. It replaces `ProtectSystem=strict` and explicit `/dev /sys`
read-only paths with `ReadOnlyPaths=/ /proc/sys`, and explicitly lists `/proc`
with the existing writable API exceptions. All private-propagation, bounded
workspace/runtime, credential and shared-memory properties remain as before.
The original effective-mount guard remains mandatory and unchanged.

The hypothesis is that root-recursive read-only setup without systemd's implicit
strict-mode `/dev`, `/sys`, `/home`, `/run/user` and `/root` writable exceptions
will cover the observed classes together; `/proc` remains a deliberate writable
API exception, with `/proc/sys` read-only. Systemd's mount ordering and explicit
submount exceptions determine actual behavior. This has not been run in systemd,
does not establish equivalence or resolve any prior fixture, and can affect proc,
device, socket, PTY, mqueue and Codex behavior. Read-only device mounts do not
by themselves prohibit device I/O or access to UNIX sockets. Unknown or writable
mounts must still block under the existing guard.

The fake-input diagnostic of that first candidate reported 15 effective writable
entries in `/dev`, `/run` and `/sys`, including a writable cgroup descendant
beneath a read-only parent. Its private evidence analysis is in PR 246 comment
6613900396. `candidate_with_api_binds()` is a second **unwired hypothesis**: it
adds explicit recursive read-only self-binds for `/dev`, `/run`, `/sys`, and
`/sys/fs/cgroup`, while retaining the existing explicit writable API and bounded
storage exceptions. The explicit cgroup bind addresses the observed descendant;
the guard must still check every effective descendant. Prior self-binds produced
stacks under an older guard; the mount-ID observer now selects an effective mount,
but the combined configuration has not been tested. Systemd can skip or fail
submount remounts, so a requested read-only bind is not evidence of an effective
read-only tree. Read-only `/run` and API mounts can affect dependencies and do
not themselves block UNIX socket or device I/O.

PR 246 comment 6614442556 records one writable per-service cgroup alias after
that second candidate. `candidate_with_unit_cgroup()` is a third **unwired
hypothesis**: it adds a non-optional read-only recursive self-bind at
`/sys/fs/cgroup/system.slice/<unit>`, accepting only the backend's canonical
`modelfc-bounded-<32 lowercase hex>.service` name. The caller must use the same
backend-generated unit for the property and service, never task-supplied input.
Before trying it, compare the retained protected topology with that exact target
template; the public report does not disclose enough to prove they match. If the
path is absent when systemd sets up the service, setup must fail, not skip the
bind. The manager may construct, cover or stack the alias in a different order;
the unchanged effective-mount guard must still reject a writable child. This is
not evidence of service startup, actual worker behavior or prior cleanup.

`candidate_with_evidence_cgroup()` is an unwired follow-up that accepts two
trusted-operator-normalized records, verifies that distinct units and invocations
have the same cgroup-v2 device, writable protection flags and a single identical
safe target/root component, then constructs a mandatory bind for a fresh canonical
backend unit. It does not read files, authenticate reports, check their hashes,
or establish source timing. The operator must first verify the two retained
root-protected reports against their recorded hashes and invocation bindings,
confirm their boot and independent origin, then normalize only their actual
effective records; tasks must not supply them.
If either record does not match the exact expected target/root shape, stop rather
than adjusting or guessing it. Do not log returned properties publicly: systemd
properties and process arguments may expose the component to local observers.
An absent source must cause setup failure, not be made optional or created by
the helper. Check the actual effective mount and complete guard in one separately
authorized diagnostic. This function does not prove that the source exists when
systemd sets up the unit or that a later mount will not cover the bind.

After separate authorization, compare this candidate in one bounded non-model
systemd diagnostic using fake inputs and an invocation-bound root observer. Check
all effective mount identities/options and the complete guard, pipes, private
propagation, storage/credential bindings, `/dev/shm` limits, PTY and cgroup
cleanup. If the guard still blocks, retain a bounded root-protected exact list of
the remaining effective mount paths, types and relationships; report only
sanitized categories publicly. No worker, old fixture, receipt, reset, real
credential or model is required. A diagnostic pass is not Codex acceptance.
