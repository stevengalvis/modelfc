# Private EPL corner research capture: local proposal, not a started study

Owner: ZEN-74 research evidence and ZEN-75 readiness. Activation remains HOLD.
Calibration is complete: the parent reports the ZEN-74 update at 03:38 UTC on
4 November 2026 checked all 72 groups. No calibration rerun is needed. Retrospective
pooled corner scores favor the model against its declared baseline, but newest-
season intervals cross zero. BTTS remains research-only and is excluded here.

## Proposed protocol, requiring separate approval

Twelve fixed consecutive calendar weeks after a separately approved start, all
eligible E0 fixtures, unchanged venue-opponent negative-binomial model and both
teams at all four half-lines 3.5, 4.5, 5.5, 6.5. This is a practical horizon, not a
power calculation or approved study. No start date, end date or numerical
qualification threshold has been selected. Declare timezone/window boundaries,
observation cadence, eligibility/exclusion rules, denominator and treatment of
postponements before any actual capture; do not move the window after results.

Freeze the model, exact input/implementation pins, paired strictly prior venue-
frequency baseline and analysis plan before execution. Keep all lines and both
teams; no best-line selection. No odds acquisition, recommendations, bets or ROI
claim. Later evaluate proper scores and descriptive calibration on paired eligible
observations, report coverage/skips and uncertainty with dependence limitations.
Independent reviewer determines what the resulting evidence supports; this
primitive grants no qualification and provides no statistical stopping threshold.

## Minimal implementation and reuse

`pl_corner_research_capture.capture` is a disabled-by-default Python primitive,
with no CLI, scheduler, network client, results writer or publication integration.
It accepts in-memory saved calendar/history evidence plus the existing reviewed
calendar/mapping options and an existing private 0700 disposable store. It rejects
caller-supplied `as_of`; computation and writes sample the actual UTC host clock.
The caller cannot pass an existing replay artifact as a captured forecast.

It calls `pl_corner_capture.prepare` for the existing full-calendar, freshness,
identity, complete-history and whole-day model gates. The nested computation
retains its `OFFLINE_REPLAY` label; `pl_corner_capture.capture` and its
`prospective_capture=false` behavior are unchanged. A separate new persistence
receipt records the timing claim, rather than relabeling that computation or
importing an old replay. `pl_product_source.freeze` and its qualification contract
are untouched; nothing imports or invokes publication or recommendation functions.

The candidate retains the validated forecasts and readiness/skips, source/calendar/
contract/model pins, write-start time and paired Over/Under probabilities. Half-line
probabilities reuse `corner_line_probabilities` with the existing forecast means
and dispersion; stable Under tails are preserved. Venue baseline frequencies use
exactly the same complete historical observations, with the observation hash
verified equal to the model-input hash. Same-day/future results never train either
forecast. Four lines and both teams are frozen together without outcome fields.

## What durability does and does not establish

Under an exclusive nonblocking directory lock, `private_store.Store.put` writes an
unnamed inode, fsyncs the file, links it without overwrite and fsyncs the directory.
Only after that call succeeds does the new primitive sample the host clock and
write a separately hashed receipt binding the candidate/request to that sample.

`PAYLOAD_DURABLE_PRE_KICKOFF` means **the writer observed completion of candidate
file and directory fsync before the earliest authenticated kickoff in the batch**.
It is a trusted-host attestation of candidate durability. The receipt itself may
be written/durable later; no claim says that receipt existed before kickoff.
This is not independent timestamp witnessing, tamper-proof storage or external
proof of time. It depends on a trustworthy host clock, filesystem fsync semantics
and writer integrity. The owning account can rewrite files and recompute hashes;
operational access controls and evidence custody remain separate requirements.
No receipt timestamp may be inferred from mtimes, replay cutoff or a later retry.

Equality with or crossing kickoff during candidate persistence is `LATE_EXCLUDED`;
clock rollback detected between samples is `CLOCK_REJECTED`. Undetected host clock
error is not solved by this code. No eligible forecasts gives explicit readiness/
skip evidence and `NO_ELIGIBLE_FORECASTS`, not a positive capture claim. The existing
whole-day gate remains conservative: one invalid/postponed fixture can block that
whole batch. This must remain a visible coverage loss; the primitive does not
silently redefine the study's all-eligible-fixtures denominator or split that gate.

One supplied store holds one immutable attempt. Exact repeats return the original
candidate/receipt without observing a new clock or recomputing. Any changed inputs,
model/source pins, calendar kickoff or options conflict. If candidate persistence
or receipt writing is uncertain, the caller receives `ResearchWriteUncertain` and
must inspect the same store. Candidate without receipt remains `INCOMPLETE`; even
if kickoff has not passed it cannot be automatically repaired/re-timestamped.
An already complete valid receipt can be returned on retry, including after kickoff.

Input corrections cannot overwrite this candidate. Outcome ingestion and corrected
results are deliberately not implemented: a separately reviewed append-only result
record must reference the original forecast hash, authentic fixture identity,
result source and its own original/correction times. Postponements/rescheduling
must preserve the initial attempt and be counted explicitly; any later separate
attempt needs a reviewed supersession/dedup policy, never a new first-publication
claim or deletion of the old record. No retrospective forecast backfill is allowed.

## Readiness and remaining blockers

This is a locally tested persistence building block, not an executable 12-week
study. Before a study could start, separately approve and verify:

- The protocol/start window, ownership, intended research-only use, analysis plan,
  observation cadence, retention and missing/postponed/correction rules. No
  qualification approval or allowlist exception is required or manufactured by
  this private primitive; using the product/publication path would still require
  its existing independently approved qualification contract.
- Fresh authenticated E0 calendar, exact provider/team/fixture bindings and saved
  source receipts/history accepted at actual capture time. Existing retrospective
  or synthetic evidence does not substitute for these. Any acquisition and its
  account budget require separate authorization; this code makes zero requests.
- An approved private writer environment with clock-health evidence, filesystem
  durability acceptance, private permissions, source/environment pins, and an
  explicit acceptance of the trusted-host timing limitation (or stronger external
  witnessing if required). No host acceptance was attempted here.
- A small reviewed runner/inventory that binds attempts to the approved protocol,
  enforces its fixed window/cadence, counts every scheduled fixture and maps one
  attempt store per intended batch without cross-store duplicate capture. This
  primitive intentionally does not choose/start a protocol or supply a runner.
- An explicit decision on conservative whole-day exclusion, future postponed
  fixture attempts and append-only final-result/correction reconciliation. These
  are required before claiming complete prospective study coverage.

Use synthetic inputs/disposable stores only until those gates are satisfied.
No provider, production data, credentials, configuration, VPS, public publication,
model tuning, qualification policy or deployment is changed by this work.
