# Local-only native candidate bridge integration

NOT PREPARED, NOT INSTALLED, NOT RUNTIME ACCEPTED. Existing README and historical
review documents describe the retained v3 baseline, not approval for this change.

Baseline: canonical v3 archive 3f2a405f3b03d179f8f66d332e4c3287944ab78e0fbf040160923cc860aa8294.
Recovery independently checked the base and both published delta hashes, each
replacement size/hash, full inventories, and byte-exact deterministic v2/v3
archive reconstruction. No retained historical source was changed.

The bridge now imports owner_binding_v4. Its argument is exactly:
`{snapshot: <v4 observation>, reviewed_snapshot_sha256: <digest>}`.
The adapter checks the unchanged v4 validator and matches the canonical digest
before amending the disposable dependency plan. verify_result and finalize use
the same bridge path; a stale binding fails before stage-object reads.

owner_context_v4.py is a byte-identical copy from deployed source tree d0856737846e96a07ef79ecb76ac82df36c2223f
(test-portability c91b36894d7b2b3cc2ba20ee152f66d2f087b785 has the identical tree).
Its provenance and limits are unchanged. The caller must independently
authenticate saved capture bytes and supply the reviewed digest. A malicious
caller can invent both a snapshot and its digest; this module cannot authenticate
that caller. No snapshot is read from the VPS here. v4_synthetic.json is entirely
synthetic, generated with the deployed test_owner_adapter.fixture and pure adapter.
It must never be used for host preparation. The real digest is not embedded as
a default or an authorization token.

Only two current-host source ownership observation records may change.
A subsequent explicitly reviewed zlib_amendment changes exactly one additional
library content hash using NATIVE_ZLIB_PROVENANCE.json; it changes no ownership,
source route, target mode or policy. Its tests separately bind all affected pins. Existing four
content-addressed bundled acquisition routes remain the bridge's preexisting
behavior. Reporter, supervisor, launcher transformation, unit, permission policy,
historical dependency pins, symlinks and resource ceilings are unchanged.
The legacy owner_context.py remains for its original tests and historical record;
it is no longer the bridge's input validator.

| Acceptance item | Evidence/status |
| --- | --- |
| v4 schema plus independently supplied digest binding | Synthetic positive/negative tests only |
| Two-source-only amendment, unrelated dependency and policy preservation | Exact fixture comparisons |
| Finalizer validates binding before stage reads | Mocked negative test plus existing fake descriptor tests |
| Genuine saved-byte ownership replay | Separate PR191 report6453534354; raw snapshot not in this package |
| Current installed dependency bytes and fresh stage identities | Not checked by this local work; never repinned by inference |
| Native Codex startup/reporter/proc alias semantics | Still requires separately approved operational smoke test |
| Network, credentials, filesystem/process boundaries | No new live proof; retain existing fail-closed probes |
| Resource ceilings and terminal cleanup | Runtime sources unchanged; no new enforcement measurement |
| Model access / coding task / mailbox worker | Not enabled or tested |

Before a future stage, review this exact candidate, authenticate the complete
saved v4 snapshot against its reviewed digest, and perform the existing source,
fresh-object and topology checks. Dependency drift is an explicit blocked input,
not permission to update hashes. Preserve the consumed diagnostic and immutable
acceptance defaults. No current evidence calls for another ownership diagnostic.

Local verification: `python3 -B verify_compact.py` and
`python3 -B -m unittest discover -s tests -p 'test_*.py' -q`.
The four external inputs have subsequently been replayed with the existing
verifier (4/4). Running without them still reports 0/4; no additional signature
authentication of the historical systemd source is implied. These tests do not require
chroot, sudo, native Codex, providers or any host security mutation.
