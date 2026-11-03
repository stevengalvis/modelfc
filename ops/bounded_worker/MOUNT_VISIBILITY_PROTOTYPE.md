# Mount visibility model: offline, not an admission check

`mount_visibility_prototype.py` is an isolated pure model; the bridge and
`storage_guard.py` do not import it. It demonstrates selecting an entry only when
a trusted mountpoint observation reports that entry's mount ID, while retaining
separately visible aliases. It rejects missing/unknown/inconsistent observations,
changed snapshots, noncanonical paths, and ambiguous basic inputs. It does not
verify mount permissions, source identities, credentials, cleanup or isolation.

The model assumes observations that do not yet exist in this implementation. A
future observer would need to pin the service's mount, user and PID namespace
identities, anchor resolution at the correct root, securely open each mountpoint
without following symlinks or magic links, and obtain the opened object's kernel
mount ID through a verified kernel interface. Open failure or unsupported kernel
behavior must block. The observer must account for path aliases and mount IDs,
protect its own descriptors, and apply the **unchanged writable-mount and identity
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

Before any integration, independently review the actual kernel observer and
guard changes, exercise stacked and covered mounts and adversarial aliases in a
disposable real namespace, and test the exact complete systemd service on the
target host under separate authorization. A passing model or snapshot is not
host acceptance. Prior fixture state, unit and cleanup uncertainty are unchanged.
