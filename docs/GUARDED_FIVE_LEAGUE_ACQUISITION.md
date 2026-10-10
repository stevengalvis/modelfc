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
future results. The latest prior result must be within 14 days unless the separately
authorized independent E1 calendar proves completeness (see below). A failed corner gate
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

## Calendar-aware E1 history freshness

The age-only fallback is unchanged: without an authorized completeness calendar,
prior results must be within 14 days. A break in play can now permit older E1
history **only** after an independent, complete results calendar proves that every
finished match before the forecast cutoff is installed. This never lowers the
five-observation venue minimum, BTTS/corner cohort rules or model parameters.
Other leagues still have no supported models. The existing production collector,
refresh health checks and history files are unchanged.

The upcoming OddsPapi acquisition calendar is not completion evidence. The optional
proof uses a saved, independently reviewed **football-data.org v4 ELC full-season
matches response**; it does not replace Football-Data.co.uk historical model inputs.
The [official competition contract](https://docs.football-data.org/general/v4/competition.html)
describes the season filter, fixture IDs, result counts and finished matches.
[Match statuses](https://docs.football-data.org/general/v4/match.html) distinguish
finished, scheduled, postponed and cancelled fixtures. No API client or calendar
acquisition is added, and no source request is made by this integration.

The bounded bundle (maximum 2,000,000 bytes) has exactly:

- `version`: integer 1.
- `source`: `football-data.org`.
- `retrieved_at`: independently verified UTC retrieval timestamp, at most six hours old.
- `team_bindings`: the 24 season participants, each with `source_team_id`, exact
  `source_name` and exact canonical `history_name` from the shared E1 registry.
- `payload`: the original response text, retained verbatim rather than reconstructed
  market or result objects. Its UTF-8 bytes receive their own SHA-256.

The reviewing operator must verify the original response's independent source,
season, country/competition and all source team IDs/names against historical
identities. These IDs are **not** OddsPapi IDs. No fuzzy aliases or global mappings
are accepted. The bundle fingerprint protects reviewed bytes; a label, timestamp
or hash by itself does not authenticate a feed. Do not manufacture a calendar,
remove matches, invent mappings, or relabel an OddsPapi schedule to authorize a run.

The parser accepts only the current July-boundary ELC regular season with no
status/date/matchday filtering. It checks count/played consistency, unique fixture
IDs, consistent competition IDs and the full ordered-pair matrix for all 24
verified participants (552 fixtures). Truncated calendars cannot establish
completeness even if their advertised count is self-consistent. Playoffs, unknown
statuses, incomplete schedules and past unresolved scheduled/in-play fixtures fail
closed. An explicit postponed/cancelled fixture requires no historical result;
an installed result for that fixture is a contradiction. A scheduled kickoff
alone never establishes completion. This proof is unavailable beyond the last
regular-season fixture date; no playoff completeness is inferred.

Finished fixture IDs join one-to-one through exact ordered canonical teams and
**Europe/London match date** to the CSV's competition/date/home/away identity.
Final scores must agree. Missing, extra, ambiguous or un-covered current-season
results reject the proof. The existing completed-corner cohort is preserved;
missing corners are never fabricated. Same-date and future results are excluded
from both completeness comparison and frozen model inputs.

### Offline preparation and acceptance

Use an already available, authentic independent response and verified bindings.
If neither exists, the override remains unavailable; this PR does not authorize
fetching a new calendar. Offline, wrap the original UTF-8 response text in the
bundle above, preserving the text exactly, and use:

```python
from datetime import datetime
from modelfc.history_completeness import parse_calendar, verify_completeness
from modelfc.offline_forecasts import load_history_bytes

# Explicit acceptance time, not a forged present-day observation timestamp.
as_of = datetime.fromisoformat("2026-10-09T12:00:00+00:00")
proof = parse_calendar(bundle_bytes, as_of=as_of)
assessment = verify_completeness(proof, load_history_bytes(config_path, "E1"),
                                 cutoff=as_of.date(), as_of=as_of)
```

Operator-reviewed authorization may add the single optional
`history_calendar_sha256` field, pinning the complete bundle. Only then may the
future reviewed caller pass `history_calendar_path` to `run_once`. Supplying just
a path or just a hash is rejected before credential construction/reservation.
Existing descriptor-relative ownership, no-follow, single-link and read-stability
checks apply; there is no new permission grant, launcher, timer or host installation.
Invalid/stale supplied proof rejects execution even when result ages are recent.
Omitting proof preserves the original age gate; omission cannot accept old history.

Freshness evidence is atomically published in the **existing private acquisition
store**, alongside each forecast, with rule version, cutoff, bundle/source hashes,
matched completed fixture IDs and installed history fingerprints. Forecast model
records and probabilities are unchanged. Reuse requires proof of the exact frozen
history bytes; changed sources cannot silently certify an older forecast. Proof
freshness is checked again immediately before the irrevocable claim/reservation.
No history, production evidence, account controls or services are migrated.

### October 9 acceptance remains conditional

PR #124's authentic VPS report establishes history through September 20 and an
age-gate rejection on October 9. It supplies **no independent completed-results
calendar** establishing that the intervening period contains no missing matches.
The unchanged upstream CSV is not independent completeness evidence. Authentic
calendar evidence sufficient to unblock that replay has therefore **not yet been
verified**. The full-season tests here are explicitly synthetic, not a claim about
actual September/October results. A fresh, authentic, reviewed calendar captured
for the replay time must pass the exact history join on the VPS; a later capture
must not be backdated. If unavailable, stale or contradictory, acceptance remains
blocked. Insufficient venue samples continue to reject corners independently of
BTTS. No live acquisition is authorized by this document or by merging the PR.
