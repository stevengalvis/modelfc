# Inactive bounded task bridge

This source is not installed or active. No policy, credential, timer, command
thread, account or standing grant is supplied. The trusted one-shot modes are
`python -m ops.bounded_worker.bridge_entry dispatch SETTINGS`, `publish SETTINGS`
and `status SETTINGS`. A separately authorized operator must arrange distinct
trusted execution contexts and scheduling for each mode; the worker never receives
transport or publisher credentials. The code does not provision them.

## Approval and setup prerequisites

Before installation or live use, obtain explicit approval for the repository,
a designated PR conversation number, the exact authenticated numeric GitHub
actor ID, allowed existing files, task budgets, publication authority, credential
custody, host accounts and persistent scheduling. Independently review this exact
revision, the publisher and intake boundaries, the runner's existing containment
and crash rules, and the specific host configuration. Separately perform the
runner's outstanding containment, crash-reconciliation and live adapter acceptance.
Install trusted code and its Python runtime under protected root-owned paths; do
not run repository code or tests selected by a worker as root.

Provide an independently maintained root-owned local bare mirror whose `main`
is current, trusted and updated through a separately approved process. Its entire
tree must be root-owned and not group/other writable, without symlinks or hard
links. Each of the five disjoint mirror, state, worker-workspace, evidence and
publisher-checkout roots must have root-owned protected ancestors; the state,
evidence and publisher roots are mode 0700. Worker workspace root is mode 0711.
The worker installation, identity, isolated account and systemd backend remain
subject to the existing [worker contract](README.md). Preserve workspaces and
private evidence after uncertain results. No cleanup or retry is automated.

A root-owned mode 0600 JSON policy is disabled unless `enabled` and `publish` are
both explicitly true. Example values below are illustrative placeholders, **not
an authorized policy**. The initial policy should use only existing documentation
files and exclude workflow, deployment, authentication and runner infrastructure.

```json
{"version":1,"enabled":false,"repository":"stevengalvis/modelfc","thread":0,"actor_id":0,"allowed_paths":["docs/example.md"],"timeout":90,"output":262144,"diff":4096,"publish":false}
```

A separate root-owned mode 0600 settings JSON names `policy`, `state_root`,
`mirror`, `workspace_root`, `evidence_root`, `publisher_root`, `username`,
`executable`, `executable_sha256`, `reader_token_file`, and
`publisher_token_file`. These are operator-selected values, never task inputs.
Each token file must be root-owned mode 0600 with protected ancestors and outside
all five roots. Reader access must permit GraphQL PR conversation reads, including
comment author IDs and edit metadata; publisher access must additionally permit
branch/commit creation, draft PR creation, PR reads and conversation status
comments. Limit permissions to this repository. Publisher credentials are read
only by the separate publisher/status process, never the dispatcher or worker.
Both token files and all protected configuration must be outside workspaces and
unavailable to the worker account. Validate these requirements on the actual host.

Once separately installed, schedule the three one-shot modes with a frequency
appropriate for GitHub rate limits and only after explicit persistent-execution
approval. The library starts no timer and makes no live call on import. The
GraphQL reader retrieves at most the last 100 conversation comments and fails
closed when earlier comments exist; a replacement channel requires another policy
approval. Network errors, rate limits, partial data and GraphQL errors fail closed;
there is no automatic network retry within an invocation. The global nonblocking
lock prevents overlapping execution and publication.

## Assistant-to-channel flow

After separate provisioning, the assistant can post one canonical JSON comment to
the approved PR conversation using its authenticated GitHub account. The user
approves the bounded task in this conversation; a comment is posted only for that
approved request. A command has exactly these keys in sorted order, UTF-8 encoded
without whitespace. The example uses placeholders and is not a real command:

```json
{"base":"<40-lowercase-hex-main>","diff":4096,"expires":2000000000,"job_id":"example-unique-id","output":262144,"paths":["docs/example.md"],"prompt":"<bounded documentation request>","publish":true,"timeout":90,"version":1}
```

