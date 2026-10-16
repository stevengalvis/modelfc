# Independent Model Picks strategies

`independent-value-strategies-v1` starts when the existing live prospective writer
first invokes it. A separate immutable, hashed activation record stores that actual
UTC time. Every new schema-2 pick binds to it. No operator-provided timestamp,
GET, replay, migration or backfill publishes or activates this policy.

Both strategies require the existing supported market evidence, odds >= -200,
no-vig edge >= .05 (1e-12 tolerance), and positive EV. Best Value chooses highest
EV with the existing deterministic edge/probability/price/evidence ties. Higher
Probability FIRST filters to actual modeled win probability >= .65, then uses the
same EV ordering. It does not choose highest probability or merely relabel Best
Value. Best eligible bookmaker is still chosen per identical selection.

Each strategy owns one immutable slot per existing provider-aware fixture/market
identity (each home/away corner total, or E1 BTTS). Slots exclude line, direction,
book and quote time so later changes cannot replace a published strategy pick.
The original timestamp, offer, formula policy, and evidence stay frozen. A separate
selection identity includes the actual line/direction or BTTS Yes/No, excluding
price/book/time, so identical selections across strategies can be recognized.
Each strategy retains its own price and publication time even for matching selections.

An optional All Unique Picks financial aggregate is intentionally not exposed.
Choosing which price to use when strategies publish the same selection at different
prices/times requires an explicit accounting choice. This draft shows separate
strategy totals and never sums those totals into an alleged unique total.

## Legacy isolation

Historical schema-1 files stay byte-for-byte in `published-recommendations`, using
the original Best Value plus Higher Probability subset semantics. The production
writer now writes schema-2 files in `strategy-picks` and never upgrades legacy
records. The legacy feed remains available separately, with its original semantics.
Old publication functions remain for explicit legacy replay tests, not the live
runner. New namespaces use the existing dedicated API-reader immutable publication
ACL helper; no account, service, host permissions or root-installed files change.

## Simulated accounting

A unit means $2 risked, for favorites and underdogs alike. Units/profit settlement
remain the existing one-unit American-odds calculation; dollar profit is exactly
2 × units. ROI is unchanged. Pushes return the $2 stake, with zero profit. Pending
picks have null profit and are excluded from settled ROI. These are simulated
results, not actual wagers; there is no execution path. Existing unsupported void
settlement and rejected conflicting BTTS correction behavior remain explicit.

## Reads and UI

The existing allowed GET path adds `view=strategies`, strategy, current/history
scope, supported league/market filters, and offset/limit (default five, maximum
20). Payloads include at most the requested page, plus filtered strategy totals
and market totals over the same versioned cohort. The page summary remains separate
for response integrity checks. No aggregate mixes legacy subset picks with the new
strategies. Recommendations loads current strategy choices; alternative-price
analysis loads only on request. Performance separates Strategy Results, Model
Accuracy, and legacy subset records. Model accuracy and existing research metrics
are preserved and loaded only when selected. No CLV, drawdown or benchmarks are
fabricated; existing model calibration stays with accuracy, not strategy ROI.

Read and write publication capacity is 2,000 records; immutable record size is
bounded at 64 KiB. New strategy evidence traversal is capped at 10,000 entries and
1 MB per JSON file. Capacity violations fail explicitly, never silently truncate
results or imply an empty history. Current/empty requests do not return historical
rows; empty pages avoid inventory reconstruction. Populated aggregate reads still
reconstruct source evidence under shared locks. A materialized, validated summary
index is a separate performance follow-up before these capacity limits are reached;
this draft does not claim constant-time historical queries or unlimited retention.
