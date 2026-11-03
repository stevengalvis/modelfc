# Mount visibility model: offline, not an admission check

`mount_visibility_prototype.py` is an isolated pure model; the bridge and
`storage_guard.py` do not import it. It demonstrates selecting an entry only when
a trusted mountpoint observation reports that entry's mount ID, while retaining
separately visible aliases. It rejects missing/unknown/inconsistent observations,
changed snapshots, noncanonical paths, and ambiguous basic inputs. It does not
verify mount permissions, source identities, credentials, cleanup or isolation.

`mount_observer_prototype.py` experimentally holds namespace descriptors, checks
the `/proc` filesystem type and current-thread identity, uses `openat2` with
in-root, no-symlink and no-magic-link resolution, reads the mount ID from thread
fdinfo and compares mountinfo and namespace identities. Unsupported
platforms or resolution errors block. It returns raw mount records and is not a
sanitized report, trusted observer, production check or guard integration.
The prototype caps mountpoints and checks a monotonic deadline between calls;
an individual blocking kernel call is not interrupted by that deadline.

`mount_guard_candidate.py` composes this view with the existing guard's mount,
source identity and working-directory checks in a standalone experiment. It
does not launch a child or participate in admission. Ignoring a covered entry
changes the guard's current behavior and is not yet proved equivalent or safe.

These checks reject an ordinary substituted filesystem; they do not authenticate
procfs against a privileged administrator, prevent replacement and restoration
between checks, or anchor all reads to the pinned descriptor. Before use, an
observer must establish trusted procfs and root provenance, keep
exclusive ownership of its descriptors (including against other threads), and
pin the service's mount, user and PID namespaces across observation and use.
It must bound elapsed time and work, account for path aliases and mount IDs,
and apply the **unchanged writable-mount and identity
policy** to every effective entry. Mountinfo parent IDs and propagation fields,
mount stacking, filesystem identity, inherited standard descriptors and access
through `/proc` or other processes need separate verification. A path-based ID
alone does not prove that a covered mount is unreachable by every other handle.

Two identical snapshots do not rule out a mount change followed by a revert,
mount-ID reuse, or later host-to-service propagation. The trust model must keep
privileged mount changes and competing worker launches controlled throughout the
entire task; unprivileged namespace creation and capabilities must be addressed
without breaking required nested tools. Root and other authorized privileged
administrators remain trusted. If these assumptions cannot be established, block.
`PrivateMounts=yes` can still receive host mount events. A private propagation
setting is a candidate for a separately reviewed test, not proof by itself, and
may keep host filesystems busy. Any worker-accessible mount, socket, file
descriptor or namespace escape capable of writing outside the bounded storage
is relevant; defending against a malicious host root is not the goal.

Actual integration also needs an immutable, identity-pinned way to load these
modules: the current guard is executed as a standalone `python3 -I` script, so
relative imports cannot simply be added or fixed by trusting the working
directory. Observation descriptors currently close before guard evaluation;
ordinary host mount propagation can invalidate the view before or after that
evaluation. A production candidate must make the mount topology stable for the
worker lifetime and verify its propagation mode, not infer safety from two reads.

Before any integration, independently review the actual kernel observer and
guard changes, exercise stacked and covered mounts and adversarial aliases in a
disposable real namespace, and test the exact complete systemd service on the
target host under separate authorization. A passing model or snapshot is not
host acceptance. Prior fixture state, unit and cleanup uncertainty are unchanged.
