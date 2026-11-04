# Unwired no-model sandbox candidate

This offline candidate has no operator entrypoint and is **not ready for a host
run**. It neither implements fixture preparation nor supplies acceptance evidence.
It proposes a fixed direct `codex sandbox` command and a test worker for the
existing launcher. It does not start an app-server, request a model, or prove
app-server/code-mode behavior. Its probe output is worker-shared and untrusted.

The retained prior prepared-baseline report is PR 234 comment 6566424521; it
used Codex 0.166.0, disposable inputs and the direct sandbox CLI, on an older
revision without this storage boundary. It reported normal and interrupted
metadata checks and cleanup, while the outer supervisor exited 1 and the
interrupted terminal signal was not retained. PR 233 comment 6565547327
explains CLI and app-server helper-grant differences. No historical cleanup or
new-system acceptance follows from those reports.

Before any host proposal, a separate reviewed supervisor must: provision fresh
disposable fixtures; provide valid syntactic fake config and fake auth with
creation-receipt identity and content checks; invoke this worker through the
unchanged storage launcher and prepared baseline; bound its output and timeout;
and bind independently observed host process identity to the unit/cgroup before
interruption. The nested process may use a PID namespace, and checking the
`systemd-run` client alone is not proof that the probe is alive. Independently
verify cleanup and snapshots after both normal and interrupted cases. Preserve
evidence and keep all bridge services paused. The existing storage fixture's
fake config is deliberately not valid TOML and cannot be silently replaced.

The candidate omits managed configuration: the pinned CLI can perform cloud
configuration bootstrap when it is included, and the current outer unit does
not establish network isolation for that bootstrap. Thus its effective profile
is not verified equivalent to app-server and configured child network denial is
not proof of actual network behavior. Review the exact binary's source and
resolve the no-network and configuration gates before any host execution. The
operator must independently verify the current protected binary and helper pins.
