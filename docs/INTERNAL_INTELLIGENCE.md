# Private Zeno research intelligence V1

This is a deterministic operator/research interface, not an agent, MCP server,
public API, frontend, experiment runner or promotion mechanism. It makes no
network requests and cannot alter predictions, markets, opportunities, outcomes,
budgets, collection cadence or models.

## Snapshot contract and storage

`corner_research.create_snapshot(state_dir)` appends schema-1 private manifests
under `state/research-snapshots/<32-hex-snapshot-id>.json`. The trusted Python
caller supplies configured state; future transports must bind that configuration
server-side and expose IDs only. The CLI reads `MODELFC_STATE_DIR` (default
`/var/lib/modelfc/state`) and has no path option.

The exact manifest fields are `schema_version`, `record_type=research_snapshot`,
`snapshot_id`, `created_at_utc`, `release_sha`, `competition=E1`,
`family=team_corners`, `cohort`, `evidence_cutoff_utc`, `entries`, `record_hash`.
The cohort identifier is `E1-team-corners-all-predictions-paired-frozen-source-targets-v1`.
Each prediction entry references capture, prediction, optional shadow, frozen
current targets, relevant pre-kickoff observations, optional policy/assessment,
production opportunities, and the exact outcome-chain prefix. A reference has
only `family`, `id`, optional `parent`, and canonical `hash`. No raw captures,
historical rows, control, budget events or credentials are copied into manifests.
Capture references hash the complete immutable capture; other references use the
existing canonical record hashes. Source/history hashes and model provenance
remain in the referenced immutable predictions.

The snapshot ID is the existing deterministic UUID5 identity over the manifest
payload before adding ID/hash. The record hash is canonical SHA-256 over the
payload including ID, excluding the hash. Creation time distinguishes separate
snapshot requests. Publication uses the existing synced temporary-inode and
exclusive hard-link publication primitive. Existing manifests are never replaced.
The private directory is runtime-owned mode 0700; files use the storage helper's
0600 mode. Publication does not opt into public evidence ACLs. The runtime state
must retain its established ownership/ACL boundary, with no default ACL granting
API access to private descendants. No host ACL installation is included here.

Creation acquires shared `prospective/runner.lock`, then shared `state/.lock`,
whenever those lock files exist, including before the first prediction. A runner
lock without a lazy state lock is valid before evidence exists. Truly uninitialized
state with no runner lock fails closed with RESEARCH_EVIDENCE_UNAVAILABLE: it
cannot exclude concurrent first initialization. No lock is bootstrapped. An
initialized runner with zero evidence remains a valid empty snapshot population.
It validates the current public chain and private inputs, identifies and publishes
the manifest under the same coherent read boundary, then releases both locks.
Only identification/integrity work occurs under locks; aggregation happens later.
Bounded filesystem checks occur under the established locks once initialized. No forecast/probability generation or history CSV read occurs.

`evidence_cutoff_utc` is the maximum timestamp from included prediction, target,
shadow, observation, policy, opportunity and outcome evidence; it is not a claim
of provider completeness. Missing shadow/assessment at creation remains missing
for this snapshot even if recovered later. New targets and observations are not
included retroactively. Future shadow comparisons use only the frozen supported
source-observation target cohort, exactly as existing private comparisons do.

Diagnostics read only manifest-referenced files and validate hashes/relationships
without production locks, directory inventories or current history. Corrupt,
missing or symlinked referenced evidence fails closed with fixed research error
codes. Later unrelated files do not affect an existing snapshot. Exact outcome
chain prefixes are validated independently: later corrections do not change an
old snapshot's results; a new snapshot adopts the then-current authoritative tip.
Hashes assume the existing trusted-runtime writer boundary, not protection against
that writer deliberately forging evidence. Keep referenced files during retention
or rollback; deleting them makes affected snapshots unavailable.

## Deterministic tools

- `read_snapshot(state_dir, snapshot_id)` validates the snapshot and returns small
  metadata: ID, hash, creation time and included-prediction count.
- `research_summary(state_dir, snapshot_id)` returns identity/cutoff/release,
  coverage, existing paired forecast report, existing private market-decision
  report, champion versions, fixed shadow version, policy versions and caveats.
  Paired MAE and decisive Brier use frozen source probabilities and validated
  outcomes. PUSH targets are excluded from decisive Brier. Zero samples produce
  null metrics. RMSE/bias/log loss/calibration are not added to this private paired
  contract because the existing paired report does not supply them.
