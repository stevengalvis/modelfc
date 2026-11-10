# Protected Linear-to-Agents host composition

This is an offline source implementation. It installs nothing, changes no saved
configuration, provisions no credentials, starts no timer and makes no API call
on import or construction. `activated: false` is not an installed-host check.
Exact-head CI and authorized host acceptance remain separate requirements.

## One dispatcher, two explicit targets

The existing direct `linear_coding_entry` and paired-approval
`linear_remote_entry` constructors accept an optional `api_settings` path only
with an `agents-api` grant. Both use `FixedAPIExecutor` under the same held
`coding.lock` and `coding.sqlite` connection as subscription tasks. Without that
path, API requests return `unsupported_target`. Subscription construction is
unchanged; neither target falls back to the other or resumes an interactive chat.

An API execution still needs the original Linear actor/audience/scope approval,
v2 target request, confirmed acknowledgment and shared launching receipt. The
fixed factory additionally validates an exact task-bound API model/spend/runtime
grant, independent verification plan and JSON-call oracle policy before any API
key is read. It checks all source/install pins first. The API settings and the
original host settings are rechecked at the controller's operation boundaries.
Changed settings block new input while preserving cleanup authority.

`api_settings` cannot be combined with the subscription storage or patch
publication settings. It never uploads a patch or creates a PR. Only existing
sanitized acknowledgment/result messages can be sent through the approved Linear
destination. API/session/test evidence remains in the protected evidence tree.

## Protected configuration

The API settings file is a root-owned, mode-0600 JSON file under protected
root-owned directories, with exactly these fields:

- `version`: integer 1.
- `enabled`: literal boolean; false is disabled, true still requires every gate.
- `broker`: the exact serialized `BrokerSpec`, including its nested
  `PreparedTarget` and all required source/interpreter/profile digests.
- `api_approval`: absolute path to the separate protected `APIApproval` file.
- `verification_plan`: absolute path to the protected `VerificationPlan` file.
- `oracle_policy`: absolute path to the protected `JSONCallPolicy` file.

These control files must be outside the workspace, mirror, state, evidence, broker
code and rootless runtime directories. The shared workspace, mirror, state and
evidence roots must be distinct and protected. Broker UID/GID must match the existing fixed
credential-owner account; there is no account creation or arbitrary credential
path. Copying a sample hash or setting `enabled` does not produce acceptance.

Both targets retain exactly the same `runtime.workspace_root`, evidence root,
state root, mirror and held receipt-store lock. The workspace is root-owned mode
0711. Before enabling either target bundle, the operator must provision its fixed
`.agents-api-checkouts` child as root-owned mode 0700. API construction validates
that child and every ancestor, without creating it, changing ownership/modes or
accepting a symlink alias. The existing `Checkout` 0700 requirement is unchanged.
An older API-only 0700 workspace does not satisfy this shared layout.

Subscription task directories remain worker-owned 0700 immediate children of
the workspace. Task IDs cannot contain a dot, so they cannot collide with the
reserved API child. Each API baseline is a new root-owned 0700 directory beneath
that private child; the subscription worker cannot traverse it. State and
evidence stay in separate root-owned 0700 roots. Both adapters require the same
protected local mirror's `refs/heads/main` to equal the approved base revision.
The trusted API checkout's clone/checkout Git children use umask 0022 so regular
source files meet the verifier's 0644 contract even under a 0077 supervisor.
The supervisor's umask and every other Git caller's defaults are unchanged.
No live directory or permission changes are performed by the source change.

The dedicated CI `tests.test_linear_workspace_layout --check-root` gate uses
only its own temporary filesystem/Git fixture and requires all real root,
ownership, mode, alias and cross-user isolation checks to run without skips.
Ordinary nonroot discovery skips these tests explicitly; mocked dispatch tests
are not a substitute for this gate or installed-host acceptance.

The approval must name the canonical exact Task digest, an explicit model,
positive total spend limit in cents, the exact broker/runtime and worker
acceptance digests, and a future expiry. The verification plan and oracle policy
must agree on Task/runtime, fixed harness, test image/acceptance, expected test
names and execution/output bounds. Expected values are protected outside the
candidate container. This profile supports bounded Python JSON-call assertions,
not arbitrary task-supplied test commands or a general repository test runner.

The minimal pinned broker installation includes the fixed existing credential
reader and its terminal-helper dependency, in addition to the repository broker
closure. The complete root supervisor Python inventory is pinned separately.
Changing either installation requires a newly reviewed pin and applicable host
acceptance; it must not silently inherit an older source acceptance.

## Existing credentials and cleanup

After shared dispatch, exact policy and installation checks, one fixed short-lived
Python helper runs as the existing credential owner and invokes the original
`read_credentials` implementation. That implementation retains its UID, no-follow
directory walk, ready marker, file ownership/mode and key-format checks. Root does
not bypass it or inspect unrelated stores. The helper has empty supplementary
groups, a minimal environment, closed inherited descriptors, no stdin and only a
private stdout pipe. The bounded canonical response never appears in logs, argv,
environment variables, source snapshots or new files.

The root supervisor retains the application key. Only the separate environment
key enters the fixed rootless runtime broker. This code does not create/save a
key, authorize its use, widen API account access or grant a budget. Actual use
requires applicable credential, source-sharing and spend authorization.

Every adapter exit attempts bounded runtime close, including preparation errors
and known failed cloud turns. A failed or uncertain close blocks the shared job.
The runtime does not kill the only containment cleanup helper or infer container
emptiness from helper exit. Unknown operations remain held without retry,
history reset, automatic reconciliation or target fallback.

## Next acceptance steps

1. Publish the reviewed source only when authorized, then obtain exact-head CI.
2. Approve and verify the installed supervisor, minimal broker/interpreter pins,
   worker/test images, effective network profile and root/nonroot handoff.
3. Exercise source import/export, nested sandbox, both EOFs, cgroup cleanup and
   the independent oracle on that installation without a provider call first.
4. Provision the existing shared store's explicit pickup/API migrations and
   protected task grants without overwriting historical receipts. Use one
   upgraded controller set; older admission code must not run alongside it.
5. With explicit target/task/source/model/spend/key authorization, run one bounded
   Linear-to-ack-to-result task per target, retaining private exact-head evidence.
6. Enable only the approved finite pickup schedule after those gates pass.

A successful Responses pilot, synthetic API sentinel, or subscription run does
not substitute for either real target's acceptance. No live acceptance is claimed
by the offline tests.
