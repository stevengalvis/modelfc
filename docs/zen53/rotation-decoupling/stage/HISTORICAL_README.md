# ZEN-53 finish sender: offline controller candidate

This is a prospective, unexecuted evidence-only capture chain, not accepted
host evidence or the operational sandbox. The controller starts a held host collector, waits
for its exact ready event, starts a restricted collector through a **separately
supplied and reviewed restricted launcher**, requires restricted exit 0 and a
complete clear output, writes exactly seven bytes `finish\n` through the held
collector's private stdin pipe, requires its ACK and completion, then requires
host exit 0. It returns BLOCKED on any absent, malformed, late, incomplete or
excess output. Both child streams are drained concurrently with finite caps.
Each child starts in its own session; an unreaped leader reserves its PID/PGID
until bounded group cleanup. Cleanup uncertainty converts success to BLOCKED.

The `BoundedSpawner` supplies `spawn_host` and `spawn_restricted` callables.
They implement
`subprocess.Popen`'s keyword and process interface, including
`start_new_session=True`, and must not reap their child behind the
controller's `waitid(WNOWAIT)` calls. The callables and argv are **not**
authenticated by this module. Passing two names does not ensure distinct
namespaces: synthetic tests intentionally use `subprocess.Popen` twice.
The [retained evidence report](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384364441)
shows both command forms invoke the same Python collector; its `restricted`
argument is only a label and creates no namespace. The different UID map
and namespace observations prove that the *previous* restricted invocation
ran in a distinct context, but neither command nor retained source identifies
the external runner that placed it there. The published ZEN-53
`candidate/launch.py` and `candidate/supervisor.py` are operational
sandbox launchers; they do not launch this dual-context evidence collector
and cannot be substituted without a separate boundary review. A prospective
reviewed runner is sufficient; historical sender internals need not be
recovered. The runner must preserve pipes and the exact restricted context,
and its source/command bytes must be pinned. `restricted_launcher.py` now
proposes a dedicated Bubblewrap command and strict read-only projection;
its actual behavior remains untested.

The returned `PROTOCOL_COMPLETE` means only that the supplied processes
exchanged expected messages in the tested order. `evidence.py` now stores
the four bounded raw streams, their hashes and the controller event order
under a **precreated empty root:root 0700** directory. It writes only new
0600 regular files, never repairs an existing path, publishes a completion
marker last, and provides a read-side verifier that rejects partial,
replaced, extra, FIFO and mismatched objects. It no-follow-walks protected
ancestors: root-owned nonwritable directories, with an exact root-owned
sticky `/tmp` exception; later ancestors must be owned by the required
owner and not group/other writable. The default owner/root contract is
UID/GID 0; synthetic tests override it with the local test user and mapped
root UID. A real preparer must use the defaults and verify that the pathname
is within its approved protected evidence location. The raw
host stdout hash can serve as `control_record_sha256` and the summary hash
as `sender_ordering_record_sha256` in a *later independently authenticated*
v3 input. Those hashes prove only byte identity; the v3 validator checks
schema, not observer authenticity. A trusted preparer must still bind the
reviewed collector hash and both real namespace observations to the raw
record. Root operators must cooperate: shared locks cannot prevent an
arbitrary other root process from replacing a directory during collection.
Do not publish raw streams before sanitization. No runtime stage or sandbox
acceptance follows.

Published dependency: [v3 index](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384945786),
deterministic archive SHA256
`3f2a405f3b03d179f8f66d332e4c3287944ab78e0fbf040160923cc860aa8294`;
`capture_ownership_v3.py` SHA256
`5ea2e4269204b72b4dab7d1fbda3a5d1a94e76f6db08756a5a15c77f00a8b604`;
`owner_context.py` SHA256
`9e4734d1b071678f4f9556ef8334160ba7a6a14157a8850bb7262624193df045`.
The [sender inspection](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6386908586)
found no original sender implementation. The old capture remains exit 2 and
its actual delivery/order remain UNKNOWN. This package does not repair it.