- `segment_comparison(..., segment)` accepts only `venue` or `venue_history`.
  Venue groups are HOME/AWAY. Frozen relevant venue-count bands are `[0,10)`,
  `[10,20)`, `[20,infinity)`, plus UNKNOWN for legacy absent context. Groups have
  stable ordering and include empty groups, paired team N, the same scoring
  reports, shadow-minus-champion MAE/Brier differences and warnings. Negative
  metric differences favor shadow. UNKNOWN is never reconstructed from CSVs.
  Team and user-selected date bins are deferred. Prediction/observation counts
  inside segments describe member venue rows, not independent fixtures; missing
  assessments/later-only observation-level exclusions can occur in both venue
  groups and must not be summed as unique global counts.
- `inspect_fixture(..., prediction_id)` requires snapshot membership and returns
  one frozen fixture, champion/shadow forecasts/targets, historical context,
  recorded prices and frozen paired decisions/policy, settlement from the exact
  snapshot tip, hashes and provenance. Missing research evidence remains explicit.
  Hypothetical units use the existing settlement/American-price convention.

Summary decision states are neither/champion-only/shadow-only/both. Each paired
selection is a decision snapshot; repeated observation snapshots are not separate
placed bets. Qualifying-event W/L/P, units and ROI retain existing hypothetical
one-unit event accounting, including pushes in the settled denominator. Unique
target/bookmaker counts are also retained. Existing champion watchlist selection
limits which later prices were collected; this is not an unbiased market panel.

The low-sample warning uses fewer than 30 settled paired fixtures in summary and
fewer than 30 settled paired team forecasts per segment. This is only a neutral
presentation rule, not a power calculation or significance threshold. All metrics
are descriptive and never authorize promotion or claims of profitability.

## Bounds and failure behavior

V1 rejects populations above 1,000 predictions, 256 targets per prediction,
12 relevant observations, 256 opportunity events and 256 outcome-chain records
per prediction, 20,000 filesystem entries in inspected
evidence families, 2 MB per referenced record, 128 MB aggregate evidence per operation or 8 MB per
manifest. Referenced-file counts and bytes are bounded on replay as well. This is an
explicit bounded V1 cohort, not silent sampling. Larger histories require a
separately reviewed paging/cohort design. Normal diagnostics produce at most
64 KiB of JSON; an oversized result fails explicitly. Fixture views retain the
first 64 target IDs, first 64 frozen shadow targets, first 64 decisions/selections
per observation in their stable evidence order and expose omitted counts.
There are at most four segment groups. No arbitrary filters/formulas are supported.

CLI examples (configured private operator identity only):

```sh
python -m modelfc.corner_research snapshot
python -m modelfc.corner_research summary --snapshot-id <id>
python -m modelfc.corner_research segment --snapshot-id <id> --by venue
python -m modelfc.corner_research segment --snapshot-id <id> --by venue_history
python -m modelfc.corner_research fixture --snapshot-id <id> --prediction-id <id>
```

Successful commands emit JSON and exit 0; research failures emit a fixed JSON
error and exit 1; invalid CLI syntax exits 2. No raw exception/path is emitted.
A publication failure does not modify production evidence or retry collection.
Before activation, authorize operator execution separately; this PR does not run
production commands, install a launcher or change systemd.

## Future transport and public boundary

A future private MCP/agent can adapt these deterministic functions with strict
ID/enum arguments and configured state, without moving calculations into the LLM.
No transport library or LLM call is added. Internal output intentionally includes
bookmakers, odds, no-vig probabilities, model edges, champion/shadow qualification,
hypothetical units/ROI and private challenger provenance.

A future public Zeno ChatGPT interface must have a separately reviewed football
analytics projection. It must not inherit this internal contract or grant an LLM
raw state/filesystem access. Public FastAPI/Caddy/frontend contracts remain unchanged.
Rollback leaves private snapshots untouched. Older runners/readers ignore this
new family; snapshot readers support schema 1 only and fail on unknown schemas.

Snapshot schema 1 pins the existing DeepFC commit/model validation contract. Future
live model constants do not redefine it; supporting another shadow candidate requires
a reviewed schema/validator addition while retaining this old contract.

## Operator lock scheduling

Snapshot creation is an explicit operator task, not part of run-once or a timer.
The runner takes its exclusive runner lock nonblocking. An overlapping snapshot
can therefore cause that scheduled invocation to report BUSY rather than wait.
Before separately authorizing production use, schedule creation away from hourly
:05 UTC and outside active collection, and measure validation duration on the
actual evidence population. Do not repeatedly create snapshots automatically.
The hard input bounds limit work but are not a wall-clock guarantee. Summary,
segment and fixture queries use no production locks and have no such conflict.
Qualification replay dispatches the preserved v1 evaluator by the frozen policy
version, independently of the live production entrypoint. Unknown versions fail
closed. Future formulas must add a versioned evaluator and retain v1; they must
never redefine old snapshot semantics. The current formula and decisions are unchanged.