Expiry is an integer Unix timestamp, at most 24 hours ahead at receipt; job IDs
must be unique. The exact base must match the trusted mirror's main before
checkout and GitHub main immediately before publication. Only exact paths in
protected policy and existing UTF-8 regular files are accepted. No task may select
an executable, provider, host path, command, environment, account or credential.
The backend uses the existing adapter's `gpt-6.1-terra`, medium effort and default
service tier, and `prepared_baseline=True`. The pilot example caps runtime at 90 seconds,
combined output at 262144 bytes and patch bytes at 4096.

GraphQL authenticates the comment's numeric author and PR association, and both
`lastEditedAt` and `includesCreatedEdit` must indicate no edit. The dispatcher
binds exact canonical bytes, comment identity and job ID in a durable receipt,
then refetches and revalidates before checkout. A later edit cannot cancel an
already-launched worker; publication performs a fresh check and refuses edited,
changed or expired requests. GitHub's reads and external writes are not atomic
with edits or main-branch movement. Unknown edit metadata, races or inaccessible
API data must block. The live credential's access to these fields and the edit
semantics must be verified during separately authorized acceptance.

After the normal authorization, expiry and envelope checks, an already-recorded
job is reported as `duplicate_observed` only when job ID, comment ID, node ID,
request hash and exact body bytes all match the same receipt. A job, comment or
node collision that does not match is reported as `collision_rejected` and is
never launched. In a poll containing a new eligible job, dispatch continues past
earlier duplicates or collisions and reports the new job's result with at most
one bounded `duplicate` binding and a `collision_rejected` flag when observed.
If no new job runs, a collision takes precedence over a duplicate in the result;
otherwise the first exact duplicate is reported. This observation does not create
or modify a job receipt and does not itself prove host-level no-relaunch or PR
uniqueness; those require separate live acceptance.

Each successful worker result must pass the existing inspect and cleanup checks.
The separate publisher checks the task and patch hashes, current base, existing
file type and mode, and applies a strictly parsed patch as data to its own clean
checkout. It checks the resulting remote commit and draft PR before reporting.
It never approves, merges or deploys. Status comments contain only fixed outcomes,
job ID, base, request and patch hashes, and draft URL. They contain no raw model
output, private path, patch, prompt or secret. A status-post attempt, like each
external write, is durably marked before sending and is never blindly repeated.
For verified terminal `failed`, `timeout`, or `rejected` outcomes, status includes
one fixed reason code and a fixed instruction to review retained attempt evidence
before approving another task. These are based on the trusted terminal outcome,
not on model text or unverified error details. Previously posted statuses are not
reposted. An uncertain job, cleanup or external write remains blocked rather
than receiving a misleading terminal reason. Usage and cost remain unknown when
not retrieved from trustworthy evidence; no value is inferred from these codes.

Under the shared lock, every mode blocks before worker or GitHub I/O if any job is
reserved or launching, any publication is branch-, commit- or PR-pending, any
status post is pending, or durable state is malformed, inconsistent or unknown.
Completed jobs without publication remain eligible for publication; published
jobs can receive status. Failed, timed-out and rejected terminal results are
eligible for status only after the existing launcher has verified cleanup.
Already-posted statuses are not repeated. No mode automatically reconciles a
pending PR, even if a matching PR exists. Preserve receipts and evidence for
separately authorized operator investigation; do not delete them to force a retry.
Finished evidence may be inspected read-only. This change does not provide crash
recovery or establish unattended host acceptance.

## Separately authorized host upgrade and deadlines

Keep all bridge timers disabled while reviewing and upgrading. For a separately
approved upgrade, install the reviewed exact source revision using the existing
protected operator process; retain the existing receipt database, lock, evidence,
workspaces and policy. Do not reset or migrate receipts to clear uncertainty.
Review unresolved records and the corresponding worker cgroup or external writes
through the separately authorized investigation procedure before any future use.
This change requires no token replacement, broader permissions or new model trial.

