# Reviewed mailbox operator artifact

Published by user approval after the retained offline review. Historical review/scope files describe their pre-publication state; publication and NEW-release synthetic staging are now approved. Real credentials, live jobs, activation, merge and deployment remain excluded.

Source: `aab60b0c72f4e7081ee801c40372e6df786927e4`, draft PR203. Installed `c1d98ea9560fa5df9bbb8fed5e9f96ea3dabd3a3` remains unchanged. This artifact-only branch is separate from the application source and has no workflow or activation hook.

`SHA256SUMS` binds the exact reviewed helper, manifest, tests, review evidence, patch and recoverable Git bundle. Verify it before use. The bundle requires base c1d98ea. No secrets are included. The source manifest covers all 503 tracked source files.

Synthetic acceptance: 39 caller/mailbox tests and 15 operator tests. On a different host set test-only `FACTORY_TEST_SOURCE` to its disposable source copy; production helper source remains fixed. Root operator startup must actually use a clean environment and `python3.12 -I -S -B`; do not invoke production main for synthetic staging.
