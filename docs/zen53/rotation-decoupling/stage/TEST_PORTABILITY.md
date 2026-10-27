# ZEN-53 builder test portability correction

The [first VPS preparation report](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6391466007)
verified the exact predecessor package and recovery bytes, then stopped at one
synthetic-test failure. It did **not** invoke the real preparation CLI. Its
nonce `7cca7ca29452c6d75fc0096d541c5cb1` is consumed; no tool/capture
stage or launch manifest was created.

The failed test conflated two predicates. It changed the expected **file UID**
and demanded `BUILD_SOURCE_ANCESTOR`. On a real root-run fixture, each
root-owned ancestor satisfies `FileSource._parent` independently of the
expected file UID, so the source correctly rejects the file at
`BUILD_SOURCE_METADATA`. In a non-root temporary fixture, an owner-owned
ancestor can instead fail before the file is opened. Neither result changes
the production acceptance predicate.

The corrected UID test injects an already-open directory descriptor at the
parent seam and requires `BUILD_SOURCE_METADATA` for the mismatched file UID.
A separate real-path fixture creates an owner-controlled `0777` directory and
requires `BUILD_SOURCE_ANCESTOR`. Both tests require one exact error, not an
either/or set. The mode, GID, deadline, aggregate-byte and parent-replacement
negatives remain intact. An additional identity-adapter test requires a
root-effective UID with a non-root effective GID to stop **before** object
creation.

Portability review of the full test suite: filesystem fixtures use dynamic
`os.geteuid()`, `os.getegid()` and root-ancestor identity, or supply explicit
synthetic `0:0` identities through mocked adapters. Literal `/tmp` paths in
launcher/controller tests are parsed as data and are not operational stage
paths. The only assertion whose expected error depended on the runner's
actual root identity was the UID/ancestor conflation corrected above. Tests
that use a real temporary directory exercise local no-follow/mode behavior;
they do not assert that cloud owner IDs equal VPS root IDs. No production code,
source policy, resource limit or runtime contract changed.

The cloud suite runs under a non-root identity and proves the corrected
synthetic logic only. The real root:root preparation command, host source
metadata/topology, directory construction, systemd/cgroup behavior, namespace
isolation and physical I/O enforcement remain **unrun/unproved** for this
candidate. A future fresh-nonce preparation requires separate publication and
operator use of the exact reviewed archive; passing tests alone is not a
preparation or isolation result.
