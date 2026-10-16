# Published recommendations v1

UI names: **Model Picks** for published selections, **Model Picks Performance**
for results, with subtitle **Results of our published recommendations**.
These strings live in `web/lib/track-record-labels.ts`.

The record starts empty. It never reconstructs historical publication from quote
retrieval, analysis creation, opportunity qualification or page views. The existing
quote-event simulation and all forecast/outcome files remain unchanged. Model
accuracy remains separate. Existing alternative-line shortlist views are explicitly
labelled analysis; the published feed on Performance is the authoritative pick list.

## Selection policy: first-team-total-value-v1

The existing prospective writer, after its normal evidence/settlement/acquisition
work, selects from the same saved-price shortlist eligibility and latest-capture
projection (up to 100 nearest upcoming fixtures). Supported TEAM_TOTAL only;
price >= -200, no-vig edge >= 0.05 with the existing 1e-12 tolerance, positive EV,
valid consistent prices, and last-known available coverage are required. As with
the shortlist, retrieval age alone may be stale; original quote time remains
visible and publication does not assert a live executable price.

1. For each exact selection (forecast, team side, direction and line), choose the
   best eligible decimal price across bookmakers. Existing deterministic ties:
   bookmaker ascending, newest retrieval, observation/selection IDs ascending.
2. Rank these selections using the existing Best Value order: EV descending,
   no-vig edge descending, decisive probability descending, decimal price
   descending, then prediction/target/bookmaker IDs, newest retrieval and evidence
   IDs. All candidates have already qualified. This does not pick the longest odds
   across different lines.
3. Freeze the first ranked selection per market slot. The slot is competition,
   provider, provider fixture ID, normalized kickoff, TEAM_TOTAL and HOME/AWAY.
   Home and away team-corner totals are distinct markets: at most two picks per
   fixture. Lines, over/under direction, bookmaker, observation, prediction and
   policy version are deliberately absent from the slot identity. Alternative
   lines, price changes and new forecasts cannot create replacement picks.
4. Every pick belongs to Best Value. Higher Probability is membership of that same
   frozen pick when actual win mass >= 65% (not probability conditional on no push).
   It never selects a second line. Overall counts never add overlapping views.
   The existing analysis view can prefer another high-probability line; this is
   deliberately not a separate published pick.

## Publication and reads

`corner_prospective.run_once` invokes `publish_locked` while owning runner.lock.
The writer holds the existing state lock, validates saved evidence, and uses its
own UTC clock immediately before creating an immutable, hashed record. Callers
cannot provide a publication timestamp. Forecast creation, retrieval and availability
checks must precede or equal publication; publication must precede kickoff. A second
clock check immediately before atomic linking rejects crossed kickoff or clock
regression. Concurrent runs serialize; retries retain first records. A partial
publication failure keeps already published picks and reports the existing storage
failure rather than overwriting evidence. No GET or inventory/replay function
publishes. Saving the record is publication to the public feed, not a claim that
any particular user viewed or placed a wager.

The new record family uses the existing immutable publication helper and dedicated
API-reader ACL mechanism; no account, launcher, host ACL, credential, service or
permission setup is installed by this PR. The existing reader/writer lock order
is retained. The public GET `/api/v1/prospective/performance?view=model_picks` is no-store and read-only;
absent records return an explicit empty series. Integrity/read failures are errors,
not empty success. Reads bind frozen picks to original validated quotes/forecasts
and the latest validated outcome-chain tip, never today's line or price.

## Settlement and limits

One hypothetical unit at the original American odds: win returns odds profit,
loss -1, push 0. ROI divides profit by settled WIN/LOSS/PUSH stakes. Win rate
excludes pushes. Missing results, including expired fixtures, remain PENDING and
are excluded from both denominators. No actual user stakes or wager returns are
claimed. Counts include unique fixtures, league/market groups and both view subsets.

There is no validated sportsbook cancellation/void evidence in the existing
settlement pipeline. V1 exposes `void_settlement_supported=false`, zero voids and
an explicit UI explanation. It never infers voids from missing results. A separate
reviewed evidence-backed void/cancellation extension is needed before voids can
be settled; this PR does not invent a new result source.

After separately approved merge/deployment, the next successful existing collector
run can publish eligible upcoming picks at that actual time. No historical
backfill, provider request, new timer or host installation is needed for this
publication step. This PR does not merge, deploy or run the collector against live
providers. Published picks on the Recommendations landing page, broader markets,
and void evidence are follow-ups; the existing analysis tools remain available.
