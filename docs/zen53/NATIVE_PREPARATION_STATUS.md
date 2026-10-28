# Native preparation candidate: local only, not launch-ready

The current local revision adds the versioned
[provisional inherited-proc proof](native-compatibility/PROVISIONAL_PROTOCOL.md).
It is not published or run. Previously published preparations and failed
attempts remain immutable. Fresh-proc compatibility is explicitly unsupported
in this revision; no missing observation is accepted as a denial.

This source snapshot integrates the reviewed v4 ownership bridge, fixed-map
materialization, deterministic packaging and offline repository tests. No VPS
stage, native execution or installed change of this revision has occurred. The lifecycle patch
and PR215 admission work are separate.

## Explicit zlib reconciliation completed offline

The operational bridge now applies a separately pinned, tested one-library
amendment for `/usr/lib/x86_64-linux-gnu/libz.so.1.3`: historical SHA256
`86200da370f20476a2507e9097a789b5ef97269b4ca8d5e164ad82dab9d99892` becomes
`f61677c7bdb7c2aa2a243ea781bfd16afcb361cede7b4ef76127a9d8eb9a5242`.
See [the exact proof](native-compatibility/NATIVE_ZLIB_PROVENANCE.json).

The retained October26 Ubuntu InRelease signature was independently rechecked
against the already-pinned Ubuntu archive key. Its Packages.xz hash, zlib1g amd64
version 1:1.3.dfsg-3.1ubuntu2.3 stanza, archive hash and regular 113000-byte library
member all verified. The signed envelope is newer than the prior collector's
proof, but the package/member identity is identical. No key, trust source,
installed source ownership/mode, symlink, policy or runtime limit changed.
Historical dependency/map bytes remain intact; explicit code derives current
hashes and rejects stale, already-amended or foreign inputs. No library bytes
are bundled as a fallback. Host preparation must still match the actual file.

The four exact external inputs have now been supplied to the existing verifier:
`external_verified=4 external_required=4`. This checks archive identities and
vendor excerpts against the pinned .dsc/tar inputs; it is not a claim of new
OpenPGP authentication of the historical systemd .dsc or current VPS acceptance.
The zlib signed-index replay is a separate, actually performed check.

No known installed-library hash mismatch remains between this candidate and the
collector successor for their shared installed-source routes. The native
reporter and launcher still have their separately declared candidate changes. Current host routes, stage seeding,
mount topology and exact-stage preparation still require authorized host checks.
No new ownership capture is needed if the retained authenticated snapshot still
matches its reviewed digest and the current source identities remain unchanged.

## Delivery and test entrypoints

`python3 -B docs/zen53/package_native_compatibility.py` verifies the source
inventory and reports deterministic archive bytes/hash; optional `--output`
writes a new archive exclusively. It performs no source acquisition or execution.

`python3 -B -m unittest -q tests.test_zen53_native_preparation` runs the complete
package suite, strict inventory verification and packaging/corruption checks.
The materializer's [preparation contract](native-compatibility/PREPARATION_CONTRACT.md)
defines its root-only API and required separately reviewed seeding/topology checks.
There is deliberately no turnkey host or native-execution CLI in this candidate.

Publication, host preparation, and native smoke execution remain separate actions.
All four pinned external inputs were replayed offline. This does not replace
current host verification or authorize native execution.
