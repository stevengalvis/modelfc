# MLS ASA saved-response inspection

`modelfc.asa_saved_history.normalize` is disabled by default and has no runtime caller, network client, filesystem reader or persistence. It accepts a map of receipt names to saved JSON bytes, the exact receipt-array bytes and SHA256, and an aware `as_of` datetime. Receipt paths are opaque provenance; they never select files. Raw responses and receipts must remain outside Git and public reports.

The bounded policy `asa-mls-regular-season-2023-2026-v1` accepts one teams response and one to four distinct season responses. It verifies each response's hash, byte count and row count against the pinned receipts. It accepts only HTTP 200 receipts and the exact HTTPS base `https://app.americansocceranalysis.com/api/v1`, `/mls/teams`, and `/mls/games?season_name=YYYY&stage_name=Regular%20Season&status=FullTime`. Missing, duplicate, additional or differently encoded query parameters fail closed. Receipt order is immaterial; the full receipt hash remains part of composite input identity. Pins bind supplied evidence but do not authenticate a caller's receipt claims.

The supplied schema was verified by a separate research worker against private cached responses. This executor has not read those bytes: the Library transfer failed, including its one bounded retry. Tests use explicitly synthetic schema reproductions. No actual byte verification, genuine-file normalization, complete-season acceptance or current-match census is claimed.

## Score scope and outcomes

A game must match its request season and UTC calendar year, resolve both distinct team IDs in the pinned teams response, have integer nonnegative scores (not booleans), exact `FullTime` status and explicit `knockout_game=false`. Provider timestamps must use `YYYY-MM-DD HH:MM:SS UTC`; kickoff precedes request time, and update time lies between kickoff and the supplied observation time. Duplicate game IDs across seasons and duplicate kickoff/home-ID/away-ID identities reject the entire batch. Duplicate team IDs or identical team names under different IDs also reject; no alias merging or cross-provider identity is inferred.

Regular-season request provenance is essential because the response does not echo stage and playoff FullTime scores may include extra time. The [2023 MLS rules](https://www.mlssoccer.com/news/2023-mls-competition-guidelines) and [current competition rules](https://www.mlssoccer.com/league-reports/competition-guidelines/) distinguish regular-season draws and playoff extra-time procedures. This bounded cohort policy does not generalize to playoffs, earlier seasons or the 2027 calendar transition.

Absent `extra_time` remains `None` (unknown), not false. Only an explicitly false value is accepted when present; true, null, strings and numeric booleans reject. An echoed stage must agree with Regular Season. A new `completed_at` field rejects as unreviewed schema rather than silently changing completion semantics. Optional descriptive fields are not required; expanded minutes and matchday, if supplied, must be nonnegative integers and never determine chronology.

Each normalized result preserves provider game/team IDs, team labels, season, UTC kickoff, scores, provider update, request timestamp and source hash. `btts` is the observed boolean outcome and `total_goals` is the score sum, not a probability, forecast or recommendation. Normalized results and `EMPTY` responses both retain unverified coverage and false validated/live flags. Empty data cannot establish a complete season.

## Model consumers stay blocked

Response completion/acquisition time is UNKNOWN. Checking request/update times against `as_of` does not establish that these bytes were available by `as_of`. Every result has `completed_at=None`; every batch reports `BLOCKED_COMPLETION_TIME_UNKNOWN` and an unreviewed timezone mapping. Request start is preserved as `requested_at`, not relabeled as retrieval, response completion or final whistle. Neither `last_updated_utc`, expanded minutes, kickoff offsets nor request time substitutes for actual completion. No `GoalObservation`, model fit, odds consumer or readiness activation is produced. Existing shared goal/BTTS/totals contracts and their completion-based freshness semantics remain unchanged.

The composite input hash binds policy version, pinned receipt bytes and response identities, including the teams source. Preserve those original private artifacts together. This is evidence identity, not a claim that data was historically available before its recorded request. A later model bridge needs genuine completion and receipt timing evidence, reviewed team/timezone bindings and the unchanged prior-day/known-at-freeze gates; it must not use this batch to bypass them.

## Outstanding acceptance

- Materialize the already cached private archive through an authorized supported transfer, verify archive and receipt pins, and run the adapter on the exact preserved bytes. No refetch is part of this work.
- Reconcile genuine schema, identities, timestamps, duplicate and cohort diagnostics; retain explicit missing completion/extra-time facts. Report counts as observations rather than enabling a model.
- Establish separately reviewed completion-time, availability-time and timezone semantics before shared consumers can accept observations. Genuine current fixture/calendar coverage, cross-provider mappings, suitable model history and freshness remain separate gates.
- Shared readiness remains unchanged. No corners, paid requests, scheduled acquisition, production writes or activation are added.
