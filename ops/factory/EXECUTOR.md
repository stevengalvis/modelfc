# Concrete fixed-workflow executor — local, not activated

This candidate extends the reviewed local dispatch adapter without changing the
accepted repair coordinator, proposal family, preparation APIs, isolation policy,
resource ceilings or cleanup/acceptance predicates. No configuration for a real
host, credential, login, service, schedule or live invocation is supplied.

`concrete_executor.receive_configured_once(...)` is the explicit root integration
with `dispatch.receive_once`. It constructs the configured executor before the
receiver runs, then uses the existing receiver delivery reservation and repair
journal. It has no CLI, daemon or deployment hook. The original PR203 mailbox
and manual no-code caller remain byte-identical.

## What is implemented

1. `protected_inputs.py` authenticates bounded file bytes against independently
   supplied SHA256/size/mode pins, through no-follow descriptors and protected
   root-owned ancestors. It checks held/named identities before returning bytes.
   Generic inputs reject credential locations. It never reads OAuth files.
2. The executor authenticates a complete fixed source closure and compiles those
   exact Python snapshots. It rejects ambient cached modules, even ones claiming
   the same `__file__`; deferred pipeline/runtime/usage loaders use the same
   snapshot loader instead of `.pyc` or arbitrary paths. Embedded `.inc`, profile,
   failure-context and provenance files are pinned too. The privileged bootstrap
   and OS interpreter/stdlib remain trusted; this module cannot authenticate code
   that ran before its own import.
3. Historical/recovery/object/owner evidence is independently pinned. Before a
   model call, the existing bridge authenticates the exact plan and the existing
   materializer's bounded source reader verifies installed runtime dependencies.
   That specialized fixed dependency route includes the already-reviewed native
   package member under `/root/.codex/packages/.../bin/codex`; it does not relax
   generic secret-path rejection or read login data. No library repinning occurs.
4. Fresh stages use the existing strict `/tmp/zen53-...` sticky-parent contract,
   exclusive root-owned creation and held stage/objects identities. The generic
   protected-input helper is not weakened to accept `/tmp`. Failed preparation
   residue is preserved, not repaired or retried.
5. Read-only admission extracts only reviewed `idle`, `production`, lock metadata
   and their pure helper definitions from the pinned supervisor source, never
   its module body. Existing timer, busy-worker, headroom, API-rotation and
   AppArmor predicates are retained. Root systemctl/busctl binaries are pinned,
   use minimal fixed environments and bounded query output/deadlines.
6. `model_process.py` runs the pinned resolved native Codex binary as the specified
   non-root UID/GID with supplementary groups cleared. Only that binary FD and
   normal stdio reach the child. Its environment contains configured normal-user
   HOME/CODEX_HOME plus fixed PATH/LANG; no root environment, root authentication,
   API keys or provider overrides are inherited. The fixed command is the
   reviewed `exec --ephemeral --ignore-user-config --ignore-rules
   --skip-git-repo-check --sandbox read-only --json --cd ...` invocation with
   approval policy `never`, provider `openai`, and a synthetic stdin prompt.
7. Nonblocking stdin and stdout/stderr collection enforces a 90-second model
   deadline and 65536-byte limit on each captured output stream. Termination uses
   up to five seconds of grace, then KILL of the owned group, followed by up to
   five seconds to reap. Thus observation/return may take up to 100 seconds;
   the work deadline is not extended. An unreaped child reserves the PID/PGID
   until all possible signals have been sent. No signal occurs after entering
   a potentially reaping wait, including interrupted waits. Missing support or
   uncertain termination fails the attempt, without retry.
8. The exact private capture is persisted, read back and passed through the real
   strict capture/proposal pipeline. The supplied replacement is materialized
   through the existing APIs, preserving sealed sources and pins. Overflow,
   timeout, invalid/tool-bearing capture or invalid proposal never launches a
   worker. Incomplete usage is unknown, not zero.
9. The pinned root Python executes the exact fresh authenticated supervisor with
   `-I -B .../supervisor.py --approved-launch`, a minimal environment and no OAuth
   context. **There is no outer timeout, process group, kill or terminate path for
   the supervisor.** Its protective hold can keep this call pending indefinitely;
   an interruption never kills it or advances the reservation.
