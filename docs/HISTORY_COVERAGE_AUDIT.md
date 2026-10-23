> Reused for ZEN-56 from PR134 head `e046a4a059a2d874f5c2bac3f9e7fcd7ca92e537`.
> The original audit contract below is preserved; historical context is October 12, 2026.
> See [PL history readiness](PL_HISTORY_READINESS.md) for the opt-in integration,
> current limitations and remaining acceptance.

# Offline E1/E0 history coverage audit

Before daily corner/shot rankings or equal-match-count season comparisons, measure
what the existing saved history actually contains. This independent draft adds
only `python -m modelfc.history_coverage`; no HTTP route, UI, provider client,
refresh, publication, ACL, model, schedule or production state is changed.

The command reads at most 32 explicitly selected regular, non-symlink canonical
E0/E1 CSV files, each at most 2,000,000 bytes. Every source requires its exact
SHA-256 and actual retrieval timestamp. It validates the pinned bytes through the
existing strict Football-Data team-match-stats adapter, using temporary copies in
a private directory that is removed afterward. Originals are not reread or changed.
Duplicates, mismatched league, malformed values, contradictory scores/FTR and
shots-on-target exceeding shots reject the entire audit. There is no silent repair,
quarantine, result fetch or fallback to incomplete rows. The known November 10,
2024 Burnley/Swansea contradiction must receive a separate explicit data decision;
it is not silently excluded by this command.

## Bounded disposable acceptance

Use an existing reviewed release and locked interpreter with networking disabled.
First work on disposable copies of the actual saved history under the authorized
writer identity. This does not grant access to any source or authorize new fetches.
Each repetition of `--source` binds one CSV to its hash and genuine retrieval time:

```sh
PYTHONPATH=src python -m modelfc.history_coverage \
  --competition E0 --before ACTUAL_EXCLUSIVE_DATE \
  --source /REVIEWED/E0_2526.csv ACTUAL_SHA256 ACTUAL_RETRIEVAL_TIMESTAMP \
  --source /REVIEWED/E0_2627.csv ACTUAL_SHA256 ACTUAL_RETRIEVAL_TIMESTAMP
```

Run E1 separately. Unknown retrieval time is a blocker: do not substitute file
mtime, copy time or today's time. Record stdout privately with an explicitly chosen
output path; the command does not publish it. Verify original CSV hashes before
and after. For deterministic comparisons, pin the same inputs and cutoff; only the
observation clock/source age changes between ordinary CLI runs. The Python
`audit_sources` function accepts an explicit observation time for offline tests.

Exit 0 means validated supplied rows were audited. Exit 1 with the fixed
`HISTORY_COVERAGE_REJECTED` diagnostic means no report was produced. Do not accept
a partial report after one source fails. Investigate the exact pinned file using
the existing adapter in private operator scratch if row-level diagnosis is needed;
private source paths/rows never enter this command's failure output.

## Meaning of the report

- `scope=OBSERVED_ROWS_ONLY`, `calendar_completeness=NOT_ASSESSED`: CSV validation
  does not prove that every expected match/result is present. A fresh download of
  an incomplete/stale CSV is still incomplete/stale. There is no FRESH/READY label.
- `sources` preserve canonical filename, hash, actual retrieval timestamp, age at
  observation and presence/absence of each home/away statistic column. Hashes
  identify bytes; provenance still requires independent operator verification.
- Eligible rows have dates strictly before `--before`, which cannot exceed the
  observation's England/London date. Same-day and future rows are excluded from
  all season/team coverage denominators; their separate count is not a sample.
  All input rows are still validated; malformed future rows are not silently erased.
- `eligible_fixtures` counts unique matches once, not mirrored team observations.
  Seasons are derived by the existing adapter's July boundary from actual dates.
  For each statistic, `complete_fixtures/fixture_denominator` counts matches with
  both team values. `known_team_values/team_value_denominator` and missing values
  retain one-sided missingness. Real zero is known; missing is not zero.
- Per-season team rows expose actual matches plus known-for/known-against counts
  for goals, corners, shots and shots on target. They are descriptive coverage,
  not means, rankings, probabilities or betting samples. An empty cohort returns
  no season rows and a null latest date, not a statistic with value zero.
- Latest eligible match date and days since that match are separate from retrieval
  age. Neither metric proves calendar completeness or public-reader authorization.
  `provider_requests` is always zero; there is no provider dependency.

Acceptance should exercise genuine available CSVs plus synthetic absent columns,
partial cells, real zeros, old seasons, duplicates, changed hashes, contradictory
shots and cutoff boundaries. Independent fixture/calendar reconciliation is still
needed before asserting complete recent history or promoting an insight.

## Exact outstanding data and host requirements

This executor contains no genuine current E1/E0 CSVs or E0 calendar response. The
only committed E0 CSV is a three-row 2023/24 adapter test fixture with goal columns
and no corner/shot columns; it is not current coverage evidence. Synthetic tests
prove the audit's behavior, not provider/league coverage. No real-data report is
claimed here. The parent's Oct12 16:35 E1 forecasts using supplemental history
through Oct11 do not establish fresh public CSV publication or E0 coverage.

The [metadata/reuse draft PR133](https://github.com/stevengalvis/modelfc/pull/133)
documents the complementary odds and calendar blockers. In particular:

1. Supply existing canonical E1/E0 CSVs with reviewed hashes/retrieval provenance;
   run this audit privately and inspect counts for actual goals/corners/shots.
   Report absent columns separately from missing cells and lack of recent matches.
2. Supply a saved E0/PL calendar, actual retrieval time/hash and independently
   verified 20-team provider-ID/name/history bindings. The existing ELC transport
   and completeness parser are E1-specific (24 teams, 552 fixtures, 46 rounds).
   The provider-neutral fixture/status types and immutable cache can be reused,
   but E0 needs its own reviewed 380-fixture/38-round validation. No league switch,
   guessed roster, forecast waiver or live request is authorized by this audit.
3. Public capabilities explicitly authorize only E1; refresh publication grants
   the API identity current E1 CSV access only. E0 requires separate code-policy,
   publication/ACL and actual API-identity acceptance. An ACL grant alone is not
   sufficient. Do not expose multi-season raw history just to serve daily insights;
   prefer a separately reviewed sanitized read-only aggregate once coverage passes.
4. PR132's saved-calendar projection needs reviewed source persistence/publication;
   it deliberately reports E0 missing. Do not change its reviewed head. Any future
   integration with PR129–133 requires combined regression and disposable actual-
   identity acceptance, not a merge/deploy/access/schedule change in this task.

After those checks, the smallest insights follow-up can compare the same number
of actually covered prior matches per team/season, exposing denominators and
missingness. Means remain descriptive and must not be presented as validated
line probabilities or a reason to manufacture a daily bet. E0 BTTS stays disabled.
