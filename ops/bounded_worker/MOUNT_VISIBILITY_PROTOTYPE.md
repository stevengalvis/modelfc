# Offline property candidate for PR 245 diagnostic

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
stacks under an older guard; the standalone guard candidate selects an effective mount,
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
