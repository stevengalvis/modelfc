# Concrete no-network OS acceptance candidate

Implementation owner: the API adapter task. Local base:
`c2a86f70cabca28b03b3ed2bab86bf203c437f4e`.

`agents_os_backend.py` implements an actual launch path through the unchanged
`SystemdContainment` and `/usr/bin/bwrap`. This is a separate **synthetic sandbox
acceptance program**, not an Agents API executor. It supplies no arbitrary
command, repository, credential, control-channel connection or live mode. Merely
importing it or building its command does not execute anything. Its explicit
`run_acceptance(spec)` function would launch a real service and namespaces;
that function has only been tested with mocked OS/process boundaries here.

## Concrete containment path

1. Require a root supervisor and the exact fixture naming pattern
   `/srv/modelfc-agents-fixture-<32 lowercase hexadecimal characters>`. Require
   protected root-owned ancestors, a mode-0755 fixture root and a mode-0700
   `evidence` directory. Arbitrary production paths are rejected.
2. Inspect `image` under that root. Its root/directories must be root-owned
   mode 0555; files must be root-owned mode 0444 or 0555, with one hard link.
   Symlinks, devices, sockets, FIFOs, writable entries, nested mounts and device
   changes are rejected. The tree is bounded to 8,192 entries, 512 MiB total and
   128 MiB per file. It must contain a nonempty regular executable
   `/usr/bin/python3`, and empty `proc`, `dev`, `tmp` and `workspace` mountpoints.
   Only `usr`, `lib`, `lib64`, `proc`, `dev`, `tmp`, `workspace` are permitted at
   its top level. This deliberately excludes a general distribution rootfs with
   symlinks or account configuration.
3. Match a caller-pinned canonical image digest and a caller-pinned digest of
   the root-owned, non-setuid, mode-0755 host `/usr/bin/bwrap`. Compare full file
   identities as well as content again immediately before dispatch and after
   cleanup. The image digest is SHA-256 over compact JSON of the sorted tuples
   `(relative path, permission mode, content SHA-256 or null for a directory)`.
   `inspect_image(image)[0]` computes it without running image code.
4. Create a new exclusive attempt directory named by the supplied 64-hex
   attempt ID. Sync its reservation, including image/bubblewrap/probe digests,
   UID/GID and initial host namespace identities, before calling the backend.
   Reusing that directory fails; there is no replay or automatic cleanup.
5. Launch the fixed bubblewrap command inside the existing transient systemd
   cgroup. It uses `--unshare-all`, `--disable-userns`, `--die-with-parent`,
   `--new-session`, `--cap-drop ALL`, explicit non-root UID/GID and `--clearenv`.
   The only host tree mounted into it is the inspected image, read-only at `/`.
   `/proc` and `/dev` are newly provided. `/tmp`, a fresh private home and
   `/workspace` are namespace-local tmpfs mounts. No host worktree is bound and
   no host evidence directory is visible to the child. The fixed Python probe
   receives only synthetic identity/binding data; it runs directly, not through
   a shell. It does not execute a model, open a socket, connect to a service or
   request credentials.
6. Independently read back the service's configured memory, task, CPU, deadline,
   stop, privilege, delegation and cgroup-protection properties. Unknown values
   fail closed. Current accepted limits are 1 GiB memory, 64 tasks, 100% CPU,
   180-second service runtime and five-second stop timeout. The systemd CLI's
   expected time formatting is checked exactly; different versions may block
   rather than silently weaken verification.
7. Read one bounded probe report within ten seconds, draining stderr without
   retaining its raw contents. The probe checks UID/GID/groups, all five empty
   capability sets and `NoNewPrivs`; six namespace identities differing from
   the supervisor; clean environment and inherited descriptors; read-only image
   root; empty private home; and a writable new tmpfs workspace. It writes and
   reads one fixed synthetic file, reports its workspace device/inode and then
   sleeps so startup can be observed and the supervisor can stop it.
8. Always attempt backend termination and independent whole-cgroup emptiness
   verification. Retain a private `acceptance.json` containing exact bindings,
   accepted observations, effective limits, cleanup status and a fixed failure
   stage if applicable. A successful local result requires every check plus
   unchanged image/bubblewrap identities. `ready_for_live` is always false.

## What must already exist before a separately authorized run

- A disposable Linux host with system systemd, cgroup v2 and a separately
  authorized root supervisor. The host tools and Python controller must be
  trusted and protected. The selected non-root UID/GID must be dedicated to
  this test; the program does not provision an account or prove UID exclusivity.
- An installed, compatible non-setuid bubblewrap build supporting these flags,
  with unprivileged user/network/mount/PID/IPC/UTS namespace creation permitted
  under the exact systemd, LSM and seccomp constraints. No settings are changed
  or bypassed to make it work. A rejected flag or denied namespace is BLOCKED.
- A separately reviewed minimal, credential-free image with the exact layout
  and ownership above and a functioning Python 3 runtime plus its dependencies.
  The candidate does not build, install or download that image, guess its pin,
  copy host directories into it, or establish exec-server compatibility.
- Exclusive operator control of the prepared root and image while acceptance
  runs. Root ownership/read-only permissions prevent writes by the worker, not
  mutation by an administrator. Rechecks are not an atomic filesystem snapshot
  against a concurrent trusted-root change. The pinned contents must be reviewed
  before running any image code.
- Explicit approval for that target host, its prepared inputs and one bounded
  local run. Creating the fixture, installing software, changing user-namespace
  policy or running the real service is outside this implementation task.

## Limits and honest interpretation

No real OS acceptance was run here. The earlier namespace probe was blocked;
this implementation does not retry it, alter host settings, request extra
privileges or use a weaker route. Mock success proves contract handling and
command integration, not that the target host can create these namespaces.

Even a future `passed` report would establish only this fixed harmless probe
under that exact prepared host/image. It is not an adversarial escape test,
full descendant-escape acceptance, a kernel audit, a hard disk/swap guarantee,
or nested Codex command-sandbox compatibility. Writable tmpfs consumes memory;
the existing backend does not set `MemorySwapMax`, so no swap cap is claimed.

The existing systemd backend is intentionally unchanged. It conservatively
requires confirmed startup and stop before accepting empty containment. If a
startup or stop response is lost, this program can remain BLOCKED even after
the processes disappear. It does not infer cleanup, edit the backend's flags
or recover the lifecycle across controller crashes. Retain the unit and attempt
evidence for operator reconciliation; do not retry or delete it automatically.

Network isolation here blocks **all** external connectivity, including the
Agents API executor control channel. This therefore cannot run the real API
executor. Protected restricted-key injection, a separately enforced control
channel, denied command-network access, API session/task orchestration, budget
approval and recovery remain separate work. Do not turn this into a live backend
by adding an application key, broad egress, host credential mounts or arbitrary
probe arguments.

## Offline verification performed

27 focused tests passed on Python 3.12.14. They exercise file inventory/pins,
reject unsafe inputs, construct the complete bubblewrap command through the
unchanged systemd backend with `Popen` mocked, validate bounded reports and
effective limits, enforce exclusive reservations, and block on uncertain cleanup
or changed input identities. Ownership and process/service readbacks are modeled;
no real privileged host, services or namespaces are used.

    python -B -m unittest -q tests.test_agents_os_backend
    ruff check ops/bounded_worker/agents_os_backend.py tests/test_agents_os_backend.py

Pinned Ruff 0.17.2 passed. Full repository CI, trusted host acceptance and live
execution are distinct and are not claimed by these local results.
