# Preparation API contract — local candidate, not installed

This candidate adds a fixed-map materializer, not a daemon or an executable
operator CLI. Nothing in this package launches native Codex, subprocesses,
services, mounts, provider calls or credentials. The retained historical
`prepare.py` must never be used as the materializer: it dynamically discovers
and repins host dependencies. Its bytes remain historical data only.

The new root-only `materialize.prepare` requires a separately approved, freshly
created stage whose path satisfies the bridge's existing `/tmp/zen53-...` rule.
The caller must authenticate this package and the existing private captured
snapshot through the pinned readers, match the reviewed snapshot digest, and
retain the original diagnostic records unchanged. Synthetic fixtures are not
valid preparation evidence. This package does not issue/retire approval records.

Before calling the API, a trusted preparer must exclusively create a root:root
0700 stage with exactly three children: root:root 0555 empty `empty-home` and
`empty-vartmp`, plus root:root 0700 `objects`. Observe the two real bind identities,
call the pure bridge with those identities, and exclusively seal the four source
objects required by its routes at root:root 0444, single-link. Recheck no-follow
path identity and mount topology: no mounts/aliases at or below these objects.
Do not copy synthetic identities or change existing source owners or modes.

The API rederives the bridge plan, checks sealed source bindings through the
existing finalizer, validates the pinned recovery map and all bundled hashes,
then writes the 122 exact manifest targets and ARTIFACT_SHA256.json. Installed
source files are opened only at their fixed resolved routes with no-follow
component walks; regular type, single-link owner/mode, bounded size, content
hash and descriptor/named stability must match. The two Codex sources additionally
match captured device/inode values. There is no dependency discovery, changed
hash acceptance, fallback library, or path substitution.

New runtime files retain their declared modes: 0444/0555, with the historical
explicit 0755 Bubblewrap exception. Nine declared symlinks are created as links,
never traversed for output writes. New directories are sealed 0555 except empty
`rootfs/root` at 0750 and evidence at 0700; stage remains 0700. New fixed empty
mountpoint/readiness/daemon directories are created, without mounting or changing
services. Source files and earlier stages are never chmodded, deleted or repaired.

MATERIALIZATION_RESERVED.json is exclusively created before target writes.
Failure leaves all partial output intact and prevents a second invocation in
that stage. There is no cleanup/retry path. Completion is written last, only after
byte/hash readback, directory sealing and final path/bind checks. Its state is
MATERIALIZED_NOT_RUNTIME_READY: it is not an execution grant, cleanup acceptance,
or evidence that host security admission or runtime probes passed.

The trusted preparer still independently checks complete stage membership,
directory identities/metadata and mount topology after return, seals a durable
private preparation report, and obtains exact-stage review before any proposed
native invocation. Arbitrary cooperating-root violations and blocked kernel IO
cannot be excluded by these filesystem checks; no hard wall-clock guarantee is
claimed. Runtime limits and admission policies are unchanged and not exercised.

## Provenance and pending facts

Recovery archive a0468ba1cc95fea86f0485595dba352a399a39d001d07193ad861e08cda2baf9
was reconstructed from all 35 published parts, verified as 236 regular members
against its published inventory, and its 56 bundled objects were independently
reconstructed by its offline provenance verifier. This package includes only
the pinned map, those exact 56 objects and their provenance reference. Thirteen
recovery tests passed locally; that is not the four-input external verifier.

All four pinned external archives were supplied and the existing verifier passed
external_verified=4 external_required=4, including exact source-excerpt checks.
The default verifier without supplied inputs still accurately reports 0/4.
This is byte/provenance replay, not a new signature authentication of the systemd
source .dsc. Current host source bytes have not been rechecked here.

The explicit zlib_amendment module reconciles only the authenticated zlib package
member recorded in NATIVE_ZLIB_PROVENANCE.json. Historical map and dependencies
stay unchanged. The derived manifest changes ten keys (the prior nine plus the
library target), retaining 122 targets, 43 dependencies and nine symlinks.
The dependency hash and runtime pins agree, and the library is still read from
its exact pinned host route, never a bundled fallback. Any other drift must stop
preparation and requires separately reviewed evidence, not discovery or repinning.

The prior 12-file lifecycle patch and inactive admission-wait work are excluded.
No genuine stage or native operation has been executed by these tests.
