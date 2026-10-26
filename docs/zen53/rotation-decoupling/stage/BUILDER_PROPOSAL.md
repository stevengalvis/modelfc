# ZEN-53 fresh-stage builder: conditional operator proposal

This is an offline candidate, not authority to prepare a VPS stage or start a
scope. The predecessor archive SHA256
`7bd886a00a0f651ce367d6883c6892df5d8edf5cd8eca64def335dec43e32ef0`
and historical failed reports remain unchanged. Only disposable-root synthetic
tests have run. Do not reuse an existing or partial stage.

## Inputs and immutable prerequisites

An operator must first obtain and authenticate this candidate archive and its
`INVENTORY.json` from a separately approved publication. The SHA256 arguments
are external trust pins; the builder cannot authenticate its own package from
its contents. Its `recovery_contract.json` is the new zlib-only candidate derived from
retained recovery data (SHA256 `bc8f681b770a804f3cb044245865cb2ee7df47c95b9bcbd719620f9d498d41f4`).
The old recovery contract and its historical source observations remain in the
preceding archive, unchanged. See `ZLIB_REFRESH_PROVENANCE.json` for the
Ubuntu-signed package chain and the precise new zlib pin.
The three referenced runtime recovery `objects/<sha256>` and the 39 installed
sources must be present with exact contents, UID, GID and mode. The other
retained recovery evidence objects are pinned historical material but are not
read or certified by this stage-only builder. The two current project sources
`stage_launch_source.py` and `stage_pins_source.json` are packaged because the
older recovery table contains superseded versions for these paths; their
current SHA256 values are `14a8ec5d0b1eb043566ef6c59d3186fe6f06090cc1515bc65a6c43b838d3e29c`
and `b5346565221ae40199ff677ff767f1609833589a5ea6317ecb246964177ea7a4`.
The manifest and dependency pins change only to include that new diagnostic
pins file and the authenticated zlib 1:1.3.dfsg-3.1ubuntu2.3 member. The old
zlib digest remains historical evidence and is no longer an accepted current
source pin. No other runtime route changes.

The builder's real CLI requires root, a protected root-owned package and
recovery root, the pinned archive itself, a 32-hex fresh nonce, the approved
archive/inventory hashes, and verified effective originating-disk IDs. A
read-only preflight must establish those IDs and the full direct topology
before a host preparation is separately approved. The hardcoded real base is
`/var/lib`; no other real base or host path is approved. Package and recovery
inputs are read without following symlinks and with `O_NOATIME`; a denial
blocks. Individual and aggregate byte limits and a 180-second monotonic
source-read deadline apply. The deadline is a checked soft limit, not a
kernel-enforced interruption of a stalled read.

## Exact preparation effect if separately approved

The builder requires euid 0 and egid 0. It creates only fresh
`/var/lib/zen53-owner-tool-<nonce>` and
`/var/lib/zen53-owner-capture-<nonce>` root:root `0700` directories. The
tool tree contains exactly ten root:root `0444` runtime files and a private
root:root `0600` `LAUNCH_MANIFEST.json`. The capture tree contains exactly
`projection`, `capture_ownership_v3.py`, `host-evidence`, and
`controller-evidence`. The evidence directories are empty root-owned `0700`.
The projection has the exact 44 pinned regular files, nine symlinks and three
declared additions, with root:root `0555` directory ancestors and read-only
files. Tool files, collector and the persisted manifest are read back by
no-follow descriptors for exact bytes and metadata before reporting prepared.
The builder does not acquire production locks or create a scope. It
does not execute the manifest's argv, collector, Bubblewrap or Codex.

Exclusive no-follow creation rejects existing names, symlinks and stale
objects. On any failure after creation, new trees remain for review and must
be treated as consumed; the builder best-effort writes `CONSUMED_PARTIAL`.
An `ENOSPC` failure can prevent even that marker, so existence alone also
means nonreusable. No automatic removal, repair, chown or chmod of existing
objects occurs. Raw future evidence remains private; no GitHub post is
implemented.

The private launch manifest records source observations and an ordered
source-to-origin backing snapshot separately from the exact finite per-disk
I/O targets. It includes proposed limits (360 seconds, 50% CPU, 384 MiB RAM,
zero swap, 32 tasks, four finite `io.max` caps per originating disk and a
90-second schedule margin) and an argv **as data only**. Its canonical SHA256
seal detects accidental changes relative to a separately retained trusted
digest; it is not a signature against a hostile root operator. Subsequent
operator work must reauthenticate the package and recheck all source, scope,
mount, lock, schedule and identity predicates before any launch. A builder
PASS alone is no containment or measured I/O evidence.

## Conditional next approval and stop rules

First independently review the published exact bytes and current host facts:
protected input roots and file metadata, installed source pins, direct backing
mapping, source aliases, production quiescence and the installed controller
pins. If these prerequisites hold, a later approval could cover **one** fresh
stage construction under `/var/lib` and retention of its private manifest.
That is a host write and a root builder process, but no diagnostic process,
transient scope or lock acquisition. Stop on any mismatch or partial state.

Launching one bounded diagnostic would be a further, distinct approval. The
operator would have to construct the exact scope, confirm the effective
originating-disk `io.max` set and resource limits, hold both production locks,
verify schedule and service quiescence, run one pinned collector/probe, retain
private raw evidence, independently review sanitized results, and stop without
automatic retry. The trusted-owner assumption remains: root and service UIDs
1000/1001 cooperate; their writable host aliases and lock ancestors are not
made immutable by these checks. Actual systemd creation, namespace isolation,
cross-user behavior, physical throttling and eventual native Codex containment
remain unverified.
