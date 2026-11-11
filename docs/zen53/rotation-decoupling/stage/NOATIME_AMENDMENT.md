# No-atime source-read amendment

The prior offline successor archive remains unchanged at SHA256
`2b4a34b6c921fbf6d6baa2340f3f22e9c618fad43f4b80b35285b6f75d7ec4f0`.
This separate candidate adds `O_NOATIME` to the installed Codex file hash
opens in `restricted_launcher.verify_release_files` and installed controller
hash opens in `admission.cooperative_sources_verified`. The reported host
release lives on ext4 with `relatime`; ordinary reads may update atime.
There is no fallback if a no-atime open is denied. The file-inode/content
and cooperative lock contracts are otherwise unchanged.

This prevents these *two explicit preflight reads* from updating atime. It
does not claim that an entire live diagnostic has zero metadata side effects:
executing ordinary tools and reading other mounted files may affect access
times under host policy. No content, permissions, mounts, services or production
state are intentionally changed by the diagnostic design. The live test still
requires separate approval and all predecessor runtime gates.

Synthetic tests assert the source-open flags and fail-closed permission errors;
they do not prove filesystem behavior on the VPS. Do not use the historical
operator procedure as a ready-to-run command.
