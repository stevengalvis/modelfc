# ZEN-53 restricted Python closure successor

This is an offline source candidate stacked on PR #212. See
[the closure contract](stage/PYTHON_CLOSURE.md) for the defect, authenticated
seven-file dependency addition, regression coverage and remaining host gates.
`SOURCE_TREE_MANIFEST.json` identifies the two **new** archives and inventories.
No archive is interchangeable with the October 27 failed attempt's packages.
The original files remain available at PR212 commit
`0a2b1771c1770193f95d917ace1bcdd4a944598f`.

The historical `packaging/zen53_reconstruct_pr212_archives.py` recipe remains
unchanged and only reconstructs its own pinned historical checkout. For this
successor, `packaging/build_python_closure_candidate.py --gzip-sha256 HASH
--output NEW_DIRECTORY` verifies all derived inventories/pins and emits the new
archives; `--refresh` is a developer-only source regeneration operation. The
gzip hash must be independently authenticated on any target machine; local
cloud ownership/hash is not a VPS pin. Both recipes retain the same bounded
gzip invocation and source checks. Tests use the existing local gzip explicitly.

A checkout is not an installed package. The successor requires independent
review, exact-head CI/OFFLINE gates and separately authorized preparation and
installation before any future diagnostic. The failed nonce, approval, claim,
protected audit and evidence remain unchanged with no retry or retirement.
No service, security policy, production package or persistent access is changed.
