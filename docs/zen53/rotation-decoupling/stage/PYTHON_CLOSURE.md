# Restricted collector dependency closure — offline successor

The October 27 failure imported the actual `collector_v3.py` into a projection
built for the older report. It omitted datetime, struct, signal, ctypes and its
ELF dependency. Adding datetime alone would expose the next missing module.
The collector is unchanged. Seven regular files and the libffi SONAME link
complete its import/startup closure on the exact pinned Ubuntu Python 3.12.3.
`_datetime`, `_struct` and `select` are built into that interpreter; `_ctypes`
is an extension requiring libffi. No unused pure-Python datetime fallback,
whole standard library, host mounts or policy changes are added.

`PYTHON_CLOSURE_PROVENANCE.json` binds the Ubuntu archive key, signed InRelease
and SHA256-indexed packages to every new member. All existing Python and linked
runtime-library hashes match those packages, including the pinned interpreter.
Package ownership is package metadata, **not a VPS observation**. Added routes
require the existing no-follow, no-atime hash/metadata checks on the host before
any fresh stage. No historical host metadata was invented or overwritten.

The dependency inventory, runtime manifest, recovery routes, diagnostic pins,
launcher constants, stage inventory and operator package chain must agree.
The source validator now expects 62 routes (55 previous plus seven files).
The old reconstruction script remains exclusively for its immutable historical
checkout; use the successor packaging recipe for this candidate.

## Offline checks

Acquire authenticated packages into a disposable test cache using
`packaging/prepare_python_fixture.py --download --cache CACHE --output FIXTURE`.
Without `--download` it requires already cached pinned archives and stays offline.
Set `ZEN53_PYTHON_FIXTURE=FIXTURE` and run unittest discovery in `stage`.
The startup test copies only pinned Python/ELF members and the declared SONAME
links into a fresh projection; starts the pinned loader/interpreter with
`-I -S -B`; executes the real collector's entire startup through its signal
registration, stopping before host observations; exercises datetime, struct,
ctypes/libc, select, and hashing; and rejects any external module or file mapping.
The loader cache is disabled. The locale is C to avoid reading cloud locale data.
Every added file/link is removed in turn and must fail, including host fallback.
The original membership reproduces the datetime traceback through the real
controller: restricted exit 1, host process-group kill (-9), no finish command,
reaped children, and blocked outcome. An uncertain reap remains CLEANUP_UNCERTAIN.

CI also requires `ZEN53_CHROOT_TEST=1`: the disposable runner's existing sudo
runs `chroot --userspec` to drop back to the test UID/GID before starting the
pinned interpreter. This test uses the literal declared links and no host mounts,
/proc, or host-library fallback. It executes only startup and exercises, then
removes every new file/link in turn and requires failure. Local executors without
sudo/chroot report that one test skipped; CI must pass it without a skip.

These tests verify the exact interpreter/library projection, not Bubblewrap
namespaces, cross-user ownership, live probes or complete cleanup acceptance.
The cloud executor rejects Bubblewrap UID mapping with a read-only filesystem;
no policy adjustment is made. Existing isolation/host acceptance gates remain.

## Protected failed attempt

Nonce c849ac66f41f09b4f173770caf422bb7, attempt
85a9d88f4d368218bdbba29af71e6308 and manifest
53531cda623b0ce3aba5163f00dc0c99cda557a7a155cd4ed90a93ecdc590bdc
remain immutable, held, UNKNOWN_OR_BLOCKED_NO_RETRY / cleanup UNVERIFIED.
A child SIGKILL in the synthetic regression establishes a possible controller
path; it does not identify the live signal sender or prove/exclude OOM.
Scope/cgroup absence, no sampled descendants and unchanged/unheld locks do not
satisfy the complete acceptance contract. No retry, retirement, installation,
new approval, host diagnostic or production change is authorized by this code.

