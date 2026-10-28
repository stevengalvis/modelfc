# Fresh release deployment after merge

This deployment path is separate from the trusted PR validator. A push to
`main`, after both GitHub Actions jobs `test` (backend) and `frontend` succeed
for that same push, requests exactly that push's SHA. Failure, cancellation or
skipping of either required job prevents deployment. Pull requests run both
validation jobs but never run the production `deploy` job. The reviewed
controller installed at `/opt/modelfc-deploy/deploy_main.py` creates a new,
independent Git repository in bounded acquisition staging, exports it to
`/srv/modelfc/releases/<sha>-<nonce>/`, installs
a fresh `.venv` there, and runs offline tests. Only a passing release is pointed
to by `/srv/modelfc/current`. The incoming release never supplies the controller
or the systemd unit. This PR only provides source; it does not install anything.

## Automatic merged-PR deployment report

After each `Tests` workflow completes for a same-repository push to `main`,
`Production deployment status` runs from trusted default-branch code, including
when tests fail, deployment times out or the original run is cancelled. It never
runs for ordinary PR checks, connects to the VPS, or changes deployment gating,
promotion, rollback, SSH arguments or exit codes. It does not trigger Trusted
OFFLINE or LIVE validation.

The reporting entrypoint now publishes only the bounded JSON artifact and GitHub
job summary. It does not look up PRs or read/create/update PR comments; the job
needs only `contents: read` and `actions: read`. This avoids the observed
`COMMENT_WRITE/API_INTEGRATION_ACCESS_DENIED` 403 without expanding permissions.
`Reporting: SUMMARY_ONLY` describes delivery, independently of the deployment's
SUCCESS/FAILED/UNVERIFIED outcome. Evidence-validation and output failures still
fail the reporter. The historical comment-publication helpers and regression
coverage below remain available only through explicit Python calls; the workflow
and CLI entrypoint do not enable them. Historical comment behavior documented
below is not the current delivery path. Validator triggers are unchanged.

The original deployment step best-effort copies its already sanitized JSON
output to a seven-day artifact named by the workflow attempt. Failure to copy or
upload it cannot change the deployment result. The reporter reads only that exact
run/attempt artifact as bounded, strict JSON data in a separate temporary
directory, never as executable code. Missing or rejected artifacts cannot establish
success. It independently reads the original run and attempt-specific job results
from GitHub; raw logs, stderr and credential material are never fetched or posted.

The commit-to-PR API resolves the attempted push SHA. A comment is permitted only
on one closed, merged PR whose exact merge commit matches that SHA and whose base
is this repository's `main`. Direct pushes without that association produce no
PR comment. An ambiguous association fails closed. No PR number comes from an
artifact, commit message or untrusted text.

One `github-actions[bot]` comment per PR uses the stable
`zeno-production-deployment:v1` marker and records the attempted SHA, exact run and
attempt link, backend/frontend outcomes, stage and deployed SHA evidence. Later
attempts update it; human comments are never edited. Serialized reporting and
run/attempt stamps prevent late older completions from overwriting newer results.

