# Model FC V1 product requirements

This document records the product direction for both coding agents. The detailed
wire contract and integrity rules live in [API_V1.md](../API_V1.md).

## Outcome and priorities

Steve can use a small mobile-friendly application for the complete corner
analysis workflow without normally operating CLI commands or entering final
corner counts manually.

Model FC stays multi-league. Championship (`E1`) is the deepest proving ground
for useful analysis, model evidence, and future content. La Liga 2 / Segunda
División (`SP2`) is the next deliberate expansion. This is a product strategy,
not evidence that either league provides a profitable predictive advantage.
Existing supported leagues remain available; avoid adding unrelated leagues
before the first complete workflow is demonstrated.

League readiness means verified corner coverage, forecast eligibility, reliable
fixture resolution, maintained results, and measured evaluation. A parser that
recognizes a league name does not establish readiness. Kaggle match counts alone
do not establish corners, dates, team identity, or a maintained current feed.

## Normal user journey

1. Paste messy sportsbook text containing one fixture and multiple corner
   markets/odds. Recognize available league, date, teams, market type, side,
   line, and price without requiring a sequence of dropdown selections.
2. Show the detected fixture and an editable market table. Highlight ambiguous
   or missing values next to the relevant input. Allow correction without
   rewriting the entire paste. Do not silently infer an unknown league/date.
3. Analyze all valid supported rows with one action and a shared fixture forecast.
   Keep unsupported/unresolved input visible with an explanation. Never drop a
   row silently or send an invalid enum that rejects an otherwise valid batch.
4. Display backend probabilities, break-even probability, edge, expected profit
   per $1, expected corners, and applicable warnings. Whole lines must expose
   push probability and explain the decisive probability used for edge.
5. Allow selection of zero, one, many, or all supported results. No positive-EV
   filter forces a selection. Only `Log selected` creates official picks.
6. Preserve the selected forecast and offered odds. A retry cannot create
   duplicate picks. Show the reason when logging is unavailable.
7. Retrieve final corner counts automatically after the match and validated
   source refresh, then settle eligible picks without another user action.
8. Show saved picks and running results in Predictions and Performance.

Primary navigation is Analyze, Predictions, Performance. Avoid a prominent
league selector or model-tuning controls in the normal flow. Small correction
controls are appropriate when detection is ambiguous. An admin exception view
can handle unresolved results without replacing normal automatic settlement.

## Supported interpretation

V1 focuses on full-match home/away team corner totals and match corner totals,
OVER/UNDER, whole and half lines, and American odds. Implement only types the
backend accepts. Exact limits and enums are in `API_V1.md`. Partial-period,
handicap, and quarter-line markets must not be silently reinterpreted.

The frontend currently contains a deterministic parser. Keep parsing and editing
there while integrating the existing implementation; backend request validation
and authoritative fixture identity remain mandatory. Revisit a shared backend
parser only with an explicit contract change. An LLM parser is deferred.

For valid analysis, do not assume that picks can be logged: the backend's
`pick_logging` status is authoritative. A trusted UTC kickoff is required by
the existing contract. A supplied date or a guessed 19:00 kickoff is insufficient.

## Evidence and performance

- Count MAE/RMSE measure corner-count error. Count NLL compares distributions.
- Brier score, binary log loss, and calibration bins evaluate market probabilities
  on chronological samples using the production coverage gates.
- Historical calibration diagnostics are not a fitted calibration layer.
- Profit, ROI, and W-L-P apply to explicitly logged picks. Open, review, and void
  counts remain visible according to the contract's accounting rules.
- Multiple markets from one match are dependent observations. Report fixture
  counts as well as scored observations; do not present them as independent bets.
- Preserve the current champion while researching alternatives. A model change
  requires a reproducible comparison and a separately reviewed promotion.

## Completion milestones

| Milestone | Evidence required |
| --- | --- |
| Live analysis | A real E1 fixture and canonical team names produce backend results from current validated history, visible in the UI with mocks off; invalid input and unavailable data are handled. |
| Explicit picks | Trusted fixture/kickoff resolution, zero/one/many/all selection, immutable terms, retry/conflict behavior, and pre-kickoff enforcement work through the UI/API/storage boundary. |
| Automatic settlement | An installed refresh-and-settle job ingests a completed result, settles logged picks once, exposes exceptions, and leaves unselected analyses out of performance. |
| Performance | Predictions and Performance agree with backend ledger records, including pushes, open items, review items, and voids. |
| SP2 expansion | The same journey passes for SP2 after its source coverage, history, identity, refresh, and evaluation are established. |

Synthetic integration tests exercise these boundaries in CI. A production claim
also requires an observed run against deployed services and genuine data; a
passing synthetic test does not prove feed freshness or operational scheduling.

Defer additional leagues, new model families, elaborate dashboards, and autonomous
incident-repair agents until these milestones have working evidence.
