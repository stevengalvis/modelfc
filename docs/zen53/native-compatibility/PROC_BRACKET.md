# Local evidence-only instrumentation; not published or run

This document describes the a64d152 evidence-only layer. The new local
[provisional protocol](PROVISIONAL_PROTOCOL.md) adds a separately gated
conditional acceptance path; its requirements and limitations also apply.

The existing reporter still rejects inherited-proc ENOENT. No probe, runtime
policy, cleanup rule, acceptance predicate or external observer is added.
The bridge injects definitions and three hooks into the generated launcher;
historical sources remain unchanged. Existing generated launcher/dependency/
manifest pins are derived normally. No installed dependency is repinned.

Before the native fork, the launcher opens a CLOEXEC pidfd for itself and samples
its existing pinned procfd identity, namespace IDs (including time), UID/GID maps
and visible procfs metadata. On the first complete numeric reporter context line,
before the unchanged output classifier rejects the following failure line, it
checks the same pidfd/identity and samples again. Only matching observations
produce BOUND_OUTER_LIVE_BEFORE_FORK_AND_AFTER_CONTEXT. Missing facilities,
malformed context, wrong PID/start identity, dead pidfd or changed observations
produce UNKNOWN. No new observation authorizes PASS. One bounded record is
emitted; the pidfd is closed on launcher exit and not inherited by native exec.

The bracket is causal: before native fork -> reviewed reporter's probe -> pipe
context reception -> same launcher alive. It does not assume that inner and outer
monotonic clocks are comparable. The reported clock interval is checked only for
internal order. A matching live pidfd before and after establishes continuity of
that process, not whether every observation in an untrusted line is truthful.
Use requires the existing authenticated, fixed reporter and launch protocol.
The record hashes the exact received context line for evidence binding.

Outer procfs options and credential/namespace observations are recorded; the
existing inner context supplies its namespace IDs, NSpid, mappings and procfs
device/mount ID. Matching device plus known mount topology supports inheritance,
but is not by itself proof of the procfs PID-namespace association or the kernel
reason for ENOENT. This instrumentation can remove the dead-outer ambiguity; it
does not alone authorize changing the inherited-proc denial predicate.

The cloud namespace experiment could not create its UID map: /proc/self/uid_map
is read-only. Fixtures exercise ordering and uncertainty, not kernel hidepid
behavior. A real isolated reproduction remains unavailable in this environment.
The consumed VPS stage, PR217 and separate lifecycle/proc-visibility patches
remain unchanged. No execution, installation, publication or retry is included.
