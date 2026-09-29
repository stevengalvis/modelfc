# Read-only prospective API ingress

This is a staged installation plan, not authorization to install or activate
anything. The only public routes are exact `GET /api/v1/predictions`,
`GET /api/v1/opportunities`, and `GET /api/v1/prospective/performance`.
The reviewed template also permits `GET /api/v1/opportunities/<32 lowercase hexadecimal characters>`.
All other methods and paths, including analysis writes, capabilities, malformed
detail IDs, trailing slashes and FastAPI's documentation, return 404 at Caddy. CORS is a browser
policy, not the access-control boundary.

## 1. Repository review, merge, and code deployment

Review `ops/vps/api_launch.py`, `deploy/modelfc-corner-api.service`,
`deploy/modelfc-api.Caddyfile`, public error handling and frontend behavior.
Review the narrowly scoped prospective evidence ACL changes and trusted
`ops/vps/prospective_launch.py` update as part of the same compatibility gate.
Merging deploys a release through the existing controller. It does not install
trusted files, install Caddy, start the API, enable the API unit, switch Vercel,
alter DNS or touch the prospective timer/state. The deployed release includes
the Python dependencies already used by the existing FastAPI tests.

## 2. Separately authorized API runtime installation

Before any host change, record current release SHA, effective services, listeners,
firewall state, owners/modes, and history/state lock inodes. Preserve copies of
any files replaced. Create a dedicated non-sudo `modelfc-api` account with no
membership in `modelfc-runtime`, deploy, validator or credential groups. Check
that the account cannot inspect the running writer's `/proc/<pid>/environ`,
send it signals, read the source credential or traverse private budget paths.
Do not give the API account blanket access to state or history.

Under the existing prospective `runner.lock` (and the state lock if present), grant the new identity
traversal on `/var/lib/modelfc`, traversal on state, read access to the
existing `state/prospective/runner.lock` and, **only if it already exists**,
`state/.lock`, and read
plus traversal **only** on the six public evidence directories and their
existing nested directories: `analyses`, `analysis-outcomes`, `predictions`,
`prediction-targets`, `market-observations`, `opportunities`. Grant read access
only to their existing referenced JSON records. Record before/after metadata,
bytes and hashes; do not replace, edit or reserialize any immutable evidence.
Do not grant traversal/read to `prospective/control.json`, `budget-events`,
refresh internals or provider credentials. Check an unrelated root-owned or
runtime-private file remains unreadable. Existing `0600` records need an
explicit one-time read ACL; directory inheritance alone cannot expose future
temporary files created as `0600`.
An empty prospective state may have no `state/.lock`: the writer creates it lazily
on the first state publication. Treat that absence as valid during installation;
do not create a placeholder lock or change the writer's lock inode. Verify the
existing lock is an empty, singly linked regular file owned by the runtime account before granting
its one-time read ACL, if present. The one-time ACL on `runner.lock` is still
required even for an empty state.

Install the reviewed updated `prospective_launch.py` as the root-owned trusted
launcher only after the account, ACL tool, migration and compatible reviewed
release are verified. Coordinate with the hourly timer, wait for the current
run to exit, and preserve the old launcher for rollback. The new trusted
launcher opts the writer into `MODELFC_EVIDENCE_ACL_USER=modelfc-api` in its
clean child environment. Only the six evidence record families receive a read
ACL on the unnamed temporary inode **before** atomic publication; their new parent
directories receive narrowly scoped traversal/read ACLs through validated
directory descriptors, with path identity checked after each ACL. ACL failure aborts
that record before publication and does not refund provider reservations.
The writer creates an unnamed temporary evidence inode in the destination
filesystem, keeps it open through serialization and ACL setup, then links the
validated descriptor directly into the immutable record name. No temporary
pathname alias remains writable after publication. If the filesystem cannot
create/link unnamed temporary files, public evidence publication fails closed;
verify this capability with disposable offline state before enabling the new
trusted launcher.
For the first evidence publication, the writer creates and holds the real
`state/.lock`, validates it as a regular file owned by its runtime UID, and
rejects additional hard links. It grants the API identity read access through
a validated descriptor for the writer's **already-held state lock**, verifying
that the pathname still names that inode, before linking the immutable evidence into
place. The same validation and ACL grant occur for later publications without
replacing the lock inode. If the lock or evidence ACL cannot be established,
publication fails closed; investigate before relying on the public reads.
Older installed launchers do not opt in even when new code is deployed, so
code merge alone does not change the active writer or its state. Verify new
file ACL behavior using disposable offline fixture state, not real collection.

