# Local conditional inherited-proc proof, version 1

This revision is not published, installed or executed. Published a64d152 and
its consumed stages remain immutable. It changes the diagnostic acceptance
path only after complete evidence; it does not change isolation settings,
limits, locks, cleanup hold or recovery behavior. It is not coding-agent
containment acceptance or a proven fix on the VPS.

## Security property and scope

The claim is that all seven correctly addressed, existing targets of the same
live outer process were inaccessible from the fixed reporter. It is not a
claim about which kernel branch produced ENOENT. It requires authenticated,
pinned launcher/native/reporter sources and the existing cooperative-host
assumption. A nonce or digest alone does not authenticate arbitrary malicious
reporter assertions. Before/after checks do not defeat privileged concurrent
mount or process manipulation.

This candidate supports the observed inherited-proc route only. The old fresh
proc branch is rejected explicitly, not silently treated as inherited. Restoring
fresh-proc compatibility needs its separate namespace-addressing proof; the
same-superblock criterion must not be applied to a fresh procfs instance.

## Ordered protocol

Before the final existing release-lease check and native fork, the launcher holds
a self pidfd, procfs root, numeric process directory opened relative to that root,
and metadata-only O_PATH references to root, cwd, fd/0, fd/1, fd/2, ns/mnt and
ns/user. The numeric directory must match the already pinned process descriptor;
PID/start/invocation/credentials must match the provisioned launcher and getpid.
All seven targets must exist. New descriptors are non-inheritable and closed on
failure; the child retains the existing close-range rule and resource ceiling.

The reporter opens its actual procfs root with no-follow semantics, binds named
and held root identity to mountinfo, and rejects overlays at or beneath the
numeric outer-process path. It performs exactly the seven original O_PATH
access probes relative to that root. Any successful open or error other than
EPERM/EACCES/ENOENT fails immediately. ENOENT is merely a provisional outcome.
The reporter checks topology stability, closes its observation descriptor, then
performs the unchanged inherited-FD inventory and final topology check. Only
then can its completion flags become true.

Exactly one canonical ZEN53_PROVISIONAL_V1 JSON frame is permitted, at most 8192
bytes within the existing 16384-byte native output budget. The parser rejects
duplicate keys, unknown fields, non-integer numeric types, unsupported outcomes,
missing or duplicate probes, malformed status values, truncation and duplicates.
Numeric status fields are normalized from real kernel tab-separated formatting.
The legitimate outer_proc_denied value is provisional, never forged into 1.
Only a schema-valid frame bypasses the general fatal-text detector; the existing
single exact PATH-alias warning is the only other permitted native output.

Reporter exit zero alone is insufficient. After child exit zero and EOF, the
launcher rechecks the same live process and all positive target references,
procfs-root identity, mount policy and namespace bindings. The reporter and
outer procfs roots must identify the same held instance/root, fixing numeric PID
addressing and preventing device-number reuse. Inner namespaces remain distinct,
credential maps remain narrow and all prior privilege/report checks hold.
Causal execution order brackets the probes; cross-namespace clocks are not used.

The launcher emits a structured certificate bound to exact frame bytes and the
reference snapshot. The supervisor independently repeats the pure verification,
checks unique ordered finish/attestation/completion records and compares identity,
nonce and provision digest against its own transaction. All existing helper
transition, privilege, bookkeeping, resource, production and cleanup checks remain.
Malformed or unavailable evidence produces failure, never inferred success.

## Packaging and validation

Historical/candidate source inputs are preserved. The bridge deterministically
injects the reviewed definitions and exact-site changes into generated launcher,
reporter and supervisor bytes; existing pins, manifest and sealed-object routes
derive their hashes normally. No dependency route, host-library hash, symlink,
service policy or mount allowance changes. Generation rejects repeated or
unexpected source sites. No old stage may be rebuilt in place.

Fixtures cover positive correlation, wrong identities/namespaces/procfs,
dead/reused processes, missing/replaced targets, overlays, topology drift,
successful access, malformed/conflicting frames, stale transaction binding,
output completion, descriptor cleanup and actual tab-separated status formatting.
These tests are synthetic. Cloud UID-map creation is unavailable and privileged
chroot cannot run here; installed-kernel and native behavior are not established.
