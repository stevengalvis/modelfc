# Prepared storage integration — offline proposal only

This document does not authorize installation, configuration, a model turn or
bridge activation. PR #256's [prepared-only report](https://github.com/stevengalvis/modelfc/pull/256#issuecomment-6641320066)
records one fully allocated and mounted image and successful in-memory storage
admission. Its `capacity_pending` terminal observation is not, by itself, proof
that the final postcondition returned successfully. The separate operator
record and its contents must be independently verified. Preserve the unresolved
stage marker and all fixture and receipt evidence.

## Proposed bridge settings

The exact protected terminal observation is SHA-256
`7be678d162e3c14771c270beb9b601b0330162873df12f36d9b661407e1f918d`.
The separately fsynced operator record is SHA-256
`aab5f93bc9861c6162ea10183f408282b3051826d87ad6dbf6a60c3455e4e3b1`.
Their private paths and image identities must come from protected retained
evidence, not placeholders or guesses. Check the operator record actually binds
this observation, successful `prepared_only` return and final capacity check;
matching a hash alone does not establish those statements.

The new settings are exactly the current settings with `workspace_root` replaced
by the observation's `proposed_storage.workspace`, and the `storage` object set
to the observation's `proposed_storage`. All other keys and values stay the same.
The old settings' exact bytes must hash to the terminal's `settings_sha256`.
`storage_settings_proposal.propose` computes this candidate without filesystem
I/O or installing it. It does not examine the opaque operator record's meaning,
verify host state, or authorize the result. Never publish the private output.

Before any separately approved settings change, verify current protected bridge
release and settings, same active boot/mount, image/backing/mount identities,
full allocation, trim exclusion, source metadata and operator component. Run
the existing protected `StorageConfiguration` and `admit` checks on the target
host. Under the pinned bridge lock, keep timers disabled and services inactive,
and block on unknown receipt, unit, cgroup, process or launch state. Verify all
state, evidence, publisher and mirror roots remain disjoint from storage and
token files remain outside the mount. Preserve the current settings byte-for-byte
in protected rollback evidence; stage a root-owned 0600 candidate and verify
the existing `_settings` parser and all protected paths before any atomic switch.
Recheck pinned settings/identities before the switch. Do not start a bridge mode,
rewrite old receipts, clear the marker, or remove an old workspace. On uncertainty
retain the old active settings where possible and stop for reconciliation.

The normal application deployment at `538046ff8451ee918d11caabee5279cb4d5be418`
does not establish that the separately installed protected bridge points to this
code. The last reported bridge entrypoint was `d40635666355e543a1edab4e40d305b0318003e8`;
recheck rather than assuming it remains current. A separately approved protected
bridge upgrade must install and verify a release containing the merged PR #245
storage code before applying these settings. Preserve the previous release and
its exact rollback path. Because the fstab entry is `noauto`, a reboot requires a
separately reviewed mount/identity recheck before admission; never reformat it.

## Real Codex boundary and acceptance

The outer app-server intentionally receives the worker's Codex auth/config. The
adapter config requests denial of that home credential directory to generated
tools, disabled tool networking and exact workspace write permissions. This is a
configured nested sandbox policy, not a verified kernel boundary. Bridge reader
and publisher credentials belong to separately protected root-owned files and
processes; file ownership/DAC and the service's namespace are the outer boundary,
not the app-server filesystem profile alone. The fake-storage fixture does not
establish generated-tool denial, alias handling, inherited descriptors, socket
relays, refresh, or Codex app-server behavior.

The direct no-model CLI probe in PR #250 does not run the production app-server
profile and was not admitted on the host; it must not be substituted for this
boundary. A future real-Codex acceptance requires a separately approved bounded
model turn using the pinned installed app-server and exact generated-tool profile,
the actual worker UID/GID and storage namespace, fake canaries, a trusted observer
outside the worker, and a fixed metadata-only credential-denial check that never
reads secret content. It must bind the tool process and observations to the exact
unit/invocation and independently verify cleanup and storage reinspection. A
successful targeted check alone would not prove absence of unknown aliases or
socket relays. If the necessary reachable-resource boundary or observation is
not enforceable, stop rather than enable the bridge or assert isolation.
