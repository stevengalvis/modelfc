# ZEN-64: Denmark and Scotland saved evidence

This offline adapter parses pinned, saved Sportmonks v3 fixture pages for Denmark Superliga (271) and Scotland Premiership (501). It makes no requests and changes neither the existing Championship Sportmonks guards nor the five-league OddsPapi registry or maximum-five batch. ZEN-65 owns future odds acquisition/matching planning. No entitlement or current season coverage has been established.

The league identities come from the official [league endpoint documentation](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/leagues/get-all-leagues). Season, stage, group and team identities must be supplied explicitly and reviewed against retained source evidence; these are not inferred from league names. Sportmonks [stages](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/season-schedule/stages) must be mapped explicitly to regular season, split group or playoff. All seasons, stages, groups, teams, fixtures and statistics in the new tests are synthetic.

## Inputs and bounded usage

`modelfc.providers.sportmonks_saved.assess` takes a competition (`DK_SUPERLIGA` or `SC_PREMIERSHIP`), an aware `as_of`, pinned contract bytes and a tuple of `SavedPage(raw, sha256, retrieved_at)` objects. `retrieved_at=None` preserves missing original receipts. Read already saved bytes; do not replace an original capture timestamp with the time this report is run. SHA-256 pins establish byte integrity, not authenticity or independent validation.

The version-1 contract requires exactly these fields:

- `version: 1`, `provider: sportmonks`, competition and matching integer league ID.
- Explicit integer `season_id`, ISO `season_start` and `season_end`, and `timezone` (`Europe/Copenhagen` or `Europe/London`). No implicit European season arithmetic is applied.
- `stages`: each has an integer `id`, exact `name`, `phase` (`REGULAR_SEASON`, `SPLIT_GROUP`, `PLAYOFF`) and explicit `group_ids`; split groups require non-null group IDs.
- `team_bindings`: exact provider ID/name and canonical `history_name`; duplicates are rejected.
- `statistics_scope: FT_90_REGULATION` and provenance label `UNKNOWN`, `SYNTHETIC` or `SUPPLIED`. This label cannot qualify a model or validate genuine data.

The test helpers in `tests/test_sportmonks_saved.py` are executable examples of the schema, clearly labeled synthetic rather than suggested production identity mappings.

Saved JSON requires `data` and `pagination.current_page/has_more`. Supply the complete finite pagination chain in any order; the adapter never follows URLs. Limits are 32 pages, 200 fixtures/page, 8 MiB/page, and 16 MiB total. A complete supplied pagination chain is not evidence of a complete season: provider filters, missing stages and missing future fixtures can all hide fixtures.

Each fixture pins league/season/stage/group, ID, kickoff string and Unix timestamp, state, participants, and available statistics. Exact embedded season/stage/group identities are checked when present. UTC kickoff is converted to the explicit local timezone for date checks. Duplicate IDs or kickoff/team identities, contradictions, invalid/missing identities and partial participant statistics block normalization. FT requires length 90; CURRENT score pairs and unpartitioned regulation corner type 34 are accepted. Extra-time/penalty scores and explicit nonregulation corner periods are rejected. AET, penalties, abandoned/suspended and unfinished fixtures supply no completed regulation history. Zero is a real statistic; absent/both-null pairs remain missing. Same-local-day results are excluded from history.

`normalize` returns frozen fixture records carrying original page hash, contract hash and original retrieval timestamp. Records do not fabricate actual completion times from kickoff or retrieval, and are not directly claimed to satisfy the goals model's frozen-input contract.

## Report interpretation

Corners, BTTS and goal totals have separate observed history gates and per-team home/away counts. Missing corner statistics cannot become available because goal scores exist, and vice versa. The totals capability references `goal_totals.VERSION`; this adapter implements no second totals model. Model consumption still requires the shared totals frozen-input contract and separately accepted qualification evidence.

The report always has `validated=false`, `live=false`, unknown entitlement, unassessed odds, and zero provider requests. Each market remains `model=UNQUALIFIED` and `input_state=BLOCKED`. Observed history validity means only that supplied rows passed these structural checks. It does not certify samples, calibration, fixture completeness or production readiness.

Original provenance remains UNKNOWN if the contract or relevant capture receipts are unknown. Calendar capture freshness uses six hours. History reports stale when captures or the latest usable result exceed fourteen days. These conservative report windows are diagnostics, not provider acquisition policy or evidence of model calibration.

An optional separately pinned calendar index requires competition, season ID, a source reference and fixtures with exact fixture/stage/group/kickoff/home/away identity. Mismatches report missing and extra/conflicting counts. Even an exact match is `MATCHED_SUPPLIED_INDEX` with `INDEPENDENT_CALENDAR_ACCEPTANCE_REQUIRED`; overall calendar completeness stays `NOT_ESTABLISHED`. Copying the observed subset into an index cannot attest completeness.

## Genuine-data acceptance still outstanding

No saved genuine Denmark/Scotland fixture payloads were available in this workspace. The next step is a read-only offline run against retained pages and original capture receipts, with independently checked season/stage/group/team mappings and a complete calendar reference. Preserve source bytes and hash pins. Verify the account's actual accessible fields/coverage separately; successful synthetic parsing proves neither entitlement nor genuine completeness.

For each market separately, retain actual regulation statistics, missing/sample counts, completion-time evidence needed for model freezes, and accepted model qualification before considering readiness. Exact fixture/book/period/line odds matching remains a separate downstream requirement. This slice neither requests provider access nor activates collection, forecasts, schedules or production writes.