10. After terminal supervisor exit, the executor reauthenticates held stage,
    manifest, immutable files, supervisor and protected result bytes. Fresh
    read-only observations check exact unit/cgroup/dynamic-user/private-tmp/stage
    mount absence, original lock identity and unheld state, and unchanged
    production baseline. An independent descriptor-based readback reopens the
    actual fixed workspace, tests and canaries using the protected published
    provision identity, nonce and digest. It does not accept a claimed workspace
    hash as independent evidence. The existing strict pipeline validates all
    result/proof/task/readback bindings before a receiver receipt can advance.

## Required configuration — no host values are invented

The caller supplies a pinned configuration file record with exact `path`,
`sha256`, `size` and numeric `mode`. The file itself must be root-owned with
protected ancestors. It has these exact top-level fields:

- `version: 1`, the approved 40-hex `revision`, and physical `release_root` matching
  the executing reviewed release.
- `sources`: exactly the fixed `REQUIRED` relative source names in
  `concrete_executor.py`, each mapped to a complete protected file pin. Module
  and embedded-byte hashes must come from the reviewed release, not current
  unverified host discovery.
- `tools`: exact protected pins for resolved native `codex`, `python`, `systemctl`
  and `busctl` ELF binaries. Symlink launcher paths or shell/Node wrappers are
  not accepted. Tool hashes and actual resolved paths remain missing host inputs.
- `model`: nonzero existing `uid`/`gid`, passwd-matching `home`, existing
  `codex_home`, two distinct pre-provisioned private empty `workdirs`, and
  `cli_version: "0.164.0"`. That version is an operator's independently verified
  pin-to-version assertion, not a version inferred from a filename. No login,
  credential migration or new account is performed. The actual existing OAuth
  UID/home has not been established by these local tests. Root OAuth is not a
  fallback.
- `historical`, `objects`, `bundled`: logical names/digests mapped to exact protected
  byte-file pins; `owner_evidence` and `recovery`: exact protected file pins.
  Existing bridge and recovery contracts further authenticate their contents.
- `stages`: exactly two approved fresh `/tmp/zen53-...` paths; `reports`: exactly
  two separate pre-provisioned empty root-owned 0700 report directories. These
  locations do not come from GitHub text. Existing paths are not reused.
- `production_sha256`: canonical SHA256 of the entire approved `production()`
  baseline, including current service process/invocation identities. This is
  deliberately **per-grant configuration**, invalidated by ordinary service
  rotation. It is not a standing automatic-operation contract. No implicit
  weakening to a static-only baseline is introduced here.

Expiry/revocation is checked again inside the executor immediately before the
model call and immediately before the worker launch. The existing receiver also
checks authority before each reservation and result publication. No new token,
spending or HTTP-request budget is invented. Reported CLI token counts retain
the existing observational meaning and internal retries are not harness attempts.

## Limits and next real gate

Local mocked tests are not evidence of installed execution. Read-only OS queries
and protected files establish the narrow existing supervisor cleanup predicates;
they do not prove arbitrary global external effects or containment of escaped
stock CLI tools. Host Codex tool availability is unchanged. Instructions plus
post-hoc event rejection do not prevent a tool from executing. No separate HTTP
health endpoint is introduced or claimed verified; the existing production
identity semantics are used.

Filesystem operations rely on a functioning trusted local filesystem. The copied
supervisor read-only traversal semantics are preserved; they are not an additional
host-wide hard deadline. A supervisor hold remains pending until a separately
reviewed recovery decision. There is no automated retirement, forced cleanup,
reservation reset or result repost.

The remaining action-time gates are the actual reviewed host configuration and
source/tool/version evidence, existing normal-user OAuth identity/access, approved
private paths and expiring exact request scope, then separate installation/live
validation. Do not silently create those inputs or use these tests as authority
for model/GitHub/native calls. This candidate provides concrete code, not an
activated no-copy agent service or general repository-coding acceptance.