For separately installed one-shot service definitions, recommended finite starting
caps are `TimeoutStartSec=900` for dispatch, `1800` for publish and `120` for
status; use `TimeoutStopSec=30`, `Restart=no`, `KillMode=control-group` and
`SendSIGKILL=yes`. These are recommendations to review on the actual host, not
claims about installed units. Dispatch includes bounded checkout commands, a
90-second pilot turn and cleanup; publication includes checkout, patch checks and
multiple bounded GitHub calls. Many paths or slow operations can exceed these
caps: a deadline may interrupt a valid invocation and leave an uncertain receipt.
Adjust only after measuring the approved scope and preserve finite limits.

A bridge-service timeout or empty bridge-service cgroup does **not** establish
that the separate worker containment boundary is empty. Treat a reserved or
launching receipt as unresolved until an authorized operator independently
establishes worker-boundary quiescence. Do not enable timers or run a model as
part of applying or reviewing these source and host-unit changes.

### Optional bounded storage

The protected settings file may contain a `storage` object matching
`StorageConfiguration` in `storage.py`; omission retains the existing behavior.
The operator supplies one already mounted, fully allocated loop-backed ext4 image,
its exact image and filesystem identities and limits, free byte/inode reserves,
workspace and runtime directories, the original worker auth/config paths, and a
single `cgroup_component` from separately authenticated operator evidence. The
setting is root-protected, not task supplied; its validation does not authenticate
the prior reports. The backend combines it with its own fresh unit name for a
mandatory read-only cgroup bind. If the source is absent or the guard sees an
unexpected writable mount, startup fails closed; no fallback or path creation is
performed. Verify the selected component against protected evidence for the exact
host/systemd setup before authorizing use. Configuration and generated properties
are bound to attempt evidence; old storage records or settings lacking this field
are not silently migrated and remain blocked for operator review.
The workspace must equal `workspace_root`; evidence, bridge state and publisher
storage must remain outside the bounded mount. This code does not create, format,
mount, resize or remove the image, and does not copy credentials. Admission fails
closed on missing, replaced, nested, wrongly owned, exhausted or mismatched storage,
including ext4 discard and loop devices writable by the worker or with ACLs.
The loop node's ACL xattr query must return ENODATA; unsupported or unreadable ACL
state blocks admission and must be evaluated on the target host before activation.
The operator must also exclude host-wide trim of the image while it is in use.
Tasks and comments cannot select service properties.

The manager binds the existing host-mounted workspace and per-task runtime into
its private service mount namespace. The runtime supplies writable CODEX_HOME and
temporary directories; the original auth and config are read-only bind mounts at
their original CODEX_HOME names. A protected pre-exec guard checks effective mount
and file identities before the existing privilege drop. The loop device's
kernel-reported backing device and inode are checked independently of the image
pathname. Root-owned evidence records the admission identity, which is checked
before launch, after independently
verified cleanup, and again when inspecting a result. Storage loss or replacement
blocks acceptance and retains evidence. A failed or interrupted attempt is not
resumed. The bounded runtime directories are retained for operator reconciliation.

`ProtectSystem=strict` does not establish complete API-filesystem, device or IPC
confinement. Storage-enabled services request `/dev` and `/sys` read-only; the guard
checks effective mount IDs and rejects unrecognized visible writable mounts, including
`/sys` and its descendants, while allowing only the explicitly listed writable API
mounts. Ambiguous resolution or non-private propagation blocks. This relies on a
trusted host administrator, an exclusive worker identity, no inherited file handles
other than the three pipe streams and the untrusted worker being unable to reveal
covered mounts. Two specific user-namespace test paths did not reach a mount on one
host; that is not proof that every route is blocked. They also request private
mount propagation to prevent host mount events from propagating into the worker
namespace. This can retain a host filesystem in the service namespace after host
unmount until the unit exits; verify actual behavior and cleanup on the target
systemd/kernel before use. This property alone does not validate mount visibility
or establish the confinement boundary. The guard observes mounts in a child and
stops waiting after eight seconds, requests child termination and exits 127 on
failure. A child stuck in uninterruptible kernel sleep may survive that request;
the service cgroup must still be stopped and independently verified empty before
any result can be accepted. Host provisioning and exact service
namespace readback, byte/inode exhaustion, nested tool behavior, integrated coding,
authentication refresh and interruption acceptance require separately authorized
host work. The isolated startup fixture is not evidence of production integration.
