# ZEN-53 PR #212 source reconstruction: local review candidate

This is an offline packaging route, not permission to publish, install or run a sandbox. The earlier no-subprocess requirement was withdrawn. The sole reconstruction subprocess is fixed GNU gzip; Python constructs both tar streams.

## Immutable inputs and outputs

- Source: draft PR #212, commit `f79866666a8b464854929875825498b8fd8ccdf0`, full tree `cc93858a99e642afb915cbd2a8c1bdd17213b988`, source subtree `844411fa065487b29afb30b05bb8058fc5a6a61e`.
- Stage: 37 source files, internal inventory SHA256 `c74f259e3a94fc31c1792d66c13fff4f2149430688dc18db932137db157a985c`; reconstructed archive 126618 bytes, SHA256 `a527085d5fb968fed1577a9afdea4d8173fef1b931ec06012cc49c3d909a4d33`, 38 tar members.
- Reduced operator: 17 source files, internal inventories SHA256 `b8edd449ae8ef62b8596ba4c52c38c0f84dc7a82fd80636be9d529cc922ebd80` and `842069099abebbedd4bcc80f0a31ccc7c2ff01f7f5418e14d0df5b5e4fe6ebfe`; reconstructed archive 67338 bytes, SHA256 `692386f512bdea0dab54426c5a0336f4f9d0ae9fbcfce712eb89143275b705a1`, 19 tar members.

The acquisition step must verify `git rev-parse HEAD` equals the exact commit above, with credential prompting disabled and no hooks/submodules/build steps. The reconstruction script separately checks exact source member sets and every inventory hash; it does not invoke git. The source checkout alone cannot be passed to the installed builder, which requires the authenticated stage archive.

## Reconstruction boundary

`zen53_reconstruct_pr212_archives.py` constructs the stage GNU-format tar in Python with sorted names, fixed UID/GID/mtime and the original file modes. Python constructs the operator PAX tar/gzip level 9. Only the stage stream invokes `/usr/bin/gzip -n -6 -c`, because Python zlib level 6 produces different compressed bytes. The subprocess uses no shell, a clean environment, a fixed absolute executable/arguments, 1 MiB input and output bounds, 10-second CPU and 30-second wall bounds, 256 MiB address-space bound and fail-closed exit handling. Source files are bounded regular single-link no-follow reads with before/after identity checks. Neither archive is written until both exact output sizes and SHA256 pins match; the new output directory is exclusive and any partial write is retained, never repaired in place.

The recipe requires a 64-digit expected `/usr/bin/gzip` SHA256 and root:root non-writable regular-file metadata on the VPS. The expected hash must come from independently checked installed-package evidence; a caller-supplied hash alone is not authentication. The original archive output pins protect against different gzip or Python behavior but do not by themselves prove a hostile executable has no side effects. The existing trusted-root/cooperating-operator assumption still applies. No VPS gzip pin has yet been established in retained evidence.

## Offline verification and remaining approval

The exact PR checkout reconstructed both original archives byte-for-byte using local GNU gzip 1.13 and Python 3.12.14. Eight focused tests passed, including poisoned `GZIP` environment, changed/extra/symlink source, wrong checkout, wrong gzip pin/owner, oversized input, timeout/kill and nonzero exit. Local gzip SHA256 `1fdd057912e3808c8cb8fa939bb94fcb27ae9d2961e6b8abbbe7888e63fbc9cc` and cloud-namespace UID/GID 65534 are **not** VPS pins. The recipe and test file are unpublished review candidates. A preparation-only VPS handoff still needs approved delivery of this exact recipe and a trusted installed gzip package/binary identity check, then exact output-hash verification. No change to sandbox policies or host settings is needed by reconstruction.

Before any v4 admission-policy installation or fresh stage, independently check the installed API unit `/etc/systemd/system/modelfc-corner-api.service` as root:root 0644 regular, SHA256 `7e40efa99e085d33778d2abe2ecb687e455a02e59700d495fc8091427214879a`, and launcher `/usr/local/libexec/modelfc-api-launch.py` as root:root 0755 regular, SHA256 `c80e60c20b9560abec88f31a23e1a4050153ae279a0148f4ec2c86521de85408`. Inspect effective systemd state and drop-ins without starting or reloading services. A mismatch blocks policy installation. Preparation must preserve v3 and historical stages, run the existing 107/67/23 synthetic suites and 55-route source preflight, and stop before approval issuance, locks, scope, collectors, native Codex or model calls.
