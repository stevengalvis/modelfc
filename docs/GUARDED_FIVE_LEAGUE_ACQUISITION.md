# Guarded five-league acquisition foundation

Milestone 3 connects the offline planner and shared snapshot consumers to durable
private storage and the existing shared request budget. It is disabled by default.
There is no CLI, service, timer, calendar discovery, retry or fixture fallback.
The current production collector, its cadence, APIs and evidence remain unchanged.
Merging this source does not authorize installation or provider requests.

## Fixed acquisition contract

Only FanDuel and `GET /v4/odds-by-tournaments` are supported. Parameters are the
verified Lab shape: `tournamentIds`, singular `bookmaker=fanduel`, `language=en`,
`verbosity=3`. At most five unique IDs are serialized in registry order:
E1=18, E0=17, SP1=8, I1=23, MLS=242. Each league can be independently enabled.
The eligible window remains 21 through 27 hours before UTC kickoff, including MLS
UTC boundaries. The original fixture-level adapter still rejects this endpoint.

The coordinator consumes a previously verified local calendar, not a new discovery
request. It requires provider identity `oddspapi`, verification within six hours,
known tournament and participant IDs, and explicit arrays for every enabled league.
A missing array is an error; an empty array means a verified empty calendar.
E1 participant IDs and canonical names use the shared verified identity registry.
The operator authorizes the exact calendar and metadata SHA-256 fingerprints.
Calendar verification must include provider identity and kickoff provenance; the
hash protects the reviewed bytes, rather than proving their provider origin alone.

## Durable forecasts and private evidence

The operator must pre-provision one runtime-owned, mode-0700 private directory.
The root-owned authorization binds that directory and the existing state directory;
a caller cannot switch namespaces to evade claims or use a different budget.
Traversal uses descriptor-relative `O_PATH` and `O_NOFOLLOW`, so execute-only
parents do not require directory listing. Files must be regular, single-link,
owned by the expected identity and unchanged during reads. Private records are
mode 0600. Publication uses an unnamed inode, fsync, exclusive link and directory
fsync. Existing records are never overwritten; identical publication is idempotent
and conflicting publication fails closed. Storage requires Linux O_TMPFILE support.

Corner and BTTS forecasts are prepared independently from coherent locked history,
using the existing methods, versions and minimum-history gates. Only E1 has validated
models. Other leagues produce market inventory only. Cutoff excludes same-date and
future results. The latest prior result must be within 14 days. A failed corner gate
can still leave BTTS available. If neither E1 model is available, the run stops for
review before reservation. Forecasts preserve participant IDs, competition, kickoff,
cutoff, source fingerprints and exact existing model outputs. They are read back from
durable storage before request authorization. Retries after pre-request crashes reuse
frozen forecasts, without recomputing them from later market prices. Forecast keys include competition, tournament ID, fixture ID, kickoff and model.
A rescheduled fixture can receive a separate immutable forecast; its prior record
remains intact. Conflicting participants for the same fixture/kickoff cannot replace
an existing forecast.

The coordinator retains the bounded private response, its retrieval timestamp and
SHA-256, then creates one shared typed snapshot with metadata provenance. Every
known returned fixture must agree with calendar participant IDs, names and kickoff.
Unrequested tournaments fail closed. Incidental fixtures retain inventory but never
receive retrospective forecasts. Existing consumers produce independent private
corner probability and BTTS research records only when frozen_at < retrieved_at <
kickoff. Missing, inactive, stale, incomplete and unsupported coverage remain in the
shared snapshot. Nothing is published into current corner, BTTS, shadow or public
recommendation namespaces. Goal totals and first-half corners remain inventory only.

Responses are capped at 16 MiB; metadata remains capped at 8 MiB and 25,000 definitions.
Calendar input is capped at 2 MB and 500 fixtures per league. Private JSON envelopes
are capped at 24 MiB to accommodate base64 encoding of a 16 MiB response; this does
not raise the HTTP response limit. Evidence retention and disk provisioning require
separate operator review. Claims, forecasts, reservations and receipts must never
be deleted to re-enable acquisition.

## Authorization and accounting

`guarded_acquisition.run_once` does nothing when `authorization_path` is absent.
A future reviewed launcher must deliver the existing provider credential through
systemd LoadCredential and the existing protected credential handoff. No credential
is stored in plans, snapshots, diagnostics or receipts. This PR adds no launcher.

Authorization has an exact version-1 schema:

- `experiment_id`: unique lowercase alphanumeric/hyphen identifier, up to 64 characters.
- `issued_at`, `expires_at`: UTC timestamps, active now, maximum 24-hour lifetime.
- `state_dir`, `private_dir`: exact approved absolute directories.
- `calendar_sha256`, `metadata_sha256`: lowercase 64-character hashes.
- `enabled_competitions`: nonempty unique subset of the five league codes.
- `max_requests`: exactly 1 per authorization.
- `quota_observed_at`: independently checked account quota timestamp, within one hour.
- `quota_remaining`, `quota_floor`: positive integers; one request must leave the floor intact.
- `version`: exactly 1.

The original authorization is root-owned mode 0600 staging input for LoadCredential,
never a path passed to the runtime coordinator. The runtime accepts only a
root-owned mode-0440 systemd-delivered copy with an exact ACL: owner read, named modelfc-runtime
read, owning group none, mask read, other none. No other user/group grants are
accepted. This is separate from general file reading. The future launcher and
credential snapshot permissions require cross-user acceptance before activation.
Authorization and eligible windows are rechecked immediately before claiming work.

The existing prospective runner lock is acquired exclusively and nonblocking;
BUSY causes rejection without HTTP or reservation. Existing enrolled calendar-month
accounting and rollover remain authoritative, with the unchanged 180 monthly cap.
Only the narrowly reviewed TOURNAMENT_ODDS/one-request guard profile is added.
Reservation is durably saved before HTTP, and pacing/last-request accounting use
the same guard as the production collector. Failed calls are charged and never refunded.

The coordinator enforces the non-root modelfc-runtime UID before any authorized preflight.
A single atomic session claim covering every fixture/FanDuel/EARLY_24H obligation
precedes reservation. Recovery derives all completed obligations from those complete
session records, including interrupted runs and later authorizations. Private namespace
inventory is bounded to 20,000 records; exceeding that bound requires operator review.
Once claimed, an obligation cannot trigger a second request, even with a new plan.
A crash between claim and reservation may sacrifice coverage without consuming a
credit. A crash after reservation conservatively consumes a credit even if dispatch
cannot be proven. Neither case is retried automatically. Operators inspect immutable
claims, reservation receipts, shared control and provider usage before any follow-up.
HTTP failure receipts contain fixed safe codes only; URLs, bodies, headers and
exception text are excluded. SSH, provider and VPS acceptance has not been performed.

## Separate activation requirements

Before any installation or acquisition, separately review and authorize:

1. Exact merged release, protected marker and trusted launcher integration. No timer
   or routine near-kickoff capture is approved by this milestone.
2. A verified, sufficiently fresh calendar export, pinned metadata, coherent history,
   runtime-owned private storage, disk bounds and cross-user permission tests.
3. Root-installed authorization and systemd credential delivery, including the strict
   authorization snapshot ACL. Do not run the coordinator as root or broaden ACLs.
4. Shared enrolled budget and current provider quota, then mocked/captured-response
   acceptance on the physical release. Actual private Lab captures were not tested here.
5. A separately authorized bounded request, only after forecast persistence, immutable
   claims, shared locks, failure accounting and consumer isolation are independently
   verified. Production-path replacement and evidence promotion require later review.
