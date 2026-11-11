# Offline next-day cutoff comparison

The existing `pl_chronological_evaluation` and `pl_evaluation_comparison` commands
accept `--next-day` only as an explicit option. Their enable flags remain required;
without the option the historical strictly-before-target-date policy is unchanged.
Python callers use `evaluate(..., enabled=True, next_day=True)` or
`compare(..., next_day=True)`.

For a target London calendar date D, next-day mode trains strictly before D−1.
This corresponds to the deployed next-day forecast's actual observation-day cutoff.
Targets and outcomes retain their real date D. The corner path calls the existing
`predict_corner_fixture` with that filtered history and unchanged model/gates/lines.
Its paired empirical venue baseline uses the same cutoff and eligible observations.
BTTS model and baseline also use the earlier cutoff as retrospective research only;
this does not add BTTS to the deployed dispatch path.

Report `training_policy` is
`EXPANDING_SUPPLIED_FIVE_SEASONS_STRICTLY_BEFORE_TARGET_MINUS_ONE_DAY`.
Paired-row fields are unchanged, so the existing fixed-bin calibration reader can
consume the pinned new comparison report. The existing seed, date-block bootstrap,
season reporting and nonfinite-loss handling are unchanged. No thresholds or
qualification decisions are introduced. Production forecasting files and their
implementation pin are unchanged by this evaluator-only adjustment.

## Bounded real-data run, not performed here

Reuse the exact five originals E0_2223.csv through E0_2627.csv and their individual
original acquisition timestamps and SHA-256 pins recorded in ZEN-74. Preserve the
prior retrospective dataset cutoff `2026-11-04T01:28:23.585721+00:00` when comparing
with the previous report; record the new actual execution time separately. Do not
rewrite originals or represent this data cutoff as a prospective capture time.

Use the existing commands with `--enable-offline-evaluation` or
`--enable-offline-comparison`, `--season-year 2026`, `--as-of` that dataset cutoff,
`--next-day`, and five existing `--source CSV SHA256 RETRIEVED_AT` tuples. Retain the
output hashes; pass the new comparison bytes/hash to the existing calibration
reader. Keep the originals and result rows private, and report cohort changes,
paired losses/uncertainty and calibration without model tuning.

This is numeric retrospective cutoff/model equivalence, not operational parity:
calendar freshness/completeness, whole-day eligibility, authentic historical
availability, actual pre-kickoff persistence and live quotas remain unassessed.
Existing eligibility can differ from deployment's calendar-driven whole-day holds.
Historical snapshots acquired after fixtures cannot prove what was known at kickoff.

Workspace filename searches (including ignored files, excluding dependency/git and
credential directories) found no E0 season CSV originals or saved comparison/
calibration JSON. The original five-season, paired comparison and calibration
Linear posts expose hashes and summaries but no downloadable attachments. Thus no
real-data evaluation was run. Reading/exporting retained VPS originals requires
separate authorization; this change does not grant it or authorize provider access.
