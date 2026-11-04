# Disabled acquisition-to-EPL saved-input handoff

This local slice adds optional `retain_handoff=True` to the existing guarded
acquisition call. The default is unchanged. It retains exact metadata bytes and
an immutable experiment/session/reservation/payload/snapshot correlation in the
same private acquisition store before writing the successful completion receipt.
It adds no request, budget ledger, scheduler, provider client or production caller.
A correlation-write failure leaves the existing claimed attempt nonretryable.
Old receipts are not backfilled or assigned reconstructed retrieval timestamps.

`pl_acquisition_handoff.prepare(..., enabled=True)` reads a completed pinned chain
and returns inputs for the existing `pl_saved_orchestration` observe stage. It
requires an already-frozen qualified E0 TEAM_TOTAL forecast, explicitly supplied
approved contracts and cross-provider fixture binding, and one home/away line
3.5, 4.5, 5.5 or 6.5. The forecast must precede the original quote retrieval; the
handoff must occur before kickoff. It never creates a forecast or qualification.
The reader reparses exact retained payload/metadata and compares the complete
saved snapshot. Claimed fixture, ordered teams, kickoff and bookmaker must match.

Correlated unavailable/stale prices return `quote_eligible=False` but still supply
observation inputs: the existing observation writer must retain that failed latest
observation so an older quote cannot reappear. Provenance/identity/qualification
failures reject instead. The receiving existing coordinator rechecks its current
contract and actual clock. No publication call is made by this helper.

This is a trusted-writer correlation, not a signature or proof against a privileged
store writer. Inputs and store identity must be supplied by an accepted deployment.
The completion receipt is the immutable commit marker; interrupted/missing chains
are not repaired. The optional writer remains under acquisition's existing shared
runner lock, reservation and no-retry behavior; the read-only helper adds no lock.

Synthetic acceptance covers real saved E0 forecast/quote handoff, preservation of
original retrieval time, repeated reads, unapproved/wrong/late inputs, missing and
altered chain members, unclaimed incidental fixtures, latest-unavailable suppression,
and one existing reservation despite interrupted correlation. No genuine inputs,
provider requests, production state, live model qualification or publication were
used. Next operational integration still needs qualified E0 freeze before dispatch,
authentic fixture mapping and explicit runtime/cadence/budget authorization.