Install the reviewed API launcher as root:root, mode 0755 at
`/usr/local/libexec/modelfc-api-launch.py`, and the reviewed unit as root:root,
mode 0644 at `/etc/systemd/system/modelfc-corner-api.service`. Supply the
verified production Vercel origin, for example `https://modelfc.vercel.app`,
as **one** root-owned regular file `/etc/modelfc/api-cors-origin` (mode 0644).
That file is an origin, not a URL with `/api/v1` or a comma-separated list.
Inspect the effective unit and reject unexpected drop-ins, environment files,
credentials, `ReadWritePaths`, startup hooks, shell wrappers or public binds.

`WorkingDirectory=/srv/modelfc/current` selects one physical release at
process start. The trusted root-installed launcher verifies that physical
`<sha>-<nonce>` directory, the protected SHA marker, the venv and source paths,
the external config and private state, then passes a clean explicit environment
to the pinned venv's Uvicorn. No release-provided shell executes. Uvicorn binds
only `127.0.0.1:8000`, with one worker and bounded concurrency. `MODELFC_DATA_CONFIG`
is `/etc/modelfc/corner_data.json`, `MODELFC_STATE_DIR` is
`/var/lib/modelfc/state`, and `MODELFC_CORS_ORIGINS` comes from the verified
origin file. There is no OddsPapi credential or provider environment.

The API runs under the distinct `modelfc-api` UID. Its selected read ACLs let it
open future `0600` evidence while it cannot inspect the writer process or its
environment. `ProtectSystem=strict` and no write exceptions make its
production filesystem read-only inside the API unit;
credential directories, including systemd's `/run/credentials`, are hidden.
Confirm this **on the host** with a safe,
separately created disposable fixture: it can read future-style private files
and take shared locks, but cannot write a fixture within state. Do not test a
write against real evidence or mutate a live lock. Verify effective sandbox,
environment and loopback bind, and external inability to reach port 8000.
Confirm that this account cannot read other state files, history, budget events
or the writer's credential even if the API unit's mount sandbox were absent.

After a verified start, this long-running process continues using its pinned
physical release through any deployment promotion. `RuntimeMaxSec=30min` and
`Restart=always` make it start afresh from `current` within roughly 30 minutes
of a successful promotion, without granting the deployment controller new sudo
commands or changing the prospective timer. The short restart interrupts in-flight
reads. If the new release is incompatible, it fails closed and retries after
10 seconds; inspect its startup SHA and health after every promotion. Keep old
successful releases while an API process may reference them. Existing failed
promotion cleanup can remove a briefly selected candidate: coordinate promotions
with API restarts until that deployment limitation is fixed, and stop the API if
the selected release is deleted. A rollback that changes `current` requires a
separate reviewed deployment decision; restarting the API alone does not roll
back state schemas or undo immutable evidence.

## 3. Separately authorized Caddy and TLS installation

