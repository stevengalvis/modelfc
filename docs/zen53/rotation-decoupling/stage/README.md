# ZEN-53 two-file successor, offline candidate only

## Inactive deploy user-manager admission amendment

The [2026-10-25 stopped report](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6401854139)
verified sources and the prepared stage but stopped before scope launch because
`user@1001.service` was inactive. `admission.py` now retains its active-manager
job, service, PID and FD-store queries. For the pinned `modelfc-deploy` UID 1001
only, an inactive manager can satisfy the same sampled quiescence requirement
without activating it **only** if both user and runtime-directory system units
are loaded, inactive/dead and have no PID, FD store, cgroup or trigger; the
system job queue is empty before and after; logind has no UID 1001 user or
session; protected linger and runtime-directory names are absent; the user
cgroup is absent or empty; and a bounded `/proc` scan finds no process with
any UID field equal to 1001. Incomplete, malformed, changing or contradictory
observations fail closed. The existing system-service/timer, controller,
source, lock, resource and schedule gates still run afterward. The active
manager path is unchanged except for the preliminary manager-state query.

This is a **sampled** check under the previously approved cooperation of root
and UIDs 1000/1001. It cannot prevent a privileged operator or new login from
starting work between samples. `_idle` is still called before children and at
finalization; it is not a freeze. Systemd 255 documents that user processes
can be in session scopes outside `user@.service` and that login or lingering
can start/retain the manager. Its system-logind D-Bus ListUsers/ListSessions
methods are read-only. Actual host serialization and absence topology remain
untested. No host activation, new service, security-policy change or VPS retry
is authorized by this candidate.

`admission.py` is one of the ten sealed prepared-tool files. The existing
nonce `ac7e0b2b20405fb206646455077f5e64` and its manifest pin the **old**
bytes; they cannot accept this change. A later authorized attempt therefore
requires a fresh protected stage, fresh nonce and separately reviewed matching
finite-launch helper/manifest. Preserve the old prepared stage, stopped
launcher directories and evidence. Do not edit, repair or repin those objects.

## Narrow authenticated zlib refresh

The previous candidate pinned a zlib1g `1:1.3.dfsg-3.1ubuntu2.2` library.
[Saved VPS evidence](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6397989416)
records a complete 113,000-byte source read with SHA256
`f61677c7bdb7c2aa2a243ea781bfd16afcb361cede7b4ef76127a9d8eb9a5242`,
and the [read-only metadata report](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6398463961)
records installed `zlib1g:amd64` version `1:1.3.dfsg-3.1ubuntu2.3`.
`ZLIB_REFRESH_PROVENANCE.json` records the verified Ubuntu archive signature,
index and package hashes; the `.3` package member has exactly those observed
bytes and hash. This candidate changes the current zlib dependency and derived
pins only. Historical source metadata retains the `.2` digest. Other source
routes and every live isolation gate remain unverified. No arbitrary current
host digest is accepted, and this archive authorizes no VPS preparation or run.

This is a separate, unexecuted candidate. The original fail-closed archive and
the consumed VPS stage are unchanged. The latest [read-only inspection](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6389524692)
establishes extra members in the installed Codex release and service-owned
`0700` coordination-lock parents. It does not establish sandbox acceptance.
This revision adds the fail-closed source-read behavior described in
`NOATIME_AMENDMENT.md`; the preceding successor archive is also unchanged.

This separate offline topology amendment corrects the prior assumption that a
partition node has its own `slaves` directory. It verifies the partition's
sysfs device and partition-number attributes, the canonical parent disk's
device identity and links, and the *parent disk's* empty `slaves` directory.
Missing sysfs identity, substituted links or mounts, layered devices and
identity changes fail closed. The [preflight report](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6390510062)
stopped before any scope or collector; its prepared stage is consumed.

The further offline I/O correction separates the filesystem source ID (for
example partition `8:1`) from the originating physical disk ID (`8:0` in that
example). `verify_backing` records an ordered identity for **every backing
path**: resolved path, full covering mountinfo row, source device, canonical
no-follow block-node identity and verified parent disk. Only the deduplicated
set of verified parent-disk IDs may be supplied to admission. Both the
root-owned single-link source node and parent disk node must match sysfs;
source aliases, exact source-node submounts and substituted/layered topology
block. The complete per-path snapshot is compared again before locks, before
each child and after evidence reload. A path moving between two already
represented partitions does not pass. `ADMISSION_IO_*` requires the **exact**
effective parent-disk `io.max` set, with all four finite caps on every line;
additional disk or partition lines block. No caps are relaxed and no
host-wide I/O setting is changed. See `IO_OPERATOR_PROPOSAL.md` and
`CONTRACT_COVERAGE.md`.

