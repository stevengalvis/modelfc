# ZEN-35: offline Premier League calendar groundwork

This separate parser and read-only CLI validate an explicitly supplied, saved
football-data.org **PL** season response for internal competition **E0**. They do
not fetch anything, register a live provider, produce picks, or authorize a model
consumer. BTTS, OddsPapi coverage, history completeness, venue audit, corner-only
preparation, schedules, deployment and public APIs remain outside this change.

## Contract and provenance

Main's `history_completeness.py` and `providers/football_data_calendar.py` establish
the saved football-data.org v4 competition-matches response shape used here:
`filters`, `competition`, `resultSet` and `matches`, with each match carrying
`id`, `utcDate`, `competition`, `season.startDate/endDate`, `stage`, `matchday`,
`homeTeam`, `awayTeam`, `status`, and `score.fullTime`. The E1 parser is unchanged:
its 24-team / 552-pair / 46-round rules cannot safely validate PL by changing a
competition string. PR #132's separate validity/age concept and bounded reading
pattern are reused without merging or altering that draft. Saved-response
preparation in #133 remains a separate concern.

**No genuine E0 payload, roster or reviewed binding contract is committed or
claimed.** Tests use explicitly synthetic teams and IDs (including a synthetic
competition ID). This implementation is a proposed strict subset of the existing
source contract, pending acceptance against authentic PL evidence. If an actual
saved response has missing kickoffs, partial coverage, ongoing scores or another
unsupported shape, rejection is intentional; do not invent values to pass it.

Two UTF-8 JSON files are supplied by the operator:

* Bundle: exactly `version: 1`, `source: "football-data.org"`, `retrieved_at`
  (the original timezone-aware retrieval timestamp), and `payload` (a string
  containing the original response bytes decoded as UTF-8, **not** parsed and
  serialized again). Encoding that string back to UTF-8 must reproduce the
  source bytes exactly, including whitespace. The parser retains both raw bundle
  and raw payload as bytes and hashes both. No fallback to mtime or current time
  exists. A missing or naive retrieval time is invalid.
* Separately reviewed contract: exactly `version: 1`,
  `source: "football-data.org"`, `competition: "E0"`,
  `source_competition_code: "PL"`, `source_competition_id` (reviewed positive
  integer), `season_year` (explicit starting year), and `team_bindings`.
  There must be exactly 20 bindings, each with `source_team_id`, exact
  `source_name`, and exact `history_name`. IDs and names must be unique. The
  operator must review these against the actual provider response and historical
  team identity, including promotion/relegation and venue continuity. The parser
  checks consistency; it cannot establish that this external review happened.

The caller must pin the exact bundle and reviewed contract SHA-256 values.
Hashes establish byte identity, **not authenticity or retrieval provenance**.
Do not derive retrieval time from now or a file timestamp, infer aliases, fill a
roster from memory, or equate football-data.org IDs with OddsPapi IDs. The source
namespace is explicit in the contract and normalized fixture snapshot.

## Completeness and readiness

Only a full unfiltered regular season is accepted: 380 distinct fixture IDs,
380 unique ordered home/away pairs across the supplied 20 teams, and 38 rounds
of ten fixtures, each containing every team exactly once. Counts alone are
insufficient. Duplicate JSON keys, duplicate/conflicting identities, inconsistent
rounds, wrong competition/season, unsupported statuses, missing exact kickoffs,
score inconsistencies, and truncated input fail closed. Row season boundaries
must agree and lie within July-to-June of the explicit season. A partial provider
response—even if its reported count agrees with its length—cannot establish an
empty selected day or full-season readiness. Obtain authentic complete evidence
through a separately authorized process; this CLI never fills gaps.

The CLI prints one bounded count-only JSON object (no raw payload, team names,
credentials, input paths or provider exception text):

* `validity`: `MISSING`, `INVALID_OR_UNSUPPORTED`, or `VALID`.
* `freshness`: `UNKNOWN`, `FUTURE`, `STALE`, or `FRESH`. The existing reviewed
  six-hour policy is inclusive at six hours and uses original retrieval time.
* `selected_day_state`: `UNKNOWN`, `EMPTY`, or `FIXTURES_PRESENT`. Only fresh,
  valid, complete in-season evidence can establish `EMPTY`. Days use
  `Europe/London`, including midnight and DST; exact kickoff identity stays UTC.
* `status_counts`: postponed/cancelled fixtures remain visible rather than being
  mistaken for an empty day. Finished and active statuses remain distinct.
* `prior_state`/`unresolved_count`: nonterminal fixtures before the selected
  London day or before the explicit observation instant are unresolved, including
  earlier kickoffs on the selected day. Postponed/cancelled are terminal for this
  calendar assessment only; no history/results completeness is asserted.

Exit zero means valid, fresh evidence with no unresolved prior fixtures. It does
not mean picks, live authorization or history readiness. Missing/invalid/stale/
future evidence and unresolved prior fixtures exit one. Arguments are sanitized
on failure. Files must be bounded regular files; final-component symlinks and
special files are rejected. Only the explicitly supplied files are read.

```sh
PYTHONPATH=src python -B -m modelfc.pl_calendar_readiness \
  --bundle SAVED_BUNDLE.json --bundle-sha256 REVIEWED_BUNDLE_SHA256 \
  --contract REVIEWED_CONTRACT.json --reviewed-contract-sha256 REVIEWED_CONTRACT_SHA256 \
  --competition E0 --season-year 2026 --day 2026-10-17 \
  --as-of 2026-10-17T19:00:00Z
```

These are placeholders, not a real evidence bundle or acceptance command.
`--as-of` is explicit for deterministic replay; it is never used as retrieval time.
The module API preserves all exact identities and offers `exact_fixture()` for
an exact provider ID + UTC kickoff + ordered history-team join. Its optional
`FixtureSnapshot` conversion uses the shared provider-neutral model. In-play,
paused and suspended states cannot be represented by that model, so conversion
rejects them instead of coercing them into scheduled fixtures. The report still
represents those source statuses. No cache is written or published.

## Remaining acceptance

1. Supply authentic full-season PL bytes and independently supported original
   retrieval time; verify byte hashes and actual provider shape.
2. Review and pin the actual 20-team, provider-namespaced contract and historical
   bindings. No real mappings have been fabricated in this PR.
3. Exercise genuine positive and negative selected-day cases, including an
   eight-fixture day, with independent review of exact ordered identities.
4. Separately audit E0 history/venue continuity and explicitly authorize any
   corner-only preparation. This draft cannot itself produce EPL picks.

E1 parser/client/consumer behavior is unchanged. E1 `corner_refresh.py` already
retains genuine HTTP-200 CSV provenance when `retain_result_evidence` is set;
this work neither replaces that retention nor enables any E0 capture.
