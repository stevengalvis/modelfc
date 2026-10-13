# Calendar retention without consumer activation

This producer-only change is based on main ce8a070 and reuses the reviewed private
`odds_calendar` publication format from integration PR #139 (d12054d). It does not
merge or rewrite that integration branch. Later integration must retain the failure
policy and capacity guard here when resolving the shared producer/module changes.
No collector gate, metadata cache, new endpoint, UI, scheduler, provider call,
credential, unit, permission grant or deployment change is included.

The already-scheduled forecast runner retains the validated original E1
football-data.org season bundle after its existing fetch/provenance checks and
before the no-eligible return, under the existing shared prospective runner lock.
`payload` round-trips the exact UTF-8 HTTP body, including whitespace. The envelope
already carries source identity, actual response retrieval time, and explicit team
bindings; its validated payload SHA-256 and bundle SHA-256 are recorded in the run
receipt. No timestamp is synthesized from the run time, file mtime or later check.

The runtime-owned `odds-calendar/` directory must be mode 0700. Each immutable
`<bundle_sha256>.json` (0600) wraps the unmodified bundle in `raw`; an atomically
replaced `latest.json` (0600) selects it. This is compatible with PR #139's retained
calendar reader. File/directory owner, mode, link, size and hash checks reject unsafe
or conflicting evidence. Exact replay preserves bytes and mtimes. Backwards time
and different bytes at the same retrieval time are rejected. A shared lock serializes
publishers; this module must not be called as an unlocked alternative writer.

## Failure policy and storage tradeoff

Calendar retention is best-effort **only for the existing forecast producer**.
Receipt `calendar_retention.status` is NOT_ATTEMPTED before validated evidence,
RETAINED only after publication and directory fsync, or UNAVAILABLE on storage or
validation failure. Other receipt fields keep their existing semantics. Provenance
fields on UNAVAILABLE describe the attempted input, not a durable artifact.
Retention errors do not cause retries, new requests, a model/history bypass, or a
forecast rejection. Existing forecast/receipt storage failures still fail normally;
this change cannot make forecasts succeed on a genuinely full shared filesystem.
Exception details are not exposed. Consumers remain absent/off, and future consumers
must independently validate bytes, provenance and current freshness; they must never
interpret a successful forecast as evidence that retention succeeded.

Before each new publication, available blocks must cover the serialized record,
4096 bytes for the pin, and a 64 MiB floor; at least 18 free inodes are required.
A low/unknown capacity check skips retention. This is a conservative guard, not a
reservation or guarantee against concurrent writers, quota limits or later I/O
failure. There is no deletion, automatic archival policy or hidden collection. Each
ordinary new retrieval can add a new immutable bundle, even with unchanged match
content. At the floor, evidence becomes unavailable/stale until separately approved
storage remediation. Operator acceptance must measure actual size, schedule and
headroom; the floor is not a claim about sufficient production forecast capacity.

Before pin replacement, failures leave the old pin and all existing records intact;
a completely written unreferenced record can remain for replay. A crash/fsync error
after atomic pin replacement can leave a complete visible new pin with durability
unconfirmed; receipt stays UNAVAILABLE. A subsequent exact replay fsyncs both parent
and evidence directory before reporting RETAINED. Partial records are never selected.
A corrupt/unsafe old pin is not silently repaired. Its failure is surfaced while
forecasting may continue with the independently validated in-memory response.

## Required disposable acceptance before deployment approval

Use the pinned candidate in a disposable copy of runtime state under an equivalent
runtime UID/unit filesystem boundary; deny provider networking and production paths.
Supply recorded synthetic transport through the actual command entrypoint. Verify
0700/0600 ownership, create/link/rename/fsync permission, exact replay, old-record
preservation, shared-runner exclusion, eligible/no-eligible receipt behavior, and
failure at write/link/pin/fsync under denied permissions and exhausted space/inodes.
Do not fill the production filesystem: use a bounded disposable filesystem. Report
injected failures separately from real filesystem tests. Measure representative
record size and capacity; confirm the API identity cannot read private calendars.
No host permission widening or installation is authorized by this document.

After independent acceptance and explicit staged deployment approval, use only the
next ordinary forecast timer run. Read its receipt and retained bytes/hash/time,
including a NO_ELIGIBLE_FIXTURES run. Copy that genuine artifact into isolated
consumer acceptance for PRs #132/#138. Do not invoke an extra fetch to accelerate
acceptance. Consumer flags stay off until separately accepted and approved.

## Inspected follow-ups, not implemented

OddsPapi's ordinary markets response is available in memory in the existing provider
path. A separate change could retain original response bytes and the actual response
retrieval time there on a future scheduled request, then perform offline metadata
import. Existing archives without verified original times cannot bootstrap provenance.

The CSV refresher's later status/check timestamp is not historical body acquisition
time. A separate change could capture immutable response versions and actual receipt
provenance on future ordinary successful body fetches. A 304/check/copy/mtime must
not manufacture or reset original retrieval time. New retrieval provenance does not
make old match content current. No metadata/CSV capture or extra polling is added here.
