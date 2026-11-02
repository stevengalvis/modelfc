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

Each successful worker result must pass the existing inspect and cleanup checks.
The separate publisher checks the task and patch hashes, current base, existing
file type and mode, and applies a strictly parsed patch as data to its own clean
checkout. It checks the resulting remote commit and draft PR before reporting.
It never approves, merges or deploys. Status comments contain only fixed outcomes,
job ID, base, request and patch hashes, and draft URL. They contain no raw model
output, private path, patch, prompt or secret. A status-post attempt, like each
external write, is durably marked before sending and is never blindly repeated.

Reservations, launching records, uncertain publication writes and uncertain status
posts are never automatically retried. Any unresolved reservation or launch also
blocks later worker launches, because the previous cgroup may still be active. An uncertain draft-PR creation may be reconciled only after independently
rebuilding and checking the exact commit and finding one matching draft. Other
pending writes remain blocked; an operator must investigate the exact branch
and PR. Do not delete receipts to force a retry. Finished evidence may
be inspected read-only. This implementation does not perform crash recovery or
claim unattended host acceptance.
