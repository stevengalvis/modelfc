# Optional Sportmonks credential at service startup

This change depends on PR129's retained-history and explicit missing-provider
behavior. Deploy its runner before installing this service template. Installation
is a separate approved host operation; a Git merge/deployment does not replace
root-installed units.

The only service change adds this **public, non-secret** default alongside the
unchanged absolute `LoadCredential=sportmonks.key:...`:

```ini
SetCredential=sportmonks.key:MODELFC_SPORTMONKS_NOT_CONFIGURED
```

systemd gives the loaded file priority. If its source is absent, it delivers the
public marker through the existing credential directory. The runner maps exactly
those marker bytes to `None` before any provider construction. It never sends the
marker as a token. Direct missing-file invocation remains supported. Delivered
empty, whitespace-only, non-ASCII, oversized, linked or malformed-marker inputs
reject; permission errors are not converted to absence. Actual tokens retain the
existing handling. An expired/revoked real token is still present: HTTP 401/403
uses PR129's sanitized authorization block/cooldown, not the missing-file path.

Calendar credentials and forecast configuration remain mandatory. Credential
source paths, runtime identity, private systemd credential delivery, sandbox,
permissions, launcher and schedule remain unchanged. No raw-key filesystem access
or environment-token fallback is added. `SetCredential` is never used for a secret.

Official contract: [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#SetCredential=).
The [systemd v255 implementation](https://github.com/systemd/systemd/blob/v255/src/core/exec-credential.c)
makes ENOENT nonfatal when a fallback exists; other acquisition errors still fail.
Verify the installed host version and effective behavior rather than assuming it.

## Isolated host acceptance, before installation

This development container has no systemd manager as PID 1. Parser/unit-contract
and actual Python subprocess tests are not a systemd credential-delivery test.
The real test remains a required VPS acceptance step.

Inspect the installed unit and local `systemd-run`/systemd documentation first.
Use uniquely named, bounded transient oneshots and a new disposable root with only
dummy inputs. Do not copy real credentials, invoke the installed launcher, touch
production state, change installed units or reload the manager configuration.

Reproduce the template's runtime User/Group, credential names, absolute source
loading, new SetCredential default, hidden source directory, umask and relevant
sandbox restrictions. Substitute disposable paths and a harmless probe entrypoint
for production source/state/ExecStart. Deny networking (`PrivateNetwork=yes` and
appropriate address-family restrictions), deny production state and credentials,
limit runtime/tasks/memory, and inspect effective properties. The probe uses the
candidate's `_sportmonks_credential` and checks only booleans/expected dummy values;
it emits fixed statuses, never credential contents. Also verify direct source
reads are denied from inside the service. Use separate cases:

1. All dummy inputs present: loaded dummy token wins over fallback; startup reaches
   the probe and the source directory remains inaccessible.
2. Only dummy Sportmonks source absent: startup reaches the probe, delivered marker
   maps to None, and no client/network attempt occurs.
3. Dummy calendar or configuration source absent: credential loading fails before
   the probe. These remain required.
4. Empty or malformed dummy Sportmonks input: delivery must not silently select the
   fallback; the Python parser rejects. Test an actual non-ENOENT acquisition error
   safely if supported; otherwise report that specific host case untested.
5. Run the real runner entrypoint with offline injected history/calendar/transport
   on separate copied state, covering complete retained history and missing history.
   Preserve original clocks/pins; label historical-clock replays as simulations.

Record systemd version, exact candidate SHA, real identities, effective isolation,
entrypoint reached/not reached, credential precedence and sanitized results. Clean
up only the test's identified units/processes/files. Do not remove real keys to
prove absence. A harness failure blocks installation, not permission weakening.

## Proposed host change — approval required

After PR129 and this change are reviewed, merged and deployed, install only the
reviewed forecast service template (or an explicitly reviewed equivalent single
SetCredential directive) under the existing ownership/mode. Validate effective
configuration and reload systemd only with separate host approval. Keep the timer,
launcher, real credential files and all ACLs unchanged; do not manually trigger
provider acquisition. Verify the next ordinary run and original bytes read-only.
If rollback is needed, reverting just the service default restores the prior
mandatory behavior; no data/credential rewrite is involved.

## Separate research settlement activation

PR131 `research_outcomes.sweep` already examines all frozen odds-free research
fixtures independently of the upcoming runner's 21–27-hour window. Future kickoffs
are UPCOMING; elapsed fixtures require validated completed final evidence. Missing
sources remain UNRESOLVED (`NO_SAVED_RESULT`/`RESULT_NOT_AVAILABLE`), conflicts reject,
and replay reuses outcomes. Elapsed kickoff alone is not final-state proof.

No new model or acquisition-window change is needed for that sweep. It accepts an
explicit pinned CSV or a pinned saved-evidence index; it does not fetch or discover
finals. Supplemental history alone is not automatically a saved settlement bundle:
existing manifest/response/independent-calendar pins must satisfy the adapter.
Existing bookmaker-only BTTS is covered by the separate `btts_outcomes` command;
the research sweep enumerates odds-free records, not a new universal inventory.

The missing operational piece is a separately reviewed bounded offline invocation
and source/index publication contract: an existing authorized evidence publication
hook, or a dedicated offline oneshot/timer independent of forecast eligibility.
It must use existing runtime/state locks, narrowly published API-read records,
read-only pinned evidence, no credentials/network, bounded runtime and explicit
unresolved receipts. New root-installed launcher/unit/configuration and any timer
schedule need separate approval. If automated publication of the pinned evidence
index is desired, that glue is additional code, not included or claimed installed
here. Do not promise automatic future settlement until both invocation and valid
final-evidence availability have been proven.
