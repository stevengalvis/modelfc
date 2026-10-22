# ZEN60/61: Spain and Italy saved inputs

`spain_italy_readiness.report(evidence, as_of=..., quotes=...)` wraps the existing
five-league report. It adds the same saved-calendar adapter to SP1 and I1 and
separate corners, BTTS and goal-totals gates. E1, E0 and MLS rows are unchanged.
The original five-tournament maximum and acquisition registry remain unchanged;
SP1=8 and I1=23 are OddsPapi identities, never calendar-provider IDs.
The shared qualification registry deliberately keeps `calendar_adapter=null`;
the additional `saved_calendar_adapter` field identifies this structural evidence
boundary, not a newly qualified shared consumer.

This is a supplied **normalized saved input schema**, not a parser for a native
provider response. A later source-specific normalization must be reviewed against
the retained original response. No provider IDs, mappings, entitlement or genuine
coverage are supplied by this implementation. No network or production writes,
models, collectors or runtime activation are added. All new fixtures are synthetic.

## Explicit input contract

Use existing immutable `five_league_readiness.Evidence` and `SavedQuotes` values.
Calendar and mapping are UTF-8 JSON bytes with SHA256 pins; duplicate JSON keys
are rejected. Mapping maximum is 20,000 bytes, calendar maximum is the existing
`MAX_CALENDAR_BYTES`. These fields are exact; additional fields are rejected.

Mapping version 1 contains:

- `version`: integer 1; `competition`: SP1 or I1; `season_year`: Evidence year.
- `source`, `source_competition_id`, `source_season_id`: explicit nonempty strings
  from reviewed source evidence. Numeric provider IDs must be represented as
  decimal strings; IDs from different providers are never substituted.
- `timezone`: Europe/Madrid for SP1, Europe/Rome for I1.
- `team_bindings`: exactly 20 objects, each containing `source_team_id`,
  `source_name`, `history_name`. All are trimmed printable strings of 1–100
  characters, independently unique including case-folded collisions. Names are
  exact, never fuzzy aliases. The roster must be reviewed for the requested season.

Calendar version 1 contains:

- `version`, `competition`, `source`, `source_competition_id`, `source_season_id`
  matching the pinned mapping.
- `season_start`, `season_end`: explicit ISO dates inside July–June of the
  Evidence season; `stage`: REGULAR_SEASON only.
- `retrieved_at`: original aware ISO timestamp, or null if unknown. Never replace
  a missing original receipt with report time or filesystem modification time.
- `source_payload`: exact original UTF-8 source text, including original whitespace;
  `source_sha256`: SHA256 of its UTF-8 bytes. Non-UTF-8 sources need a different,
  separately reviewed adapter. This pin proves retention, not that normalization
  faithfully represents that source. Do not fabricate a source payload or receipt.
- `matches`: exactly 380 fixture objects with these fields: `fixture_id`, `kickoff`,
  `matchday`, `home_id`, `home_name`, `away_id`, `away_name`, `status`, `goals`.
  IDs are nonempty strings, fixture IDs unique, names/IDs match the bindings.
  Kickoffs are aware ISO times within explicit local-season dates. Matchday is an
  integer 1–38. Each round uses all 20 teams exactly once; each ordered home/away
  pair occurs once. `status` is FT, SCHEDULED, POSTPONED or CANCELLED; FT means
  regulation time only and requires two nonnegative integer goals `[home, away]`
  and kickoff before retrieval (or observation when receipt is unknown).
  Other statuses require null goals. AET/penalties and active states are unsupported.

Structural completeness is established by the roster, pairs and rounds, never a
caller-supplied `complete` flag. It does not authenticate the roster, fixture
observations, stage policy or normalization. A supplied or synthetic label cannot
promote evidence to genuine acceptance. `normalization_authenticated` is false.
The calendar has the existing six-hour freshness bound. Unknown provenance stays
UNKNOWN. Earlier unresolved fixtures block the calendar, including postponed or
cancelled fixtures; absence is never automatically reported as a verified empty day.

## Reuse and independent market gates

History uses the existing strict Football-Data validation: canonical SP1_YYZZ.csv
or I1_YYZZ.csv source tuples with original bytes, SHA256 and original retrieval
time. Corners use GOALS_CORNERS; BTTS/totals use GOALS. Unrelated shots are explicitly
unassessed, not corrected or represented as genuinely absent. Bad goals/FTR still
fail every market. Missing or invalid corners do not invalidate the goals scope.

Prior completed calendar results and current-season history are reconciled both
ways by exact local date and mapped ordered names, including score agreement.
All matches on the earlier of selected day and observation's local day are
excluded from this historical comparison. A partial corner history gets its own
gap code. This reconciliation is a consistency check, not sufficient training
history or prospective forecasting acceptance.

Saved quotes use the existing shared normalized snapshot and pin/freshness gates.
Quote counts are inventory only: no calendar-provider fixture ID is equated with
an OddsPapi fixture ID, no cross-provider quote binding or model consumer is
qualified. Every market remains `input_state=UNSUPPORTED`, `validated=false`,
`live=false` until explicit consumers and authentic acceptance exist.

Next acceptance needs genuine retained calendar bytes/receipts, reviewed complete
season roster and provider IDs, demonstrated faithful normalization, pinned
Football-Data history and independently reviewed fixture/quote bindings. Do not
acquire anything to satisfy this offline task. Shared-contract/MLS changes require
coordination; this slice adds only its own module, tests and documentation.
