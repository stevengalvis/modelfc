# ZEN-48 activation handoff — separate approval required

This PR stages validation source only. Do not run the current installer against
this checkout: its unchanged hashes intentionally reject these bytes. Do not
change permissions, host files, trust pins or deployment configuration as part of
ZEN-48. Local/source PASS is not deployed-validator PASS.

Before activation, prepare and review a separate operator change:

1. Fix the reviewed candidate commit and compute SHA-256 for every file in
   `release_evidence.FILES`, plus its aggregate `release_evidence.revision`.
   Review the harness and both report consumers together.
2. Prepare an installer revision with explicitly reviewed pins and backup/rollback
   for **both** `validate_capture.py` and `offline_report.py`. The current installer
   assumes the harness is already installed and does not replace it; merely
   updating its hashes is insufficient. Preserve existing owner/mode requirements.
3. Coordinate activation with the default-branch report consumer: the old installed
   report lacks `strategy_validation` and is intentionally rejected by the new
   consumer. An old installed PASS cannot satisfy this new contract. Do not merge
   into the coordinated release path until this rollout order is reviewed.
4. Obtain separate explicit approval for installation/pin changes. After approval,
   use the reviewed operator procedure while validators are idle, retain exact
   before/after hashes and rollback artifacts, and verify installed revision.
5. Run exact-head OFFLINE acceptance twice against the reviewed candidate, require
   strategy/core/replay PASS, zero provider requests, complete cleanup and no
   credential leakage. Exercise stale-SHA rejection and report-contract negatives.
   Record candidate SHA, installed revision, image/configuration binding and receipt.
6. Only then describe strategy validation as installed acceptance. LIVE/provider
   compatibility and public UI checks are outside this activation. PR170 public
   functional checks need not be repeated for this source-only change.

No steps above have been executed by this PR. Merge and deployment are not authorized.
