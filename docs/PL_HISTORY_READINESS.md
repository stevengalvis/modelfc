# ZEN-56: offline EPL calendar and corner-history readiness

This slice belongs to ZEN-56 under integration parent ZEN-55. ZEN-35's completed
audit remains separate. Forecast capture belongs to later ZEN-57.

The local integration starts from PR177 `520242fb2c2366025cef629ccb1703cad32f852d`
and merges main `ad4068fd283b4b126bb29139012dc13fcb920337`. PR177's four files
are unchanged. PR134 `e046a4a059a2d874f5c2bac3f9e7fcd7ca92e537` supplies the
strict pinned CSV audit, documentation and regression tests. Its source loading
is extracted into `validated_sources`; the original `audit_sources` defaults and
output are preserved. Only the new EPL caller allows explicit unknown retrieval
times, enforces filename/row season agreement, and rejects results after their
claimed acquisition date or current-season kickoffs at/after retrieval. Neither draft's old validation
is acceptance of this combined implementation.

## Invocation and interpretation

`python -B -m modelfc.pl_history_readiness` is a separate, offline-only command.
It has no production caller, workflow, provider acquisition, forecast model call,
publication, public route or activation flag. It reports DISABLED unless invoked
with `--enable-offline-audit`. Production remains disabled even on a passing audit.

Supply the same pinned `--bundle`, `--bundle-sha256`, `--contract`,
`--reviewed-contract-sha256`, `--season-year`, `--day`, and `--as-of` as PR177
(the competition is fixed to E0), plus repeated:

```text
--source /supplied/path/E0_2324.csv EXACT_SHA256 ORIGINAL_RETRIEVAL_TIMESTAMP
```

Use literal `UNKNOWN` for an unavailable original retrieval timestamp. No mtime
or observation-time fallback exists. The selected London day must equal the
London date of `--as-of`; historical replay is explicit, never current acceptance.
All source reads are bounded regular-file reads with symlink rejection. Temporary
private copies support the unchanged strict CSV adapter; supplied files stay
unchanged. Invalid arguments and source failures emit fixed diagnostics.

The report contains the reviewed contract's exact provider IDs, provider names,
history names, exact UTC fixture IDs/kickoffs/ordered teams, source hashes and
retrieval ages. This is an internal report: unlike PR177's count-only CLI, it
contains reviewed team names. It does not contain input paths or raw source text.
Hashes and supplied timestamps do not independently authenticate acquisition or
human review. `history_provenance: SUPPLIED` means supplied metadata, not verified
external attestation. Evidence review remains a separate acceptance gate.

Readiness requires all five named E0 season sources ending in the calendar
season, strict validation without repairs or exclusions, known original retrieval
times, current-season retrieval age at most 14 days, and latest eligible result
age at most 14 days. Historical source age is reported but is not substituted for
current-season freshness. Missing source seasons remain explicit. These gates
are conservative first-slice policy, not statistical confidence guarantees.

Only records strictly before the selected London day count, matching the corner
engine's date cutoff. Finished calendar fixtures before that day must match
current-season history by exact date and ordered history labels, agree on final
score, and have both corner fields populated. Extra current-season history also
blocks readiness. Same-day results are excluded and counted. This proves only
current-season coverage before the cutoff; it does not prove completeness of
older seasons or resolve aliases. Real zero corners count; missing corners do not.

The audit reports all 20 mapped teams' home/away complete-corner counts across
that five-season window and seasons lacking exact-label corner history. It never
borrows Championship history or silently merges historical aliases. Missing older
history can reflect promotion, a label change or absent evidence; the report does
not infer which. Venue means home/away, not physical stadium identity or continuity.

The fixed thresholds match the existing corner engine defaults: 100 complete
team observations globally and five home observations for the home team/five away
observations for the away team. Team `home_eligible`/`away_eligible` fields describe
only these local venue thresholds. Fixture `corner_inputs_eligible` also requires
all global gates, a fresh resolved calendar, and a scheduled/timed kickoff strictly
after observation. Cancelled, postponed, completed and active fixtures cannot pass.
A valid fresh empty calendar day is reported as EMPTY only when history gates
also pass; stale/missing calendar evidence never establishes emptiness. Exit zero
means INPUTS_ELIGIBLE or EMPTY, not permission to forecast or publish.

## Dated evidence and outstanding real-data acceptance

The posted October 20 PR177 audit at
https://github.com/stevengalvis/modelfc/pull/177#issuecomment-6282622370
reports 380 fixtures and 20 reviewed mappings at head `520242fb...`. Calendar
payload SHA256: `7d90fb2d856c3e6905314b79f14a04c1c2372427213b88df7384679731023a21`;
reviewed contract: `467fa6a3954f88c38d338a4657ca9eda291a860cda9f3d4f74b12b19adfcb5b4`.
Original retrieval was October 20 03:02:36.694675 UTC; fresh at the recorded audit
03:25:02.549729 UTC, not fresh on October 22.

The separate history evidence at
https://github.com/stevengalvis/modelfc/pull/177#issuecomment-6282520228
reports E0 2023/24 SHA256
`b2e057b0ed959f198b0f63d2391c01239f3608e6de5db68edab3f88e04d07ff3`,
retrieved October 20 03:13:00.656885 UTC, strict-reader pass for 380 fixtures,
and combined five-season pass for 1,580 fixtures. Those bytes remain private;
this cloud workspace has no supplied evidence files to replay. Tests are synthetic.

Remaining acceptance:

1. Supply accessible preserved original calendar bytes, reviewed contract,
   history CSVs and their receipts; verify pins and review provenance. Older
   2022/23, 2024/25, 2025/26 and 2026/27 retrieval provenance is still unknown in
   the posted evidence and must remain unknown until established.
2. Replay the dated capture at its genuine observation time, then separately
   establish fresh calendar/current results evidence through authorized work.
   Posted current results stop October 12; do not manufacture later results or
   reuse the old calendar as fresh.
3. Review actual per-team/home/away counts, promoted-team gaps, and the separate
   2022/23 Nottingham versus later Nott'm Forest alias. This implementation does
   not authorize alias merging or infer physical venue continuity.
4. Demonstrate genuine positive-day (including eight fixtures), empty-day,
   stale/missing evidence and reconciliation failure behavior on the final code.
5. Obtain independent review, full backend/frontend CI and exact-head Trusted
   OFFLINE acceptance after publication is authorized. Local synthetic checks
   cannot replace these gates. No VPS actions are authorized in this task.

E1 code, runner artifacts, public capabilities, odds acquisition, BTTS and goal
forecasting are unchanged relative to current main. PR177 remains draft; no push,
comment, state change, merge, deployment or runtime activation is performed here.
