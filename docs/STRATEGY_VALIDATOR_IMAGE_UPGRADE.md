# Reproducible ZEN-48 validator image and bounded transition

Preparation only. No production installation, image/configuration/cache change,
merge or deployment is authorized by this document. Both PRs remain draft.

## Immutable build inputs

Target: CPython 3.12.12, Debian bookworm, Linux amd64. Official Python base platform
manifest `docker.io/library/python@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c`,
local base image ID `sha256:9cb180cb36bd45dc786105b834d3cb5f3b1d69a6a63c79ca10fc64f108aa649a`.
The floating discovery tag was used only to select/review this immutable base;
the recipe contains the digest and cannot accept a floating build argument.

The strategy path imports Pydantic via `btts_market_data`, `btts_model` and
`btts_research`. Its complete Linux dependency closure is:

| Package | Version | Wheel SHA-256 |
| --- | --- | --- |
| annotated-types | 0.8.0 | `f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0` |
| pydantic | 2.13.5 | `346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73` |
| pydantic-core | 2.46.5 | `0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2` |
| typing-extensions | 4.16.0 | `481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8` |
| typing-inspection | 0.4.4 | `65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147` |

Versions/hashes reuse the established deployment lock and were checked against
official [PyPI Pydantic metadata](https://pypi.org/pypi/pydantic/2.13.5/json) and
each dependency's versioned PyPI JSON metadata. Full URLs, wheel filenames,
dependency declarations and hashes are in `ops/vps/validator-image-inputs.json`.
Pydantic's email/Windows timezone extras are not requested. No FastAPI/HTTP client
dependency is needed by this strategy path; actual complete execution verifies
the closure rather than stopping at the first successful import.

`requirements-validator.lock` uses exact versions, `--require-hashes` and binary
wheels only. `prepare_validator_image.py` verifies input bytes before creating a
new bounded context. It accepts only local verified wheels or explicitly enabled
official-PyPI downloads. The multi-stage recipe installs wheels into a fresh build
stage and copies those newly installed packages into the final pinned base. It
never copies a host virtual environment/site-packages directory. Build execution
has `--network=none --pull=never`; no downloads/install occur at validation runtime.

```text
python3 -B ops/vps/prepare_validator_image.py --output <new-context> \
  --wheelhouse <verified-development-wheelhouse>
<isolated-rootless-podman> build --network=none --pull=never \
  --platform linux/amd64 --timestamp 1792108800 --identity-label=false \
  --no-cache --tag localhost/zen48-candidate-dev <new-context>
```

The immutable base must already exist in that private development store. Do not
use an active production store/tag/configuration. `--download` is an alternative
to the wheelhouse only during explicitly authorized build preparation.

Buildah 1.33.7 / Podman 4.9.3, normalized context/package mtimes and disabled
bytecode generation produced the **same image ID in two independent no-cache
builds**:
`sha256:6e632bc0eba8d02a188d050ccd7b59552b2c1b3e78217982026e976120f2a7af`.
`ops/vps/validator-image-build.json` records recipe/lock/harness and wheel hashes,
base, platform and result. Image ID means the local immutable configuration ID
used by the controller, not a tag, registry manifest digest or archive checksum.
Retain the local OCI archive; load/inspect must reproduce that exact ID. No image
was published to an external registry. The former site-packages-copy fixture is
historical diagnostic evidence and must never be promoted.

## Review-required future activation sequence

1. Separately approve both final heads, this exact image ID/manifest, and the
   coordinated transition. Verify installed predecessor hashes/metadata, current
   main, both source heads, exact-head CI, host identity, existing access and
   effective consumer/deployment coordination. Drain old runs/workers. Keep
   serving application and unrelated jobs intact. Flags/empty queues alone do not
   establish holds. No change to credentials, permissions or security is implied.
2. Stage the exact reviewed installer and identical PR171/PR172 five-file payload.
   Load the reviewed candidate through the existing authorized staging route into
   the validator's store, inspect its exact ID/platform, and confirm the old image
   `sha256:3ea9d3919aa47eef62861ec31e2464c3dcbf9353a1ccb327a37b72f96980d1b6`
   is still available. Do not overwrite an active tag, remove the old image, or
   fetch dependencies at runtime. Loading into production needs new approval.
3. Under the existing validation lock, verify unchanged canonical main and old
   payload/image/configuration. Durably back up five files, original configuration
   bytes/metadata and the bounded cache; fsync the backup and parent before any
   mutation. Persist the recovery journal, quarantine old cache, install the
   journal-aware controller first, install the complete matching payload, and
   atomically change **only** the configuration's image value. Preserve all other
   values and file ownership/modes. Unexpected configuration ACLs/xattrs/labels
   stop before mutation; this bounded procedure does not silently discard them. Verify complete payload, new configuration
   bytes/binding, both image IDs and cache absence before clearing the journal.
4. Retain effective consumer holds and run documented local plus dedicated
   pinned-SSH PR-head/current-main acceptance, including strategy PASS, cleanup,
   zero-provider, repeat/cache and stale-SHA checks. Compare actual installed
   revision/image/configuration binding to reviewed expectations. A development
   container PASS is not this installed acceptance.
5. Only with new merge/deployment approval and every premerge check PASS, reverify
   refs, release any competing deployment lock, mark only PR171 ready and use the
   normal exact-head merge process. PR172 stays draft/unmerged/unretargeted until
   independently authorized otherwise. Fresh release validation must bind the
   actual merge SHA to the installed payload/image/configuration; follow the
   automatic pipeline, environment gates, serving SHA and health separately.

## Recovery requirements

Before main changes, an installation/acceptance failure restores original five
files, **exact original configuration bytes and metadata**, original cache or
absence, and its old image binding. Validate every backup before any restore.
The new controller is restored last, after other modules/configuration/cache,
so its journal guard remains effective through recovery. Verify all old hashes,
configuration/cache metadata, old image availability and unchanged canonical main
before clearing the journal or resuming old consumers.

Process death leaves a durable marker blocking new controller execution and cache
reuse. Resume only reviewed recovery from its recorded backup. A crash during
atomic journal publication may leave a second root-only hardlink to that same
journal inode; it contains only backup identity and manifest hash. This does not
permit candidate writes. Existing old controllers predate the marker, so consumer
holds remain required until complete verified recovery. Unrecognized/corrupt
journals, changed configuration, missing old image or unavailable/advanced main
are recovery blockers; no partial state is success.

After main changes, **do not perform a host-only rollback**. Preserve the new
payload/image, backup and evidence, inspect actual serving release/pipeline state,
and obtain separate approval for coordinated repair or a normal consumer revert
plus matched host/configuration/cache recovery. The premerge rollback refuses a
main different from its recorded tip. Even after a separately approved consumer
revert, do not bypass that check: a separately reviewed recovery artifact must
verify the reverted five-file consumer and actual current main before recovery. Never reuse stale evidence, force a gate,
rewrite main, or execute an unreviewed revert.