The [kernel cgroup v2 IO interface](https://docs.kernel.org/admin-guide/cgroup-v2.html)
keys `io.max` lines by device major:minor. [Systemd 255 resource-control](https://www.freedesktop.org/software/systemd/man/255/systemd.resource-control.html)
describes using the originating device for simple partition-backed filesystems
and applying I/O bandwidth/IOPS limits through `io.max`. These primary sources
support the *intended* per-scope enforcement policy. Synthetic tests do not
measure actual physical I/O throttling or prove that all host I/O is attributed
to this scope; effective cgroup lines and runtime behavior remain live gates.
No live attempt or new stage is authorized by this amendment.

## Two-file projection

`restricted_launcher.py` proposes one root-owned read-only runtime projection
and exactly two `--ro-bind SOURCE SOURCE` file mounts for the pinned executable
and `codex-package.json`. The projection must contain two empty root-owned
`0444` target files and their root-owned `0555` ancestors. No whole release
directory, `codex-resources`, `codex-path`, code-mode host or release symlink is
bound. The original copied `/opt/codex` runtime files remain in the projection
as historical inputs but are not aliases of the new `/root/.codex/...` binds.

`verify_release_files` hashes only the two pinned host files with no-follow
component walks, fixed metadata, bounded reads and before/after named/FD
identity checks. It permits unrelated siblings because they are not exposed.
The unchanged v3 collector holds source descriptors and checks the source
binding, content hash, metadata, mount observation and namespace before and
after the restricted run. This bracket detects ordinary mutation; it is not a
freeze. Host UID 1001 and root can still modify or replace host aliases.
Cooperating trusted operators and the existing process quiescence gate remain
assumptions. A read-only bind does not make a host alias immutable.

`owner_context_v4.py` is a new schema; `owner_context_v3.py` remains intact as
historical data. The v4 adapter requires a distinct restricted old and unique
mount ID for each file, an exact file-bind mountpoint/root for each source, the
read-only bit in both mountinfo and statmount, clear IDMAP, matching pinned
underlying device/inode/content and cross-context metadata, and no additional
visible aliases within the observed restricted mount namespace. The two host
files must still share one original host mount. The collector and projection
mechanism have not been run under genuine Bubblewrap. Kernel mountinfo
serialization and statmount behavior are runtime acceptance gates.

## Cooperative production locks

`admission.py` retains the original strict default. Its opt-in
`COOPERATIVE_LOCK_POLICY` allows only the two observed service-owned `0700`
ancestor identities, after no-follow hashing the *installed* deploy controller
at SHA256 `26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c`
and the two inspected validator controllers. It continues to pin both lock
files, hold exclusive flocks, recheck named and open identities, require
schedule headroom, check production quiescence before child spawns, and retain
locks during uncertain child cleanup. All `modelfc-` system services except
the continuously required corner API must have zero main/control PIDs and FD
stores; this includes cleanup units outside the older name filter. Owner-writable
ancestors can be replaced by their service owners despite flock. This mode
therefore protects coordination among cooperative pinned programs, not against
a hostile or newly privileged UID 1000/1001 actor. Additional MAC policy and
uninspected installers remain unknown. Installed controller hashes alone do
not prove all possible service entrypoints or eliminate races. The entrypoint
requires an explicit `--cooperative-service-owners` final argument to opt in;
the default remains fail-closed on the observed owner-writable directories.
Use of that option requires a later trust-model decision and is not authorized
by this offline review. Existing production permissions and policy are
unchanged. Cleanup outside lock lifetimes is addressed through independent
system- and user-manager jobs/services, cgroup, process and FD-store checks,
not by claiming the production lock covers those phases. The user-manager
queries use [systemd 255's documented](https://www.freedesktop.org/software/systemd/man/255/systemctl.html)
`--user --machine=user@.host` form;
actual connectivity, service naming and read-only behavior need live review
before any invocation. Inaccessible managers fail closed. System and user
service queries each have an aggregate 16-second deadline and finite unit
counts. After collector
completion and durable evidence reload, `Admission.finalize()` rechecks
the open and named lock identities, source controller pins, both manager
quiescence and schedule clearance before any provisional result is returned.

The earlier 360-second scope, 50% CPU, 384 MiB memory, zero swap, 32 tasks,
finite I/O caps, 90-second scheduling margin, output limits, evidence writer,
protocol and fail-closed cleanup remain unchanged. This package does not
authorize execution, publication, installation, host changes or a retry.

The entrypoint now verifies its own bounded cgroup scope and exact effective
disk limits **before** taking production locks, then rechecks both before
entering the protective-hold lifetime. A mistaken out-of-scope invocation
cannot acquire those locks and wait for a deadline that does not apply to it.
System-service, timer and user-manager query fanout have finite counts and
aggregate deadlines. Scope creation remains an operator-only step.
`stage_builder.py` now constructs only new nonce-named root-owned tool and
capture trees and emits a private data-only `LAUNCH_MANIFEST.json`. It checks
exact recovery and installed inputs with no-follow, no-atime bounded reads;
verifies the projection and separate source and originating-disk identities;
and leaves a partial stage consumed on failure. This builder has been tested
only under disposable synthetic roots. See `BUILDER_PROPOSAL.md` before any
separately approved host use.

Focused tests use local fixtures and synthetic processes only. They cannot
prove actual mount topology, host aliases, cross-user ownership, systemd
service census, controller installation or runtime containment. Do not use
`HISTORICAL_OPERATOR_PROCEDURE.md` for this candidate.

`TEST_PORTABILITY.md` records the test-only correction after the first VPS
preparation stopped at its synthetic-test gate. The historical report, its
consumed nonce and all runtime code remain unchanged.