Choose the stable production API **hostname** before this stage; it is the one
unresolved identifier. Install stock Caddy and the reviewed Caddyfile as
root-controlled files. Supply `MODELFC_API_HOST` to Caddy through a trusted
root-owned systemd drop-in, not an untrusted release or a guessed repository
default. Validate its effective unit and `caddy validate --config
/etc/caddy/Caddyfile` before starting. Ensure the hostname resolves to the VPS
and that trusted public TLS issuance and renewal work. Restrict the firewall to
the intended SSH access and HTTPS plus the minimum ACME challenge port needed
by the selected certificate method. Never publish port 8000 or a wildcard
upstream route. HTTP may be used for ACME and redirects; application JSON must
be delivered over trusted HTTPS. If a name is not available, do not activate
this host-template configuration as a bare IP: Caddy's automatic local-IP
certificate is not a publicly trusted browser certificate.

Before a separately authorized ingress update, validate the reviewed Caddyfile
and test a real-format detail ID through TLS. Verify valid GET reaches the API
(returning detail or sanitized `OPPORTUNITY_NOT_FOUND`), malformed/uppercase IDs,
trailing slash and POST/PUT/PATCH/DELETE return Caddy 404, the three existing
GET routes still work, and analyses, capabilities and docs remain blocked.
Inspect no-store response headers and that the upstream remains loopback-only.
For rollback, restore the previously verified Caddyfile and reload Caddy only
under separate host authorization. The detail page will then receive a 404;
the three existing reads and immutable evidence remain available. A code-only
rollback must account for an ingress rule still pointing at the old detail route.
Newly published prospective predictions use schema v2 for frozen historical
counts. Schema v1 remains readable by the new runner/API, but older releases
that accept only schema v1 cannot safely resume against a state containing v2
predictions. Before a code rollback, inspect the immutable prediction inventory;
if any v2 record exists, roll forward to a compatible release rather than
editing evidence or starting an incompatible old runner. Caddy-only rollback
does not change record compatibility.

Stock Caddy does not ship an HTTP request rate-limiting module. Do not add a
third-party plugin solely for this deployment. Caddy rejects all but three GET
routes; Uvicorn limits concurrency and systemd limits CPU, tasks and memory.
Monitor request volume, 503s, memory and response latency. If public load
demonstrates a need for per-client limits, review a separate stock-compatible
edge or firewall solution before expanding exposure. There is no claim of
per-IP rate limiting in this first installation.

## 4. DNS and external verification

Separately authorize a DNS A record for the selected name. From outside the
VPS, verify a trusted certificate and each of the three JSON GETs. Verify
`POST /api/v1/analyses`, capabilities, malformed opportunity details, docs, OpenAPI,
unknown paths, trailing slashes, and non-GET methods fail at Caddy. Test exact
origin CORS from the production frontend; `OPTIONS` should not be required for
the simple browser GETs after their unnecessary Content-Type header is removed.
Test that even direct loopback POST from the API's sandbox cannot write state.
Inspect that evidence bytes, provider credentials, and prospective timer remain
unchanged; only the separately approved access ACL metadata may differ.
The read routes may return transient 503 while the writer holds its lock;
do not weaken locking to avoid that status.

## 5. Separately authorized Vercel live-mode switch

After external verification, set production `NEXT_PUBLIC_MODELFC_API_MODE=live`
and `NEXT_PUBLIC_MODELFC_API_URL=https://<selected-host>/api/v1` in the existing
Vercel project, then deploy a new build. These values are public client-side
configuration, never secrets. `api-cors-origin` must equal the frontend's actual
production origin. `/predictions` and `/performance` consume real evidence;
`/analyze` explicitly remains unavailable. A preview deployment needs its own
explicit origin decision; do not permanently allow a temporary wildcard.

## Rollback

Restore the previous Vercel environment/build if necessary, and stop/disable
the new API/Caddy units or remove the new ingress under separate host approval.
If rolling back application code, verify its compatibility with current
prospective state, including discovery retry fields, before selecting an older
release. If reverting the trusted prospective launcher to one without ACL
publication, take the public API offline first: new immutable files otherwise
become unreadable to the API account. Never restore or delete immutable evidence, reset budget control, or
stop the prospective runner as a shortcut. Preserve existing release and host
manifests for comparison; revoke only the new exposure, leaving writers active.
