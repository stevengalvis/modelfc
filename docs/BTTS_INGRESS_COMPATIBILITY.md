# ZEN-49 BTTS ingress compatibility patch — not installed

[PR182 diagnosis](https://github.com/stevengalvis/modelfc/pull/182#issuecomment-6271310716)
confirmed the active API supports E0/ALL/as-of (HTTP 200 direct), while Caddy's
`@btts_performance` accepts only empty query or `competition=E1` (404 public).
This corrects that omitted rollout dependency for
[ZEN-49](https://linear.app/galvis/issue/ZEN-49/complete-league-performance-and-history-parity).
No application, validator, collection, settlement or scheduling change is included.

## Exact patch and scope

`ops/vps/patch_btts_ingress.py` prepares a **separate candidate** from a SHA-256-pinned
copy of the installed file. It replaces only the diagnosed expression inside the
unique `@btts_performance` block. The block must have the diagnosed exact path,
GET method, expression and directive order; drift/duplicates/already-patched input
fail closed. Whitespace/newlines and every byte outside that expression survive.
The output must not exist. It neither installs nor reloads anything.

Reviewed patch artifact SHA-256:

```
bbfdcc17c341e5358b9409e0b55e65956069d14cc86a3620f8916e3daaa49644  ops/vps/patch_btts_ingress.py
84c575cbfe448ccf10bd7b68c27594ed98e94ab94ea4ee0261633de4f3fdc51f  deploy/proposals/btts-performance-query.expression
```

The candidate's own SHA-256 depends on the genuine installed file and must be
recorded during operator preparation. No full host configuration/hash is invented.
**Never replace the installed Caddyfile with `deploy/modelfc-api.Caddyfile`: that
repository template omits separately installed routes.** No import or new runtime
file dependency is installed; the expression is embedded in the candidate.

Accepted raw-query grammar (anchored, bounded, no additional parameters):

- Empty query; `competition=E1`, `E0`, or `ALL`.
- Optional single `as_of=YYYY-MM-DDTHH:MM:SS[.fraction]ZONE`, alone or alongside
  competition, in either order. Fraction has 1–6 digits; ZONE is `Z` or a signed
  `HH:MM` offset. Colons may be literal or `%3A`/`%3a`. Positive signs must be
  `%2B`/`%2b` (raw `+` means space in query decoding). Negative signs stay literal.
- This includes real-client URLSearchParams encoding and its UTC millisecond
  cutoff. Keys and competition values must be literal, not percent-encoded aliases.
- Unknown/duplicate/empty keys, extra separators, encoded delimiters, malformed or
  double encoding, whitespace, missing timezone and overlong fractions are denied
  at ingress. Date/range validity and future cutoffs remain backend 422 validation.

The unchanged handler remains exact-path GET-only, `Cache-Control: no-store`,
upstream `127.0.0.1:8000`; sibling routes/mutations do not gain exposure. Empty EPL
remains unsupported evidence, not new collection capability. Caddy expression
semantics: https://caddyserver.com/docs/caddyfile/matchers#expression .

## Verification

`tests/test_btts_ingress.py` launches real Caddy with admin/TLS disabled on an
isolated loopback port and a synthetic HTTP upstream. It exercises the exact
rendered patch, proxy forwarding, no-store, both orders/encodings, invalid queries,
mutation/sibling rejection, backend date rejection and an unchanged unrelated
route. There are no provider or production requests. Patch tests verify byte-level
preservation and drift rejection. Dedicated CI fetches official Caddy **2.10.2**
with a pinned SHA-512 and runs all five tests; normal offline suites skip the three
socket gateway tests when no binary is supplied. Run locally with:

```sh
MODELFC_CADDY_BIN=/path/to/disposable/caddy python3 -B -m unittest tests.test_btts_ingress -v
```

The installed Caddy version has not been supplied; its own adapt/validate checks
remain mandatory before application. Do not install a different Caddy version.

## Later operator approval: backup, validate, reload, rollback

**No production application is authorized by this PR.** After code review/checks,
obtain separate approval covering only this matcher and a Caddy reload. Do not
restart the API or touch permissions, timers, providers or production records.

1. Read the actual Caddy version/service configuration and active admin configuration
   using the previously verified local admin address. Confirm the diagnosed matcher,
   public host, upstream, GET/no-store handler and fallbacks still match. Preserve
   the active baseline privately. Stop on drift; do not normalize unrelated routes.
2. In an operator-controlled private staging directory, copy `/etc/caddy/Caddyfile`
   with `cp -a` to `Caddyfile.before` (preserving metadata). Record its SHA-256 and
   verify the two patch artifact hashes above from the reviewed checkout. Prepare:

   ```sh
   python3 /REVIEWED/ops/vps/patch_btts_ingress.py \
     --input /PRIVATE/Caddyfile.before --sha256 ACTUAL_ORIGINAL_SHA256 \
     --output /PRIVATE/Caddyfile.payload
   ```

3. Inspect the unified diff: only one expression changes. Record candidate SHA-256.
   With the **installed** Caddy binary and its actual trusted configuration
   environment, run `caddy adapt --config /PRIVATE/Caddyfile.payload --adapter caddyfile`
   and `caddy validate --config /PRIVATE/Caddyfile.payload --adapter caddyfile`.
   Compare adapted before/after JSON: only that matcher expression may change.
   Keep output private. Stop on any unexpected difference or validation failure.
4. Recheck the installed file's hash and active configuration against the backup
   immediately before application; stop if either changed. Create a sibling candidate
   on the `/etc/caddy` filesystem using `cp -a` from the original, then overwrite
   **that candidate's content only** with the validated payload. Verify its content
   hash and owner/group/mode match the approved payload/original respectively.
   Atomically rename that sibling over `/etc/caddy/Caddyfile`. Do not change metadata.
5. Run the existing `systemctl reload caddy` once (not restart). Confirm success and
   use read-only admin GET to verify the loaded matcher and unchanged other routes.
   Compare direct/public GETs for E1, E0, ALL, as-of alone and both parameter orders,
   including client-encoded timestamps. Confirm expected data, no-store, and 404
   for unknown/duplicate queries and mutation methods. Verify canonical UI at
   `https://zenofc.com/research/btts/performance`; do not use the old Vercel alias.
6. If reload or acceptance fails, stop. Restore the exact metadata-preserved backup
   through a sibling file and atomic rename, validate it with the same installed
   binary/environment, then reload Caddy once and verify the original loaded config.
   Keep both hashes/reports and report any restoration failure. Never compensate
   with an API restart, broad route exposure or permission changes.

Merge alone cannot install this host patch. Public filter acceptance and broader
E0/cross-market reporting dependencies remain open until separately verified.
