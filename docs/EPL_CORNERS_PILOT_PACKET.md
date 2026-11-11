# EPL corners: offline qualification and pilot packet

Owner: ZEN-74 evidence review; ZEN-75 pilot planning. Prepared 4 November 2026.
Decision: **HOLD activation; proceed to independent corners qualification review.**
BTTS remains research-only. No qualification contract or allowlist is approved by
this document. No provider request, VPS action, publication or deployment is authorized.

## Evidence and interpretation

Reviewed comparison source: PR251 commit `e0a76a98729228ac92ffabcc15f7c4881aadf498`,
tree `8a18ab7e206edf9f918e7e0ff148f308e9f1ad54`.
Actual comparison report SHA-256:
`dd6c86ce02d7e34583c756c20c5e8d53fc2e9b48b72fe563f91546b326e60963`.
Cutoff: `2026-11-04T01:28:23.585721+00:00`. Reported execution 02:01:35–02:02:08 UTC
on 4 November. The embedded original evaluation matched exactly; originals were
unchanged. Source: [ZEN-74](https://linear.app/galvis/issue/ZEN-74/validate-epl-corners-and-btts),
comment `5fcfb4d9-b6b0-4bb9-9cb4-f2f543029036`.

Fixed model: venue-opponent negative binomial; 100 prior team observations,
five prior venue observations, smoothing five; strictly earlier dates, complete
corner pairs. Same-cohort baseline: prior empirical exceedance frequency by venue.

| Team-corner line | Pooled Brier difference, model minus baseline | Date-block percentile 95% interval |
|---|---:|---|
| 3.5 | -0.0121 | [-0.0173, -0.0075] |
| 4.5 | -0.0171 | [-0.0228, -0.0121] |
| 5.5 | -0.0165 | [-0.0218, -0.0116] |
| 6.5 | -0.0126 | [-0.0172, -0.0082] |

Each line has 2,850 team observations from 1,425 fixtures over 445 dates. Pooled
log-loss intervals also favor the model. Every newest-season interval crosses
zero (57 corner fixtures, 20 dates). All four lines remain visible; choosing only
the historically strongest line would be additional selection, not independent
confirmation. BTTS pooled Brier difference is -0.0015 [-0.0038, +0.0008], and its
log-loss interval also crosses zero: improvement is inconclusive, not disproven.

These are retrospective snapshots, not an untouched holdout or evidence of
information available at historical kickoff. Bootstrap draws retain dates within
seasons but do not address adjacent-date dependence, refitting or multiple correlated
comparisons. No odds-based edge, returns or prospective qualification is established.

## Fixed calibration follow-up, ready locally but actual results missing

The real paired rows remain private on the VPS; none were available in the cloud
workspace. `pl_corner_calibration` reads the already-produced comparison JSON,
without CSV access, model execution, fitting or network access. Run only in a
separately authorized private offline job using a reviewed exact source commit:

```sh
PYTHONPATH="$REVIEWED_SOURCE/src" "$PYTHON" -B -m modelfc.pl_corner_calibration \
  --enable-offline-calibration --report "$PRIVATE_COMPARISON_JSON" \
  --sha256 dd6c86ce02d7e34583c756c20c5e8d53fc2e9b48b72fe563f91546b326e60963 \
  > "$PRIVATE_JOB/corner-calibration.json"
```

Input is a bounded regular nonsymlink file, SHA-pinned before parsing. Duplicate
JSON keys, invalid probabilities, repeated/incomplete fixture-line-side identities,
wrong dates and cohort-count mismatches are rejected. Output contains aggregate
bins only, no team names or individual prediction rows. Retain reports privately
and review any proposed external summary separately.

Bins are fixed deciles: [0,.1), ... [.9,1], including empty bins. Report count,
distinct match dates, mean predicted probability, observed rate and observed-minus-
predicted gap. Report all four lines, home/away/both, pooled and each season.
Model and baseline use their own probability bins on the same overall cohort;
corresponding bins need not contain the same observations. Bin statistics are
descriptive: no independence-based binomial intervals, arbitrary sparse-bin
pass thresholds, ECE ranking, recalibration or automated qualification. Small
counts/date counts must remain visible.

Independent reviewer must assess these calibration tables alongside proper scores,
coverage, temporal stability and snapshot limitations. The output is explicitly
NOT_ASSESSED for qualification. Any insufficient evidence produces HOLD, not an
allowlist exception. Record a reviewed decision and its scope before constructing
a prospective contract. A later statistically powered prospective validation needs
predeclared duration/sample rules; a one-day operational pilot cannot provide it.

## Proposed smallest pilot, inactive and not yet executable

One future EPL fixture day, selected from authenticated complete calendar evidence
before any forecast or quote capture. Keep the unchanged model and all four declared
team-corner half-lines 3.5–6.5 visible; retain both Over/Under prices where available.
Exclude match-total corners, BTTS, goal totals, other leagues and wagers. Missing
lines are unavailable, never synthesized or fetched repeatedly until present.

Reuse ZEN-70 acquisition, locks/reservations, shared snapshots and ZEN-43 receipts;
reuse `pl_saved_orchestration`, `pl_product_source` and ZEN-49 reporting/settlement.
No EPL-specific collector, ledger, dashboard or always-on timer. Preserve existing
E1 FanDuel/DraftKings coverage and budget ownership. Bookmaker availability for E0
must be evidenced independently, not inferred from E1 or a shared response.

Before activation, fill and approve every item below. Blank/unknown means HOLD:

| Gate / owner | Evidence still required |
|---|---|
| Qualification / ZEN-74 | Actual fixed-bin report; independent decision; exact model and evaluation pins, existing policy, effective/expiry times. The model pin must be the canonical fingerprint of `pl_corner_capture`'s implementation map expected by `pl_product_source.freeze`, not the comparison-script hash. No allowlist write here. |
| Fixture window / ZEN-73, ZEN-75 | Authentic fresh E0 calendar bytes and receipt, exact ordered-team/provider identities, kickoffs, completeness, postponements, selected day and timezone. Latest inspected operational calendar was E1-only; prior-matchweek audit remains incomplete. No future fixture/date is invented in this packet. |
| Current history / ZEN-73 | Latest report says authorized Nov4 refresh on installed d5cfba7 succeeded: 90 matches through Nov2, SHA c429b75f681dc7aaec5641a23320402409530b4b01a9f147bb7e386fe508159b. This supersedes the old 302/stale finding, but freshness/completeness and receipt bindings must be rechecked for the chosen pilot day. The Oct23 research snapshots are not live inputs. |
| Actual runtime / ZEN-70 | Installed release and effective callers/leagues/timers, exact shared lock/budget namespace, cross-caller reservation/dedup/crash behavior and genuine acceptance. Offline PR248/planning code is not proof of installed coordination. |
| Request plan / ZEN-70, ZEN-37 | Provider allowance/reset and bookmaker entitlement from permitted evidence; local used/reserved/remaining separately; numeric ceiling covering all discovery/metadata/quotes/retries and E1 obligations. Declare exact permitted calls and cadence. Unknown allowance or coordination is not approval to spend. No numeric live ceiling is guessed here. |
| Access and publication / ZEN-75 | Actual writer/read ACLs, approved contract hashes and validity, immutable pre-kickoff freeze → quote → first publication ordering, sanitized read-only projection and fail-closed cases. Code/synthetic ACL checks do not establish installed permissions. |
| Settlement and correction / ZEN-75 | Authentic regulation-final corner results bound to exact fixtures; immutable correction handling; original recorded prices; pending/missing states. Reuse existing ZEN-49 contracts. |
| Authorization / operator | Explicit future window, request ceiling, exact configuration/publication actions, owner, stop/rollback procedure and expiry, separately approved before execution. |

Stop acquisition on unknown/exhausted quota, reservation or identity mismatch,
missing/stale calendar/history, unsupported bookmaker, unapproved/expired
qualification, kickoff passed or permission failure. A missing/stale quote blocks
publication; it does not itself authorize another acquisition. Block publication
on any timestamp-ordering violation. Preserve original receipts; no fallback, unapproved retry,
backdating or reconstructed forecast. A valid empty day spends zero unnecessary
requests and is reported as empty. Record skips, unavailable markets and genuine
no-qualifying decisions.

At window end, do not schedule continuation. Reconcile every planned fixture and
request to an outcome or explicit pending/missing reason. Report capture/quote/
publication coverage, proper forecast scores separately from any authentic priced
returns, final-result coverage and immutable corrections. An operational PASS
requires all authorized boundaries and evidence links to hold; omissions remain
visible and may produce HOLD. No score/ROI threshold or profitability claim is
inferred from this small pilot. Independent owner records continue, hold or disable.
Rollback means disabling only the separately approved pilot configuration through
the reviewed operator path, retaining evidence and leaving existing E1 services,
accounting and historical records intact. This document performs no such action.
