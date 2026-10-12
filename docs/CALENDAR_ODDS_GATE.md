# Calendar gate for E1 odds collection

This opt-in bridge saves paid discovery on proven empty days. It makes no new
calendar or odds request, changes no model/market, and does not promise prices or
recommendations. The existing forecast fetcher is the trusted source publisher;
a hash authenticates byte identity, not the provider or operator who supplied it.

The forecast-only runner retains its already-fetched, validated full-season
football-data.org bundle before the no-eligible-forecast early return. Original raw
bundle bytes live in private `state/odds-calendar/<bundle-sha256>.json` wrappers;
`latest.json` atomically selects a hash. Files are runtime-owned 0600, the directory
0700, without API ACL publication. Source hash, retrieval time and explicit team
bindings stay in the original bundle. Same-byte replay is inert, older publication
and conflicting content at the same retrieval time are rejected. Historical
bundles are retained; no pruning is introduced. Publication failure rejects that
forecast run rather than silently advancing the pin. Corrupt retained state needs
operator investigation, not deletion or provider fallback.

`corner_prospective run-once --calendar-gate` reads only that retained evidence
under the existing shared runner lock. No argument means unchanged legacy odds
behavior. This PR does not change the installed launcher or enable the flag.
Repository deployment alone will retain calendar responses but will NOT activate
the collector gate. Activation needs separate review of the fixed launcher argv;
no new credential, ACL, service, timer or provider capability is required by the
code. Verify actual host boundaries before claiming this.

The existing parser requires the entire current ELC regular-season ordered-pair
matrix, 46 valid rounds, explicit verified team bindings, consistent counts and
statuses, and retrieval age between zero and six hours. Missing, stale, future,
partial, malformed, tampered or unsafe-path evidence produces
`CALENDAR_EVIDENCE_UNAVAILABLE` and zero new provider requests. There is no paid
fallback. The six-hour bound is existing evidence policy, not a promise that a
schedule cannot change during that interval. Ordinary hourly :35 publication
usually gives :05 collection a roughly 30-minute-old source; outages remain explicit.

A complete source with no future SCHEDULED/TIMED E1 fixture on today's UTC date
records `calendar_gate.skip_reason=CALENDAR_NO_RELEVANT_FIXTURES`, even on ordinary
discovery/rediscovery runs. With no other errors this is `OK`, an empty error
`reasons` list and CLI exit 0. Genuine evidence errors remain PARTIAL/exit 1; prior
inventory errors are never cleared by an empty-day decision. This is not a
complete-odds-coverage claim or a trigger for extra retries. Existing immutable
PARTIAL empty-day receipts retain their original meaning and bytes; the reader
accepts both the earlier form and the new optional skip-decision field.
It leaves discovery and attempt history and reservations untouched. Old receipt
schemas remain readable; opted-in valid-source receipts additionally pin the
calendar bundle/source hashes and retrieval time.

On match days the existing two-discovery-per-day policy is unchanged: first run,
then one query at/after 12:00 and before 18:00, at least six hours after the first.
Cached OddsPapi fixture IDs remain provider IDs; football-data.org IDs never enter
odds requests. Exact canonical home/away teams AND UTC kickoff must match an active
calendar fixture before quoting. Duplicate provider matches are rejected. Missing
provider events report `CALENDAR_FIXTURE_UNMAPPED`; old, cancelled, postponed or
rescheduled mappings report `CALENDAR_FIXTURE_MISMATCH` and are not quoted. Valid
other fixtures can continue. Existing mappings/attempts are not rewritten and
forecasts are never regenerated to repair a mapping. A changed kickoff for an
already cached provider ID therefore needs separate reconciliation; this PR prefers
an explicit missing quote over guessing or extra acquisition.

A newly listed event can appear on the next already-allowed rediscovery. If an
empty day was skipped without discovery, a later valid source showing a match can
use the still-unused initial discovery. If both discoveries are already used,
missing events remain explicit; no emergency query or automatic retry is added.
Calendar evidence is revalidated before reservations and after request pacing.
Expiry during pacing consumes its conservative reservation but dispatches no HTTP.
The six-hour-to-more-than-15-minute initial quote window, late observation rules,
eight-request run cap, 180 monthly allowance, books, idempotency and durable
precharge remain unchanged. Provider quota is not inferred from local allowance.

PR133/135's cross-run market metadata cache is independent and remains unmerged;
this branch does not implement or activate it. No API/frontend changes are made.
Prices and no-pick reasons continue through existing views; research forecasts
remain research-only and the five-minute current-price gate remains unchanged.

## Disposable acceptance and later activation

Use copied state/dependencies, a pinned checkout, and network denial. Run
`python -m unittest tests.test_odds_calendar tests.test_forecast_only_runner tests.test_corner_prospective tests.test_prospective_run_receipts`.
Use the actual module CLI with `--calendar-gate` against disposable state. Check
zero HTTP/reservations on valid empty days and invalid evidence, exact recorded
match-day calls, cached ID reuse, cancellation/reschedule/new listing, expiry after
pacing, exhausted budgets, replay and unchanged original evidence bytes. Synthetic
full calendars and recorded transport tests do not certify live schedule coverage.

On the VPS, first verify one ordinary forecast run retained a fresh valid calendar
without extra requests. Only after separate activation approval should an inspected
fixed launcher add `--calendar-gate`; do not manually start paid acquisition to
prove it. Observe an ordinary run and its pinned receipt. Removing that flag
restores the prior discovery policy without deleting calendar or immutable records.

## Minimal read-only host check (no acquisition)

Inspect enabled/active state and next/last triggers for
`modelfc-corner-prospective.timer`, last service result and actual launcher flags.
Read the newest validated prospective completion receipt and local control period,
allowance/reserved/remaining, plus saved discovery date/query count and fixture
count. Read the newest forecast receipt and, if present, retained calendar pin/time
and parse status. Report timestamps and release SHAs; do not read credentials or
process environments, invoke run-once, modify files, reset reservations, restart
units or contact providers. An inactive oneshot between timer runs is normal.
These observations cannot establish the provider account's remaining quota.
