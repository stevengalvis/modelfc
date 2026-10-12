# Next Championship matches

`/upcoming` is read-only and uses the already existing `/api/v1/predictions`,
`/api/v1/research/btts/performance` (no query) and `/api/v1/recommendations` routes.
No new ingress, permissions or provider access is required for these reads.
The deployed route availability still needs a first-party production smoke test;
this executor's outbound proxy denied zenofc.com, so no live data was substituted.

The repository contract has no public complete calendar or acquisition-receipt
endpoint. Accordingly this page shows only future E1 fixtures that have at least
one actual frozen forecast. It explicitly says it cannot establish the next
fixture when the feed is empty. Exposing fixtures before either model freezes,
or exact private acquisition block reasons, is a separate projection/API/access
dependency. Never infer a calendar from a bookmaker quote or weaken ingress.

One card represents exact competition, home, away and UTC kickoff. Each market
selects its earliest frozen record (immutable ID breaks timestamp ties), independent
of outcomes, odds and other markets. Books/repeated snapshots/cohorts do not add
cards. Both bookmaker-research BTTS and odds-free research BTTS are supported via
the existing performance inventory. Missing markets show waiting, failed reads
show blocked/read-unavailable, and valid records remain visible during partial
outages. An absent record does not imply cancellation or a specific history error.

Corners show recorded home/away/total means; a total mean is not a validated
match-total line probability. BTTS shows frozen Yes/No probabilities labelled
research only. Freeze age is not a claim of calibration, current history or live
prices. Recommendation status comes only from the existing gated recommendation
feed, becomes unknown after a minute without refresh, and never creates offers.

The 21–27-hour rule describes the automatic research acquisition window, not a
restriction on separate local on-demand analysis. Outside that window the page
cannot promise a forecast. History links preserve all existing results/views.

Validation: strict existing response decoders, fixture/cohort grouping and rendered
component tests; Chromium desktop/mobile against a production frontend build with
synthetic intercepted API responses and all external browser destinations blocked.
This proves rendering, not a real upcoming production fixture or native devices.