Offline test: `python3 -B -m unittest -q test_controller.py test_evidence.py test_restricted_launcher.py test_owner_adapter.py`.
The tests use only `fake_child.py` synthetic processes and local temporary
directories. The already published v3 receiver
and validator suites separately test exact FINISH, wrong/partial/EOF input and
schema rejection. No genuine collector entrypoint is imported or run here.

The proposed restricted argv uses one read-only bind of a dedicated rootfs
projection, `--unshare-user/pid/net/ipc`, a fresh read-only procfs,
`--clearenv`, `--cap-drop ALL`, and no writable bind. The exact reviewed
runtime inputs are retained in this package: 44 regular files, 9 symlinks,
the pinned collector, and one empty release-directory mountpoint. It never
binds host `/`. The [upstream Bubblewrap source](https://github.com/containers/bubblewrap/blob/main/bubblewrap.c)
documents the options and one-entry UID/GID maps; it does not authenticate
the installed binary or prove runtime namespace/mount behavior.

The unchanged v3 validator requires the two Codex files to share one
restricted mount ID. Two separate file binds would fail. A single read-only
bind of their common release directory satisfies the *proposed topology*.
`verify_release_tree` requires the actual installed release to contain
**only** `bin/codex` and `codex-package.json` with the pinned metadata.
The current release listing is unavailable here. An extra entry stops
before Bubblewrap, without silently broadening access or weakening the
validator. Nested mount screening is negative only; the collector must
observe matching old/unique mount IDs and clear IDMAP on both files.

`capture_once.py` connects prospective preflight, controller, durable
evidence reload, saved host-capture hash and `owner_adapter.py` to an exact
copy of the v3 validator. It has no CLI or import-time action and is **not
approved to invoke**. Its `capture()` requires the admission context and
bounded spawn adapter supplied by `operator_entry.py`. The entry has no
import-time action and requires a separate approval for one invocation.
It checks admission before preflight and again immediately before spawn.
The validator's `source_report` URL ending
`6384364441` is a pinned historical lineage constant, not the publication
location of a new capture. The actual new evidence path/hash must be
reported separately. All tests use synthetic processes and fixtures;
Bubblewrap, the genuine collector and the operational sandbox were not run.

Before any one-shot read-only VPS capture, a trusted preparer must create a
fresh nonce-named `/var/lib/zen53-owner-capture-<32 hex>` root:root0700
stage with a protected projection plus empty host/controller evidence directories,
verify all package and installed source hashes/metadata and the entire
release listing, then use the **prospective** `admission.py` and
`operator_entry.py` envelope. It holds both pinned production locks; checks
jobs, pending requests, worker processes, active timers, API rotation and
headroom; verifies a named non-delegated systemd cgroup-v2 scope with
360-second runtime and three-second forced-stop bound, 50% CPU, 384 MiB
memory, zero swap, 32 tasks and finite per-device read/write/IOPS caps.
The final-kill deadline plus the full 90-second
production margin is checked at each admission/spawn gate. Process-local
NOFILE/FSIZE/CORE limits are applied. Mutable nonroot lock ancestors,
changed lock paths, extra mounted data roots, ambiguous backing devices and
unbounded resources block. `/run`, `/proc` and `/sys` are separately identified
virtual mounts, not counted as block I/O sources. If child cleanup cannot be
established, the guard retains locks until the independently enforced scope
final kill; the stage must be treated as uncertain and consumed. The exact
installed release listing, block topology, scope behavior, UID maps, caps,
proc/mount read-only state, alias absence and file-descriptor escape resistance
remain **unverified live facts**. See `OPERATOR_PROCEDURE.md`.

One later approval scope should explicitly cover fresh new-object
preparation, transient namespace/mount creation, one read-only two-context
capture, durable private evidence and a sanitized report. It must exclude
installation, changes to existing permissions or security policy, runtime
stage acceptance, native Codex, network/providers and production changes.
The old October 24 host exit 2 and sender delivery remain UNKNOWN and are
never repinned into a PASS.
