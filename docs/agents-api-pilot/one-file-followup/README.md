# Separately approved one-call follow-up: offline proposal

Owner: Responses one-file pilot, draft PR267. Prepared from controller
`83295d4e7ddb7f37774b12e163fae515434b2e58`. This is not approval, source publication,
VPS staging, deployment, a credit observation, or evidence of a second API call.
The JSON beside this document is deliberately invalid as live authorization.

## Original evidence stays immutable

Original task `responses-required-text-live-001`, identity
`63d219109f3b5bf120a07b2097827446e3e6f5a54bf44daf19114e99d28811ba`, has spent its
only call. HTTP 200 and reported usage 285 input/272 output were observed;
processing stopped at candidate phase, outcome unknown, with no saved candidate,
rejection reason, runtime/container identity, receipt, or patch. Unknown stays
unknown. Do not manufacture a receipt, rerun the old launcher, increase max_calls,
rename the task, clear run markers, recover/delete sidecars, or use another store.
Earlier evidence and all historical checkouts remain untouched.

Explicit schema 2 → 3 adds one table in the SAME fixed canonical store. Original
tasks/calls rows are checked exactly, hashed together and never edited. The
upgrade atomically records the new approval and original-record digest with the
version change. It requires the sole original task and exact spent call 1 with
NULL receipt/container; any discrepancy stops for review. No automatic migration
exists. Old schema 2 controllers/classifiers reject schema 3. The ordinary original
reservation path remains max_calls=1 and exhausted.

The one new row is keyed by original identity, not a new task/approval label.
It admits exactly one reservation, numbered 2, with a distinct deterministic
sandbox suffix `-2` and request hash including the complete approval digest.
Fresh labels, expiry extensions, process restarts and concurrent controllers do
not replenish it. A committed reservation with a lost acknowledgement remains
spent. A separate private run directory also refuses every reuse. Same-UID
hostile tampering or database rollback remains outside the existing threat model;
exclusive operator control and durable retention are prerequisites.

## Unchanged request and execution gates

Same original manifest, source base `1112043f391d0a263c0f66688a3ddebd606bafb8`,
`src/modelfc/providers/_csv_values.py::required_text`, source/tests/harness,
image/profile/runtime and exact wire bytes. Preserve behavior and every byte
outside the function. Luna low/default, input 8,192/output 4,096, one additional
call, no tools/stream/store/retry. Saved-key reader and account checks are reused;
no new credentials, account identifiers, price lookup or secret output.

Reservation commits with SQLite synchronous EXTRA before the key reader. Existing
bounded candidate retention and closed rejection codes apply. A patch is only a
proposal after trusted tests, exact container binding, both EOFs, exit validation,
independent cleanup and a durable new receipt. Candidate/controller failures
produce private evidence and leave this allowance spent, never a repair call.

Current estimate at full token limits, using published cache-write input
$0.125/million and output $0.50/million: $0.003072, about 0.31 US cent. This is NOT a
billing cap. Sources checked November 10, 2026:
https://developers.openai.com/api/docs/models/gpt-6-luna
https://developers.openai.com/api/docs/guides/prompt-caching
Before proceeding, observe sufficient eligible unexpired credits, the linked
subscription, and no purchased balance or billing setup shown. This limited
observation is not a guarantee that purchased-credit fallback is impossible.
No paid fallback or billing changes are authorized.

## Combined future staging and run route

Prepare ONE operator handoff only after publication/review, with an exact
published reviewed commit SHA and verified tree/source inventory filled in. Do
not run these steps now. Do not use a floating branch or the offline preparation
base as the finished source identity. Do not stage into a historical checkout.

1. Under separately authorized host staging, fetch that exact published commit
   using the existing trusted source route, verify its full SHA and clean tree,
   then stage into a new fixed operator-owned directory. Preserve source history,
   canonical store, old run files and all other controllers. Do not install or
   change the worker service. Use existing prepared account/runtime/image only.
2. Run API-free focused tests in the staged checkout before any upgrade. Use the
   existing Python environment with PYTHONPATH=src and `python -B -m unittest -q`
   for `tests.test_responses_followup_allowance`,
   `tests.test_responses_one_file_adapter`, `tests.test_responses_one_file_launcher`
   and `tests.test_responses_one_file_sandbox`, plus the canonical admission suite.
   These use temporary fixture databases and fake credentials/provider/runtime;
   they must not point at the production database or start real containers.
3. In the same handoff establish exclusive operator control, check trusted
   ancestry/ownership/modes, fixed-store schema 2 integrity, exact original rows
   and no sidecars/WAL. Read-only inspection obtains original_record_hash via
   `followup_allowance.original_record` using a read-only SQLite connection only
   AFTER the header/sidecar guards. Do not instantiate a mutating gate for this
   inspection. Record the non-secret row digest; never copy/adopt an active DB.
4. Verify the reviewed source bundle with `one_file_launcher.bundle_digest()`
   as the verified worker account; approve that digest, the original-record
   digest and exactly one additional call with the unchanged scope/settings.
   Obtain the current eligible-credit observation using only previously approved
   account access. Bind its timestamp and the user's new approval reference in
   the fixed private authorization file. This is a trusted operator attestation,
   not proof supplied by these tools. Do not fill it from inference or this
   template. Approval expires within one hour; credit observation is valid for
   at most 30 minutes. It must remain fresh at upgrade, run and key access.
5. In that same uninterrupted operator session, invoke the isolated module entry
   for `followup_launcher --upgrade` and then `--run`, using the existing trusted
   isolated source bootstrap (Python -I -S -B and an explicitly reviewed source
   root). Both use the original fixed lock. Do not replace the lock inode. No
   source edit, approval edit, credit change or delay between upgrade and run.
   Upgrade is explicit and does not itself read keys/start containers/call API.
   The run checks follow-up sandbox readiness, then reserves before credentials.
6. Stop on ANY nonzero/uncertain result. Do not resend, regenerate approval with
   another label, repeat the upgrade or remove the run directory. Inspect exact
   durable state read-only before deciding what happened. An interrupted upgrade
   may have committed; the presence of schema 3 is not permission to rerun an
   uncertain run. Expired approval after upgrade blocks rather than silently
   replacing the immutable approval.
7. The private `report.json` summarizes allowance/approval identity, usage,
   transport, candidate hash/retention/rejection code, evidence hash and patch hash.
   `evidence.json` retains full bounded controller observations. Entry failures
   report closed stage/reason labels, never exception text. Share only sanitized
   evidence status, usage, hashes and any reviewed proposed
   patch. Candidate bytes remain private inert `candidate-source.bin` data.
   Never auto-apply, commit, publish, merge or deploy the proposed patch.

Fixed new authorization path:
`/home/zeno-worker/responses-required-text-followup-001-authorization.json`.
Fixed new evidence directory:
`/home/zeno-worker/responses-required-text-followup-001`.
Store remains `/home/zeno-worker/responses-task-admission/canonical.sqlite3`.
This sequence avoids a separate redundant source-staging/readiness round trip;
its final exact-SHA commands and actual approval must be supplied only once the
reviewed source is published and the user separately authorizes the paid call.
