# ZEN-53 owner evidence: local PID 1 and completed-capture correction

This is an **offline candidate**, not a recovered or accepted VPS capture. It is
based on [PR191 report 6384364441](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384364441).
Keep that failed report, the historical 65534:65534 records, and the prior
published archive byte-identical. Do not run `bridge.finalize` or create a stage.

The report independently observed host source owner 1001:1001 and restricted
65534:65534 for the two exact Codex sources; content, inode, mode, size and
timestamps agreed. It retained raw maps, overflow=65534, and direct statmount
IDMAP-clear evidence. These file and mount observations remain useful.
However, the host-held collector exited 2 after the 180-second finish-signal
timeout. The request time is not retained, so there is no proof that FINISH
was delivered and acknowledged. Nothing in this amendment changes that outcome.

The old v1 validator incorrectly required restricted `/proc/1/ns/user` to
equal the host user namespace. In the reported restricted PID namespace,
`/proc/1` is its **local** init. The v2 predicate instead requires the trusted
host collection's `/proc/1` user/mount/PID namespace links to equal the host
collector's own links, and the restricted collection's `/proc/1` links to equal
its **own** links. It requires host and restricted namespace identities to
differ, exact UID/GID maps and overflow settings, and all pre-existing exact
file identity and ownership predicates. Both Codex files must also resolve to
one consistent old/unique mount-ID pair within each captured context; direct
IDMAP status must remain clear. It does not create an invented host
PID 1 field in the restricted observation. The host side's observed local PID
1 is the host anchor; stable source descriptors and a completed host-held
capture corroborate the association between contexts. These remain statements
from a future **trusted collector**, not self-authenticating JSON facts.

Version 2 also requires host and restricted exit codes 0, held source
descriptors, an acknowledged FINISH and five strictly ordered monotonic event
times recorded by the **host collector on one clock**: hold-ready, restricted
start acknowledged, restricted finish acknowledged, FINISH acknowledged, hold
closed. The actual exit 2 and absent FINISH acknowledgement cannot pass. The
collector must retain the raw control record and its hash; the schema checks
the supplied digest's shape, but cannot prove its provenance. A schema-valid
synthetic fixture may produce only a provisional bridge plan.

The actual `capture_ownership_v2.py` bytes are **not in this workspace or the
published archive**. The report pins SHA256
`a10c63987a7526331dde2eda7ca05a47ba182633d466d9167b1ad14f13c47902`
and retains the script privately at
`/var/lib/zen53-owner-evidence-20261023-47bda5f4ef7f348535696558/`.
The full command/control record is likewise private (reported SHA256
`d1699752090dde21dde5954ee3cccf2df0a93fbbbb428f5d8d09b3deecb531a6`).
The report and a hash **do not supply executable source**. The finish-signal
handshake cannot be patched, independently reviewed or attributed offline
without those exact retained bytes and the control log. Do not guess whether
timeout was a missed write, blocked read, ordering bug or late delivery.

Next, recover the exact collector source and control-event record *read-only*
from that retained evidence, screen them for secrets and independently verify
the reported hashes. Inspect their FINISH send/read/ack sequence and only then
prepare a separately reviewed minimal collector correction if needed. Preserve
the failed record as failed. If the retained record cannot prove completed
control, one new bounded **read-only** dual-context capture will be required
after the collector correction is reviewed. It must record the host-side
five-event sequence, both exit codes, stable descriptors and both local PID 1
namespace sets, plus rechecked file/mount/overflow facts. No earlier
observation can be retroactively converted to a successful bracket. Stage
preparation and any runtime invocation remain separate approval scopes.

No current host value, future inode, control acknowledgement, collector byte,
or successful capture is invented in this package. Tests execute only the
pure validator and bridge against synthetic data; they cannot authenticate a
collector, prove live namespace semantics or establish stage readiness.
