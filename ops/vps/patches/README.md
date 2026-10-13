# Installed-controller diagnostics: prepare only

Contributes to ZEN-34. This is a diagnostic-only backport of PR142 against the
actual installed controller, not a replacement with the current-main controller.
No production file, service, permission, schedule or selected release changes as
a result of this PR. Installation and host acceptance require separate approval.

## Verified source and exact installation diff

The installed SHA-256 reported in
[the failed normal attempt](https://github.com/stevengalvis/modelfc/pull/142#issuecomment-6152129684)
exactly matches `ops/vps/deploy_main.py` from commit
`ed412eba81c6ec08fad89868543d5e1d44457256`, Git blob
`5030ddd514b708d6f596ece4283cf8741c8afc59`.
The byte-for-byte source is retained as
`tests/fixtures/vps/deploy_main_da79.py.txt` for offline, shallow-checkout tests.
It is a historical test fixture, not an executable installation target.

| Artifact | SHA-256 |
| --- | --- |
| Installed baseline | `da79a229d040c1b7b356e107f99e31a8136a68159a83e7de13aa3b484d6cc7a0` |
| `deploy-main-da79-diagnostics.patch` | `7437f8d21df799f6f5ff5b03c3557b5c0b2a19955d7643b42b148e453c3a3479` |
| Patched candidate | `5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5` |

The adjacent unified patch is the **entire proposed installation diff**. It adds
bounded TIMEOUT/CAPTURE_ERROR/PROCESS_ERROR diagnostics, source-allowlisted last
observed test identity, explicit unknown counts, and removal of a stale failure
artifact before a new attempt. Existing ordinary failure fields remain compatible.
The private artifact is tied to the request SHA and nonce-bearing release ID;
public report `tests_run=0` remains legacy "no accepted count", while private
`tests_run=null` and `count_known=false` express uncertainty. Raw output, tracebacks,
paths, credentials and exception strings are never retained. A last observed test
is progress evidence, not proof that the named test caused the failure.

All existing functions except `tests`, `capture_test_process`,
`valid_test_diagnostics`, and `run_tests` remain byte-identical; only two diagnostic
helpers are added. Tests check this and the exact output hash. Old `deploy` /
`promote` / handoff behavior, 300-second child deadline, guards, limits and cleanup
remain unchanged. PR137/PR140 handoff changes are excluded. The normal repository
controller and installed service-unit sources are untouched.

For local preparation only, copy the baseline to a disposable directory, verify
its hash above, apply `patch --batch --fuzz=0 <temporary-copy> <
ops/vps/patches/deploy-main-da79-diagnostics.patch`, and verify the exact candidate
hash. A patch success alone is insufficient: reject either hash mismatch. Never
apply this command directly to `/opt`. The test suite performs this preparation
and exercises the result automatically without Git history or network access.

## Bounded disposable acceptance, separately approved

1. Use this reviewed exact PR head and a disposable copy of the verified installed
   baseline. Check all three hashes. Do not copy `ops/vps/deploy_main.py` from main.
2. In a disposable isolated harness, run only the diagnostic regression tests
   against the patched copy, including real child timeout/read-error cleanup,
   missing/invalid reports, stale-attempt removal, source allowlisting and unknown
   counts. No full deployment suite, promotion or production state access. Local
   tests use short child deadlines; the installed default remains 300 seconds.
3. For real-systemd acceptance, retain the host's existing test-unit identity,
   resource/security/network boundaries, 300-second child and 330-second service
   budgets. Use a disposable unit/control/release tree, never the normal deployment
   request path. Run one synthetic verbose test that emits an allowlisted identity
   then stalls. Expect TIMEOUT, count_known=false, tests_run=null, matching SHA and
   release ID, bounded artifact and no raw payload. Bound the outer harness to the
   service budget plus its existing stop allowance; no retry loop. If a faithful
   disposable harness cannot be provided, report that blocker rather than relax
   isolation or run the production suite.
4. Confirm child/descendant cleanup and no surviving unit/job/processes. Confirm
   serving release, production state bytes, controller/unit hashes and ordinary
   schedules unchanged. Remove only the exact disposable resources created here.
5. Return the evidence and obtain explicit approval **before installation**. The
   proposed installation affects only `/opt/modelfc-deploy/deploy_main.py`: recheck
   baseline hash immediately before replacement, retain a protected rollback copy,
   atomically replace with the verified candidate while preserving existing owner,
   group and mode. No unit, helper, sudo, credential, deadline or permission changes;
   no daemon-reload is needed for a Python-file-only replacement. Serialize with the
   existing deployment lock and do not replace a controller during an active job.
6. A subsequent normal deployment attempt needs separate approval. Capture its
   attempt-specific private artifact if it fails; do not raise time limits or
   attribute causality from elapsed time alone. Rollback, if approved, restores only
   the exact protected baseline bytes and original metadata.

The reported 300.879956-second exit strongly suggests a child/capture deadline,
but does not prove its cause. This patch prepares evidence collection; it does not
claim to fix the original slow/hung test. Trusted OFFLINE validates application
source only and cannot attest installation or real-systemd behavior of this patch.
