# Independent API checkout and patch verification

This offline increment extends the [API Task adapter](AGENTS_LINEAR_ADAPTER.md).
It is not a live-runtime or host-acceptance record. The subscription path is
unchanged, and the protected Agents API host factory remains disabled.

## Data boundary implemented

`CheckoutVerifier` owns a fresh independent detached clone through the existing
`bridge_checkout.Checkout`, backed by the protected local mirror. It does not
run Git on a worker-controlled checkout, import candidate Python, or execute
candidate code. The shared coding lock/store must remain held.

Preparation reserves a new private attempt directory exclusively, validates a
separately protected enabled `VerificationPlan`, and records the exact Task/base
source tree. Source copies contain only tracked regular mode-0644 files, without
Git metadata, credential/config scaffolds, symlinks or executable modes. The
bounded source layout is at most 8,192 files, 1 MiB per file and 16 MiB total.
Unchanged binary files are preserved as inert bytes. This is an explicit supported
layout, not a promise that every repository layout is accepted.

A future trusted runtime supplies a canonical, uncompressed JSON/base64 full
source snapshot, limited to 24 MiB. No tar/zip extraction or worker-supplied shell
command is used. The export binds Task, attempt and the baseline tree digest.
The verifier requires a matching canonical API approval/attempt, shared launching
job, terminal completed cloud turn and independently verified cleanup before
parsing any export. Unknown execution/cleanup remains blocked.

The export describes a **proposed patch**, not proof of every historical action
inside a worker. All original files must be present with unchanged modes; new or
removed files, unsafe paths, duplicate JSON/path entries and changes outside the
Task allowlist are rejected. The independent checkout must remain unchanged
through preparation and staging.

Changed files must be UTF-8 text without NUL bytes. If a changed candidate lacks
only its final LF, the verifier appends one LF to an in-memory derivative. It
retains the complete original export before normalization and records raw and
derived hashes/lengths and the normalization flag. It does not normalize CRLF,
strip text, overwrite the raw export or charge for a repair call. A normalization
that only restores the original bytes does not create a fake substantive patch.

The ordinary narrow unified diff is built as inert data and independently checked
and applied by the existing `bridge_patch.validate_and_apply` in the clean clone.
Its base, size, exact changed paths, modes and resulting bytes must match. The
private stage binds that exact normalized candidate tree, patch, source export,
Task, API session/environment/turn, runtime and verification plan. A stage is not
`tests_passed`, completion, publication or permission to apply to main.

## Independent test boundary

The separately protected plan names the exact expected tests, runtime, test
image/acceptance and harness digest. `harness_sha256` is intended to bind the
fixed test protocol/profile and the full protected assertion policy, including
case inputs and expected results. Candidate/task instructions cannot choose a
command, discover tests or replace that policy. The only supported assertion
protocol is `isolated-candidate-v1`: assertion authority outside candidate code.
A generic candidate-importing unittest process or its exit code alone does not
establish that guarantee.

`finish` requires a separately protected test receipt matching the staged
candidate and plan. It checks concrete test attempt/container identity, exact
image/harness/acceptance, disabled network, every expected test's known
passed/failed result, complete streams, independent cleanup, and bounded time
and output. Skips, absent tests, unknown outcomes, identity drift and missing
receipts do not become a false terminal failure or success; they raise and leave
the API attempt held. A known failed assertion is separately represented as
`tests_passed=False` after verified completion/cleanup.

Only then does `finish` produce the existing `VerifiedResult`. The test receipt
and verified result are written privately and exclusively. Tests must have run
against the exact normalized bytes, not the raw unterminated variant. The
independent checkout is rechecked both before and after reading the receipt.

This module does not create a test receipt from a boolean, launch a test process,
forge runtime acceptance or publish a patch. Its tests deliberately mock the
protected receipt producer; those fixtures are not live test evidence.

## Runtime composition and remaining gates

The existing Linear store/approvals are root-protected; the existing Podman
runtime explicitly requires a nonroot identity. A fixed, root-owned/hash-pinned
helper under a preapproved nonroot UID is needed, with clean environment/groups,
closed unrelated descriptors and a finite data protocol. Application credentials
and SQLite remain with the supervisor. Only the restricted environment key may
reach the coding broker; no key may reach the independent test candidate.

The coding image's tmpfs workspace is lost after stop. A bounded opaque export
may be captured before stop, retained as untrusted bytes, and admitted only after
independent cleanup. Do not add a host bind mount or accept a live export merely
to avoid this ordering. Source transfer, broker identity, export and independent
test profile require exact installed-runtime acceptance; the old sentinel smoke
is not that acceptance.

The plan permits at most 60 seconds of independent-test lifecycle. The adapter's
outer verifier allowance is 90 seconds, leaving room for source staging and
receipt persistence; a concrete runner must reserve full stop/readback time and
must not nest the pilot's SIGALRM wrapper inside this outer bound. Exhaustion is
uncertain, never permission to skip cleanup or replay.

Offline checks use real local Git clones/apply/snapshots, fake API events and
fake protected test receipts. No candidate code, container, model, production
service or external API is executed. A dedicated integration fixture runs the
shared Linear dispatch through the real API controller and this real data-only
verifier, then final delivery and duplicate rejection. It does not establish
real provider, containment, test-runner or scheduled pickup acceptance.
