# Paused storage upgrade candidate

This is a reviewed-source candidate, not evidence of VPS execution or real
Codex credential isolation. Its only target is commit
`538046ff8451ee918d11caabee5279cb4d5be418`, tree
`0b56426e7b4604e2f4bf1a66d07aab4bfd78623a`, from the retained old mirror
head `d40635666355e543a1edab4e40d305b0318003e8`. Do not use it for other
versions or to prepare a second image. The terminal and operator record hashes
are fixed in `storage_settings_proposal.py`. Those hashes bind retained bytes;
an operator must establish the records' meaning independently.

Before a separately approved host execution, install the exact reviewed tool
and its source dependencies from a verified protected artifact. Independently
verify the source `.git` is the matching owner-run deployment release, the
bridge lock device and inode, and the complete immutable preparation records.
Verify all three installed service ExecStart commands use the same protected
Python executable; resolve its canonical path, record its SHA-256, and check
that the reviewed candidate supports that exact interpreter. The provided
interpreter path and hash are operator attestations, not automatic systemd
discovery. If these facts are unavailable or inconsistent, stop.

The existing trusted operator hold must exclude other privileged bridge,
mirror, storage and mount changes; it must continue until the operation and
any uncertain child are reconciled. The existing lock and receipts checks
provide cooperative exclusion, not a fence against privileged actors. Keep
timers disabled and services inactive. The helper independently rechecks
paused services, worker processes, units, lock and receipt state. It re-admits
the prepared filesystem under the candidate settings before either switch.

After the above facts are established, the protected installed module is
invoked once as root using isolated Python (`-I -S -B`) and the arguments
`--source-git`, `--protected-python`, `--python-sha256`, `--lock-device`,
`--lock-inode`, `--terminal-record`, `--operator-record`, and `--attempt`
(a fresh 32-character lowercase hexadecimal identifier). The exact reviewed
artifact's module invocation must be used; neither settings nor records are
supplied by a worker or task. The explicit flags
`--operator-hold-and-record-attested yes` and
`--operator-attests-execstart-python yes` state the above operator checks.
There is no retry or automated rollback.

For a protected artifact directory verified to contain this exact candidate,
the invocation shape is:

```text
<canonical-python> -I -S -B -c 'import sys; sys.path.insert(0,sys.argv.pop(1)); from ops.bounded_worker.bridge_paused_upgrade import main; main()' <protected-artifact-directory> --source-git <verified-source-dot-git> --protected-python <canonical-python> --python-sha256 <verified-python-sha256> --lock-device <device> --lock-inode <inode> --terminal-record <protected-terminal-record> --operator-record <protected-operator-record> --attempt <fresh-32-hex> --operator-hold-and-record-attested yes --operator-attests-execstart-python yes
```

The angle-bracket values are independently verified operator inputs, not
literal shell tokens. Do not use task-provided paths or hashes.

Before fetching, the helper saves old loader and settings bytes and an intent
in a root-only journal, and creates a persistent unresolved marker in `/root`.
It retains that marker even on success for an operator to reconcile. It then
fetches through the fixed owner-run local Git transport, verifies the exact
commit and tree, stages protected files, validates the proposed settings and
filesystem identity, compare-and-swaps the mirror, switches the loader, and
finally replaces settings. Each live switch has a durable preceding intent.
Only `workspace_root` and `storage` change in settings. `installation.json`,
credentials, policy, units, timers, receipts and workspace contents are not
changed. Separate ref, loader and settings changes are **not atomic together**.

On any failure, interruption, timeout, lost result or missing completion
record, retain the hold and paused state; do not retry with a new identifier
or delete markers, staged files or records. A killed process group cannot prove
that all descendants stopped. An independent operator must first inspect the
current ref and exact loader/settings bytes against the protected journal,
resolve any in-flight process or manager work, and determine a separately
reviewed reconciliation or checked restoration. A power loss can leave an
intent without an observation. Even `configured_paused` is not authorization
to enable a timer, start Codex, or claim its credential boundary was tested.

Unit tests use synthetic subprocesses and mocked transition operations. They
do not prove real host Git, interpreter, filesystem or systemd behavior.