The first attempt on PR #121 (merge `11a4adf8765d5883e10c39ff9d474cdc182abb6a`,
[deployment run 38015226038](https://github.com/stevengalvis/modelfc/actions/runs/38015226038),
[reporting run 38015731206](https://github.com/stevengalvis/modelfc/actions/runs/38015731206))
deployed successfully and downloaded the matching `PASS / OK` artifact, but
posted no comment. The reporter caught an exception and returned zero, making
its own run green without delivery. The original generic warning discarded the
failure stage and HTTP error category, so it cannot establish which original API
operation failed or whether a permission error occurred. Current read-only API
replay verifies the exact PR association and reaches the comment-write boundary;
this is not proof that the historical write succeeded. No permission increase
or PR-association relaxation is justified by that evidence.

The corrected reporter sends explicit JSON request headers and requires a valid
comment-write acknowledgement. It logs fixed outcomes (`REPORTED`,
`COMMENT_UNCHANGED`, `OLDER_RESULT_KEPT`, `NO_MERGED_PR`). Errors use only fixed
input/event/run/PR/jobs/comment stages and allowlisted API categories, never raw
exception text, URLs, payloads or credentials. An error now fails **only the
separate reporting workflow**, making missing delivery visible on Actions; it
cannot change the original deployment run, gating, exit codes or rollback.
There are no automatic retries or additional deployment calls.

The first comment includes `@stevengalvis` only when the authoritative matched PR
detail identifies the human author as that exact login and immutable account ID
16994883. No mention is derived from a commit message, comment or artifact. Other
authors receive the status comment without this mention. Identical delivery is
a no-op; later attempts update the same bot comment without rementioning the
author. Reruns therefore do not deliberately generate duplicate mention
notifications. Email and GitHub mobile delivery still depend on the account's
[notification settings](https://docs.github.com/en/subscriptions-and-notifications/get-started/configuring-notifications).
An edited comment is not a guaranteed new notification.

The subsequent PR #122 reporter ([run 38020167430](https://github.com/stevengalvis/modelfc/actions/runs/38020167430))
failed at `COMMENT_WRITE/API_HTTP_FAILED` after its matching artifact download.
Its runner log confirms `Issues: write`; merge
`71a4249debbdb36f45159859f84da333c4067c50` associates with PR #122.
The client constructs the documented POST `/repos/stevengalvis/modelfc/issues/122/comments`
with a JSON string `body`, or PATCH `/repos/stevengalvis/modelfc/issues/comments/<id>`
for an existing bot-owned marker comment. The original HTTP status and response
were discarded. That evidence cannot distinguish a 403 permission/policy rejection,
422 validation/spam rejection, or another HTTP failure. No permission increase or
historical root-cause claim is justified.

HTTP failures now preserve only `HTTP_STATUS=<integer>` and a fixed category.
401 is `API_ACCESS_DENIED`, 404 is `API_NOT_FOUND`, 422 is
`API_VALIDATION_OR_SPAM`, and 429 is `API_RATE_LIMITED`. A 403 remains
`API_HTTP_FAILED` unless a bounded response matches the exact known GitHub
integration-access or secondary-rate-limit message. At most 16 KiB plus one
sentinel byte is inspected; malformed/oversized/unknown responses cannot alter
failure handling. Raw API messages, error arrays, headers, URLs, tokens and
payloads are never printed. No retries are added. The next independently
authorized reporting attempt must establish the actual rejection category or
successful comment delivery; offline tests cannot establish Actions-token
policy or live API acceptance.

Reporting policy:

| Evidence | Comment result | Controller-selected SHA |
| --- | --- | --- |
| Successful original workflow/jobs plus strict controller `PASS / OK` | SUCCESS | Exact requested SHA, verified at deployment completion |
| Backend or frontend failure | FAILED, BACKEND_TESTS or FRONTEND_TESTS | UNVERIFIED |
| Cancellation | FAILED, CANCELLED | UNVERIFIED |
| Host/transport failure or timeout | FAILED, VPS_ACTIVATION or fixed controller reason | UNVERIFIED |
| Missing/malformed report or incomplete CI evidence | FAILED, fixed unverified stage | UNVERIFIED |
| Controller SUPERSEDED | SUPERSEDED | UNVERIFIED |
| Controller ALREADY_CURRENT | UNVERIFIED, ALREADY_CURRENT_NOT_REVERIFIED | UNVERIFIED |

Only normal `PASS / OK` establishes the protected marker and physical release:
the installed controller creates and verifies the read-only SHA marker, validates
source/dependencies/tests and verifies the direct active release after promotion.
`ALREADY_CURRENT` checks physical HEAD but returns before rechecking that marker;
`SUPERSEDED` and failure reports can retain a SHA observed before later work.
Those values are deliberately not advertised as verified final production state.
No new SSH verification command is added. Even a successful comment is a record
of that deployment's verification, not a live production health probe.

The reporter uses only `GITHUB_TOKEN`: `contents: read` for trusted checkout,
`actions: read` for the original run/jobs/artifact, `pull-requests: read` for merge
association, and `issues: write` for comments. The deploy job keeps its existing
read-only permissions. No PAT, secret, host installation or notification service
is required. GitHub failures emit only a fixed sanitized stage/category; reporting is
not a prerequisite for deployment and cannot change the original run's result.
A failed reporting workflow or cancelled reporter may require
checking the linked Actions run. This does not infer a previous active SHA.

References: GitHub's [workflow_run completion semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run),
[commit-to-PR association](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit),
and [comment permissions](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment).
After merge the workflow becomes eligible automatically; first real deployment
reporting acceptance is still required. No deployment is executed by this PR.

## Bounded runner failure evidence

The deployment step finalizes sanitized evidence on ordinary exits, including
missing transport configuration, key rejection, temporary-file failure, nonzero
SSH exit and malformed controller output. Its report retains the existing field
schema and adds only fixed runner failure reasons. The publisher accepts those
reasons only with FAIL, null previous/final SHA and all verification fields unset;
it cannot interpret a runner failure as controller or serving-release proof.
An invalid requested SHA is emitted as null and rejected as authoritative evidence.
The publisher still obtains SHA, run ID and attempt independently from GitHub and
uses only the artifact from that exact source run/attempt.

A valid controller FAIL is preserved even if SSH exits nonzero. A nonzero exit
with missing, invalid or success-shaped output becomes SSH_NONZERO_EXIT: this does
not distinguish a connection failure from a remote command failure or establish
whether remote activation happened. No retry is performed. Invalid output with
zero SSH exit becomes INVALID_DEPLOYMENT_REPORT. Raw stdout is limited to 8 KiB
on the Linux runner and parsing reads at most 4097 bytes, accepting at most 4096;
raw stderr is discarded. Duplicate JSON keys, inconsistent success fields and
out-of-bound test counts are rejected. Raw output, exception text and keys never
enter artifacts or summaries. Temporary raw output and key files are removed.

Sanitized artifact replacement is atomic and best effort, preserving deployment
success/failure independently of storage or publication failure. The existing
8-KiB publisher output limit and seven-day artifact retention remain unchanged.
Abrupt runner termination, cancellation, unavailable Python or unwritable artifact
storage may still prevent an artifact; missing evidence stays UNVERIFIED. This
source change does not repair or determine the outcome of a historical failed
SSH invocation, install a host component or change permissions.

## Durable deployment evidence and independent publication

The status publisher writes an internally constructed schema-v1 JSON result to
`DEPLOYMENT_STATUS_OUTPUT` and a short `GITHUB_STEP_SUMMARY` before PR association
or comment publication. It updates both with the final reporting outcome. Each
output is limited to 8 KiB; JSON replacement is atomic. Raw API bodies, arbitrary
artifact fields, private state, paths and credentials are never copied into them.
No SSH, provider request, deployment retry or application import is added.

The JSON has four top-level fields:

- `schema_version`: 1.
- `source`: authoritative source run ID, attempt, SHA and one attempt-specific
  workflow URL, or null when run identity could not be verified.
- `deployment`: status/stage, repository backend/frontend job conclusions,
  controller job conclusion and controller-selected SHA when verified by the
  existing PASS/OK contract. `serving_sha` stays null; `serving_health` and
  `installed_components` stay UNVERIFIED. Controller selection is not proof that
  the running API adopted the release. Repository tests do not establish actual
  host identity, mounts or installed helper versions.
- `reporting`: outcome and, on failure, the fixed stage/cause and numeric HTTP
  status. Deployment failure and publication failure are independent. A successful
  publication of failed deployment evidence does not turn that deployment green.

PR lookup/comment failure leaves collected deployment evidence intact and returns
nonzero from the publisher. Early input/API failure produces an UNVERIFIED result.
A direct push without a merged PR still gets durable evidence and NO_MERGED_PR.
The existing comment marker, unchanged-delivery no-op and older-attempt guard stay
in place. The comment now labels the controller-selected SHA explicitly.

An always-run artifact step uploads only `deployment-status.json`, named
`deployment-status-<source-run>-<source-attempt>-<publisher-attempt>`, retained for
seven days. Missing output or upload failure is visible; neither is suppressed.
Cancellation or runner loss can still prevent final persistence/upload; missing
artifacts and NOT_ATTEMPTED are never evidence of successful publication. A file
written before interruption may contain deployment evidence with reporting still
NOT_ATTEMPTED. Output-write failure itself fails the publisher. The original Tests
workflow and its deployment result are not changed by this observer.

Use the attempt URL for detailed logs, and the existing canonical PR comment once
publication succeeds. Link that report from Linear instead of duplicating it.
Do not rerun deployment or unchanged test suites to repair publication.

Historical source run 38488060644, attempt 1, succeeded with controller PASS/OK and
1,578 tests. Publisher 38488711699/job 115608536654 downloaded its matching artifact
then received COMMENT_WRITE/API_INTEGRATION_ACCESS_DENIED HTTP 403. This proves an
integration-access rejection; it does not identify the underlying permission or
policy. The effective token already had Issues-write. This change preserves that
failure independently and does not change permissions or claim to fix the denial.
Any authorization adjustment or external write acceptance remains separately
approved. No installed host component or October 15 scheduled-cycle verification
is changed by this reporting work.

## One-time VPS installation after review and merge

1. Inspect actual VPS ownership and permissions. Create `modelfc-deploy` with no
   interactive deployment shell, Docker access, production credential access or
   access to `/root/modelfc-state`. Give the account ownership of **only** the
   new `/srv/modelfc` tree and its own `/var/lib/modelfc-deploy` directory.
   Traversal of `/root` is unnecessary. Keep `/root/dev/modelfc` and its
   historical CSVs, refresh metadata and locks under their existing ownership.
   Do not recursively change ownership of `/root`, existing history, or state.

   ```sh
   useradd --create-home --shell /bin/sh modelfc-deploy
   passwd -l modelfc-deploy
   install -d -o modelfc-deploy -g modelfc-deploy -m 0755 /srv/modelfc /srv/modelfc/releases
   install -d -o modelfc-deploy -g modelfc-deploy -m 0700 /var/lib/modelfc-deploy
   install -d -o modelfc-deploy -g modelfc-deploy -m 0700 /var/lib/modelfc-deploy/reports
   ```

2. From a specifically reviewed merged SHA, install the controller **outside**
   releases and all three fixed systemd units, owned by root. Install future
   reviewed controller changes intentionally after review. The installed unit,
   not the repository copy, determines test isolation; verify its effective
   `systemctl show` properties. The test unit must have `PrivateNetwork=yes`,
   `ProtectSystem=strict`, `ReadOnlyPaths=/srv/modelfc/releases`,
   `InaccessiblePaths=/root/modelfc-state /root/dev/modelfc`, the fixed
   `User=modelfc-deploy`, `Group=modelfc-deploy`, no supplementary groups,
   `MemoryMax=2G`, `TasksMax=64`, and trusted `ExecStart`.
   The test unit mounts `/var/lib/modelfc-deploy` read-only and grants write
   access only to its `reports/` child for the sanitized result. Its trusted
   request file and `deploy.lock` stay outside that writable child; a test
   cannot unlink the active lock and allow a second deployment to acquire a
   replacement inode. The controller checks these effective installed
   properties before starting the unit. The dependency unit cannot access
   `/var/lib/modelfc-deploy` at all. It has `ProtectSystem=strict` and grants
   write access only to the instance's fresh `.venv`; package builds can use
   its private `/tmp`. The dependency unit is also limited to `MemoryMax=2G`
   and `TasksMax=64`, and the controller verifies those effective installed
   limits before starting it.
   It cannot write other releases, `current`, history or state. It writes no
   dependency report. The test unit writes only a bounded
   test report under `/var/lib/modelfc-deploy/reports`; tests receive an allowlisted
   environment with no API, GitHub, SSH or application credential.

   ```sh
   install -d -o root -g root -m 0755 /opt/modelfc-deploy
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:ops/vps/deploy_main.py" \
     | install -o root -g root -m 0555 /dev/stdin /opt/modelfc-deploy/deploy_main.py
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:ops/vps/acquisition_mount.py" \
     | install -o root -g root -m 0555 /dev/stdin /opt/modelfc-deploy/acquisition_mount.py
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-tests.service" \
     | install -o root -g root -m 0644 /dev/stdin /etc/systemd/system/modelfc-postmerge-tests.service
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-dependencies@.service" \
     | install -o root -g root -m 0644 /dev/stdin '/etc/systemd/system/modelfc-postmerge-dependencies@.service'
   git -C /root/dev/modelfc show "${REVIEWED_SHA}:deploy/modelfc-postmerge-acquisition@.service" \
     | install -o root -g root -m 0644 /dev/stdin '/etc/systemd/system/modelfc-postmerge-acquisition@.service'
   systemctl daemon-reload
   systemd-analyze verify /etc/systemd/system/modelfc-postmerge-tests.service
   systemd-analyze verify '/etc/systemd/system/modelfc-postmerge-dependencies@.service'
   systemd-analyze verify '/etc/systemd/system/modelfc-postmerge-acquisition@.service'
   ```

3. Create a dedicated SSH keypair. Bind its public key to the controller in
   `modelfc-deploy`'s `authorized_keys`:

   ```text
   restrict,command="/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py" ssh-ed25519 AAAA... modelfc-postmerge
   ```

   `restrict` disables PTY, forwarding, X11 and user rc. The account only
   needs narrow passwordless sudo permission for `/usr/bin/systemctl start
   --wait modelfc-postmerge-tests.service`, `systemctl stop` for that exact unit,
   and start/stop for the fixed
   `modelfc-postmerge-dependencies@<validated-release-id>.service` and
   `modelfc-postmerge-acquisition@<validated-release-id>.service` instances,
   without SETENV. The trusted controller validates the release ID, and the
   installed dependency unit validates it again. The account never gets an
   unrestricted root shell. The fixed service is not enabled by a timer.
   Verify a login attempt without a valid `deploy stevengalvis/modelfc <sha>`
   request produces `INVALID_REQUEST`.

4. Independently verify and pin the VPS SSH host key. Set GitHub environment
   `production` to accept `main`; configure `MODELFC_DEPLOY_SSH_KEY_BASE64`
   (environment secret containing the canonical single-line base64 encoding of
   the dedicated OpenSSH deployment private-key bytes),
   `MODELFC_DEPLOY_HOST` and `MODELFC_DEPLOY_HOST_KEY` (variables containing
   host and known_hosts line). Never put OddsPapi keys, validator tokens, state
   contents or other production secrets in GitHub Actions.

## Runtime and historical-data wiring

For the staged refresh-only migration to `/var/lib/modelfc/history`, use
[RUNTIME.md](RUNTIME.md). It supersedes the legacy refresh wiring below only after
separate host acceptance. Deployment protects `/var/lib/modelfc` in all three
postmerge units and checks deployment-account R/W/X exclusion. Install the trusted
controller and units together before migrating history. Original checkout history
and `/root/modelfc-state` remain protected; this PR does not move either.

For the later, independently approved prospective-runner cutover to
`/var/lib/modelfc/state`, use [PROSPECTIVE.md](PROSPECTIVE.md). Normal post-merge
deployment remains code-only: it does not install that launcher/unit, provision its
credential, migrate or enroll state, start the service, or enable its timer.


Deployments do not move or copy historical CSVs. Their current location remains
`/root/dev/modelfc`; refreshing those CSVs and acquiring `data/corner-refresh/
refresh.lock` remain separate operations. The release's committed
`corner_data.json` still resolves `data_directory: "."` relative to that file.
Production commands must instead use a separately maintained runtime config
outside releases, containing the same `leagues` and `max_age_days` as the
reviewed configuration and an **absolute** `data_directory` of
`/root/dev/modelfc`. Confirm actual history file and refresh lock locations on
the host before creating that config. Do not put the config in Git; keep it
root/admin managed. The configuration parser accepts an absolute directory.

Before switching any runtime command to `/srv/modelfc/current`, verify its
service account can traverse and read the historical data and write the
existing refresh lock, while the `modelfc-deploy` account still cannot modify
history or state. Existing state configuration must continue to point to
`/root/modelfc-state`; the deploy controller does not inspect its contents.
Model FC's `git_commit_sha()` resolves the code path through the `current`
symlink and reads the independent `.git` retained in each release. Use an
absolute runtime `--data-config` pointing to the external history config.

Audit installed services and manual commands before enabling automatic
deployment. Repository examples hardcode `/root/dev/modelfc`, notably
`deploy/modelfc-corner-refresh.service` and `DATA_REFRESH.md`; the refresh
service currently runs code from the old checkout and uses its existing config.
Plan its separate migration explicitly: execute refreshed code through
`/srv/modelfc/current` while retaining `/root/dev/modelfc` as the data
directory, and pass the external config. Audit OddsPapi and prospective runner
commands and any locally installed services or scripts for their working
directory, `PYTHONPATH`, virtualenv Python and config paths. This PR does not
change or restart those installed services, modify their configuration, or
migrate any data. Until that separate wiring is completed, promoting `current`
does not itself switch the old running commands to the new code.

## Deployment and acceptance

The controller accepts only `deploy stevengalvis/modelfc <40 lowercase hex>`.
It locks the host deployment path, verifies any current release's independent
Git HEAD, mounts the fixed private acquisition tmpfs described below, initializes
a new repository there, fetches fixed `origin/main` with
isolated Git config, proves the requested SHA is on that fetched branch, and
rejects moving a verified active release backward. It checks out the exact
SHA, reaps acquisition processes, validates and exports the independent repository
to a fresh permanent candidate, reverifies HEAD and tracked source, then
installs `requirements-deploy.lock` into a fresh `.venv` at the candidate's permanent
path through the narrowly writable dependency service, verifies every tracked
source blob against the reviewed Git commit, and runs the complete offline
tests in the installed systemd sandbox. It verifies tracked source again before
promotion. Package installation has no production state or provider credential.
The host fingerprints the fresh virtualenv's Python interpreter links/files and
`pyvenv.cfg` before package installation and rejects the candidate if those
bootstrap artifacts change. This narrow check does not make arbitrary installed
Python packages safe: approved dependency artifacts are part of the trusted
runtime, while their build and installation execution remains sandboxed.
Successful tests must produce a positive final unittest count on stderr.
Promotion creates a temporary absolute symlink and atomically replaces
`/srv/modelfc/current`. The old successful release remains on disk. A failed
candidate never reaches publication and is removed only if it was created by
that run. If publication through `current` is attempted, a failed promotion
restores the previous release but retains the candidate: a launcher may have
pinned its physical path while it was selected. An unsuccessful deployment
report does not make that retained candidate active or approved. Do not delete
it without separately establishing that no process uses it. Abandoned
candidates after a crash may be inspected manually; this controller does not
garbage-collect previously published releases.
The reviewed [API handoff controller](../../docs/API_DEPLOYMENT_HANDOFF.md), once
separately installed with its fixed helper/permission, requires actual API adoption
before PASS and restores/verifies the previous code release on handoff failure.
Older installed controllers only verify the pointer and do not provide that API
health recovery. Failures before promotion leave `current` pointing to the previous
successful release. A repeated request for the
already current SHA returns `ALREADY_CURRENT` without rebuilding; a superseded
push reports `SUPERSEDED` without moving `current` backward.

Before enabling the GitHub secret, exercise a reviewed main SHA through the
forced SSH command on the actual VPS, verify the reported exact SHA and
nonzero tests, verify the current symlink's direct target and retained `.git`,
then retry that SHA. Test invalid SHA, an older event, dependency failure and
test failure in a controlled acceptance environment. Confirm historical CSVs,
refresh locks and `/root/modelfc-state` remain unchanged; confirm the installed
systemd test process cannot read them or reach the network. On a completed test
failure, inspect `/var/lib/modelfc-deploy/last-test-failure.json`. The controller
retains at most 4096 bytes outside the test-writable reports directory, after
service termination. It contains the release ID, SHA, test count and informational
diagnostics: failure/error counts (null when unavailable), at most eight
source-declared test identifiers (160 ASCII characters each), and `truncated`.
Subtest values, traceback text, stdout and unknown identifiers are never retained.
Stdout is discarded; stderr is drained in 4096-byte chunks into a 65536-byte tail
while reading, under the fixed 600-second deadline. `truncated` also indicates
omitted/unrecognized identifiers. Each new test attempt removes the previous failure artifact before starting the
service, so a missing new report cannot leave stale diagnostics presented as the
current attempt. Always match both SHA and nonce-bearing release ID. Interrupted
runs retain only TIMEOUT/CAPTURE_ERROR/PROCESS_ERROR classification, bounded
source-allowlisted last-test identity (or null), and `count_known=false`;
`tests_run` in the private failure artifact is null when no accepted terminal
summary exists. The legacy fixed GitHub report still uses zero for unknown, not
proof that zero tests executed. Verbose unittest progress supplies the last
observed identifier, not proof that it caused the interruption. A large tail can
omit earlier progress; unknown stays unknown. Abnormal exit/invalid summaries
remain failures. No raw exception messages are retained. It does not
authorize promotion or change the fixed GitHub JSON report. The normal
host execution context is used, independent of coding-agent mount restrictions.

### Supplemental failure retention (source only)

Failure callbacks now send an independent bounded outcome stream before unittest
formats exception text. Long stderr tails and rejected timing observations cannot
erase an accepted failure observation. `outcomes.events` contains at most eight
`[kind, test_id, exception_category, frames, frames_omitted]` records. Subtests use
the enclosing test identity, never parameter values. Exception categories are a
fixed built-in vocabulary or `OTHER`; custom names and all exception messages
are discarded. Each of up to four frames contains only a source-declared test
method symbol and a line number validated against that method's AST range.
Non-test frames (including setup, application and library frames), excess frames,
and nested test functions are omitted explicitly. This is a sanitized partial
traceback, not a complete stack or a diagnosis of the historical PR219 error.

The source inventory remains bounded to 256 non-symlink test files, 1 MiB per
file and 20,000 aliases. It previously stopped after 4,096 aliases, which is below
the current suite's inventory. Dynamic/inherited identities not declared in this
inventory remain unknown. No arbitrary runtime name or path can leave the sandbox.
The outcome pipe has separate sequencing, a final event count, a 1 MiB input
ceiling and 1024-byte records. `outcomes.complete` means a consistent end was
observed, not that tests passed or that all frames/events were retained.
`outcomes.truncated` marks lost/rejected input or omitted records; individual
`frames_omitted` flags mark partial stacks. Unknown or malformed outcome records
stop outcome collection, preserving only the accepted prefix. Progress remains
separate and cannot identify the failing test.

The existing 4096-byte report ceiling is unchanged. When necessary, the producer
removes slow timing entries first, then frames, then outcome details, preserving
the bounded failure-ID list and setting truncation flags. The controller validates
source bindings again and saves the protected report before candidate cleanup.
Exit status and the final unittest summary remain the only test acceptance gate.
Service-wide termination before report writing can still lose diagnostics.
This source-only change does not update `/opt/modelfc-deploy/deploy_main.py`:
a separately reviewed, authorized controller upgrade and exact installed-byte
acceptance are required before it affects any VPS deployment.

### Exact installed-baseline retention package (local preparation only)

The [2026-10-28 23:46 UTC read-only inventory](https://github.com/stevengalvis/modelfc/pull/224#issuecomment-6483724564)
reports installed controller SHA256
`26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c`,
83,946 bytes, root:root mode `0555`. This fingerprint matches the output of
`ops/ci/build_admission.py`; it is not itself installation or compatibility acceptance.
`ops/ci/build_retention.py` reconstructs that exact input through the existing
hash-verified chain and applies only `patches/deploy-main-26b7-retention.patch`.
That patch is the reviewed PR224 retention change without porting edits.
Its manifest and builder pin all three identities:

| Artifact | SHA256 |
| --- | --- |
| Installed-baseline input | `26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c` |
| Retention patch | `ed28204efdbd260ae38b3f143edae92dfd88080292953253944454e669dd35b1` |
| Candidate output (92,677 bytes) | `9d3e94dac40fae1285f13fe522efe38bce559554d58356078c2ce874cf3a3be5` |

For a local artifact only, choose a new output path:

```bash
.venv/bin/python -B ops/ci/build_retention.py --output /tmp/retention-controller.py
sha256sum /tmp/retention-controller.py
```

The builder creates the output exclusively, rejects existing files/symlinks,
checks patch applicability and final bytes, and compiles without executing the
controller. No installed path, permissions, unit or service is changed. Artifact
regressions enforce that only the reviewed retention functions/constants and
validation callsite change; every other top-level source segment remains exact
installed-baseline content, including admission, deployment, promotion, recovery
and candidate cleanup. The reconstructed artifact runs the retention privacy,
truncation, process-capture and before-cleanup report regressions locally.

The unchanged 936-byte test unit hashes to
`4833e637436a6fdd7bdc1c7b895c29c602082b7d99740a65da7931728c5e97fa`,
matching the read-only inventory. A future authorized operation replaces only
`/opt/modelfc-deploy/deploy_main.py`. It requires no unit edit, daemon reload,
new grants, API restart or deployment retry. Never install main's whole controller.

Before asking for installation approval, separately review these exact candidate
bytes and complete CI/exact-head OFFLINE plus disposable real-systemd acceptance
of this artifact and the unchanged effective unit. Earlier acceptance of `08f7`
or `26b7` is not acceptance of `9d3e`. Local PID1 is `tail`, so real-systemd
acceptance has not been performed here. No new acceptance workflow is triggered
or claimed by this package; the older harness still builds its older artifact.

After those gates and explicit installation authorization, the one-file procedure
must freshly verify the installed input hash, protected ancestors, root ownership,
mode and identity; recheck effective unit/no drop-ins; preserve the bounded prior
failure evidence under its SHA/release binding; and hold the existing deployment
exclusion with no active test/dependency/deploy invocation. Preserve a protected,
hash-verified independent copy of the exact old controller (not a hard link). Stage the approved candidate in
the same protected filesystem as root:root mode `0555`, verify its hash, fsync and
atomically replace the controller under exclusion. Re-read the installed hash,
mode and identity and confirm unit bytes/effective properties and current release
are unchanged. Do not start tests or retry deployment as part of this replacement.
If replacement or verification fails, stop further action; reconcile uncertain
publication first. Restore only if the installed bytes and identity still match
the approved candidate; an unchanged verified original is a no-op. Unknown bytes,
identity drift or third-party changes require operator review, not an overwrite.
For the verified candidate, restore the independent old-controller copy with the
same atomic procedure under exclusion and verify the original hash and metadata. Preserve
both the operation evidence and historical failure report. This documents a
future installation/rollback boundary; no installation or rollback is authorized
or performed by building this local package.

### Supplemental unittest timing (source only)

The trusted test runner wraps the same unittest discovery/verbose CLI with
startTest/stopTest observations. A separate nonblocking pipe carries bounded
monotonic microsecond events; no new execution mode, SSH selector, service,
identity, permission, path or validator capability is added. The production budget is now a fixed 600-second child deadline and 630-second
service ceiling; isolation and resource limits remain unchanged. Standard unittest outcomes and its terminal summary still determine
the gate; timing events cannot make a failing or interrupted run pass.

Failure diagnostics may include `progress`: `observed_completed`, `complete`,
`last_test_id`, `last_progress_us`, and up to eight `slowest` pairs of source
allowlisted test ID and elapsed microseconds. Observed completion includes tests
that failed or skipped, not just successes; subtests are part of their enclosing
test. `complete` describes the event stream, not successful tests or a trusted
final count. Missing terminal summaries still mean `count_known=false` and
retained `tests_run=null`. Dropped, malformed, out-of-order, unknown-ID or excess
events stop timing aggregation and mark it incomplete, preserving only the
accepted prefix. The pipe is drained even after rejection so instrumentation
cannot block tests. At most 4 MiB/20,000 events are accepted; individual records
are capped at 1024 bytes. No descriptions, parameters or raw timing-pipe payloads are retained.
Sanitized failure frames use the independent outcome channel described above. The existing 4096-byte final report ceiling is unchanged.

These candidate-process observations are supplemental, not trusted attestations
or proof that the last test caused a timeout. Timing adds small overhead. Normal
parent-handled timeout preserves accepted progress; OOM, service-wide kill or
host failure before report writing may still lose it. Existing immediate-child
reaping and systemd cgroup termination remain responsible for cleanup.

There is no automatic archive of the prior private failure record: the next
normal test attempt still deletes it. GitHub's fixed deployment artifact does
not contain these private diagnostics. Before a future attempt, an authorized
operator must preserve that bounded record through existing authorized access,
matching SHA and release ID. Controller installation, evidence preservation and
any subsequent normal deployment attempt require separate authorization. This
source change neither performs nor authorizes them, and chooses no timeout
increase or deployment remedy. Mocked/source tests do not establish systemd or
VPS acceptance; that acceptance has not been performed for this change.

Release retention and the one-time runtime path migration are administrator
tasks. Never delete a release still referenced by `current`; keep previous
successful releases until a separate retention policy is reviewed. This system
does not restart Model FC services, call providers, refresh history, settle
outcomes, or replace the trusted PR validator.

## Locked dependency and storage policy

Deployment accepts only the regular, non-symlink `requirements-deploy.lock`
tracked at the requested reviewed SHA. Its bytes must match that Git blob before
installation. Source verification runs before installation, afterward, and before
promotion. There is no requirements.txt fallback and no incidental pip upgrade.
The candidate Python invokes pip with `--require-hashes --only-binary=:all:
--no-cache-dir`; pip check and bootstrap integrity verification remain required.

Reviewed source, exact hash-approved wheels, and host Python/pip bootstrap tooling
are trusted. Wheels avoid source distributions and build backends. These controls
are not a claim of hard containment against intentionally malicious approved
wheel code. Existing service isolation still protects retained releases, current,
history, evidence and deployment-control files.

Before candidate creation and immediately before dependency installation, the
release filesystem must have at least 1 GiB available. Private dependency /tmp
and /var/tmp each use tmpfs limited to 256 MiB and 16,384 inodes. The controller
requires those exact effective systemd settings. The dependency unit uses
`PrivateTmp=no` so its two explicit tmpfs mounts take effect, with `mode=1777`
to make them writable by the deployment user. Test/acquisition PrivateTmp,
resource, credential, bind and write-boundary checks remain in force. During host acceptance,
verify inside the installed service namespace that these tmpfs mounts and limits
are actually active; static unit verification alone does not establish this.

After installation, .venv must occupy no more than 128 MiB in either logical or
allocated bytes and contain at most 10,000 entries. Measurement does not follow
symlinks. This is operational protection for a finite reviewed artifact set,
not a filesystem quota. Reconsider limits whenever the lock changes. Concurrent
unrelated host writes can still exhaust free space. Failure returns the fixed
STORAGE_LIMIT_FAILED reason, blocks testing/promotion and removes only the failed
candidate using existing cleanup. No retained-release pruning is automatic.

SSH uses ConnectTimeout=15, ServerAliveInterval=15 and ServerAliveCountMax=4;
the deployment step has a 75-minute timeout. Quiet responsive sessions continue;
unresponsive connections fail. GitHub timeout is not a replacement for host
locking, bounded service execution or cleanup.

## Activation gate

PR #59 supplies the reviewed wheels-only lock. This integration does not authorize
VPS installation or activation. Installing the reviewed controller and all three units,
configuring credentials, and host acceptance remain separate manually approved
steps. Preserve all existing acceptance checks, including state/history isolation,
mount-limit verification and failure cleanup, before enabling automatic deployment.

Reviewed Model FC source and its tests are trusted code, as are the exact
hash-approved dependency wheels. The test sandbox does not provide hard disk
containment against intentionally malicious reviewed tests. Memory, task,
timeout and free-space controls are operational safeguards against accidental
failures, not a hard filesystem quota. Test-count validation detects accidental
zero-test execution and ordinary failures; it is not adversarial attestation
against trusted tests deliberately fabricating their own result. The controller
requires effective test-service KillMode=control-group so an installed override
cannot weaken process cleanup.


## Bounded Git acquisition

The separately reviewed root-owned `acquisition_mount.py` helper has only three
operations: `mount`, `verify`, and `unmount`, each taking one validated
`<40-hex-sha>-<12-hex-nonce>` release ID. It accepts no path, mount options or
remote from the caller. Install it alongside the trusted controller from the
same reviewed revision. Add only these narrowly scoped sudoers entries, using
`visudo` and validating the resulting file before enabling deployment:

```sudoers
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/modelfc-deploy/acquisition_mount.py mount *
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/modelfc-deploy/acquisition_mount.py verify *
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/modelfc-deploy/acquisition_mount.py unmount *
```

The wildcard is constrained by the installed helper's exact argument count and
release-ID grammar. No SETENV permission, arbitrary Python execution, general
mount command or new daemon is authorized. Existing systemctl permissions remain
separate. The helper creates only `/run/modelfc-acquisition`, mounts **256 MiB /
32,768 inodes** of tmpfs there, and requires mode **0700**, dedicated deployment
UID/GID, `nodev,nosuid,noexec`, exact capacity and an attempt-specific mount source.
Its root-owned `/run/modelfc-acquisition-control.lock` serializes mount operations;
the existing host `deploy.lock` still serializes the entire deployment.

Repository initialization, full main fetch, objects, checkout and Git temporary
files live on this filesystem. Git receives an isolated configuration and staging
HOME/TMPDIR/TMP/TEMP/cache paths; automatic Git maintenance is disabled. No candidate
code runs during acquisition. The controller runs the installed acquisition worker
in `modelfc-postmerge-acquisition@<validated-release-id>.service`, with
`MemoryMax=512M`, `TasksMax=32`, `Delegate=no` and `KillMode=control-group`.
The controller attests the effective resource, lifecycle, command and filesystem
boundary before starting the unit. Its only writable deployment path is staging,
and it cannot access history, state or deployment controls. The trusted request
and bounded result use fixed files within staging. It verifies service/cgroup
termination before export or unmount. The worker has a 600-second deadline; individual
Git operations retain their bounded timeouts. Full history preserves queued-event,
forward-ancestry and supersession checks; shallow fetch is not the security boundary.

Before any persistent candidate is created, a no-follow traversal bounds the
repository plus checkout to **128 MiB logical and allocated bytes** and **16,384
entries**. Special files, Git-metadata symlinks and external object alternates are
rejected. Source symlinks are preserved, never dereferenced. Export makes fresh
files, not shared object links, and verifies the same bounds again. The permanent
candidate's exact HEAD and tracked source bytes are independently verified before
its `.venv` is created. Every retained release still has its own usable `.git`.

Capacity/inode exhaustion or fetch failure reports `FETCH_FAILED` (or
`STORAGE_LIMIT_FAILED` for local filesystem/export limits); verification failures
retain `SHA_NOT_ON_MAIN`, `ACTIVE_SHA_NOT_ON_MAIN` or `SOURCE_INVALID`. Mount or
cleanup boundary failure reports `STATE_BOUNDARY_FAILED`. Raw Git output is never
returned. Failures prevent dependencies, tests and promotion. Cleanup removes only
the current attempt's newly created candidate when publication was never
attempted, and unmounts its staging. Once the atomic replacement of `current`
has been attempted, the candidate remains on disk even if post-promotion
verification fails and `current` is restored. The attempt is treated
conservatively because a replacement that reports failure may already have
made the release selectable. Historical data and evidence remain untouched.
This protection takes effect only after the reviewed controller is separately
installed on the host; merging source does not replace the installed controller.

Handled SIGINT/SIGTERM/SIGHUP interruptions follow the same cleanup. SIGKILL, host
crash or a busy/failed unmount can leave staging behind. A subsequent attempt fails
closed rather than adopting or deleting it. An administrator must confirm no
acquisition process remains, inspect the exact mount source/limits, and invoke the
reviewed helper's `unmount <original-release-id>` operation. No lazy/forced unmount
is used. Foreign mounts, nested mounts, symlinks or an unexpected plain directory
require manual investigation; the controller never recursively deletes that state.
A reboot discards the tmpfs. Retained releases still require manual lifecycle
maintenance; this change adds no pruning policy.

Real-VPS acceptance is required before enabling deployment: verify installed sudo
restrictions and actual mount UID/GID/mode, capacity, inode count and flags; exhaust
bytes and inodes in disposable staging and confirm ENOSPC without host disk growth;
exercise success, fetch failure, export failure and SSH interruption; check staging
unmount/process reaping and unchanged current/retained/history/state paths. Test
stale-state refusal and deliberate administrative recovery. Verify queued older
main requests, divergent requests, final `git rev-parse HEAD`, capture provenance,
and locked dependency installation/tests at the permanent path. Offline tests mock
mounting and cannot attest the VPS kernel, sudo policy or actual mount lifecycle.


## Complete service termination

All three installed units explicitly set `TimeoutStopSec=30`,
`SendSIGKILL=yes` and `KillMode=control-group`. The controller checks those
**effective** properties plus `TimeoutStartUSec` before execution. Acquisition
also requires exactly **512 MiB MemoryMax / 32 TasksMax**, with no cgroup
delegation. These give Git headroom above its 256 MiB tmpfs while remaining
conservative for the 4 GB VPS. Setsid/fork descendants stay in the systemd cgroup.

| Service | Start budget | Stop budget | Controller start/wait budget |
| --- | ---: | ---: | ---: |
| Tests | 630 s | 30 s | 690 s |
| Dependencies | 510 s | 30 s | 570 s |
| Acquisition | 600 s | 30 s | 660 s |

Each controller wait includes 30 seconds of overhead beyond start plus stop.
A timed-out/interrupted systemctl call does not imply service termination. The
controller explicitly stops a possibly running unit, allowing 60 seconds for
that stop command, then verifies inactive/failed state, no queued job, zero main/control PIDs,
and an empty or removed service cgroup. It never treats an inactive main PID
alone as proof that descendants are gone. Only after confirmation may it read
results, unmount staging, clean a candidate, or release the deployment lock.

If systemd/kernel failure makes termination unverifiable, the controller stays
in a fail-closed wait with the host lock and candidate retained. It ignores
SSH-disconnect/termination signals during this termination gate. There is no
unsafe fallback timeout that deletes files under a possibly live process. An
administrator may need to restore the manager or terminate a stuck unit; even
if shutdown is later confirmed, this attempt fails with `STATE_BOUNDARY_FAILED`.
The fixed `service-pending.json` marker in the protected control directory is
written before service start and removed only after confirmed termination. It
survives a forced controller death: the next attempt acquires the host lock,
validates the recorded allowlisted unit, stops/confirms it, and refuses to resume
the crashed deployment. Incomplete candidates/staging then follow the existing
manual recovery policy. Never delete this marker as a substitute for checking
service/cgroup termination.

One-time sudo policy must authorize only the following fixed command forms
(in addition to the existing fixed mount helper), with validated release IDs:

```sudoers
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start --wait modelfc-postmerge-tests.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl stop modelfc-postmerge-tests.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start --wait modelfc-postmerge-dependencies@*.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl stop modelfc-postmerge-dependencies@*.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start --wait modelfc-postmerge-acquisition@*.service
modelfc-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl stop modelfc-postmerge-acquisition@*.service
```

The trusted controller supplies one exact allowlisted unit argument. No candidate
supplies service names, privileged options or resource settings; instance IDs
are validated before acquisition code executes. Install the reviewed unit and
controller together; no automatic installation occurs in this PR.

Real-VPS acceptance must verify cgroup v2 accounting and effective limits, provoke
memory/task exhaustion in disposable acquisition staging, exercise start timeout
and SIGTERM-resistant descendants for all three units, and confirm cgroups are
empty before export/cleanup. Also exercise controller timeout, SSH disconnect,
and pending-marker recovery while checking lock exclusion and unchanged current/
retained releases. Ordinary offline tests simulate manager states and cannot
prove actual kernel/systemd enforcement or installed sudo policy.


## Effective environments, trusted helper, and runtime permissions

All three service boundaries now query `Environment`, `EnvironmentFiles`,
`PassEnvironment`, and `UnsetEnvironment` with `systemctl show --all`.
Unverifiable output fails before execution; the typed inspection below handles
specific empty values omitted by systemd 255. Test/acquisition units may configure
only `PYTHONDONTWRITEBYTECODE=1`; the dependency unit may configure no Environment
assignments. EnvironmentFiles and PassEnvironment must be empty. The complete
UnsetEnvironment list must contain exactly `ODDSPAPI_API_KEY`, `GITHUB_TOKEN`,
`SSH_AUTH_SOCK`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT`, `PYTHONPATH`, and
`PYTHONHOME`. Unexpected assignments are rejected, not merely scanned for known
bad names. The explicit unsets also remove these loader/Python overrides from
inherited manager defaults. Install the reviewed updated units with the controller.

Before **every** sudo mount-helper invocation, the controller opens `/`, `opt`,
`modelfc-deploy`, and `acquisition_mount.py` through directory-relative no-follow
lookups. Every directory must be a real root-owned directory without group/world
write permissions; the helper must be a regular root-owned non-symlink file with
no group/world write permissions. Failure prevents sudo. Root administrators
remain trusted to install reviewed code; this checks path ownership and integrity,
not a new controller/helper signing system.

Exported source and Git directories receive explicit mode **0755**, independent
of umask. Regular files become readable by the production account, retain their
executable/non-executable distinction, preserve existing owner-write permission,
and lose all group/world write permissions. No write permission is added to a
read-only file. Source symlinks stay symlinks and are never followed or chmodded.
The fresh `.venv` is normalized with the same policy both before bootstrap
fingerprinting and after successful installation/bootstrap verification. Interpreter
and library symlinks are preserved; external targets are never normalized.

The one-time `/srv/modelfc` and `releases` ancestors must remain traversable by the
intended production account. After verified export, the trusted controller creates
`.git/modelfc-deployed-sha` exclusively inside each permanent release: exactly the
verified 40-character SHA, deployment-account ownership and mode **0444**. It
rechecks the file before promotion. Tests/dependency services cannot write it;
the distinct runtime account cannot modify it or its protected parent directories.
Tracked-source and deployment Git verification remain unchanged.

Runtime `git_commit_sha()` accepts this metadata only beneath
`/srv/modelfc/releases/<sha>-<nonce>`, checks protected directory ownership/modes,
opens directories and metadata without following symlinks, and requires the
metadata SHA to match the release name. Resolving `/srv/modelfc/current` selects
the new release automatically. This needs no runtime `safe.directory` entries.
Missing/invalid metadata supplies no SHA; the existing Git fallback remains for
development checkouts and older releases. Host administrators and the deployment
account remain trusted. Real distinct-account readability still requires VPS
acceptance, since restricted test containers may forbid UID/GID changes.

All three service boundaries attest both `ExecStart` and `ExecStartEx`: exactly
one expected executable and complete argument vector, with **empty execution
flags** in `ExecStartEx`. Privileged (`+` / `privileged`), other flags, missing or
unprintable properties fail before service execution. Confirm installed systemd
255 effective output during VPS acceptance; the committed units need no changes.


## systemd 255 effective-property serialization

The controller combines `systemctl show --all` with read-only
`/usr/bin/busctl --system --json=short get-property` on the same allowlisted unit.
`busctl` is supplied by Ubuntu's systemd package; no Python D-Bus package, sudo
permission, unit override, or installed-boundary relaxation is required. The bus
query uses a clean environment, a 15-second timeout, and the system bus.

| Effective properties | systemd 255 text representation | Required verification |
| --- | --- | --- |
| `LoadCredential`, `LoadCredentialEncrypted` | `[unprintable]` for empty or configured arrays | Always require typed `a(ss)` empty arrays from D-Bus |
| `SetCredential`, `SetCredentialEncrypted` | `[unprintable]` for empty or configured arrays | Always require typed `a(say)` empty arrays from D-Bus |
| `ImportCredential` | Empty string when unconfigured | Require empty text and typed `as` empty array |
| `EnvironmentFiles` | No line for an empty array | If omitted, require typed `a(sb)` empty array |
| `ExecCondition`, `ExecStartPre`, `ExecStartPost`, `ExecStop`, `ExecStopPost` | No line for an empty array | If omitted, require typed `a(sasbttttuii)` empty arrays |
| `Job` | `Job=` for no queued job | Require typed `(uo)` value `[0, "/"]` before treating it as zero |

All other checks remain exact, including environment allowlists, capabilities,
stdin, resource/lifecycle limits, complete `ExecStart`/`ExecStartEx` commands and
empty execution flags. A missing credential text property is still rejected.
Explicitly configured text or nonempty typed arrays are rejected. Missing bus
results, failed queries, incorrect signatures, duplicate keys, malformed JSON,
and unexpected serialization fail closed. No credential data is included in
failure reports. Job normalization never substitutes for zero process IDs and
an empty or removed cgroup.

This policy follows the systemd v255
[systemctl printer](https://github.com/systemd/systemd/blob/v255/src/systemctl/systemctl-show.c),
[generic property printer](https://github.com/systemd/systemd/blob/v255/src/shared/bus-print-properties.c),
and [effective credential definitions](https://github.com/systemd/systemd/blob/v255/src/core/dbus-execute.c).
Regression fixtures retain the real VPS text representation and typed empty-array
responses. Host acceptance must still inspect actual effective properties as
`modelfc-deploy`; static unit verification alone does not prove the boundary.

## Worker runtime verification after Phase 5 findings

The controller records its own network namespace device/inode in the protected
test request immediately before starting the test service. It remains alive
under the deployment lock throughout the service run, keeping that namespace
identity live. This is the normal host controller invoked by SSH; candidate code
does not supply the reference. The existing read-only control-directory mount
protects the request from the test process. The test wrapper requires a valid
reference and a different self namespace, verifies it is the running MainPID of
the fixed test unit with `PrivateNetwork=yes`, no `NetworkNamespacePath` and no
`JoinsNamespaceOf`, and requires exactly the isolated loopback interface. Only
then may it inspect the candidate Git HEAD or launch candidate Python/tests.
Missing or contradictory evidence, unreadable self namespace, failed manager
queries, or unexpected interfaces fail closed. The wrapper never reads PID 1's
namespace: [procfs namespace-link access is ptrace-gated](https://man7.org/linux/man-pages/man7/namespaces.7.html),
and the intended unprivileged worker receives EACCES on this host. An unreadable
PID 1 is not treated as proof of isolation. Host administrators and the installed
controller remain trusted; these checks do not defend against a hostile root.

On systemd 255, [mount priority and duplicate removal](https://github.com/systemd/systemd/blob/v255/src/core/namespace.c)
select `PrivateTmp` directory bind mounts ahead of `TemporaryFileSystem` at the
same path. The corrected dependency unit explicitly disables that competing
mechanism while retaining separate private 256 MiB / 16,384-inode tmpfs mounts
at both `/tmp` and `/var/tmp`. No writable path is added. Before any candidate
Python or pip invocation, the installed dependency wrapper checks its own
mountinfo and opens each directory without following a leaf symlink. It requires
one full-root tmpfs mount per path, no nested mounts, rw/nodev/nosuid, distinct
devices matching the open descriptors, root-owned mode 1777, and exact kernel
byte/inode totals from fstatvfs. Ext4 private directories, absent mounts, wrong
limits/permissions, shared devices or inspection errors block installation.

After independent review/merge, install the controller and dependency unit from
the same reviewed commit and daemon-reload; the helper and other units do not
change. Repeat real acceptance before activation. Disposable diagnostics of this
correction verified isolation with PID 1 inaccessible, rejection when isolation
was disabled, both actual tmpfs mounts, byte and inode ENOSPC on each, and rejection
of legacy private directories, missing mounts and incorrect limits. Those
diagnostics did not install trusted production components or resume deployment.

### Diagnostic-only controller follow-up

This source change does not install the controller or authorize a production
retry. The installed controller currently differs from repository main in API
handoff behavior. Do not copy the whole current-main controller onto the host:
a separately reviewed diagnostic-only patch against installed hash
`da79a229d040c1b7b356e107f99e31a8136a68159a83e7de13aa3b484d6cc7a0`
must preserve that host's existing handoff behavior and exclude PR140. Disposable
real-systemd timing/cleanup acceptance and explicit installation approval remain
required. Keep the 300-second child deadline, service/resource/security limits,
state isolation and promotion gate unchanged. Missing reports after controller
termination still provide no last-test evidence; do not infer a cause.

### Installed-baseline diagnostic acceptance

`ops/vps/patches/deploy-main-5c1b-test-progress.patch` applies only the PR176
instrumentation to the complete retained installed-controller fixture. The CI
harness pins baseline SHA-256 `5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5`,
patch `efaf214ccd6d5ee8e9576de4ba8a4f17d1884062b00addd733c4d3f6040332c3`,
and result `882cb40931695df85e2b0c7d72d607b5e266e2207c895a66467e080d49ee528d`.
It does not introduce main's additional handoff/recovery behavior.

The current `Production test budget systemd acceptance` PR workflow uses a
fresh GitHub-hosted Ubuntu 24.04 VM, the system Python (version recorded in its
report), no production environment/secrets and read-only repository permission.
It reconstructs the candidate, then executes only `--run-tests` with synthetic
Git releases using the real fixed test unit. The unit's reviewed bytes are pinned;
CI additionally hides home directories. Fixture path/account collisions, absent
systemd/cgroup-v2 or mismatched effective limits fail closed. Normal completion,
malformed/flooded progress, the real 600-second child deadline within 630 seconds,
and a setsid descendant holding inherited pipes are checked. Results are consumed
only after the cgroup is empty. All fixed fixture units/files/accounts are removed
in finalization and an always-run cleanup step. No installed VPS service is used.

The sanitized `diagnostic-acceptance-<head SHA>` Actions artifact contains the
acceptance report and reconstructed exact candidate/baseline inputs; download
from that immutable run and hash-check before proposing operator installation.
A passing run establishes these synthetic real-systemd behaviors, not VPS
acceptance, outer controller deployment coordination, live API behavior or an
actual production test timing. Those controller paths remain source/mock-tested.
Review the recorded Python/systemd/runtime information against the operator's
actual host. This workflow does not install on the VPS, preserve its failure
record, authorize a retry, merge, or change installed permissions or deadlines.

### Reviewed production test budget (not installed by merging)

The completed PR180 diagnostic measured 355.202791 seconds for candidate
`471e4a36df2b47ba925e663252decff035d511af` (1748 tests, 23 skips, zero failure
categories). That diagnostic is not production acceptance. This proposal changes
only the production test budget: child600s, service630s, controller wait690s,
progress ceiling630000000 microseconds. Stop30s, controller stop60s, fail-closed
cgroup cleanup, 2-GiB/64-task limits and all access/isolation controls remain fixed.
Dependency/acquisition/pip/validator deadlines and the 75-minute CI deployment
job are unchanged. No arbitrary timeout argument or environment override exists.

`ops/vps/patches/deploy-main-882c-production-budget.patch` applies only to the
retained installed882cb controller fixture. Its exact base, patch and candidate
hashes are enforced by the existing acceptance builder; the old diagnostic patch
is preserved. The compatibility candidate excludes main's latest-main rechecks,
API handoff/adoption/recovery and new helper dependencies. Never install the
whole main controller as a shortcut. The artifact includes the exact unit too.

Immediately before test request/report/failure deletion, the outer controller
reads actual `modelfc-corner-api.service` properties as `modelfc-deploy`. Exact
loaded/active identity, positive PID/start timestamp, valid invocation ID and
effective RuntimeMaxSec=30min are required. Monotonic time sampled after the
bounded15-second query must leave at least780 seconds before scheduled rotation.
Missing, unavailable, malformed, future or insufficient values reject with
STATE_BOUNDARY_FAILED before any test-stage writes or service launch. At this
pinned baseline no restart, waiting for a new window, retry, alias, credential or
privilege change is made; the ZEN-52 draft below proposes bounded waiting.
Earlier dependency/acquisition work is not included in this stage-specific window.
This reduces scheduled-rotation risk; it cannot prevent manual restarts or crashes
and is not an API-adoption proof. Existing deployment cleanup still applies.

Acceptance checks actual systemd reads as the deployment UID, missing/inactive
API and wrong limit, and exact780-second/insufficient/future boundaries (only
the clock is advanced in the disposable boundary fixtures). Rejection preserves
evidence and the test-unit invocation ID. Real normal/failure/malformed/flood
and600-second timeout runs preserve report bounds and descendant cleanup.

Operator installation requires separate approval of pinned controller+unit bytes,
fresh baseline/idle checks, evidence backups, original lock exclusion, protected
atomic replacements and daemon-reload/effective-property verification. A partial
pair must never execute: restore both verified old files on verification failure
while retaining exclusion, then verify restoration before releasing it. Merging
does not install the trusted controller and can trigger deployment. Coordinate
installation first, then separately approve merge and one ordinary exact-main
deployment. Verify actual production test result, cleanup, promotion and
process-bound API adoption; diagnostic success cannot substitute for those.
PR179 remains a separate reviewed fixture optimization. No further diagnostic
run, deployment, API restart or host operation is authorized by this draft.

### ZEN-52: bounded natural-rotation admission (draft; not installed)

This change is separate from merged ZEN-36 PR191. PR190's
[retained diagnosis](https://github.com/stevengalvis/modelfc/pull/190#issuecomment-6296001061)
proves API age of at least 28m52s after dependency installation, beyond the
17-minute admission cutoff; a separately approved retry later passed. PR189 run
39467974292 and PR191 run 39471896581 failed STATE_BOUNDARY_FAILED before tests
and promotion; their precise historical causes remain UNKNOWN. The selected VPS
release is reported as `c0080d1b07f01bebfdeb7d8ed925c94fd58b43f1`; actual serving
identity is unverified. ZEN-50 and ZEN-51 remain Done.

The proposed controller first rechecks the full deployment boundary, immutable
candidate source, venv size, unchanged selected release and canonical-main target
eligibility after dependencies. Immediately before test-stage writes it queries
fixed API properties. Age <=1020 seconds admits immediately. Only an otherwise
valid active/running API older than that starts a 900-second monotonic wait,
retaining the existing exclusive deployment lock. Each sleep is at most 5 seconds;
each API query is at most 15 seconds and is shortened to the remaining deadline.
A query or final revalidation completing at or after the deadline cannot admit.
The callback uses existing bounded boundary/source/target queries; if those
checks consume the remaining interval, admission expires after they return.
The deadline bounds admission eligibility, not instantaneous cancellation of
existing filesystem/source checks. No API restart or deployment retry is issued.

During that age-only wait, loaded units with the exact 30-minute runtime limit
may be observed in activating/auto-restart (PID zero, empty or valid invocation),
activating/start, or deactivating/stop or stop-sigterm (positive PID/start and valid
invocation). Those states never admit. Inactive, failed, missing, other transitional
states, malformed/duplicate/extra properties, future timestamps and unexpected
limits fail closed. A fresh active/running invocation must differ from the old
invocation and have a later start. After observing it, the controller repeats the
full boundary/source/target checks and then queries API identity and age again.
Identity change during that revalidation rejects. Manual restart/crash prevention
and process-bound serving verification are not implied by an admission PASS.

Five fixed reasons extend the existing exact report schema and allowlists:
`ADMISSION_QUERY_FAILED`, `ADMISSION_PROPERTIES_INVALID`,
`ADMISSION_AGE_WAIT_EXPIRED`, `ADMISSION_IDENTITY_CHANGED`, and
`ADMISSION_TARGET_SUPERSEDED`. Other boundary,
source and target failures retain their existing codes. The workflow sanitizer
and deployment observer accept the new reasons only as FAIL, NOT_RUN, zero tests,
and NOT_ATTEMPTED promotion; arbitrary strings/properties and raw query errors
remain rejected. No test request, result or last-failure evidence is deleted and
no test service launches before admission. Existing cleanup, pending markers,
isolation, promotion and recovery contracts remain in force.

The lock can now be held up to 15 minutes longer, plus the bounded final checks.
Competing requests continue to fail with DEPLOYMENT_BUSY; no queue or automatic
retry is introduced. A target superseded while waiting cannot stage tests. The
75-minute deployment step timeout is unchanged. Operators must account for the
longer exclusion interval when coordinating separately approved operations.

#### Exact compatibility artifact and readiness

Never install main's whole controller. `ops/ci/build_admission.py` reconstructs the
retained production-budget candidate through the existing hash-verified builder,
then applies only `patches/deploy-main-08f7-admission.patch`. Its required input is
SHA256 `08f7b8df9f7807249a2057fc72e64ae0326bce85e9ee633c386d7f1bae37f785`.
`patches/deploy-main-08f7-admission.json` pins input, patch and output SHA256.
The patch adds only admission logic, its revalidation callback, a bounded
canonical-main eligibility query and fixed report reasons; it does not import
main's API handoff/adoption/recovery or privileged helper dependencies. No unit,
permission, timer or service configuration changes are included.

For a local artifact only, with a new output path:

```bash
.venv/bin/python -B ops/ci/build_admission.py --output /tmp/admission-controller.py
sha256sum /tmp/admission-controller.py
```

Code-ready requires focused tests for both main and reconstructed compatibility
bytes, strict workflow/reader tests, full CI and independent review. Tests use
synthetic clocks and properties and local disposable Git repositories; they do
not establish actual systemd transition semantics or installed behavior.
Install-ready additionally requires separately approved read-only confirmation
of current installed bytes and effective units, disposable real-systemd acceptance
of these exact new bytes (including natural rotation and rejection evidence),
review of the pinned patch and reader rollout ordering, and separate explicit
installation approval with the existing backup/exclusion/atomic-replacement and
rollback protocol. The coding environment has `tail` as PID1, so real-systemd
acceptance is unavailable here and remains a blocker. Existing production-budget
acceptance artifacts are not evidence for this new patch. Merging neither installs
it nor authorizes a production retry. No external Codex review request or merge
is part of this task. Track this work in
[ZEN-52](https://linear.app/galvis/issue/ZEN-52/bound-deployment-admission-waits-and-diagnose-failures),
which blocks ZEN-36 and relates to ZEN-45.

The separate `Natural rotation admission acceptance` PR workflow now targets the
new output identity through `ops/ci/build_admission.py`; the old production-budget
workflow and its `08f7b8...` target are preserved. The new disposable Ubuntu VM
checks the exact installed fixture bytes as the deployment UID, real boundary and
candidate/local-Git eligibility, missing/inactive/wrong-limit rejection, unchanged
test invocation and retained evidence. A synthetic `sleep infinity` API has the
real 30-minute runtime limit and `Restart=always`: the harness waits until real age
exceeds 17 minutes, then exercises the controller's real wait through systemd's
runtime-triggered restart. It does not accelerate clocks or edit runtime properties
during that case. The test holds the original lock, records the roughly 13-minute
admission wait, and requires a changed invocation, fresh age, final revalidation,
no test launch and complete fixture cleanup. The job budget is 40 minutes.

That CI result must be read from the exact PR head and its
`natural-admission-<head SHA>` artifact, including pinned input/patch/output and
runtime information; old budget acceptance cannot substitute. Source-only harness
tests merely check fixture syntax and guards. No real-systemd acceptance was run
inside the coding environment, and disposable CI acceptance still does not attest
installed VPS identity or authorize installation.


#### First-deployment supersession report correction

If admission revalidation finds that main advanced and there was no selected
release at entry, the controller reports `FAIL / ADMISSION_TARGET_SUPERSEDED`.
`previous_sha` and `final_sha` both remain null; tests are `NOT_RUN` with count zero
and promotion is `NOT_ATTEMPTED`. It never substitutes the requested or new main
SHA for an unobserved active release. Supersession with an existing selected
release retains the prior `SUPERSEDED` report and actual selected SHA.

Both strict readers explicitly allow the new reason only with those null
identities, a verified fetch, created candidate, installed dependencies, and an
enforced boundary. They continue rejecting `SUPERSEDED` with null `final_sha`.
This is a fixed failure reason, not a nullable-success schema relaxation. The
producer regression drives real admission waiting and post-rotation revalidation
with deterministic system queries, then passes the resulting reports through the
workflow sanitizer and status reader. It checks the no-active and active cases,
prior evidence, candidate cleanup, and zero test-service/promotion calls. The same
producer tests run against reconstructed compatibility bytes.

The compatibility input remains
`08f7b8df9f7807249a2057fc72e64ae0326bce85e9ee633c386d7f1bae37f785`.
After this correction, the patch SHA256 is
`c99286caf022763311289e2fa317e9306356f6f2f7bccc48f08f2aee8291b8d0`
and output SHA256 is
`26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c`.
The manifest and local builder enforce these exact identities.

Prior evidence remains historical, not acceptance for the corrected head/bytes:
full CI and Trusted OFFLINE run 39482204717 belong to head
`4c1b7c8fc2c169cceff3dd8351b35fcc17a5853a`; natural-rotation run 39476961035 and
artifact 12291019606 accepted output `5f9fba95...77b58bd`, not the corrected output.
Required next checks are full CI on the new head, the separate
`Natural rotation admission acceptance` workflow on exact output
`26b7acb8...e66e797c`, and renewed review plus exact-head Trusted OFFLINE acceptance
when separately authorized. The old budget workflow still targets input `08f7b8...`
and cannot replace new-output acceptance. No external review/OFFLINE request,
installation, deployment, provider call, or merge is authorized by this correction.
The inherited cleanup assurance caveat remains: its stop return is ignored and
only the test-unit cgroup is checked, so a passing cleanup step is not full proof
that the synthetic API cgroup was emptied.
